"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Avatar,
  Button,
  Card,
  Field,
  formatDate,
  hueFor,
  inputStyles,
  Notice,
  PageTitle,
  Pill,
  useAction,
} from "@/components/ui";
import { api, type Store, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

export default function StoresPage() {
  const { user } = useAuth();
  const { data: stores, error, reload } = useData<Store[]>("/stores");

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!stores) return <p className="text-ink-soft">Loading…</p>;
  return user?.role === "admin" ? <AdminStores stores={stores} reload={reload} /> : <MemberStore store={stores[0]} />;
}

function StoreAddress({ store }: { store: Store }) {
  return store.url ? (
    <a href={store.url} className="break-all text-accent underline underline-offset-4">
      {store.url}
    </a>
  ) : (
    <span className="text-ink-soft">No address yet</span>
  );
}

function StatusPill({ status }: { status: Store["status"] }) {
  return <Pill tone={status === "ready" ? "good" : status === "failed" ? "muted" : "warn"}>{status}</Pill>;
}

function MemberStore({ store }: { store?: Store }) {
  if (!store) {
    return (
      <PageTitle title="You're not in a store yet">
        Ask your admin to add you to a store. Chat and your API key start working as soon as they do.
      </PageTitle>
    );
  }
  const nextSteps = [
    { href: "/chat", title: "Ask the agent", text: "Orders, products and stock, in plain words." },
    { href: "/api-key", title: "Get your API key", text: "Reach this store from Claude Code or Cursor." },
  ];
  return (
    <>
      <PageTitle title="Your store">
        You work in {store.name}. Chat and your API key only reach this store.
      </PageTitle>
      <Card className="max-w-xl">
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
      <div className="mt-4 grid max-w-xl gap-4 sm:grid-cols-2">
        {nextSteps.map(({ href, title, text }) => (
          <Link
            key={href}
            href={href}
            className="rounded-2xl border border-line bg-white p-5 transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span className="font-display text-lg font-semibold text-accent">{title}</span>
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
  const [openId, setOpenId] = useState<string>();
  const { data: people } = useData<User[]>("/team/members");
  const members = people?.filter((person) => person.role === "member" && person.is_active) ?? [];

  return (
    <>
      <PageTitle title="Stores">Create stores and choose who works in each. A member can be in one store at a time.</PageTitle>

      <Card className="max-w-xl">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await api("/stores", { body: { name } });
              setName("");
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

      {stores.length === 0 ? (
        <p className="mt-8 text-ink-soft">No stores yet. Create the first one above.</p>
      ) : (
        <ul className="mt-8 grid items-start gap-4 sm:grid-cols-2">
          {stores.map((store) => {
            const open = openId === store.id;
            return (
              // The store you open takes the full row, so its people have room.
              <li
                key={store.id}
                className={`relative rounded-2xl border border-line bg-white p-5 pl-6 ${open ? "col-span-full" : ""}`}
              >
                <span aria-hidden="true" className={`absolute inset-y-5 left-0 w-1 rounded-r-full ${hueFor(store.name)}`} />
                <div className="flex items-start justify-between gap-3">
                  <h2 className="min-w-0 font-display text-xl font-semibold break-words">{store.name}</h2>
                  <StatusPill status={store.status} />
                </div>
                <p className="mt-1 text-sm">
                  <StoreAddress store={store} />
                </p>
                <Button
                  variant="quiet"
                  className="mt-4"
                  aria-expanded={open}
                  onClick={() => setOpenId(open ? undefined : store.id)}
                >
                  {open ? "Hide members" : "Manage members"}
                </Button>
                {open && <StoreMembers store={store} candidates={members} />}
              </li>
            );
          })}
        </ul>
      )}
    </>
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
    <div className="arrive mt-4 space-y-4 rounded-xl bg-canvas p-4">
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
