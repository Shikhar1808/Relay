"use client";

import { useState } from "react";

import { Button, Card, formatDateTime, inputStyles, Notice, PageTitle, Pill, useAction } from "@/components/ui";
import { api, type AuditEvent, type ChainCheck } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

const PAGE = 50;

// Each view is one question an admin asks of the log. `path` ends ready for more query parameters.
const views = [
  {
    id: "tool_call",
    label: "Tool calls",
    path: "/audit-log?category=tool_call&",
    hint: "Every tool call that went through the gateway: who made it, on which server, how long it took and how it ended.",
  },
  {
    id: "auth",
    label: "Refused sign-ins",
    path: "/audit-log?category=auth&",
    hint: "Requests to the gateway with a missing, wrong or revoked key. The key itself is never kept.",
  },
  {
    id: "admin",
    label: "Server changes",
    path: "/audit-log?category=admin&",
    hint: "Servers an admin added, edited or removed, recorded before the change was saved.",
  },
  {
    id: "registry",
    label: "Registry watch",
    path: "/audit-log?category=registry&",
    hint: "What the gateway itself saw change in the list of servers. A change here with no matching server change was made in the database directly.",
  },
  {
    id: "upstream_health",
    label: "Server health",
    path: "/audit-log?category=upstream_health&",
    hint: "Servers that stopped answering, and when they came back.",
  },
  {
    id: "platform",
    label: "Team, keys and stores",
    path: "/audit-log/platform?",
    hint: "Members added or deactivated, API keys issued or revoked, stores created and who was given access.",
  },
  { id: "all", label: "Everything from the gateway", path: "/audit-log?", hint: "All gateway events, newest first." },
] as const;

const levels = ["INFO", "NOTICE", "WARNING", "ERROR"] as const;
const levelTone = { INFO: "muted", NOTICE: "accent", WARNING: "warn", ERROR: "danger" } as const;

const outcomes: Record<string, string> = {
  ok: "Succeeded",
  error: "The tool reported an error",
  denied: "Refused: no such tool, or it belongs to another store",
  timeout: "Timed out",
  unreachable: "The server could not be reached",
  rate_limited: "Over the call limit",
};

const actions: Record<string, string> = {
  "upstream.create": "Server added",
  "upstream.update": "Server edited",
  "upstream.delete": "Server removed",
  "upstream.added": "The gateway started serving a server",
  "upstream.removed": "The gateway stopped serving a server",
  "upstream.changed": "The gateway saw a server change",
  "member.create": "Member added",
  "member.invite_resend": "Invite sent again",
  "member.deactivate": "Member deactivated",
  "api_key.issue": "API key issued",
  "api_key.revoke": "API key revoked",
  "store.create": "Store created",
  "store.member_add": "Member added to a store",
  "store.member_remove": "Member removed from a store",
};

const detailLabels: Record<string, string> = {
  arguments: "Arguments",
  args_hash: "Arguments fingerprint",
  error: "Error",
  result: "Result",
  token_type: "Signed in with",
  failures: "Refused this minute",
  limit: "Calls allowed a minute",
  changes: "What was sent",
  changed: "What changed",
  prefix: "Key starts with",
  store: "Store",
  name: "Name",
};

// The same columns for the heading row and every event, from the `lg` width up.
const columns = "lg:grid-cols-[6.5rem_minmax(0,1.6fr)_minmax(0,1fr)_11.5rem_4.5rem_1rem]";

/** What happened, in words: a headline and the line under it. */
function describe(event: AuditEvent): { title: string; note: string } {
  const detail = event.detail ?? {};
  switch (event.category) {
    case "tool_call":
      return {
        title: event.upstream ? `${event.upstream} › ${event.tool}` : (event.tool ?? "Tool call"),
        note: outcomes[event.outcome] ?? event.outcome,
      };
    case "auth":
      return { title: "Sign-in refused", note: `${detail.failures ?? "?"} from this address in a minute` };
    case "upstream_health":
      return { title: event.upstream ?? "A server", note: event.outcome === "connected" ? "Answering again" : "Stopped answering" };
    case "system":
      return { title: "The gateway started", note: "" };
    default:
      return {
        title: actions[event.action] ?? event.action,
        note: [event.upstream ?? event.target, detail.store, event.outcome === "failed" && "could not be saved"]
          .filter(Boolean)
          .join(" · "),
      };
  }
}

const who = (event: AuditEvent) =>
  event.user_email ?? event.user_id ?? (event.category === "auth" ? (event.client_ip ?? "Unknown caller") : "The gateway");

/** One value from an event's detail: short ones inline, long or structured ones in a block. */
function shown(key: string, value: unknown): { text: string; block: boolean } {
  if (key === "arguments" && typeof value === "string") {
    try {
      return { text: JSON.stringify(JSON.parse(value), null, 2), block: true };
    } catch {
      return { text: value, block: true }; // arguments cut at the size limit are no longer valid JSON
    }
  }
  if (Array.isArray(value)) return { text: value.join(", "), block: false };
  if (value !== null && typeof value === "object") return { text: JSON.stringify(value, null, 2), block: true };
  return { text: String(value), block: key === "error" || key === "result" };
}

export default function AuditPage() {
  const { user } = useAuth();
  const [viewId, setViewId] = useState<(typeof views)[number]["id"]>("tool_call");
  const [level, setLevel] = useState("");

  if (user?.role !== "admin") return <PageTitle title="Audit log">Only the admin can read the audit log.</PageTitle>;

  const view = views.find((candidate) => candidate.id === viewId)!;
  const hasLevels = view.id !== "platform"; // those rows are all the same level
  const path = view.path + (hasLevels && level ? `severity=${level}&` : "");

  return (
    <>
      <PageTitle title="Nothing goes unrecorded.">
        What went through the gateway, and what was changed here. Rows can be added, never edited or deleted.
      </PageTitle>

      <ChainStatus />

      <div role="group" aria-label="What to show" className="flex flex-wrap gap-x-6 border-b border-line">
        {views.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            aria-pressed={id === view.id}
            onClick={() => setViewId(id)}
            className={`-mb-px border-b py-2 font-display whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${id === view.id ? "border-ink text-ink" : "border-transparent text-ink-soft hover:text-ink"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {/* What this view is, and beside it the one filter the gateway views share. */}
      <div className="mt-3 mb-4 flex flex-wrap items-end justify-between gap-4">
        <p className="max-w-prose text-sm text-ink-soft">{view.hint}</p>
        {hasLevels && (
          <div>
            <label htmlFor="level" className="block text-sm font-medium">
              Level
            </label>
            <select id="level" value={level} onChange={(e) => setLevel(e.target.value)} className={`mt-1.5 ${inputStyles}`}>
              <option value="">All levels</option>
              {levels.map((name) => (
                <option key={name} value={name}>
                  {name[0] + name.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Keyed by what it shows: changing the view or the level starts a fresh list. */}
      <EventList key={path} path={path} />
    </>
  );
}

function ChainStatus() {
  const { busy, error, run } = useAction();
  const [check, setCheck] = useState<ChainCheck>();
  return (
    <Card className="mb-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-display text-xl">Has the gateway&apos;s log been tampered with?</h2>
          <p className="mt-1 max-w-prose text-sm text-ink-soft">
            Each row is sealed to the one before it, so a changed or missing row breaks the chain. Checking reads
            the whole log. It covers gateway events, not team, key and store changes.
          </p>
        </div>
        <Button variant="quiet" disabled={busy} onClick={() => run(async () => setCheck(await api<ChainCheck>("/audit-log/verify")))}>
          {busy ? "Checking…" : "Check now"}
        </Button>
      </div>
      {(check || error) && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
          {check && !error && (
            <Notice tone={check.intact ? "success" : "error"}>
              {check.intact
                ? `Untouched. All ${check.verified} rows fit the chain.`
                : `Row #${check.broken_seq} was changed, or a row before it was removed. The ${check.verified} rows before it are fine.`}
            </Notice>
          )}
        </div>
      )}
    </Card>
  );
}

function EventList({ path }: { path: string }) {
  const { data: newest, error, reload } = useData<AuditEvent[]>(`${path}limit=${PAGE}`);
  const [older, setOlder] = useState<AuditEvent[]>([]);
  const [reachedEnd, setReachedEnd] = useState(false);
  const more = useAction();

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!newest) return <p className="text-ink-soft">Loading…</p>;

  const events = [...newest, ...older];
  const hasMore = !reachedEnd && events.length > 0 && events.length % PAGE === 0;
  const refresh = () => {
    setOlder([]);
    setReachedEnd(false);
    void reload();
  };

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p aria-live="polite" className="text-sm text-ink-soft">
          {events.length === 0 ? "Nothing here yet." : `Showing the newest ${events.length}.`}
        </p>
        <Button variant="quiet" onClick={refresh}>
          Refresh
        </Button>
      </div>

      {events.length > 0 && (
        <div className="border-b border-line">
          <div className={`hidden gap-x-4 border-y border-edge px-3 py-2.5 text-xs font-medium tracking-wide text-ink-soft uppercase lg:grid ${columns}`}>
            <span>Level</span>
            <span>What</span>
            <span>Who</span>
            <span>When</span>
            <span className="text-right">Took</span>
          </div>
          <ul className="divide-y divide-line">
            {events.map((event) => (
              <EventRow key={event.seq} event={event} />
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 space-y-3">
        <Notice tone="error">{more.error}</Notice>
        {hasMore && (
          <Button
            variant="quiet"
            disabled={more.busy}
            onClick={() =>
              more.run(async () => {
                const page = await api<AuditEvent[]>(`${path}limit=${PAGE}&before=${events.at(-1)!.seq}`);
                setOlder((all) => [...all, ...page]);
                setReachedEnd(page.length < PAGE);
              })
            }
          >
            {more.busy ? "Loading…" : "Load older"}
          </Button>
        )}
      </div>
    </>
  );
}

function EventRow({ event }: { event: AuditEvent }) {
  const { title, note } = describe(event);
  return (
    <li>
      <details className="group">
        {/* Two lines on a small screen; from `lg` up the second line's parts become columns of their own. */}
        <summary
          className={`grid cursor-pointer list-none grid-cols-[auto_minmax(0,1fr)_1rem] items-center gap-x-4 gap-y-1 px-3 py-3 transition-colors hover:bg-ink/[0.03] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden ${columns}`}
        >
          <span>
            <Pill tone={levelTone[event.severity]}>{event.severity.toLowerCase()}</Pill>
          </span>
          <span className="min-w-0">
            <span className="block truncate font-medium">{title}</span>
            {note && <span className="block truncate text-sm text-ink-soft">{note}</span>}
          </span>
          <span aria-hidden="true" className="text-ink-soft transition-transform group-open:rotate-90 lg:order-last">
            ›
          </span>
          <span className="col-span-full flex flex-wrap gap-x-3 text-sm text-ink-soft lg:contents">
            <span className="truncate">{who(event)}</span>
            <time dateTime={event.ts}>{formatDateTime(event.ts)}</time>
            <span className="tabular-nums lg:text-right">{event.duration_ms === null ? "" : `${event.duration_ms} ms`}</span>
          </span>
        </summary>

        <dl className="arrive grid gap-x-6 gap-y-2 border-t border-line bg-accent-wash/60 px-3 py-4 text-sm sm:grid-cols-[11rem_minmax(0,1fr)]">
          <dt className="text-ink-soft">Event</dt>
          <dd className="font-mono text-xs leading-6">
            #{event.seq} · {event.category} · {event.action} · {event.outcome}
          </dd>
          <dt className="text-ink-soft">Who</dt>
          <dd className="break-all">{who(event)}</dd>
          {event.client_ip && (
            <>
              <dt className="text-ink-soft">From address</dt>
              <dd className="font-mono text-xs leading-6">{event.client_ip}</dd>
            </>
          )}
          {Object.entries(event.detail ?? {}).map(([key, value]) => {
            const { text, block } = shown(key, value);
            return (
              <div key={key} className="contents">
                <dt className="text-ink-soft">{detailLabels[key] ?? key}</dt>
                <dd className="min-w-0">
                  {block ? (
                    <pre className="overflow-x-auto bg-canvas p-3 font-mono text-xs break-words whitespace-pre-wrap">{text}</pre>
                  ) : (
                    <span className="break-all">{text}</span>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      </details>
    </li>
  );
}
