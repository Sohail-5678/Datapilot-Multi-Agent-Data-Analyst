"use client";

import { LogOut, Menu, UserRound, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { signOutAction } from "@/app/actions";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/app", label: "Workspace" },
  { href: "/databases", label: "Databases" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/about", label: "About" },
];

export function NavLinks({ navy }: { navy: boolean }) {
  const pathname = usePathname();
  // The menu closes itself on navigation: it is only "open" for the path it was opened on.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn === pathname;
  const setOpen = (v: boolean) => setOpenOn(v ? pathname : null);
  return (
    <>
      <nav aria-label="Main" className="ml-6 hidden items-center gap-8 md:flex lg:ml-14">
        {LINKS.map((l) => {
          const active = pathname === l.href || pathname.startsWith(l.href + "/");
          return (
            <Link key={l.href} href={l.href} aria-current={active ? "page" : undefined} className={cn("caps link-underline pb-1 !text-[0.7rem] transition-opacity", active ? "opacity-100" : "opacity-70 hover:opacity-100")}>
              {l.label}
            </Link>
          );
        })}
      </nav>
      <button type="button" className="ml-auto grid size-9 place-items-center rounded-full border border-current/20 md:hidden" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)}>
        <Menu className="size-4" />
      </button>
      {open && (
        <div className="fixed inset-0 z-50 bg-navy text-ivory md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="flex h-20 items-center justify-end px-4">
            <button type="button" className="grid size-9 place-items-center rounded-full border border-ivory/30" aria-label="Close menu" onClick={() => setOpen(false)}>
              <X className="size-4" />
            </button>
          </div>
          <nav className="flex flex-col gap-2 px-8">
            {LINKS.map((l, i) => (
              <Link key={l.href} href={l.href} className="flex items-baseline gap-4 border-b border-ivory/15 py-5">
                <span className="font-mono text-xs tracking-[0.2em] text-stone">{String(i + 1).padStart(2, "0")}</span>
                <span className={cn("display text-4xl", navy && "")}>{l.label}</span>
              </Link>
            ))}
          </nav>
        </div>
      )}
    </>
  );
}

export function UserMenu({ user, navy }: { user: { name: string; role: string; image: string | null; login: string | null } | null; navy: boolean }) {
  const [open, setOpen] = useState(false);
  if (!user) {
    return (
      <Link href="/login" className={cn("btn !px-4 !py-2 text-xs", navy ? "border border-ivory/40 hover:bg-ivory/10" : "btn-ghost")}>
        Sign in
      </Link>
    );
  }
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="menu" className={cn("flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-xs font-semibold", navy ? "border-ivory/30" : "border-line-strong")}>
        <span className="grid size-7 place-items-center overflow-hidden rounded-full bg-accent text-white">
          {user.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={user.image} alt="" className="size-full object-cover" />
          ) : (
            <UserRound className="size-4" />
          )}
        </span>
        <span className="hidden sm:inline">{user.login ?? user.name}</span>
        <span className="label hidden !text-[0.55rem] sm:inline">{user.role}</span>
      </button>
      {open && (
        <div role="menu" className="card absolute right-0 top-12 z-50 w-56 p-2 text-ink">
          <p className="px-3 py-2 text-xs text-muted">
            {user.role === "guest" ? "Guest session · 10 questions/hour" : `Signed in as ${user.login ?? user.name}`}
          </p>
          <form action={signOutAction}>
            <button type="submit" role="menuitem" className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-surface-2">
              <LogOut className="size-4" /> Sign out
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
