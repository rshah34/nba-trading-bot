-- The league's roster list: who is on each team today. Box scores only say where a
-- player last played, which is wrong for anyone traded, signed or waived since.
CREATE TABLE IF NOT EXISTS rosters (
    player_id INTEGER PRIMARY KEY,
    team_id   INTEGER NOT NULL REFERENCES teams(team_id)
);
