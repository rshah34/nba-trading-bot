"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search } from "./Search";

const LINKS = [
  { href: "/", label: "Tonight", match: (p: string) => p === "/" || p.startsWith("/games") },
  { href: "/projections", label: "Projections", match: (p: string) => p.startsWith("/projections") },
  { href: "/fantasy", label: "Fantasy", match: (p: string) => p.startsWith("/fantasy") },
  { href: "/players", label: "Players", match: (p: string) => p.startsWith("/players") },
  { href: "/teams", label: "Teams", match: (p: string) => p.startsWith("/teams") },
  { href: "/news", label: "News", match: (p: string) => p.startsWith("/news") },
  { href: "/picks", label: "Picks", match: (p: string) => p.startsWith("/picks") },
  { href: "/research", label: "Research", match: (p: string) => p.startsWith("/research") },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-[var(--border)] bg-plane/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold tracking-tight text-primary">
          <span className="h-2.5 w-2.5 rounded-full bg-accent" aria-hidden />
          NBA Trading Bot
        </Link>
        <nav className="flex items-center gap-1" aria-label="Sections">
          {LINKS.map((l) => {
            const active = l.match(pathname);
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-md px-2.5 py-1 text-sm font-medium transition-colors ${
                  active ? "bg-accent/15 text-accent" : "text-secondary hover:text-primary"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex flex-1 items-center justify-end gap-2">
          <Search />
          <Link
            href="/ask"
            aria-current={pathname.startsWith("/ask") ? "page" : undefined}
            className={`flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors ${
              pathname.startsWith("/ask")
                ? "bg-accent text-plane"
                : "border border-accent/50 bg-accent/10 text-accent hover:bg-accent/20"
            }`}
          >
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden>
              <path d="M10 2c4.42 0 8 3.13 8 7s-3.58 7-8 7c-.9 0-1.77-.13-2.58-.37L3.5 17.5l1.1-3.3C3.02 12.95 2 11.07 2 9c0-3.87 3.58-7 8-7Zm-3.5 6a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm3.5 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm3.5 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z" />
            </svg>
            Ask
          </Link>
        </div>
      </div>
    </header>
  );
}
