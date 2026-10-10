-- Player-prop betting + news-derived player signals.
--   games.tipoff_at   : real tip-off instant (ScoreboardV3), so odds snapshots can
--                       be proven pre-tip and digests can show start times.
--   prop_odds         : per-book over/under snapshots for player props.
--   prop_bets         : sized paper bets on props (one per player per game), settled
--                       against the box score with CLV vs. the last pre-tip snapshot.
--   news_signals      : structured, LLM-extracted player-level signals from news
--                       (minutes limits, role changes, absences) that adjust projections.

ALTER TABLE games ADD COLUMN IF NOT EXISTS tipoff_at TIMESTAMPTZ;

ALTER TABLE news_articles ADD COLUMN IF NOT EXISTS signals_extracted_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS prop_odds (
    id          SERIAL PRIMARY KEY,
    game_id     TEXT NOT NULL REFERENCES games(game_id),
    sportsbook  TEXT NOT NULL,
    market      TEXT NOT NULL,              -- Odds API key, e.g. player_points
    player_name TEXT NOT NULL,
    player_id   INTEGER,                    -- resolved by normalized name; NULL = unmatched
    line        NUMERIC NOT NULL,
    over_price  INTEGER,                    -- American
    under_price INTEGER,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prop_odds_game_player_idx ON prop_odds (game_id, player_id, market);
CREATE INDEX IF NOT EXISTS prop_odds_captured_idx ON prop_odds (captured_at);

CREATE TABLE IF NOT EXISTS prop_bets (
    id            SERIAL PRIMARY KEY,
    game_id       TEXT NOT NULL REFERENCES games(game_id),
    player_id     INTEGER NOT NULL,
    player_name   TEXT NOT NULL,
    team_id       INTEGER REFERENCES teams(team_id),
    model_version TEXT NOT NULL,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    market        TEXT NOT NULL,
    side          TEXT NOT NULL,            -- 'over' | 'under'
    line          NUMERIC NOT NULL,
    sportsbook    TEXT NOT NULL,            -- the book offering the price we took
    n_books       INTEGER NOT NULL,
    decimal_odds  NUMERIC NOT NULL,
    model_prob    NUMERIC NOT NULL,
    market_prob   NUMERIC NOT NULL,         -- de-vigged consensus for our side at our line
    edge          NUMERIC NOT NULL,
    ev            NUMERIC NOT NULL,
    stake_fraction NUMERIC NOT NULL,
    proj_mean     NUMERIC NOT NULL,
    proj_phi      NUMERIC NOT NULL,         -- variance/mean of the projected distribution
    proj_minutes  NUMERIC,
    notes         TEXT,                     -- adjustments applied (teammate out, news signal)
    -- Settlement:
    closing_prob  NUMERIC,                  -- de-vigged close for our side, translated to our line
    clv           NUMERIC,                  -- closing_prob - market_prob (positive = market moved to us)
    actual_value  NUMERIC,
    result        TEXT,                     -- 'won' | 'lost' | 'push' | 'void'
    pnl           NUMERIC,
    settled_at    TIMESTAMPTZ,
    CONSTRAINT prop_bets_game_player_model_key UNIQUE (game_id, player_id, model_version)
);
CREATE INDEX IF NOT EXISTS prop_bets_unsettled_idx ON prop_bets (settled_at) WHERE settled_at IS NULL;

CREATE TABLE IF NOT EXISTS news_signals (
    id            SERIAL PRIMARY KEY,
    article_id    INTEGER REFERENCES news_articles(id) ON DELETE CASCADE,
    team_id       INTEGER REFERENCES teams(team_id),
    player_name   TEXT NOT NULL,
    player_id     INTEGER,
    kind          TEXT NOT NULL,            -- out | doubtful | questionable | minutes_limit | role_up | role_down | returning | other
    minutes_mult  NUMERIC NOT NULL DEFAULT 1,
    usage_mult    NUMERIC NOT NULL DEFAULT 1,
    confidence    NUMERIC NOT NULL,
    summary       TEXT NOT NULL,
    published_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ NOT NULL,
    model         TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS news_signals_player_idx ON news_signals (player_id, expires_at);

-- Every line the model priced, bet or not: the model's probability next to the
-- market's, so the model can be scored against real books on hundreds of lines a
-- night instead of waiting on a handful of bets.
CREATE TABLE IF NOT EXISTS prop_projections (
    id            SERIAL PRIMARY KEY,
    game_id       TEXT NOT NULL REFERENCES games(game_id),
    player_id     INTEGER NOT NULL,
    player_name   TEXT NOT NULL,
    market        TEXT NOT NULL,
    model_version TEXT NOT NULL,
    as_of         TIMESTAMPTZ NOT NULL DEFAULT now(),
    line          NUMERIC NOT NULL,          -- the most widely quoted line
    n_books       INTEGER NOT NULL,
    model_over    NUMERIC NOT NULL,          -- P(over | no push), raw model
    market_over   NUMERIC NOT NULL,          -- de-vigged consensus P(over)
    proj_mean     NUMERIC NOT NULL,
    proj_minutes  NUMERIC,
    actual_value  NUMERIC,                   -- filled at settlement; NULL if he didn't play
    CONSTRAINT prop_projections_key UNIQUE (game_id, player_id, market, model_version)
);
