"use client";

import { useId, useState } from "react";

const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Shared by inputs, selects and textareas. */
export const inputStyles = `block w-full rounded-xl border border-line bg-white px-3.5 py-2.5 text-base transition-colors placeholder:text-ink-soft/60 hover:border-ink-soft/50 ${focusRing}`;

const buttonStyles = {
  primary: "bg-accent text-white hover:bg-accent-deep",
  quiet: "border border-line bg-white text-ink hover:border-accent hover:text-accent",
  danger: "border border-line bg-white text-danger hover:border-danger",
};

export function Button({
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof buttonStyles }) {
  return (
    <button
      type="button"
      {...props}
      className={`rounded-full px-5 py-2.5 text-sm font-medium transition active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 ${focusRing} ${buttonStyles[variant]} ${className}`}
    />
  );
}

export function Field({
  label,
  hint,
  action,
  className = "",
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; action?: React.ReactNode }) {
  const id = useId();
  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id} className="block text-sm font-medium">
          {label}
        </label>
        {action}
      </div>
      <input id={id} aria-describedby={hint ? `${id}-hint` : undefined} {...props} className={`mt-1.5 ${inputStyles}`} />
      {hint && (
        <p id={`${id}-hint`} className="mt-1.5 text-sm text-ink-soft">
          {hint}
        </p>
      )}
    </div>
  );
}

/** A password box you can reveal, so a typo is seen before it is sent. */
export function PasswordField(props: Omit<React.ComponentProps<typeof Field>, "type" | "action">) {
  const [shown, setShown] = useState(false);
  return (
    <Field
      {...props}
      type={shown ? "text" : "password"}
      minLength={8}
      maxLength={20}
      action={
        <button
          type="button"
          aria-pressed={shown}
          onClick={() => setShown(!shown)}
          className={`rounded text-sm text-ink-soft hover:text-ink ${focusRing}`}
        >
          {shown ? "Hide" : "Show"}
        </button>
      }
    />
  );
}

export function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`rounded-2xl border border-line bg-white p-5 sm:p-6 ${className}`} />;
}

const hues = ["bg-apricot", "bg-rose", "bg-orchid", "bg-sky", "bg-butter"];

/** The same name always gets the same colour, so people and stores are easy to spot again. */
export const hueFor = (name: string) => hues[[...name].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % hues.length];

export function Avatar({ name }: { name: string }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
  return (
    <span
      aria-hidden="true"
      className={`inline-flex size-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${hueFor(name)}`}
    >
      {initials}
    </span>
  );
}

const pillStyles = {
  good: "bg-good-wash text-good",
  accent: "bg-accent-wash text-accent-deep",
  warn: "bg-brass-wash text-brass",
  danger: "bg-danger/10 text-danger",
  muted: "bg-canvas text-ink-soft",
};

export function Pill({ tone, children }: { tone: keyof typeof pillStyles; children: React.ReactNode }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${pillStyles[tone]}`}>
      {children}
    </span>
  );
}

const noticeStyles = {
  error: "bg-danger/10 text-danger",
  success: "bg-good-wash text-good",
  warning: "bg-brass-wash text-brass",
  info: "bg-accent-wash text-accent-deep",
};

export function Notice({ tone, children }: { tone: keyof typeof noticeStyles; children: React.ReactNode }) {
  if (!children) return null;
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`arrive rounded-xl px-3.5 py-2.5 text-sm ${noticeStyles[tone]}`}>
      {children}
    </p>
  );
}

/** A value the API returns exactly once (temporary password, raw API key). */
export function Secret({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="status" className="arrive rounded-2xl border border-brass/30 bg-brass-wash p-4">
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-2 rounded-lg bg-white/70 px-3 py-2 font-mono text-sm break-all select-all">{value}</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          variant="quiet"
          onClick={async () => {
            await navigator.clipboard.writeText(value);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
        <p className="text-sm text-ink-soft">This is shown once. It can&apos;t be retrieved after you leave this page.</p>
      </div>
    </div>
  );
}

export function PageTitle({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="font-display text-3xl font-semibold tracking-tight">{title}</h1>
      {children && <p className="mt-2 max-w-prose text-ink-soft">{children}</p>}
    </header>
  );
}

const iconPaths = {
  stores:
    "M4 9.5 5.5 4h13L20 9.5M4 9.5V20h16V9.5M4 9.5c0 1.4 1.1 2.5 2.7 2.5s2.6-1.1 2.6-2.5c0 1.4 1.2 2.5 2.7 2.5s2.7-1.1 2.7-2.5c0 1.4 1 2.5 2.6 2.5S20 10.9 20 9.5M9.5 20v-4.5h5V20",
  team: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM2.5 20c0-3.3 2.9-5.5 6.5-5.5s6.5 2.2 6.5 5.5M16 4.3a3.5 3.5 0 0 1 0 6.4M18.5 14.9c1.9.8 3 2.6 3 5.1",
  chat: "M4 5.5h16v11h-9.5L6 20v-3.5H4z",
  key: "M14.5 9.5a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM13.2 12.7 20 19.5M17 16.5l1.8-1.8",
  account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 20.5c0-3.6 3.3-6 7.5-6s7.5 2.4 7.5 6",
  audit: "M7 3.5h10A1.5 1.5 0 0 1 18.5 5v14a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5ZM9 8.5h6M9 12h6M9 15.5h3.5",
  send: "M12 19V5M5.5 11.5 12 5l6.5 6.5",
  servers: "M5 4h14v6.5H5zM5 13.5h14V20H5zM8.5 7.25h.01M8.5 16.75h.01",
};

export function Icon({ name }: { name: keyof typeof iconPaths }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={iconPaths[name]} />
    </svg>
  );
}

/** The mark: an urumi's ribbon blade, in the sign-in panel's colours. */
export function Logo({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-flex size-9 shrink-0 items-center justify-center rounded-xl bg-linear-to-br from-[#f7a56e] via-[#e83fd0] to-[#6a2fe0] ${className}`}
    >
      <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="white" strokeWidth="2.25" strokeLinecap="round">
        <path d="M4 16c4 3 8 1 7-3s3-7 9-5" />
      </svg>
    </span>
  );
}

/** Runs an async action from a form or button, tracking its busy and error state. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export const formatDate = (iso: string) => new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
export const formatDateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
