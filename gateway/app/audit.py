"""The audit log: who did what through the gateway, and how it ended.

Rows go to the audit_log table of a separate database (mcp_logs, created by
deploy/db-init.sh). The gateway's role can only add and read rows, and a
trigger refuses UPDATE, DELETE and TRUNCATE.

Every row has a severity and is linked to the row before it:
hash = HMAC-SHA256(AUDIT_HMAC_KEY, prev_hash | the row's columns). Editing,
removing or reordering a row breaks the chain from there on, and
`python -m app.audit` finds the place. Each event is also logged as one JSON
line with its hash, so a copy of the chain exists outside the database; that
copy is what shows rows cut off the end, which the chain alone cannot.

AUDIT_DETAIL sets how much of a tool call is kept:
  minimal   a hash of the arguments only
  standard  the arguments with secret-looking fields redacted, and the error text
  full      the arguments as sent, and the result text

Known limit: someone with the HMAC key AND the right to disable the trigger
can rebuild the chain. The logged lines are the defence against that.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.config import settings

log = logging.getLogger("audit")

INFO, NOTICE, WARNING, ERROR = "INFO", "NOTICE", "WARNING", "ERROR"  # the severities in use

GENESIS = "0" * 64  # prev_hash of the first row
# The hashed columns. The same list, in the same order, as deploy/db-init.sh.
COLUMNS = ("ts", "severity", "category", "action", "outcome", "user_id", "upstream", "tool", "duration_ms", "client_ip", "detail")
_LOCK = 0x6D63706C6F67  # "mcplog": one writer at a time across replicas, so the chain has one order
_MAX_TEXT = 2000  # arguments, results and error texts are cut to this

# Set per request by app/guard.py; None for events from background work.
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)
# Which dashboard chat and which message in it a request says it belongs to (app/guard.py reads
# the headers). Recorded only for the backend's chat agent: see call_tool in app/tools.py.
chat_ref: ContextVar[dict[str, str]] = ContextVar("chat_ref", default={})


def chain_hash(prev_hash: str, event: dict[str, Any], key: str | None = None) -> str:
    fields = {column: event[column] for column in COLUMNS}
    # Written out by hand: isoformat() drops the microseconds when they are zero.
    fields["ts"] = event["ts"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hmac.new((key or settings.audit_hmac_key).encode(), f"{prev_hash}|{body}".encode(), hashlib.sha256).hexdigest()


def link_ok(row: Any, prev_hash: str) -> bool:
    """Does this row follow the one whose hash is prev_hash, unchanged?"""
    return row["prev_hash"] == prev_hash and hmac.compare_digest(row["hash"], chain_hash(prev_hash, row))


_SECRET_FIELD = re.compile(r"password|passwd|secret|token|api[_-]?key|authorization", re.IGNORECASE)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[redacted]" if _SECRET_FIELD.search(k) else _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def describe_call(arguments: dict[str, Any], outcome: str, result_text: str | None) -> dict[str, str]:
    """The detail of a tool call, as much as AUDIT_DETAIL allows."""

    def dump(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    level = settings.audit_detail
    # Always kept: it shows two calls had the same arguments without storing them.
    detail = {"args_hash": hashlib.sha256(dump(arguments).encode()).hexdigest()}
    if level == "minimal":
        return detail
    detail["arguments"] = dump(arguments if level == "full" else _redact(arguments))[:_MAX_TEXT]
    if result_text is not None and outcome != "ok":
        detail["error"] = result_text[:_MAX_TEXT]
    elif result_text is not None and level == "full":
        detail["result"] = result_text[:_MAX_TEXT]
    return detail


class Audit:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._stored_last = True  # did the last write reach the database

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(settings.logs_database_url, min_size=1, max_size=2)

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def usable(self) -> bool:
        """False while the log cannot be written. Tool calls are refused then,
        so nothing runs unrecorded. Only asks the database after a failed write."""
        if not self._stored_last:
            try:
                await self._pool.fetchval("SELECT 1", timeout=2)
                self._stored_last = True
            except Exception:
                pass
        return self._stored_last

    async def record(
        self,
        severity: str,
        category: str,
        action: str,
        outcome: str,
        *,
        user_id: str | None = None,
        upstream: str | None = None,
        tool: str | None = None,
        duration_ms: int | None = None,
        **detail: Any,
    ) -> bool:
        """Adds one event and says whether it reached the database. Never raises:
        a caller's request must not die of a logging failure."""
        event = {
            "ts": datetime.now(timezone.utc),
            "severity": severity,
            "category": category,
            "action": action,
            "outcome": outcome,
            "user_id": user_id,
            "upstream": upstream,
            "tool": tool,
            "duration_ms": duration_ms,
            "client_ip": client_ip.get(),
            "detail": json.dumps(detail, sort_keys=True) if detail else None,
        }
        # The logged copy leaves the detail out: arguments do not belong in container logs.
        line = {**event, "ts": event["ts"].isoformat()}
        del line["detail"]
        try:
            # shortcut: one transaction per event, in turn; batch the writes when calls reach hundreds a second.
            async with asyncio.timeout(5):
                async with self._pool.acquire() as conn, conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK)
                    prev_hash = await conn.fetchval("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1") or GENESIS
                    digest = chain_hash(prev_hash, event)
                    seq = await conn.fetchval(
                        f"INSERT INTO audit_log ({', '.join(COLUMNS)}, prev_hash, hash)"
                        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) RETURNING seq",
                        *event.values(), prev_hash, digest,
                    )
        except Exception as exc:
            self._stored_last = False
            log.critical("NOT STORED (%s) %s", type(exc).__name__, json.dumps(line))
            return False
        self._stored_last = True
        log.info("%s", json.dumps({"seq": seq, "hash": digest, **line}))
        return True

    async def read(
        self, limit: int, before: int | None = None, category: str | None = None, severity: str | None = None
    ) -> list[dict[str, Any]]:
        """Events, newest first, for the dashboard. `before` is the seq of the last row already shown."""
        wanted = [
            (test, value)
            for test, value in (("seq <", before), ("category =", category), ("severity =", severity))
            if value is not None
        ]
        where = " AND ".join(f"{test} ${n}" for n, (test, _) in enumerate(wanted, start=1))
        rows = await self._pool.fetch(
            f"SELECT seq, {', '.join(COLUMNS)} FROM audit_log {'WHERE ' + where if where else ''}"
            f" ORDER BY seq DESC LIMIT ${len(wanted) + 1}",
            *(value for _, value in wanted), limit,
        )
        return [
            {**dict(row), "ts": row["ts"].isoformat(), "detail": json.loads(row["detail"]) if row["detail"] else None}
            for row in rows
        ]


    async def check(self) -> dict[str, Any]:
        """Walks the whole chain, for the dashboard. The same answer as `python -m app.audit`."""
        async with self._pool.acquire() as conn:
            verified, last_hash, broken_seq = await verify(conn)
        return {"intact": broken_seq is None, "verified": verified, "last_hash": last_hash, "broken_seq": broken_seq}


audit = Audit()


async def verify(conn: asyncpg.Connection) -> tuple[int, str, int | None]:
    """Walks the chain. Returns (rows that were fine, the last good hash, seq of the first bad row or None)."""
    count, prev_hash = 0, GENESIS
    async with conn.transaction():
        async for row in conn.cursor("SELECT * FROM audit_log ORDER BY seq"):
            if not link_ok(row, prev_hash):
                return count, prev_hash, row["seq"]
            count, prev_hash = count + 1, row["hash"]
    return count, prev_hash, None


async def _main() -> int:
    conn = await asyncpg.connect(settings.logs_database_url)
    try:
        count, last_hash, broken = await verify(conn)
    finally:
        await conn.close()
    if broken is not None:
        print(f"BROKEN at seq {broken}: that row was changed, or a row before it was removed ({count} rows before it are intact)")
        return 1
    print(f"OK: {count} events, chain intact, last hash {last_hash}")
    return 0


if __name__ == "__main__":  # python -m app.audit
    sys.exit(asyncio.run(_main()))
