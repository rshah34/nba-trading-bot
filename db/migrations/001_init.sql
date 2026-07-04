-- Initial schema for nba-trading-bot
-- Applied automatically on first container start via docker-entrypoint-initdb.d

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE teams (
    team_id     INTEGER PRIMARY KEY,        -- nba_api team id
    abbreviation TEXT NOT NULL,
    full_name   TEXT NOT NULL,
    conference  TEXT,
    division    TEXT
);

CREATE TABLE games (
    game_id         TEXT PRIMARY KEY,       -- nba_api game id
    season          TEXT NOT NULL,          -- e.g. '2023-24'
    game_date       DATE NOT NULL,
    home_team_id    INTEGER NOT NULL REFERENCES teams(team_id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(team_id),
    home_score      INTEGER,
    away_score      INTEGER,
    status          TEXT NOT NULL DEFAULT 'scheduled', -- scheduled | final
    home_rest_days  INTEGER,
    away_rest_days  INTEGER,
    is_back_to_back_home BOOLEAN DEFAULT FALSE,
    is_back_to_back_away BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_games_date ON games(game_date);

CREATE TABLE team_game_stats (
    game_id     TEXT NOT NULL REFERENCES games(game_id),
    team_id     INTEGER NOT NULL REFERENCES teams(team_id),
    points      INTEGER,
    fg_pct      NUMERIC,
    fg3_pct     NUMERIC,
    ft_pct      NUMERIC,
    rebounds    INTEGER,
    assists     INTEGER,
    turnovers   INTEGER,
    plus_minus  NUMERIC,
    PRIMARY KEY (game_id, team_id)
);

CREATE TABLE injuries (
    id          SERIAL PRIMARY KEY,
    team_id     INTEGER NOT NULL REFERENCES teams(team_id),
    player_name TEXT NOT NULL,
    status      TEXT NOT NULL,              -- out | doubtful | questionable | probable
    reason      TEXT,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_injuries_team ON injuries(team_id, reported_at DESC);

CREATE TABLE odds (
    id              SERIAL PRIMARY KEY,
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    sportsbook      TEXT NOT NULL,
    home_moneyline  INTEGER,
    away_moneyline  INTEGER,
    spread_home     NUMERIC,
    spread_home_price INTEGER,
    spread_away_price INTEGER,
    total_points    NUMERIC,
    over_price      INTEGER,
    under_price     INTEGER,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_closing_line BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_odds_game ON odds(game_id, captured_at DESC);

CREATE TABLE news_articles (
    id          SERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT UNIQUE NOT NULL,
    title       TEXT,
    published_at TIMESTAMPTZ,
    team_ids    INTEGER[],
    raw_text    TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Embedding dimension matches the embedding model configured in settings
-- (default: Voyage voyage-3, 1024 dims). Change dim + migrate if you switch models.
CREATE TABLE news_chunks (
    id          SERIAL PRIMARY KEY,
    article_id  INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    chunk_text  TEXT NOT NULL,
    embedding   vector(1024),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_news_chunks_embedding ON news_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE predictions (
    id                      SERIAL PRIMARY KEY,
    game_id                 TEXT NOT NULL REFERENCES games(game_id),
    model_version           TEXT NOT NULL,
    predicted_home_win_prob NUMERIC NOT NULL,
    predicted_spread        NUMERIC,
    market_home_win_prob    NUMERIC,        -- implied from odds at prediction time
    market_spread           NUMERIC,
    reasoning               TEXT,
    context_used            JSONB,          -- retrieved news chunk ids, stats snapshot, etc.
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (game_id, model_version)
);

CREATE TABLE prediction_evaluations (
    id                  SERIAL PRIMARY KEY,
    prediction_id       INTEGER NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
    actual_home_win     BOOLEAN NOT NULL,
    brier_score         NUMERIC NOT NULL,
    log_loss            NUMERIC NOT NULL,
    edge_vs_close       NUMERIC,            -- predicted_home_win_prob - market_home_win_prob at close
    correctly_picked_winner BOOLEAN NOT NULL,
    beat_spread         BOOLEAN,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
