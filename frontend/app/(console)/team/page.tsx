"use client";

import { useState } from "react";

import { KeyManager } from "@/components/key-manager";
import { Avatar, Button, Card, Field, Notice, PageTitle, Pill, Secret, SectionTitle, Stats, useAction } from "@/components/ui";
import { api, type MemberCreated, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

export default function TeamPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { data: people, error: loadError, reload } = useData<User[]>(isAdmin ? "/team/members" : null);
  const invite = useAction();
  const rowAction = useAction();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [created, setCreated] = useState<MemberCreated>();
  const [keysOpenFor, setKeysOpenFor] = useState<string>();
  const [resentTo, setResentTo] = useState<string>();

  if (!isAdmin) return <PageTitle title="Team">Only the admin can manage the team.</PageTitle>;
  const members = people?.filter((person) => person.role === "member") ?? [];
  const deactivated = members.filter((person) => !person.is_active).length;

  return (
    <>
      <PageTitle title="Who works where.">
        Add people, then give each one a store on the Stores page. A new member gets an email with a link to
        choose their password.
      </PageTitle>
      {people && (
        <Stats
          items={[
            { label: "People", value: people.length },
            { label: "Active members", value: members.length - deactivated, tone: members.length > deactivated ? "good" : undefined },
            { label: "Admins", value: people.length - members.length },
            { label: "Deactivated", value: deactivated, note: "Can't sign in" },
          ]}
        />
      )}

      <Card className="max-w-3xl space-y-4">
        <form
          className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            void invite.run(async () => {
              setCreated(await api<MemberCreated>("/team/members", { body: { name, email } }));
              setName("");
              setEmail("");
              await reload();
            });
          }}
        >
          <Field label="Name" required maxLength={255} value={name} onChange={(e) => setName(e.target.value)} />
          <Field label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <Button type="submit" disabled={invite.busy}>
            Add member
          </Button>
        </form>
        <Notice tone="error">{invite.error}</Notice>
        {created && (
          <Secret
            label={`${created.user.name} was added and emailed an invite. If the email doesn't arrive, give them this temporary password:`}
            value={created.temporary_password}
          />
        )}
      </Card>

      <div className="mt-10 space-y-4">
        <SectionTitle title="People" />
        <Notice tone="error">{loadError ?? rowAction.error}</Notice>
        <Notice tone="success">{resentTo && `A new invite link was emailed to ${resentTo}.`}</Notice>
      </div>
      {people && (
        <ul className="divide-y divide-line border-b border-line">
          {people.map((person) => (
            <li key={person.id} className="py-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={person.name} />
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 font-medium">
                      {person.name}
                      {person.role === "admin" ? (
                        <Pill tone="accent">admin</Pill>
                      ) : person.is_active ? (
                        <Pill tone="good">member</Pill>
                      ) : (
                        <Pill tone="muted">deactivated</Pill>
                      )}
                    </p>
                    <p className="text-sm break-all text-ink-soft">{person.email}</p>
                  </div>
                </div>
                {person.role === "member" && person.is_active && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="quiet"
                      aria-expanded={keysOpenFor === person.id}
                      onClick={() => setKeysOpenFor(keysOpenFor === person.id ? undefined : person.id)}
                    >
                      {keysOpenFor === person.id ? "Hide API key" : "API key"}
                    </Button>
                    <Button
                      variant="quiet"
                      disabled={rowAction.busy}
                      onClick={() =>
                        rowAction.run(async () => {
                          setResentTo(undefined);
                          await api(`/team/members/${person.id}/resend-invite`, { method: "POST" });
                          setResentTo(person.email);
                        })
                      }
                    >
                      Resend invite
                    </Button>
                    <Button
                      variant="danger"
                      disabled={rowAction.busy}
                      onClick={() => {
                        if (!window.confirm(`Deactivate ${person.name}? They will no longer be able to sign in.`)) return;
                        void rowAction.run(async () => {
                          await api(`/team/members/${person.id}`, { method: "DELETE" });
                          await reload();
                        });
                      }}
                    >
                      Deactivate
                    </Button>
                  </div>
                )}
              </div>
              {keysOpenFor === person.id && (
                <div className="arrive mt-4 bg-accent-wash/60 p-4">
                  <KeyManager basePath={`/team/members/${person.id}/api-keys`} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
