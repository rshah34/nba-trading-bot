-- Per-player, per-game box score stats. Feeds point-in-time player form so the
-- model can reason about WHO drives each team (dynamically, from recent
-- production) and how injuries to specific players change a matchup.
-- Player name is denormalized (the box score provides it), so no separate roster
-- fetch is required.

CREATE TABLE player_game_stats (
    game_id      TEXT NOT NULL REFERENCES games(game_id),
    player_id    INTEGER NOT NULL,           -- nba_api player id
    player_name  TEXT NOT NULL,
    team_id      INTEGER NOT NULL REFERENCES teams(team_id),
    minutes      NUMERIC,                    -- parsed from "MM:SS"; NULL/0 = DNP
    points       INTEGER,
    rebounds     INTEGER,
    assists      INTEGER,
    steals       INTEGER,
    blocks       INTEGER,
    turnovers    INTEGER,
    fgm          INTEGER,
    fga          INTEGER,
    fg3m         INTEGER,
    fg3a         INTEGER,
    ftm          INTEGER,
    fta          INTEGER,
    plus_minus   NUMERIC,
    PRIMARY KEY (game_id, player_id)
);

-- Recent-form lookups filter by player + date, joining to games for game_date.
CREATE INDEX idx_pgs_player ON player_game_stats(player_id);
CREATE INDEX idx_pgs_team ON player_game_stats(team_id);
