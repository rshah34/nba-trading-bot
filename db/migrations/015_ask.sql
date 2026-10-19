-- Ask: Claude answers plain-English questions by writing SQL. That SQL runs as its own
-- login, nba_ask, which can read only the `ask` schema below: read-only by default,
-- every statement capped at 5 seconds, and no access to the rest of the database
-- (bets, picks, odds). The login's password is set by `nba-bot init-db` from
-- ASK_DB_PASSWORD, so it never sits in a committed file.
CREATE SCHEMA IF NOT EXISTS ask;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nba_ask') THEN
        CREATE ROLE nba_ask NOLOGIN;
    END IF;
END $$;
ALTER ROLE nba_ask NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 5;
ALTER ROLE nba_ask SET default_transaction_read_only = on;
ALTER ROLE nba_ask SET statement_timeout = '5s';
ALTER ROLE nba_ask SET search_path = ask;
REVOKE ALL ON SCHEMA public FROM nba_ask;
-- Temporary tables are open to every login by default; Ask never needs one. (The app's
-- own login is a superuser and keeps them.)
DO $$ BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nba_ask;
GRANT USAGE ON SCHEMA ask TO nba_ask;

-- One row per team per game, with each side's standing going into the game. Built in
-- Python (`nba_bot.ask.data.refresh`), because the point-in-time ranks and the
-- All-Star break are easier to get right there than in a view.
CREATE TABLE IF NOT EXISTS ask.team_games (
    season               TEXT NOT NULL,
    season_type          TEXT NOT NULL,
    game_id              TEXT NOT NULL,
    game_date            DATE NOT NULL,
    team                 TEXT NOT NULL,
    opponent             TEXT NOT NULL,
    home                 BOOLEAN NOT NULL,
    team_score           INTEGER NOT NULL,
    opp_score            INTEGER NOT NULL,
    margin               INTEGER NOT NULL,
    won                  BOOLEAN NOT NULL,
    rest_days            INTEGER,
    back_to_back         BOOLEAN,
    after_all_star_break BOOLEAN,
    team_wins_before     INTEGER,
    team_losses_before   INTEGER,
    opp_wins_before      INTEGER,
    opp_losses_before    INTEGER,
    team_rank_before     INTEGER,
    opp_rank_before      INTEGER,
    PRIMARY KEY (game_id, team)
);

CREATE OR REPLACE VIEW ask.teams AS
SELECT abbreviation AS team, full_name AS name, conference, division FROM public.teams;

CREATE OR REPLACE VIEW ask.player_games AS
SELECT g.season,
       CASE substr(g.game_id, 1, 3) WHEN '001' THEN 'preseason' WHEN '002' THEN 'regular'
            WHEN '004' THEN 'playoffs' WHEN '005' THEN 'play-in' ELSE 'other' END AS season_type,
       g.game_date, g.game_id, p.player_id, p.player_name AS player,
       t.abbreviation AS team, o.abbreviation AS opponent, p.team_id = g.home_team_id AS home,
       p.minutes::float AS minutes, p.points, p.rebounds, p.assists, p.steals, p.blocks,
       p.turnovers, p.fgm, p.fga, p.fg3m, p.fg3a, p.ftm, p.fta, p.plus_minus::float AS plus_minus,
       p.points + p.rebounds + p.assists AS pra,
       tg.won, tg.after_all_star_break, tg.opp_rank_before
FROM public.player_game_stats p
JOIN public.games g ON g.game_id = p.game_id AND g.status = 'final'
JOIN public.teams t ON t.team_id = p.team_id
JOIN public.teams o ON o.team_id = CASE WHEN p.team_id = g.home_team_id THEN g.away_team_id
                                         ELSE g.home_team_id END
LEFT JOIN ask.team_games tg ON tg.game_id = p.game_id AND tg.team = t.abbreviation;

CREATE OR REPLACE VIEW ask.schedule AS
SELECT g.season,
       CASE substr(g.game_id, 1, 3) WHEN '001' THEN 'preseason' WHEN '002' THEN 'regular'
            WHEN '004' THEN 'playoffs' WHEN '005' THEN 'play-in' ELSE 'other' END AS season_type,
       g.game_date, g.tipoff_at, h.abbreviation AS home, a.abbreviation AS away, g.status,
       g.home_score, g.away_score
FROM public.games g
JOIN public.teams h ON h.team_id = g.home_team_id
JOIN public.teams a ON a.team_id = g.away_team_id;

-- The newest league injury report: every row of the latest sync.
CREATE OR REPLACE VIEW ask.injuries AS
SELECT t.abbreviation AS team, i.player_name AS player, i.status, i.reason, i.return_date,
       i.reported_at
FROM public.injuries i
JOIN public.teams t ON t.team_id = i.team_id
WHERE i.reported_at >= (SELECT max(reported_at) FROM public.injuries) - interval '10 minutes';

-- News reports read into player signals, while they still apply.
CREATE OR REPLACE VIEW ask.news AS
SELECT n.player_name AS player, t.abbreviation AS team, n.kind, n.summary,
       n.confidence::float AS confidence, n.published_at, n.expires_at
FROM public.news_signals n
LEFT JOIN public.teams t ON t.team_id = n.team_id
WHERE n.expires_at > now();

GRANT SELECT ON ALL TABLES IN SCHEMA ask TO nba_ask;

-- Every question asked, its answer and the queries behind it.
CREATE TABLE IF NOT EXISTS asks (
    id        SERIAL PRIMARY KEY,
    asked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    question  TEXT NOT NULL,
    answer    TEXT,
    status    TEXT NOT NULL,           -- running | done | error | refused | too_long
    cost_usd  NUMERIC NOT NULL DEFAULT 0,
    turns     INTEGER NOT NULL DEFAULT 0,
    queries   JSONB NOT NULL DEFAULT '[]'
);
-- 'page' for questions people ask; 'eval' for the accuracy check (`nba-bot ask-eval`),
-- which is kept out of the history and the daily limit.
ALTER TABLE asks ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'page';
