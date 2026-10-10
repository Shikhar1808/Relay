"use client";

import Link from "next/link";
import { useState } from "react";

import { AuthShell } from "@/components/auth-shell";
import { Button, Field, Notice, useAction } from "@/components/ui";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const { busy, error, run } = useAction();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  return (
    <AuthShell title="Reset your password">
      {sent ? (
        <Notice tone="success">
          If {email} has an account, a reset link is on its way. The link works once and expires in 30 minutes.
        </Notice>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await api("/auth/forgot-password", { body: { email } });
              setSent(true);
            });
          }}
        >
          <Field
            label="Email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Notice tone="error">{error}</Notice>
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Sending…" : "Email me a reset link"}
          </Button>
        </form>
      )}
      <p className="text-sm">
        <Link href="/login" className="text-accent underline underline-offset-4">
          Back to sign in
        </Link>
      </p>
    </AuthShell>
  );
}
