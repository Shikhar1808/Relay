// Typed client for the Urumi Platform API (backend/app/api/routes).

export type Role = "admin" | "member";

export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
};

export type Store = {
  id: string;
  name: string;
  slug: string;
  status: "pending" | "provisioning" | "ready" | "failed";
  url: string | null;
  created_at: string;
};

export type ApiKey = {
  id: string;
  prefix: string;
  name: string | null;
  revoked: boolean;
  created_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
};

/** An MCP server the gateway forwards to. `status` and `tools` come from the gateway's last probe. */
export type UpstreamServer = {
  id: string;
  name: string;
  slug: string;
  url: string;
  enabled: boolean;
  credential_header: string;
  has_credential: boolean;
  store_id: string | null; // null: every store
  created_at: string;
  status: "connected" | "unreachable" | "unknown";
  tools: string[];
};

export type ApiKeyCreated ={ id: string; raw_key: string; prefix: string; name: string | null };
export type MemberCreated = { user: User; temporary_password: string };
/** What sign in answers. It also carries the token itself, for scripts; the dashboard never touches it. */
export type TokenResponse = { user: User };

/** One piece of the chat's results panel. `kind` says which of the optional fields are there. */
export type ChatBlock = {
  kind: "table" | "card" | "notice" | "raw";
  title: string;
  source: string; // which server and tool the data came from
  call_id: string;
  columns?: string[];
  rows?: (string | number)[][];
  fields?: [string, string | number][];
  tone?: "info" | "success" | "warning" | "error";
  text?: string;
};
/** A change the agent wants to make, waiting for a yes or no. */
export type ChatPending = { call_id: string; server: string; tool: string; summary: string };
/** What the agent has to show for its last answer: `reply` for the chat, `blocks` and `steps` for the results panel. */
export type ChatResult = {
  reply: string;
  status: "answered" | "needs_confirmation" | "stopped" | "failed";
  blocks: ChatBlock[];
  steps: string[];
  pending: ChatPending[];
};
/** The answer to one message. `conversation_id` changes when the earlier chat had ended and a new one was opened. */
export type ChatReply = ChatResult & { conversation_id: string; store_ids: string[] };
export type ChatLine = { from: "you" | "agent"; text: string };
/** A whole chat: the open one after a reload (`conversation_id` null when none is open), or an earlier one. */
export type Chat = ChatResult & { conversation_id: string | null; messages: ChatLine[] };
export type ChatSummary = { conversation_id: string; title: string; last_used_at: string };

/** One audit row: from the gateway's log, or (category "platform") a team, key or store change. */
export type AuditEvent = {
  seq: number;
  ts: string;
  severity: "INFO" | "NOTICE" | "WARNING" | "ERROR";
  category: string;
  action: string;
  outcome: string;
  user_id: string | null;
  user_email: string | null;
  upstream: string | null;
  tool: string | null;
  duration_ms: number | null;
  target: string | null;
  client_ip: string | null;
  detail: Record<string, unknown> | null;
};
export type ChainCheck = { intact: boolean; verified: number; last_hash: string; broken_seq: number | null };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const SIGNED_OUT_EVENT = "urumi:signed-out";

let apiUrl: Promise<string> | undefined;
const baseUrl = () =>
  (apiUrl ??= fetch("/runtime-config")
    .then((r) => r.json())
    .then((config: { apiUrl: string }) => config.apiUrl.replace(/\/$/, "")));

function describe(status: number, body: unknown): string {
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: one entry per bad field.
    return detail.map((d: { loc?: string[]; msg: string }) => `${d.loc?.at(-1) ?? "input"}: ${d.msg}`).join(". ");
  }
  return `The server answered ${status}.`;
}

export async function api<T = void>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch((await baseUrl()) + path, {
      method: options.method ?? (options.body === undefined ? "GET" : "POST"),
      // The session is a cookie the API sets at sign in. No script on this page can read it;
      // the browser sends it along by itself.
      credentials: "include",
      headers: options.body === undefined ? {} : { "Content-Type": "application/json" },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (response.status === 401) {
    // Not signed in (any more): the session expired or the account was deactivated. Start over at sign in.
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT)); // AuthProvider listens and clears the user
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, describe(response.status, body));
  return body as T;
}
