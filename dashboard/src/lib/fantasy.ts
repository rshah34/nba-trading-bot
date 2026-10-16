import type { FullLine } from "./types";

type Weights = Partial<Record<keyof FullLine, number>>;

/** Default points scoring on each site; sites that score identically share one entry. */
export const SCORING: Record<string, { label: string; weights: Weights }> = {
  espn: {
    label: "ESPN",
    weights: { pts: 1, fg3m: 1, fgm: 2, fga: -1, ftm: 1, fta: -1, reb: 1, ast: 2, stl: 4, blk: 4, tov: -2 },
  },
  sleeper: {
    // FanDuel and Yahoo score the same way.
    label: "Sleeper / FanDuel / Yahoo",
    weights: { pts: 1, reb: 1.2, ast: 1.5, stl: 3, blk: 3, tov: -1 },
  },
};

export const fantasyPoints = (line: FullLine, weights: Weights) =>
  (Object.entries(weights) as [keyof FullLine, number][]).reduce((sum, [stat, w]) => sum + w * line[stat], 0);
