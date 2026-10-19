"use client";

import { useId, useState } from "react";

const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Shared by inputs, selects and textareas: a line to write on. */
export const inputStyles = `block w-full border-0 border-b border-ink-soft/50 bg-transparent px-0.5 py-2 text-base transition-colors placeholder:text-ink-soft/60 hover:border-ink ${focusRing}`;

const buttonStyles = {
  // The cut corner would clip a ring drawn outside, so this one is drawn inside.
  primary:
    "cut [--cut:9px] bg-ink text-canvas hover:bg-accent-deep focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-canvas",
  quiet: `border border-edge text-ink hover:bg-ink/5 ${focusRing}`,
  danger: `border border-danger/60 text-danger hover:bg-danger/5 ${focusRing}`,
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
      className={`px-5 py-2.5 text-sm font-medium transition active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 ${buttonStyles[variant]} ${className}`}
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
      <input id={id} aria-describedby={hint ? `${id}-hint` : undefined} {...props} className={`mt-1 ${inputStyles}`} />
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
          className={`text-sm text-ink-soft hover:text-ink ${focusRing}`}
        >
          {shown ? "Hide" : "Show"}
        </button>
      }
    />
  );
}

export function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`frame cut p-5 sm:p-6 ${className}`} />;
}

const statTones = { good: "text-good", warn: "text-brass", danger: "text-danger" };

/** A row of counts at the top of a page: a big number, what it counts, and an optional line under it. */
export function Stats({
  items,
}: {
  items: { label: string; value: React.ReactNode; note?: string; tone?: keyof typeof statTones }[];
}) {
  return (
    <dl className="mb-10 grid grid-cols-2 gap-3 lg:grid-cols-4">
      {items.map(({ label, value, note, tone }, index) => (
        // The tiles arrive one after another.
        <div key={label} className="frame cut arrive p-4 sm:p-5" style={{ animationDelay: `${index * 70}ms` }}>
          <dt className="text-sm text-ink-soft">{label}</dt>
          <dd className={`mt-3 font-display text-5xl leading-none font-light tabular-nums ${tone ? statTones[tone] : ""}`}>
            {value}
          </dd>
          {note && <dd className="mt-2 text-sm text-ink-soft">{note}</dd>}
        </div>
      ))}
    </dl>
  );
}

/** Heads a part of a page: the title on a hairline, anything in `children` at the right end. */
export function SectionTitle({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-line pb-2">
      <h2 className="font-display text-xl font-light sm:text-2xl">{title}</h2>
      {children}
    </div>
  );
}

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
      className="inline-flex size-9 shrink-0 items-center justify-center border border-edge font-display text-sm"
    >
      {initials}
    </span>
  );
}

const pillStyles = {
  good: "text-good",
  accent: "text-ink",
  warn: "text-brass",
  danger: "text-danger",
  muted: "text-ink-soft",
};

/** A status: a dot and a word, in the status's colour. */
export function Pill({ tone, children }: { tone: keyof typeof pillStyles; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-medium whitespace-nowrap capitalize ${pillStyles[tone]}`}>
      <span aria-hidden="true" className="size-2 rounded-full bg-current" />
      {children}
    </span>
  );
}

const noticeStyles = {
  error: "border-danger bg-danger/8 text-danger",
  success: "border-good bg-good-wash text-good",
  warning: "border-brass bg-brass-wash text-brass",
  info: "border-ink bg-accent-wash text-ink",
};

export function Notice({ tone, children }: { tone: keyof typeof noticeStyles; children: React.ReactNode }) {
  if (!children) return null;
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`arrive border-l-2 px-3.5 py-2.5 text-sm ${noticeStyles[tone]}`}>
      {children}
    </p>
  );
}

/** A value the API returns exactly once (temporary password, raw API key). */
export function Secret({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="status" className="arrive border-l-2 border-brass bg-brass-wash p-4">
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-2 bg-white/70 px-3 py-2 font-mono text-sm break-all select-all">{value}</p>
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
    <header className="mb-10">
      <h1 className="max-w-2xl font-display text-5xl leading-[1.05] font-light tracking-tight sm:text-6xl">{title}</h1>
      {children && <p className="mt-4 max-w-prose text-lg text-ink/75 sm:text-xl">{children}</p>}
    </header>
  );
}

const iconPaths = {
  stores:
    "M4 9.5 5.5 4h13L20 9.5M4 9.5V20h16V9.5M4 9.5c0 1.4 1.1 2.5 2.7 2.5s2.6-1.1 2.6-2.5c0 1.4 1.2 2.5 2.7 2.5s2.7-1.1 2.7-2.5c0 1.4 1 2.5 2.6 2.5S20 10.9 20 9.5M9.5 20v-4.5h5V20",
  chat: "M4 5.5h16v11h-9.5L6 20v-3.5H4z",
  key: "M14.5 9.5a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM13.2 12.7 20 19.5M17 16.5l1.8-1.8",
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

/** An icon in an outlined square, at the head of a tile. */
export function IconBox({ name }: { name: keyof typeof iconPaths }) {
  return (
    <span className="inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-edge">
      <Icon name={name} />
    </span>
  );
}

/** An urumi's ribbon blade: the mark, and the line drawn across the sign-in panel. */
export const blade = "M4 16c4 3 8 1 7-3s3-7 9-5";

/** The mark: the blade on a block of ink with its corner cut. `className` sets its size. */
export function Logo({ className = "size-9" }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`cut inline-flex shrink-0 items-center justify-center bg-ink [--cut:9px] ${className}`}>
      <svg viewBox="0 0 24 24" className="size-4/5" fill="none" stroke="var(--color-canvas)" strokeWidth="1.75" strokeLinecap="round">
        <path d={blade} />
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
