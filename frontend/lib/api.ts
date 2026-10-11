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

export type ApiKeyCreated = { id: string; raw_key: string; prefix: string; name: string | null };
export type MemberCreated = { user: User; temporary_password: string };
export type TokenResponse = { access_token: string; user: User };
export type ChatReply = { reply: string; chat_token_expires_at: string; store_ids: string[] };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const SIGNED_OUT_EVENT = "urumi:signed-out";

const TOKEN_KEY = "urumi_token";
// shortcut: the session token lives in localStorage, readable by any script on the page;
// move it to an httpOnly cookie set by the backend if third-party scripts are ever added.
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string | null) =>
  token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);

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
  const token = getToken();
  let response: Response;
  try {
    response = await fetch((await baseUrl()) + path, {
      method: options.method ?? (options.body === undefined ? "GET" : "POST"),
      headers: {
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (response.status === 401 && token) {
    // The session expired or the account was deactivated: start over at sign in.
    setToken(null);
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT)); // AuthProvider listens and clears the user
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, describe(response.status, body));
  return body as T;
}
