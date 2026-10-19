"use client";

import { useState } from "react";

import { api, type ApiKey, type ApiKeyCreated } from "@/lib/api";
import { useData } from "@/lib/use-data";

import { Button, Field, formatDate, Notice, Secret, useAction } from "./ui";

/**
 * Issue and revoke a gateway API key. `basePath` is "/team/me/api-keys" for
 * your own key or "/team/members/{id}/api-keys" when an admin manages a
 * member's. A person has at most one active key.
 */
export function KeyManager({ basePath }: { basePath: string }) {
  const { data: keys, error: loadError, reload } = useData<ApiKey[]>(basePath);
  const { busy, error, run } = useAction();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated>();

  if (loadError) return <Notice tone="error">{loadError}</Notice>;
  if (!keys) return <p className="text-ink-soft">Loading…</p>;

  const active = keys.find((key) => !key.revoked);

  return (
    <div className="space-y-4">
      {created && <Secret label="Your new API key" value={created.raw_key} />}

      {active ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border border-line p-4">
          <div>
            <p className="font-mono text-sm">{active.prefix}…</p>
            <p className="mt-1 text-sm text-ink-soft">
              {active.name ? `${active.name}, created` : "Created"} {formatDate(active.created_at)}.{" "}
              {active.last_used_at ? `Last used ${formatDate(active.last_used_at)}.` : "Not used yet."}
            </p>
          </div>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api(`${basePath}/${active.id}/revoke`, { method: "POST" });
                setCreated(undefined);
                await reload();
              })
            }
          >
            Revoke key
          </Button>
        </div>
      ) : (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              setCreated(await api<ApiKeyCreated>(basePath, { body: { name: name.trim() || null } }));
              setName("");
              await reload();
            });
          }}
        >
          <Field
            label="Key name (optional)"
            placeholder="Laptop, Cursor"
            maxLength={255}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="min-w-56 flex-1"
          />
          <Button type="submit" disabled={busy}>
            Issue key
          </Button>
        </form>
      )}
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
