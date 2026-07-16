// Wire types — mirror nba_bot.api.schemas on the Python side.

export interface TeamRef {
  team_id: number;
  abbreviation: string;
  full_name: string;
}

export interface PredictionSummary {
  game_id: string;
  game_date: string;
  season: string;
  status: string;
  home: TeamRef;
  away: TeamRef;
  home_score: number | null;
  away_score: number | null;
  model_version: string;
  as_of: string;
  predicted_home_win_prob: number;
  market_home_win_prob: number | null;
  edge: number | null;
  predicted_spread: number | null;
  market_spread: number | null;
}

export interface ModelInfo {
  model_version: string;
  n_predictions: number;
  latest_as_of: string | null;
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
