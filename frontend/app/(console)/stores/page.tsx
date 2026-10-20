"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  Avatar,
  Button,
  Card,
  Field,
  formatDate,
  formatDateTime,
  IconBox,
  inputStyles,
  Notice,
  PageTitle,
  Pill,
  SectionTitle,
  Stats,
  useAction,
} from "@/components/ui";
import { api, type Store, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

// The store worker is still on these; the page reloads until it is done.
const BUSY: Store["status"][] = ["pending", "provisioning", "deleting"];

export default function StoresPage() {
  const { user } = useAuth();
  const { data: stores, error, reload } = useData<Store[]>("/stores");
  const busy = stores?.some((store) => BUSY.includes(store.status));
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [busy, reload]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!stores) return <p className="text-ink-soft">Loading…</p>;
  return user?.role === "admin" ? <AdminStores stores={stores} reload={reload} /> : <MemberStores stores={stores} />;
}

function StoreAddress({ store }: { store: Store }) {
  if (store.url && store.status !== "ready") return <span className="break-all text-ink-soft">{store.url}</span>;
  return store.url ? (
    <a href={store.url} className="break-all text-accent underline underline-offset-4">
      {store.url}
    </a>
  ) : (
    <span className="text-ink-soft">No address yet</span>
  );
}

function StatusPill({ status }: { status: Store["status"] }) {
  return <Pill tone={status === "ready" ? "good" : status === "failed" ? "danger" : "warn"}>{status}</Pill>;
}

function MemberStores({ stores }: { stores: Store[] }) {
  if (stores.length === 0) {
    return (
      <PageTitle title="You're not in a store yet">
        Ask your admin to add you to a store. Chat and your API key start working as soon as they do.
      </PageTitle>
    );
  }
  const nextSteps = [
    { href: "/chat", icon: "chat", title: "Ask the agent", text: "Orders, products and stock, in plain words." },
    { href: "/api-key", icon: "key", title: "Get your API key", text: "Reach your stores from Claude Code or Cursor." },
  ] as const;
  return (
    <>
      <PageTitle title="Where you work.">Chat and your API key reach only the stores listed here.</PageTitle>
      <div className="space-y-4">
        {stores.map((store) => (
          <Card key={store.id} className="max-w-xl">
            <IconBox name="stores" />
            <h2 className="mt-4 mb-3 font-display text-xl break-words">{store.name}</h2>
            <dl className="grid grid-cols-[6rem_1fr] gap-y-3">
              <dt className="text-ink-soft">Address</dt>
              <dd>
                <StoreAddress store={store} />
              </dd>
              <dt className="text-ink-soft">Status</dt>
              <dd>
                <StatusPill status={store.status} />
              </dd>
              <dt className="text-ink-soft">Created</dt>
              <dd>{formatDate(store.created_at)}</dd>
            </dl>
          </Card>
        ))}
      </div>
      <div className="mt-4 grid max-w-xl gap-4 sm:grid-cols-2">
        {nextSteps.map(({ href, icon, title, text }) => (
          <Link
            key={href}
            href={href}
            className="frame cut p-5 transition-colors hover:bg-ink/5 focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-accent"
          >
            <IconBox name={icon} />
            <span className="mt-4 block font-display text-lg">{title}</span>
            <span className="mt-1 block text-sm text-ink-soft">{text}</span>
          </Link>
        ))}
      </div>
    </>
  );
}

function AdminStores({ stores, reload }: { stores: Store[]; reload: () => Promise<void> }) {
  const { busy, error, run } = useAction();
  const [name, setName] = useState("");
  const [subdomain, setSubdomain] = useState("");
  const [openId, setOpenId] = useState<string>();
  const [deletingId, setDeletingId] = useState<string>();
  const { data: people } = useData<User[]>("/team/members");
  const members = people?.filter((person) => person.role === "member" && person.is_active) ?? [];
  const count = (...statuses: Store["status"][]) => stores.filter((store) => statuses.includes(store.status)).length;
  const [ready, settingUp, failed] = [count("ready"), count("pending", "provisioning"), count("failed")];

  return (
    <>
      <PageTitle title="Every store, in one place.">Create stores and choose who works in each. A member can be in several stores.</PageTitle>
      <Stats
        items={[
          { label: "Stores", value: stores.length, note: people && `${members.length} active members` },
          { label: "Ready", value: ready, tone: ready ? "good" : undefined },
          { label: "Setting up", value: settingUp, tone: settingUp ? "warn" : undefined },
          { label: "Failed", value: failed, tone: failed ? "danger" : undefined },
        ]}
      />

      <Card className="max-w-xl">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await api("/stores", { body: { name, subdomain } });
              setName("");
              setSubdomain("");
              await reload();
            });
          }}
        >
          <Field
            label="New store name"
            required
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="min-w-56 flex-1"
          />
          <Field
            label="Subdomain"
            hint="Lowercase letters, digits and hyphens. The store's address starts with it, and it cannot change."
            required
            maxLength={30}
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            value={subdomain}
            onChange={(e) => setSubdomain(e.target.value)}
            className="min-w-56 flex-1"
          />
          <Button type="submit" disabled={busy}>
            Create store
          </Button>
        </form>
        {error && (
          <div className="mt-4">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
      </Card>

      <div className="mt-10">
        <SectionTitle title="All stores" />
      </div>
      {stores.length === 0 ? (
        <p className="text-ink-soft">No stores yet. Create the first one above.</p>
      ) : (
        <ul className="grid items-start gap-4 sm:grid-cols-2">
          {stores.map((store) => {
            const open = openId === store.id;
            return (
              // The store you open takes the full row, so its people have room.
              <li key={store.id} className={`frame cut p-5 ${open ? "col-span-full" : ""}`}>
                <div className="flex items-start justify-between gap-3">
                  <IconBox name="stores" />
                  <StatusPill status={store.status} />
                </div>
                <h2 className="mt-4 font-display text-xl break-words">{store.name}</h2>
                <p className="mt-1 text-sm">
                  <StoreAddress store={store} />
                </p>
                <p className="mt-1 text-sm text-ink-soft">Created {formatDateTime(store.created_at)}</p>
                {store.step && <p className="mt-3 text-sm">Now: {store.step}…</p>}
                {store.error && (
                  <div className="mt-3">
                    <Notice tone="error">{store.error}</Notice>
                  </div>
                )}
                <div className="mt-4 flex flex-wrap gap-3">
                  <Button variant="quiet" aria-expanded={open} onClick={() => setOpenId(open ? undefined : store.id)}>
                    {open ? "Hide members" : "Manage members"}
                  </Button>
                  {store.status === "failed" && (
                    <Button
                      variant="quiet"
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await api(`/stores/${store.id}/retry`, { method: "POST" });
                          await reload();
                        })
                      }
                    >
                      Retry
                    </Button>
                  )}
                  {(store.status === "ready" || store.status === "failed") && deletingId !== store.id && (
                    <Button variant="danger" onClick={() => setDeletingId(store.id)}>
                      Delete store
                    </Button>
                  )}
                </div>
                {deletingId === store.id && (
                  <DeleteStore store={store} onClose={() => setDeletingId(undefined)} reload={reload} />
                )}
                {open && <StoreMembers store={store} candidates={members} />}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

/** Deleting removes the store's server, its pods, its volumes and its secrets, so it asks for the name again and a reason. */
function DeleteStore({ store, onClose, reload }: { store: Store; onClose: () => void; reload: () => Promise<void> }) {
  const { busy, error, run } = useAction();
  const [confirmName, setConfirmName] = useState("");
  const [reason, setReason] = useState("");

  return (
    <form
      className="arrive mt-4 space-y-4 border-t border-line pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        void run(async () => {
          await api(`/stores/${store.id}`, { method: "DELETE", body: { confirm_name: confirmName, reason } });
          onClose();
          await reload();
        });
      }}
    >
      <p className="text-sm">
        This removes the store&apos;s MCP server, its WordPress and database, and everything stored in them. It cannot be
        undone.
      </p>
      <Field
        label={`Type "${store.name}" to confirm`}
        required
        autoComplete="off"
        value={confirmName}
        onChange={(e) => setConfirmName(e.target.value)}
      />
      <Field label="Reason (kept in the audit log)" required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="danger" disabled={busy || confirmName !== store.name || !reason.trim()}>
          Delete store
        </Button>
        <Button variant="quiet" onClick={onClose}>
          Cancel
        </Button>
      </div>
      <Notice tone="error">{error}</Notice>
    </form>
  );
}

function StoreMembers({ store, candidates }: { store: Store; candidates: User[] }) {
  const path = `/stores/${store.id}/members`;
  const { data: members, error: loadError, reload } = useData<User[]>(path);
  const { busy, error, run } = useAction();
  const [userId, setUserId] = useState("");

  if (loadError) return <Notice tone="error">{loadError}</Notice>;
  if (!members) return <p className="mt-4 text-ink-soft">Loading…</p>;

  const addable = candidates.filter((candidate) => !members.some((member) => member.id === candidate.id));

  return (
    <div className="arrive mt-4 space-y-4 border-t border-line pt-4">
      {members.length === 0 ? (
        <p className="text-ink-soft">Nobody works in this store yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {members.map((member) => (
            <li key={member.id} className="flex flex-wrap items-center justify-between gap-3 py-2">
              <span className="flex min-w-0 items-center gap-3">
                <Avatar name={member.name} />
                <span className="min-w-0">
                  <span className="block font-medium">{member.name}</span>
                  <span className="block text-sm break-all text-ink-soft">{member.email}</span>
                </span>
              </span>
              <Button
                variant="danger"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await api(`${path}/${member.id}`, { method: "DELETE" });
                    await reload();
                  })
                }
              >
                Remove from store
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await api(path, { body: { user_id: userId } });
            setUserId("");
            await reload();
          });
        }}
      >
        <div className="min-w-56 flex-1">
          <label htmlFor={`add-${store.id}`} className="block text-sm font-medium">
            Add a member
          </label>
          <select
            id={`add-${store.id}`}
            required
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            className={`mt-1.5 ${inputStyles}`}
          >
            <option value="">Choose a member</option>
            {addable.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name} ({candidate.email})
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" disabled={busy || !userId}>
          Add to store
        </Button>
      </form>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
