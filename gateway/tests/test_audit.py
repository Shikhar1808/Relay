"""The audit log: the hash chain, how much detail is kept, and what happens
when the database is away (app/audit.py). The append-only rule lives in the
database, so integration_test.py checks that one.
"""
import contextlib
import json
from datetime import datetime, timezone

import pytest

from app import audit as audit_module
from app.audit import COLUMNS, GENESIS, INFO, Audit, chain_hash, describe_call, link_ok
from app.config import settings

pytestmark = pytest.mark.anyio


def event(**changes) -> dict:
    base = dict.fromkeys(COLUMNS)
    base.update(ts=datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc), severity=INFO, category="tool_call", action="call", outcome="ok")
    return {**base, **changes}


def chain(*events: dict) -> list[dict]:
    rows, prev = [], GENESIS
    for seq, one in enumerate(events, start=1):
        digest = chain_hash(prev, one)
        rows.append({**one, "seq": seq, "prev_hash": prev, "hash": digest})
        prev = digest
    return rows


def first_broken(rows: list[dict]) -> int | None:
    prev = GENESIS
    for row in rows:
        if not link_ok(row, prev):
            return row["seq"]
        prev = row["hash"]
    return None


ROWS = chain(event(tool="a"), event(tool="b", outcome="denied"), event(tool="c"))


# ---- the chain -----------------------------------------------------------------


def test_an_untouched_chain_verifies():
    assert first_broken(ROWS) is None


@pytest.mark.parametrize("column", ["outcome", "user_id", "tool", "severity", "detail", "duration_ms", "client_ip", "ts"])
def test_changing_any_column_of_a_row_is_found_at_that_row(column):
    changed = {**ROWS[1], column: datetime(2020, 1, 1, tzinfo=timezone.utc) if column == "ts" else "changed"}
    assert first_broken([ROWS[0], changed, ROWS[2]]) == 2


def test_removing_a_row_is_found_at_the_row_after_it():
    assert first_broken([ROWS[0], ROWS[2]]) == 3


def test_swapping_two_rows_is_found():
    assert first_broken([ROWS[1], ROWS[0], ROWS[2]]) is not None


def test_a_row_written_without_the_key_does_not_verify():
    forged = event(tool="forged")
    row = {**forged, "seq": 4, "prev_hash": ROWS[2]["hash"], "hash": chain_hash(ROWS[2]["hash"], forged, key="x" * 32)}
    assert first_broken([*ROWS, row]) == 4


def test_the_hash_is_of_the_moment_not_of_how_the_time_is_written():
    from datetime import timedelta

    utc = event()
    same_moment = {**utc, "ts": utc["ts"].astimezone(timezone(timedelta(hours=5, minutes=30)))}
    assert chain_hash(GENESIS, utc) == chain_hash(GENESIS, same_moment)
    assert chain_hash(GENESIS, utc) != chain_hash(GENESIS, {**utc, "ts": utc["ts"].replace(microsecond=1)})


# ---- how much is kept ------------------------------------------------------------

ARGS = {"status": "processing", "auth": {"api_key": "sk-123", "Password": "hunter2"}, "items": [{"token": "t"}]}


def test_minimal_keeps_only_a_hash_of_the_arguments(monkeypatch):
    monkeypatch.setattr(settings, "audit_detail", "minimal")
    detail = describe_call(ARGS, "error", "boom")
    assert set(detail) == {"args_hash"} and len(detail["args_hash"]) == 64
    assert detail["args_hash"] == describe_call(dict(reversed(ARGS.items())), "ok", None)["args_hash"], "key order must not matter"


def test_standard_redacts_secret_looking_fields_at_any_depth(monkeypatch):
    monkeypatch.setattr(settings, "audit_detail", "standard")
    detail = describe_call(ARGS, "ok", "the result")
    assert json.loads(detail["arguments"]) == {
        "status": "processing", "auth": {"api_key": "[redacted]", "Password": "[redacted]"}, "items": [{"token": "[redacted]"}],
    }
    assert "result" not in detail and "error" not in detail


def test_standard_keeps_the_error_text_of_a_failed_call(monkeypatch):
    monkeypatch.setattr(settings, "audit_detail", "standard")
    assert describe_call({}, "error", "order 9 not found")["error"] == "order 9 not found"


def test_full_keeps_the_arguments_as_sent_and_the_result(monkeypatch):
    monkeypatch.setattr(settings, "audit_detail", "full")
    detail = describe_call(ARGS, "ok", "the result")
    assert json.loads(detail["arguments"]) == ARGS and detail["result"] == "the result"


def test_long_arguments_and_results_are_cut(monkeypatch):
    monkeypatch.setattr(settings, "audit_detail", "full")
    detail = describe_call({"note": "x" * 10_000}, "ok", "y" * 10_000)
    assert len(detail["arguments"]) == 2000 and len(detail["result"]) == 2000


# ---- writing ---------------------------------------------------------------------


class FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def cursor(self, sql):
        for row in self.rows:
            yield row

    async def execute(self, sql, *args):
        assert "pg_advisory_xact_lock" in sql, "the chain needs one writer at a time"

    async def fetchval(self, sql, *args):
        if sql.startswith("SELECT hash"):
            return self.rows[-1]["hash"] if self.rows else None
        *values, prev_hash, digest = args
        self.rows.append({**dict(zip(COLUMNS, values)), "seq": len(self.rows) + 1, "prev_hash": prev_hash, "hash": digest})
        return len(self.rows)


class FakePool:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.down = False

    @contextlib.asynccontextmanager
    async def acquire(self):
        if self.down:
            raise ConnectionError("database is down")
        yield FakeConn(self.rows)

    async def fetchval(self, sql, timeout=None):
        if self.down:
            raise ConnectionError("database is down")
        return 1


def started() -> tuple[Audit, FakePool]:
    log, pool = Audit(), FakePool()  # a fresh Audit: conftest only replaces the shared one
    log._pool = pool
    return log, pool


async def test_recorded_events_form_a_chain_that_verifies():
    log, pool = started()
    await log.record(INFO, "system", "start", "ok")
    await log.record(INFO, "tool_call", "call", "ok", user_id="u1", upstream="woo", tool="list_orders", duration_ms=12, token_type="api_key")
    assert first_broken(pool.rows) is None and pool.rows[0]["prev_hash"] == GENESIS and pool.rows[1]["prev_hash"] == pool.rows[0]["hash"]
    assert pool.rows[0]["detail"] is None and json.loads(pool.rows[1]["detail"]) == {"token_type": "api_key"}
    assert pool.rows[1]["duration_ms"] == 12 and pool.rows[1]["client_ip"] is None


async def test_a_database_failure_never_reaches_the_caller_but_closes_the_gate(caplog):
    log, pool = started()
    assert await log.usable()
    pool.down = True
    await log.record(INFO, "tool_call", "call", "ok", user_id="u1", secret_argument="do-not-log")  # must not raise
    assert not await log.usable(), "tool calls must be refused while the log cannot be written"
    assert "NOT STORED" in caplog.text and '"user_id": "u1"' in caplog.text, "the event must survive in the process log"
    assert "do-not-log" not in caplog.text, "the detail does not belong in the process log"


async def test_the_gate_opens_again_when_the_database_is_back():
    log, pool = started()
    pool.down = True
    await log.record(INFO, "tool_call", "call", "ok")
    pool.down = False
    assert await log.usable()
    await log.record(INFO, "tool_call", "call", "ok")
    assert len(pool.rows) == 1


# ---- reading ---------------------------------------------------------------------


class ReadPool:
    def __init__(self):
        self.asked = []

    async def fetch(self, sql, *args):
        self.asked.append((" ".join(sql.split()), args))
        return [{"seq": 7, **event(tool="list_orders", detail='{"args_hash": "abc"}')}, {"seq": 6, **event()}]


async def test_read_returns_newest_first_with_the_detail_opened_up():
    log, pool = Audit(), ReadPool()
    log._pool = pool
    rows = await log.read(50)
    assert pool.asked[0][0].endswith("FROM audit_log ORDER BY seq DESC LIMIT $1") and pool.asked[0][1] == (50,)
    assert rows[0]["seq"] == 7 and rows[0]["detail"] == {"args_hash": "abc"} and rows[1]["detail"] is None
    assert rows[0]["ts"] == "2026-10-11T12:00:00+00:00", "times must be JSON-ready"


async def test_read_filters_and_pages_with_values_kept_out_of_the_sql_text():
    log, pool = Audit(), ReadPool()
    log._pool = pool
    await log.read(20, before=90, severity="ERROR")
    await log.read(20, category="admin'; DROP TABLE audit_log; --")
    assert pool.asked[0] == (pool.asked[0][0], (90, "ERROR", 20)) and "WHERE seq < $1 AND severity = $2 ORDER BY seq DESC LIMIT $3" in pool.asked[0][0]
    assert "DROP" not in pool.asked[1][0] and "WHERE category = $1" in pool.asked[1][0]


# ---- the chain check the dashboard asks for ------------------------------------------


async def test_check_reports_an_intact_chain_and_how_long_it_is():
    log, pool = started()
    for tool in ("a", "b", "c"):
        await log.record(INFO, "tool_call", "call", "ok", tool=tool)
    assert await log.check() == {"intact": True, "verified": 3, "last_hash": pool.rows[-1]["hash"], "broken_seq": None}


async def test_check_points_at_the_first_row_that_does_not_fit():
    log, pool = started()
    for tool in ("a", "b", "c"):
        await log.record(INFO, "tool_call", "call", "ok", tool=tool)
    pool.rows[1]["outcome"] = "denied"  # edited behind the trigger's back
    result = await log.check()
    assert (result["intact"], result["verified"], result["broken_seq"]) == (False, 1, 2)
