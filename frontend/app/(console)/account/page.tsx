"use client";

import { useState } from "react";

import { Button, Notice, PasswordField, PageTitle, useAction } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AccountPage() {
  const { user } = useAuth();
  const { busy, error, run } = useAction();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [changed, setChanged] = useState(false);

  return (
    <>
      <PageTitle title={`Hello, ${user?.name.split(" ")[0]}.`}>Signed in as {user?.email}.</PageTitle>
      <form
        className="frame cut max-w-md space-y-5 p-5 sm:p-6"
        onSubmit={(event) => {
          event.preventDefault();
          setChanged(false);
          void run(async () => {
            await api("/auth/change-password", { body: { current_password: current, new_password: next } });
            setCurrent("");
            setNext("");
            setChanged(true);
          });
        }}
      >
        <h2 className="font-display text-xl">Change password</h2>
        <PasswordField
          label="Current password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <PasswordField
          label="New password"
          autoComplete="new-password"
          required
          pattern="[ -~]+"
          hint="8 to 20 letters, digits or symbols. No emoji or accented letters."
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <Notice tone="error">{error}</Notice>
        <Notice tone="success">{changed && "Password changed. Use the new one next time you sign in."}</Notice>
        <Button type="submit" disabled={busy}>
          {busy ? "Changing…" : "Change password"}
        </Button>
      </form>
    </>
  );
}
