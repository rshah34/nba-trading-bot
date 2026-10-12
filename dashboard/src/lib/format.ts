import type { StatKey } from "./types";

export const STAT_LABELS: Record<StatKey | "min" | "pra", string> = {
  min: "MIN",
  pts: "PTS",
  reb: "REB",
  ast: "AST",
  fg3m: "3PM",
  stl: "STL",
  blk: "BLK",
  tov: "TOV",
  pra: "PRA",
};

export const pct = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : v.toFixed(digits);

export const signed = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;

/** Decimal odds -> American, the way US books show them. */
export function american(decimal: number | null | undefined): string {
  if (decimal == null || decimal <= 1) return "—";
  return decimal >= 2 ? `+${Math.round((decimal - 1) * 100)}` : `-${Math.round(100 / (decimal - 1))}`;
}

/** The fair (no-vig) American price for a probability. */
export function fairOdds(p: number): string {
  if (p <= 0 || p >= 1) return "—";
  return american(1 / p);
}

// Dates arrive as YYYY-MM-DD; parse as local so the label can't slip a day.
function localDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

export const longDate = (iso: string) =>
  localDate(iso).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

export const shortDate = (iso: string) =>
  localDate(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const clock = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";

export function ago(iso: string | null): string {
  if (!iso) return "";
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** "BOS leads 2-1", "Series tied 1-1", "BOS wins 4-2". */
export function seriesLine(home: string, away: string, s: { home: number; away: number }): string {
  if (s.home === s.away) return `Series tied ${s.home}-${s.away}`;
  const [leader, hi, lo] = s.home > s.away ? [home, s.home, s.away] : [away, s.away, s.home];
  return `${leader} ${hi === 4 ? "wins" : "leads"} ${hi}-${lo}`;
}

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const BOOKS: Record<string, string> = {
  draftkings: "DraftKings",
  hardrockbet: "Hard Rock",
  kalshi: "Kalshi",
  fanduel: "FanDuel",
  betmgm: "BetMGM",
};

export const bookName = (key: string) => BOOKS[key] ?? key;
