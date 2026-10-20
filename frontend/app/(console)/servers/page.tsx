"use client";

import { useState } from "react";

import { Button, Card, Field, IconBox, inputStyles, Notice, PageTitle, Pill, SectionTitle, Stats, useAction } from "@/components/ui";
import { api, type Store, type UpstreamServer } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

export default function ServersPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { data: servers, error: loadError, reload } = useData<UpstreamServer[]>(isAdmin ? "/upstream-servers" : null);
  const { data: stores = [] } = useData<Store[]>(isAdmin ? "/stores" : null);
  const rowAction = useAction();
  const [editingId, setEditingId] = useState<string>();

  if (!isAdmin) return <PageTitle title="Servers">Only the admin can manage servers.</PageTitle>;
  const live = servers?.filter((server) => server.enabled) ?? [];
  const connected = live.filter((server) => server.status === "connected");
  const unreachable = live.filter((server) => server.status === "unreachable").length;
  const disabled = (servers?.length ?? 0) - live.length;

  return (
    <>
      <PageTitle title="Every tool, accounted for.">
        The MCP servers the gateway forwards to. Chat and API keys see each tool as{" "}
        <code className="font-mono text-sm">slug__tool</code>, and only in the stores the server belongs to.
      </PageTitle>
      {servers && (
        <Stats
          items={[
            { label: "Servers", value: servers.length, note: disabled ? `${disabled} disabled` : undefined },
            { label: "Connected", value: connected.length, tone: connected.length ? "good" : undefined },
            { label: "Unreachable", value: unreachable, tone: unreachable ? "danger" : undefined },
            {
              label: "Tools",
              value: connected.reduce((sum, server) => sum + server.tools.length, 0),
              note: "Reachable from chat and API keys",
            },
          ]}
        />
      )}

      <Card className="max-w-3xl">
        <h2 className="mb-4 font-display text-xl">Add a server</h2>
        <ServerForm stores={stores} onDone={reload} />
      </Card>

      <div className="mt-10">
        <SectionTitle title="All servers">
          <Button variant="quiet" className="px-3.5 py-1.5" onClick={() => void reload()}>
            Refresh
          </Button>
        </SectionTitle>
        <p className="text-sm text-ink-soft">
          Status is the gateway&apos;s last check: every 30 seconds, and right after a change here.
        </p>
      </div>
      <div className="mt-4">
        <Notice tone="error">{loadError ?? rowAction.error}</Notice>
      </div>

      {servers &&
        (servers.length === 0 ? (
          <p className="mt-4 text-ink-soft">No servers yet. Add the first one above.</p>
        ) : (
          <ul className="mt-4 grid items-start gap-4 lg:grid-cols-2">
            {servers.map((server) => {
              const store =
                server.store_id === null
                  ? "Every store"
                  : (stores.find((s) => s.id === server.store_id)?.name ?? "One store");
              return (
                // The server being edited takes the full row, so its form has room.
                <li key={server.id} className={`frame cut p-5 sm:p-6 ${editingId === server.id ? "lg:col-span-2" : ""}`}>
                  <div className="flex items-start justify-between gap-3">
                    <IconBox name="servers" />
                    <StatusPill server={server} />
                  </div>
                  <h2 className="mt-4 font-display text-xl break-words">{server.name}</h2>
                  <p className="mt-0.5 font-mono text-sm text-ink-soft">{server.slug}</p>
                  <dl className="mt-4 grid grid-cols-[6.5rem_1fr] gap-y-2 text-sm">
                    <dt className="text-ink-soft">Address</dt>
                    <dd className="font-mono break-all">{server.url}</dd>
                    <dt className="text-ink-soft">Store</dt>
                    <dd>{store}</dd>
                    <dt className="text-ink-soft">Credential</dt>
                    <dd>{server.has_credential ? `Stored, sent as ${server.credential_header}` : "None"}</dd>
                  </dl>
                  <Tools
                    server={server}
                    busy={rowAction.busy}
                    onToggle={(tool) =>
                      rowAction.run(async () => {
                        const locked = server.admin_only_tools;
                        await api(`/upstream-servers/${server.id}`, {
                          method: "PATCH",
                          body: { admin_only_tools: locked.includes(tool) ? locked.filter((t) => t !== tool) : [...locked, tool] },
                        });
                        await reload();
                      })
                    }
                  />

                  {editingId === server.id ? (
                    <div className="arrive mt-4 bg-accent-wash/60 p-4">
                      <ServerForm
                        server={server}
                        stores={stores}
                        onDone={async () => {
                          await reload();
                          setEditingId(undefined);
                        }}
                        onCancel={() => setEditingId(undefined)}
                      />
                    </div>
                  ) : (
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button variant="quiet" onClick={() => setEditingId(server.id)}>
                        Edit
                      </Button>
                      <Button
                        variant="quiet"
                        disabled={rowAction.busy}
                        onClick={() =>
                          rowAction.run(async () => {
                            await api(`/upstream-servers/${server.id}`, {
                              method: "PATCH",
                              body: { enabled: !server.enabled },
                            });
                            await reload();
                          })
                        }
                      >
                        {server.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        variant="danger"
                        disabled={rowAction.busy}
                        onClick={() => {
                          if (!window.confirm(`Remove ${server.name}? Its tools disappear from chat and API keys.`)) return;
                          void rowAction.run(async () => {
                            await api(`/upstream-servers/${server.id}`, { method: "DELETE" });
                            await reload();
                          });
                        }}
                      >
                        Remove
                      </Button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        ))}
    </>
  );
}

function StatusPill({ server }: { server: UpstreamServer }) {
  if (!server.enabled) return <Pill tone="muted">disabled</Pill>;
  if (server.status === "connected") return <Pill tone="good">connected</Pill>;
  if (server.status === "unreachable") return <Pill tone="danger">unreachable</Pill>;
  return <Pill tone="warn">unchecked</Pill>;
}

function Tools({ server, busy, onToggle }: { server: UpstreamServer; busy: boolean; onToggle: (tool: string) => void }) {
  const note = (text: string) => <p className="mt-4 text-sm text-ink-soft">{text}</p>;
  if (!server.enabled) return note("Disabled: its tools are hidden from chat and API keys.");
  if (server.status === "unreachable")
    return note("The gateway could not reach it at its last check. Its tools are hidden until it answers.");
  if (server.status === "unknown") return note("Not checked yet. If this stays, check that the gateway is running.");
  return (
    <div className="mt-4">
      <p className="text-sm font-medium">Tools ({server.tools.length})</p>
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {server.tools.map((tool) => {
          const adminOnly = server.admin_only_tools.includes(tool);
          return (
            <li key={tool}>
              <button
                type="button"
                aria-pressed={adminOnly}
                disabled={busy}
                onClick={() => onToggle(tool)}
                className={`border px-2 py-1 font-mono text-xs disabled:opacity-60 ${adminOnly ? "border-accent bg-accent-wash" : "border-line"}`}
              >
                {tool}
                {adminOnly && <span className="ml-2 font-sans text-ink-soft">Admins only</span>}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-sm text-ink-soft">
        Click a tool to keep it for admins. Members don&apos;t see it and are refused if they call it.
      </p>
    </div>
  );
}

/** Adds a server, or edits `server`. The slug is fixed once added: it starts every tool name. */
function ServerForm({
  server,
  stores,
  onDone,
  onCancel,
}: {
  server?: UpstreamServer;
  stores: Store[];
  onDone: () => Promise<void>;
  onCancel?: () => void;
}) {
  const initial = {
    name: server?.name ?? "",
    slug: server?.slug ?? "",
    url: server?.url ?? "",
    credential_header: server?.credential_header ?? "Authorization",
    credential: "",
    store_id: server?.store_id ?? "",
  };
  const [draft, setDraft] = useState(initial);
  const { busy, error, run } = useAction();
  const edit = (field: keyof typeof initial) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setDraft({ ...draft, [field]: event.target.value });
  const storeId = `store-${server?.id ?? "new"}`;

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        void run(async () => {
          const body: Record<string, unknown> = {
            name: draft.name,
            url: draft.url,
            credential_header: draft.credential_header,
            store_id: draft.store_id || null,
          };
          if (draft.credential) body.credential = draft.credential; // empty: none when adding, keep the stored one when editing
          if (server) {
            // Only what changed, so the audit log shows what the admin did.
            const changed = Object.entries(body).filter(([field, value]) => value !== server[field as keyof UpstreamServer]);
            await api(`/upstream-servers/${server.id}`, { method: "PATCH", body: Object.fromEntries(changed) });
          } else {
            await api("/upstream-servers", { body: { ...body, slug: draft.slug } });
          }
          setDraft(initial);
          await onDone();
        });
      }}
    >
      <Field
        label="Name"
        required
        maxLength={255}
        placeholder="Risk check"
        value={draft.name}
        onChange={edit("name")}
        className={server ? "sm:col-span-2" : ""}
      />
      {!server && (
        <Field
          label="Slug"
          required
          maxLength={50}
          pattern="[a-z0-9]+(-[a-z0-9]+)*"
          placeholder="risk"
          hint="Lowercase letters, digits and single hyphens. Can't be changed later."
          value={draft.slug}
          onChange={edit("slug")}
        />
      )}
      <Field
        label="Address"
        type="url"
        required
        placeholder="http://risk-mcp:8000/mcp"
        value={draft.url}
        onChange={edit("url")}
        className="sm:col-span-2"
      />
      <Field
        label="Credential header"
        required
        maxLength={100}
        pattern="[A-Za-z0-9\-]+"
        value={draft.credential_header}
        onChange={edit("credential_header")}
      />
      <Field
        label="Credential"
        type="password"
        autoComplete="off"
        maxLength={4000}
        placeholder="Bearer …"
        hint={
          server?.has_credential
            ? "Empty keeps the stored one. A new address needs it again."
            : "The header's whole value. Stored encrypted, never shown again."
        }
        value={draft.credential}
        onChange={edit("credential")}
      />
      <div>
        <label htmlFor={storeId} className="block text-sm font-medium">
          Store
        </label>
        <select
          id={storeId}
          aria-describedby={`${storeId}-hint`}
          value={draft.store_id}
          onChange={edit("store_id")}
          className={`mt-1.5 ${inputStyles}`}
        >
          <option value="">Every store</option>
          {stores.map((store) => (
            <option key={store.id} value={store.id}>
              {store.name}
            </option>
          ))}
        </select>
        <p id={`${storeId}-hint`} className="mt-1.5 text-sm text-ink-soft">
          With a store, only the admin and its members see the tools.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-2 sm:col-span-2">
        <Button type="submit" disabled={busy}>
          {server ? "Save changes" : "Add server"}
        </Button>
        {onCancel && (
          <Button variant="quiet" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
      {error && (
        <div className="sm:col-span-2">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
    </form>
  );
}
