import type { StatKey, StatLine } from "./types";

/** Prop markets, in the order they're offered. Mirrors nba_bot.features.props.MARKETS. */
export const MARKETS: { key: string; label: string; stats: StatKey[] }[] = [
  { key: "player_points", label: "PTS", stats: ["pts"] },
  { key: "player_rebounds", label: "REB", stats: ["reb"] },
  { key: "player_assists", label: "AST", stats: ["ast"] },
  { key: "player_threes", label: "3PM", stats: ["fg3m"] },
  { key: "player_points_rebounds_assists", label: "PTS+REB+AST", stats: ["pts", "reb", "ast"] },
  { key: "player_points_rebounds", label: "PTS+REB", stats: ["pts", "reb"] },
  { key: "player_points_assists", label: "PTS+AST", stats: ["pts", "ast"] },
  { key: "player_rebounds_assists", label: "REB+AST", stats: ["reb", "ast"] },
  { key: "player_steals", label: "STL", stats: ["stl"] },
  { key: "player_blocks", label: "BLK", stats: ["blk"] },
  { key: "player_blocks_steals", label: "BLK+STL", stats: ["blk", "stl"] },
  { key: "player_turnovers", label: "TOV", stats: ["tov"] },
];

export const marketValue = (line: StatLine, stats: StatKey[]) =>
  stats.reduce((sum, s) => sum + line[s], 0);

export const pra = (line: StatLine) => line.pts + line.reb + line.ast;
