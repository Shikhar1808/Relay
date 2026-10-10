"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthShell } from "@/components/auth-shell";
import { Button, Notice, PasswordField, useAction } from "@/components/ui";
import { api, type TokenResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// Where invite and reset emails land. The token is in the URL fragment
// (#token=...), which browsers never send to a server.
export default function SetPasswordPage() {
  const { adopt } = useAuth();
  const router = useRouter();
  const { busy, error, run } = useAction();
  const [password, setPassword] = useState("");

  return (
    <AuthShell title="Choose your password">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
            if (!token) throw new Error("This page needs the link from your email. Open that link again.");
            adopt(await api<TokenResponse>("/auth/set-password", { body: { token, new_password: password } }));
            router.replace("/stores");
          });
        }}
      >
        <PasswordField
          label="New password"
          autoComplete="new-password"
          required
          pattern="[ -~]+"
          hint="8 to 20 letters, digits or symbols. No emoji or accented letters."
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Notice tone="error">{error}</Notice>
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Saving…" : "Save password and sign in"}
        </Button>
      </form>
      <p className="text-sm">
        Link expired?{" "}
        <Link href="/forgot-password" className="text-accent underline underline-offset-4">
          Get a new one
        </Link>
      </p>
    </AuthShell>
  );
}
