"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Avatar, Button, Logo } from "@/components/ui";
import type { Store } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const isAdmin = user?.role === "admin";
  // A member works in one or more stores; they head every page they see.
  const { data: stores } = useData<Store[]>(user && !isAdmin ? "/stores" : null);

  useEffect(() => {
    if (user === null) router.replace("/login");
  }, [user, router]);

  if (!user) return <p className="p-8 text-ink-soft">Loading…</p>;

  // Each section has its own paper (globals.css); the servers' health page is the dark one.
  const links = [
    { href: "/stores", label: "Stores", tint: "" },
    ...(isAdmin ? [{ href: "/team", label: "Team", tint: "tint-blush" }] : []),
    ...(isAdmin ? [{ href: "/servers", label: "Servers", tint: "tint-night" }] : []),
    { href: "/chat", label: "Chat", tint: "" },
    { href: "/api-key", label: "API key", tint: "tint-mint" },
    ...(isAdmin ? [{ href: "/audit", label: "Audit", tint: "tint-butter" }] : []),
    { href: "/account", label: "Account", tint: "" },
  ];
  const here = links.find((link) => link.href === pathname);
  const scope = isAdmin
    ? "All stores"
    : stores === undefined
      ? "…"
      : stores.length === 0
        ? "No store yet"
        : stores.map((store) => store.name).join(", ");

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[5.5rem_minmax(0,1fr)]">
      {/* The rail: the mark, and the name read upwards. Wide screens only. */}
      <aside className="sticky top-0 hidden h-screen flex-col items-center overflow-hidden border-r border-line pt-5 lg:flex">
        <Link href="/stores" aria-label="Urumi home" className={focusRing}>
          <Logo className="size-14" />
        </Link>
        <p aria-hidden="true" className="mt-8 rotate-180 font-display text-6xl leading-none font-light whitespace-nowrap [writing-mode:vertical-rl]">
          urumi store operations
        </p>
      </aside>

      <div className="flex min-h-screen min-w-0 flex-col">
        <header className="sticky top-0 z-10 border-b border-line bg-canvas/90 backdrop-blur">
          {/* On a wide screen the links sit in the bar; on a phone they wrap to a row of their own that scrolls sideways. */}
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 px-5 pt-3 sm:px-8 lg:flex-nowrap lg:py-3">
            <Link href="/stores" className={`flex items-center gap-3 lg:hidden ${focusRing}`}>
              <Logo />
              <span className="font-display text-xl font-medium tracking-tight">urumi</span>
            </Link>
            <nav
              aria-label="Sections"
              className="order-last -mx-5 flex w-[calc(100%+2.5rem)] gap-6 overflow-x-auto px-5 pt-1 sm:-mx-8 sm:w-[calc(100%+4rem)] sm:px-8 lg:order-none lg:mx-0 lg:w-auto lg:px-0 lg:pt-0"
            >
              {links.map(({ href, label }) => {
                const current = pathname === href;
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={current ? "page" : undefined}
                    className={`border-b py-2.5 font-display whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent lg:py-1 ${current ? "border-ink text-ink" : "border-transparent text-ink-soft hover:text-ink"}`}
                  >
                    {label}
                  </Link>
                );
              })}
            </nav>
            <div className="ml-auto flex shrink-0 items-center gap-3">
              <Avatar name={user.name} />
              <div className="hidden text-sm leading-tight sm:block">
                <p className="font-medium">{user.name}</p>
                <p className="text-xs text-ink-soft capitalize">{user.role}</p>
              </div>
              <Button variant="quiet" className="px-3.5 py-1.5" onClick={logout}>
                Sign out
              </Button>
            </div>
          </div>
        </header>

        {/* `isolate` keeps the outline below behind the page but above this paper. `text-ink` is set here
            again because inherited text keeps the body's colour, not the tint's. */}
        <div className={`isolate flex-1 bg-canvas pb-16 text-ink transition-colors duration-500 ${here?.tint ?? ""}`}>
          <main className="relative mx-auto max-w-6xl px-5 pt-8 sm:px-8 sm:pt-10">
            {/* An outline that turns a step on each page, so moving between them is felt. */}
            <svg
              aria-hidden="true"
              viewBox="0 0 120 120"
              className="pointer-events-none absolute top-2 right-8 -z-10 hidden w-56 text-edge/35 transition-[rotate] duration-700 ease-out motion-reduce:transition-none lg:block"
              style={{ rotate: `${(here ? links.indexOf(here) : 0) * 47}deg` }}
              fill="none"
              stroke="currentColor"
            >
              <path d="M62 4 112 33 104 96 44 116 6 82 14 22Z" vectorEffect="non-scaling-stroke" />
            </svg>
            <p className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
              {here && <span className="font-medium tracking-[0.16em] uppercase">{here.label}</span>}
              <span className="flex min-w-0 items-center gap-1.5 text-ink-soft">
                <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-good" />
                <span className="truncate">
                  Working in <span className="text-ink">{scope}</span>
                </span>
              </span>
            </p>
            <div className="page">{children}</div>
          </main>
        </div>
      </div>
    </div>
  );
}
