"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AuthShell } from "@/components/auth-shell";
import { Button, Field, Notice, PasswordField, useAction } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { user, login } = useAuth();
  const router = useRouter();
  const { busy, error, run } = useAction();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (user) router.replace("/stores");
  }, [user, router]);

  return (
    <AuthShell title="Sign in">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void run(() => login(email, password));
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
        <PasswordField
          label="Password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Notice tone="error">{error}</Notice>
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
      <p className="text-sm">
        <Link href="/forgot-password" className="text-accent underline underline-offset-4">
          Forgot your password?
        </Link>
      </p>
      <p className="text-sm text-ink-soft">New here? Your admin creates your account and you get an email with a link.</p>
    </AuthShell>
  );
}
