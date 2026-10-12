import type { FullLine } from "./types";

type Weights = Partial<Record<keyof FullLine, number>>;

/** Default points-league scoring on each site. */
export const SCORING: Record<string, { label: string; note: string; weights: Weights }> = {
  espn: {
    label: "ESPN",
    note: "ESPN's default points scoring: makes add, missed shots and turnovers subtract.",
    weights: { pts: 1, fg3m: 1, fgm: 2, fga: -1, ftm: 1, fta: -1, reb: 1, ast: 2, stl: 4, blk: 4, tov: -2 },
  },
  sleeper: {
    label: "Sleeper",
    note: "Sleeper's default categories, without the double-double and triple-double bonuses.",
    weights: { pts: 0.5, fg3m: 0.5, reb: 1, ast: 1, stl: 2, blk: 2, tov: -1 },
  },
  fanduel: {
    label: "FanDuel / Yahoo",
    note: "FanDuel and Yahoo's default points scoring.",
    weights: { pts: 1, reb: 1.2, ast: 1.5, stl: 3, blk: 3, tov: -1 },
  },
};

export const fantasyPoints = (line: FullLine, weights: Weights) =>
  (Object.entries(weights) as [keyof FullLine, number][]).reduce((sum, [stat, w]) => sum + w * line[stat], 0);
