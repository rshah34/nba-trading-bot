"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search } from "./Search";
import { ThemeToggle } from "./ThemeToggle";

const LINKS = [
  { href: "/", label: "Tonight", match: (p: string) => p === "/" || p.startsWith("/games") },
  { href: "/players", label: "Players", match: (p: string) => p.startsWith("/players") },
  { href: "/teams", label: "Teams", match: (p: string) => p.startsWith("/teams") },
  { href: "/news", label: "News", match: (p: string) => p.startsWith("/news") },
  { href: "/picks", label: "Picks", match: (p: string) => p.startsWith("/picks") },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-[var(--border)] bg-plane/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 sm:px-6">
        <Link href="/" className="text-sm font-semibold tracking-tight text-primary">
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
                  active ? "bg-primary text-plane" : "text-secondary hover:text-primary"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex flex-1 items-center justify-end gap-2">
          <Search />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
