"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Avatar, Button, Icon, Logo } from "@/components/ui";
import type { Store } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

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

  const links = [
    { href: "/stores", label: "Stores", icon: "stores" },
    ...(isAdmin ? [{ href: "/team", label: "Team", icon: "team" } as const] : []),
    ...(isAdmin ? [{ href: "/servers", label: "Servers", icon: "servers" } as const] : []),
    { href: "/chat", label: "Chat", icon: "chat" },
    { href: "/api-key", label: "API key", icon: "key" },
    ...(isAdmin ? [{ href: "/audit", label: "Audit", icon: "audit" } as const] : []),
    { href: "/account", label: "Account", icon: "account" },
  ] as const;
  const scope = isAdmin
    ? "All stores"
    : stores === undefined
      ? "…"
      : stores.length === 0
        ? "No store yet"
        : stores.map((store) => store.name).join(", ");

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[15rem_1fr]">
      {/* One set of links: a side rail on a wide screen, a tab bar under the thumb on a phone. */}
      <aside className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-white lg:sticky lg:top-0 lg:h-screen lg:border-t-0 lg:border-r lg:px-4 lg:py-5">
        <Link href="/stores" className="hidden items-center gap-3 rounded-xl px-2 py-1 lg:flex">
          <Logo />
          <span className="font-display text-2xl font-semibold tracking-tight">Urumi</span>
        </Link>
        <nav aria-label="Sections" className="flex lg:mt-8 lg:flex-col lg:gap-1">
          {links.map(({ href, label, icon }) => {
            const current = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                aria-current={current ? "page" : undefined}
                className={`flex flex-1 flex-col items-center gap-1 px-1 py-2.5 text-xs font-medium whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent lg:flex-none lg:flex-row lg:gap-3 lg:rounded-xl lg:px-3 lg:text-sm ${current ? "text-accent lg:bg-accent-wash" : "text-ink-soft hover:text-ink lg:hover:bg-canvas"}`}
              >
                <Icon name={icon} />
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="min-w-0 pb-28 lg:pb-12">
        <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-line bg-canvas/85 px-5 py-3 backdrop-blur sm:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Logo className="lg:hidden" />
            <div className="min-w-0">
              <p className="text-xs text-ink-soft">Working in</p>
              <p className="truncate font-display text-lg leading-tight font-semibold">{scope}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <Avatar name={user.name} />
            <div className="hidden text-sm leading-tight sm:block">
              <p className="font-medium">{user.name}</p>
              <p className="text-xs text-ink-soft capitalize">{user.role}</p>
            </div>
            <Button variant="quiet" onClick={logout}>
              Sign out
            </Button>
          </div>
        </header>
        <main className="page mx-auto max-w-5xl px-5 pt-8 sm:px-8">{children}</main>
      </div>
    </div>
  );
}
