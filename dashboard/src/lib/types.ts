// Wire types — mirror what nba_bot.api returns.

export type StatKey = "pts" | "reb" | "ast" | "fg3m" | "stl" | "blk" | "tov";
export type StatLine = Record<StatKey, number> & { min: number };
/** Shot counts, carried for fantasy scoring. */
export type ShotKey = "fgm" | "fga" | "ftm" | "fta";
export type FullLine = StatLine & Record<ShotKey, number>;
export type Averages = FullLine & { gp: number };

export interface TeamRef {
  id: number;
  abbr: string;
  name: string;
  conference: string | null;
  division?: string | null;
}

export interface Report {
  player: string;
  player_id: number | null;
  team: string | null;
  kind: string;
  sources: number;
  confidence: number;
  summary: string;
  published_at: string | null;
}

export interface Pick {
  kind: "game" | "prop";
  game_id: string;
  date: string;
  decided_at: string | null;
  matchup: string;
  label: string;
  player_id: number | null;
  book: string | null;
  odds: number | null;
  model: number | null;
  market: number | null;
  edge: number | null;
  stake: number | null;
  pnl: number | null;
  clv: number | null;
  result: "won" | "lost" | "push" | "void" | null;
  actual: number | null;
  /** The one-line reason sent with the pick. */
  notes: string | null;
  guide: Record<string, { proj: number; over_at: number | null; under_at: number | null; actual?: number }> | null;
  /** The worst price, and for a prop the worst line, at which this is still a bet. */
  line: number | null;
  playable_odds: number | null;
  playable_line: number | null;
  /** Set when late injury news undercut the pick. */
  flag: string | null;
}

export interface GameCard {
  game_id: string;
  date: string;
  status: string;
  preseason: boolean;
  tipoff_at: string | null;
  /** Playoff games only: each side's wins in the series through this game. */
  series: { home: number; away: number } | null;
  /** `record` is the team's record in this kind of game (preseason or regular season). */
  home: TeamRef & { score: number | null; record: string | null };
  away: TeamRef & { score: number | null; record: string | null };
  model_home_prob: number | null;
  market_home_prob: number | null;
  key_factors: string[] | null;
}

export interface Slate {
  date: string;
  prev: string | null;
  next: string | null;
  games: GameCard[];
  picks: Pick[];
}

export interface SlateSide extends TeamRef {
  /** What the opponent allows in each stat against the league; 1 is average. Empty early in a season. */
  matchup: Partial<Record<StatKey, number>>;
}

export interface SlateLine {
  line: number;
  books: number;
  market_over: number;
  model_over: number | null;
  /** Times he went over this line in his last `games`. */
  hits: number;
  games: number;
}

export interface SlatePlayer {
  id: number;
  name: string;
  team: string;
  team_id: number;
  opp: string;
  home: boolean;
  game_id: string;
  /** Why he is in doubt, when he is. */
  status: string | null;
  notes: string[];
  proj: FullLine;
  /** Per-game averages over `season_label`, his most recent season with games. */
  season: Averages | null;
  season_label: string | null;
  l5: Averages | null;
  lines: Record<string, SlateLine>;
}

export interface SlatePlayers {
  date: string | null;
  /** Upcoming dates with games, soonest first. */
  dates: string[];
  games: { game_id: string; tipoff_at: string | null; preseason: boolean; away: SlateSide; home: SlateSide }[];
  players: SlatePlayer[];
}

export interface GamePlayer {
  id: number;
  name: string;
  status: string | null;
  proj: StatLine | null;
  actual: StatLine | null;
  /** Before the game: per-game averages from `avg_season`, his most recent season with games. */
  avg: (StatLine & { gp: number }) | null;
  avg_season: string | null;
  notes: string[];
}

export interface Injury {
  player: string;
  player_id?: number | null;
  /** As it applies to the game in question: "Out" once he is ruled out of it. */
  status: string;
  detail: string | null;
  /** Not expected back for weeks. */
  long_term: boolean;
}

export interface GameSide {
  team: TeamRef;
  players: GamePlayer[];
  injuries: Injury[];
  reports: Report[];
}

export interface GameDetail extends GameCard {
  away_side: GameSide;
  home_side: GameSide;
  picks: Pick[];
}

export interface PlayerRow {
  id: number;
  name: string;
  team: string | null;
  team_id: number | null;
  season: Averages;
  l10: Averages;
  l5: Averages;
}

export interface Directory {
  season: string | null;
  teams: { id: number; abbr: string; name: string }[];
  players: { id: number; name: string; team: string | null; last_season: string | null; pts: number | null }[];
}

export interface PlayersIndex {
  season: string;
  seasons: string[];
  players: PlayerRow[];
}

export interface PlayerGame extends StatLine {
  game_id: string;
  date: string;
  season: string;
  team: string | null;
  opp: string | null;
  home?: boolean;
  result?: string;
}

export interface MarketProjection {
  label: string;
  mean: number;
  ladder: [number, number][]; // [line, P(over)]
}

export interface PlayerDetail {
  id: number;
  name: string;
  team: TeamRef | null;
  seasons: string[];
  games: PlayerGame[];
  next: {
    game_id: string;
    date: string;
    tipoff_at: string | null;
    opp: string | null;
    home: boolean;
    preseason: boolean;
  } | null;
  projection: {
    minutes: number;
    notes: string[];
    markets: Record<string, MarketProjection>;
    /** Per stat: the lines at which the model alone would take the over / the under. */
    guide: Record<string, { proj: number; over_at: number | null; under_at: number | null }>;
  } | null;
  status: { label: string; detail: string | null } | null;
  lines: Record<string, { line: number; books: number; over: number }>;
  reports: Report[];
}

export interface TeamRow extends TeamRef {
  gp: number;
  w: number;
  l: number;
  pf: number;
  pa: number;
  margin: number;
  for: Record<StatKey, number>;
  allowed: Record<StatKey, number>;
}

export interface TeamsIndex {
  season: string;
  seasons: string[];
  teams: TeamRow[];
}

export interface TeamDetail extends TeamRow {
  season: string;
  roster: {
    id: number;
    name: string;
    stats_season: string;
    season: Averages | null;
    l5: Averages | null;
    status: string | null;
  }[];
  results: { game_id: string; date: string; home: boolean; opp: string; result: string; preseason: boolean }[];
  next: { game_id: string; date: string; home: boolean; opp: string } | null;
  injuries: Injury[];
  reports: Report[];
}

export interface PicksPayload {
  picks: Pick[];
  props: {
    n_bets: number;
    wins: number;
    losses: number;
    pushes: number;
    win_rate: number | null;
    roi: number | null;
    units: number;
    avg_clv: number | null;
    clv_positive_rate: number | null;
    guide?: { calls: number; hits: number; target: number };
    lines?: { n: number; brier_model: number; brier_market: number; brier_blend: number };
  };
  games: {
    n_bets: number;
    win_rate: number | null;
    roi: number | null;
    final_bankroll: number;
    avg_clv: number | null;
    clv_positive_rate: number | null;
  };
}

export interface CalibrationBin {
  bin: string;
  n: number;
  avg_predicted: number;
  actual_win_rate: number;
}

export interface SplitAccuracy {
  n: number;
  accuracy: number;
}

export interface BacktestReport {
  model_version: string;
  n: number;
  winner_accuracy: number | null;
  mean_brier: number | null;
  mean_log_loss: number | null;
  home_win_rate_actual: number | null;
  accuracy_confident_picks: SplitAccuracy | null;
  accuracy_home_back_to_back: SplitAccuracy | null;
  calibration: CalibrationBin[];
}

/** One side of a research finding: the slice's projection bias on one season. */
export type ResearchSide = {
  rows: number;
  players: number;
  bias: number;
  ci95: [number, number];
  bias_pct: number | null;
  mae: number;
  mean_actual: number;
};

export type ResearchFinding = {
  id: number;
  run_id: number;
  created_at: string;
  hypothesis: string;
  rationale: string | null;
  slice: string[];
  stat: string;
  multiplier: number;
  verdict: "pass" | "fail";
  applied_at: string | null;
  explore: ResearchSide;
  held: ResearchSide;
  mae: [number, number];
  brier: [number, number];
  reasons: string[];
};

/** A fix an explore-only run thinks is worth confirming later. */
export type ResearchCandidate = {
  hypothesis: string;
  rationale: string;
  slice: string[];
  stat: string;
  multiplier: number;
  evidence: string;
};

export type ResearchRun = {
  id: number;
  started_at: string;
  status: string;
  mode: "explore" | "confirm";
  focus: string | null;
  budget_usd: number | null;
  cost_usd: number;
  turns: number;
  summary: string | null;
  explored: { idea: string; outcome: string }[];
  candidates: ResearchCandidate[];
  log: string[];
  findings: ResearchFinding[];
};

export type ResearchPayload = {
  runs: ResearchRun[];
  limits: { max_budget: number; daily_cap: number; spent_today: number; running: boolean; confirms_per_run: number };
  ready: ResearchFinding[];
  held_back: { season: string; confirmations: number; next_z: number };
};
