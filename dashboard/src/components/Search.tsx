"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "@/lib/api";
import type { Directory } from "@/lib/types";

interface Hit {
  href: string;
  label: string;
  detail: string;
}

// Loaded once per visit, the first time the box is focused.
let directory: Promise<Hit[]> | null = null;

function loadDirectory(): Promise<Hit[]> {
  directory ??= get<Directory>("/directory").then((d) => [
    ...d.teams.map((t) => ({ href: `/teams/${t.id}`, label: t.name, detail: "Team" })),
    ...d.players.map((p) => ({
      href: `/players/${p.id}`,
      label: p.name,
      // A player who sat out the newest season is still findable; say which year the numbers are from.
      detail: [p.team ?? "—", p.pts !== null && `${p.pts.toFixed(1)} pts`, p.last_season !== d.season && p.last_season]
        .filter(Boolean)
        .join(" · "),
    })),
  ]);
  directory.catch(() => (directory = null));
  return directory;
}

// "Doncic" should find "Dončić".
const fold = (s: string) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

export function Search() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [all, setAll] = useState<Hit[]>([]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);

  const hits = useMemo(() => {
    const q = fold(query.trim());
    if (!q) return [];
    const starts = all.filter((h) => fold(h.label).split(" ").some((w) => w.startsWith(q)));
    const rest = all.filter((h) => !starts.includes(h) && fold(h.label).includes(q));
    return [...starts, ...rest].slice(0, 8);
  }, [all, query]);

  // "/" focuses search from anywhere, the way most stat sites do.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement).tagName);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        input.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function go(hit: Hit) {
    setQuery("");
    setOpen(false);
    input.current?.blur();
    router.push(hit.href);
  }

  return (
    <div className="relative w-full max-w-xs">
      <input
        ref={input}
        type="search"
        value={query}
        placeholder="Search players and teams  /"
        aria-label="Search players and teams"
        onFocus={() => {
          setOpen(true);
          loadDirectory().then(setAll, () => {});
        }}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onChange={(e) => {
          setQuery(e.target.value);
          setCursor(0);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setCursor((c) => Math.min(c + 1, hits.length - 1));
          else if (e.key === "ArrowUp") setCursor((c) => Math.max(c - 1, 0));
          else if (e.key === "Enter" && hits[cursor]) go(hits[cursor]);
          else if (e.key === "Escape") input.current?.blur();
          else return;
          e.preventDefault();
        }}
        className="w-full rounded-lg border border-[var(--border)] bg-surface px-3 py-1.5 text-sm text-primary placeholder:text-muted"
      />
      {open && hits.length > 0 && (
        <ul className="absolute right-0 z-20 mt-1 w-full overflow-hidden rounded-lg border border-[var(--border)] bg-surface shadow-lg">
          {hits.map((h, i) => (
            <li key={h.href}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(h)}
                onMouseEnter={() => setCursor(i)}
                className={`flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-sm ${
                  i === cursor ? "bg-[var(--gridline)]/60" : ""
                }`}
              >
                <span className="truncate text-primary">{h.label}</span>
                <span className="tnum shrink-0 text-xs text-muted">{h.detail}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
