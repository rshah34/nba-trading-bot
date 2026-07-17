-- Extend team_game_stats with the raw box-score counts the Four Factors need.
-- Until now only percentages + totals were stored, which can't express eFG%,
-- TOV%, OREB%, FT rate, or pace. These come straight from the V3 team box
-- (same fetch as the player box, so no extra API calls). Both teams' rows are
-- already ingested per game, so a team's DEFENSIVE factors are just its
-- opponent's offensive row joined on game_id.
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS fgm    INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS fga    INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS fg3m   INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS fg3a   INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS ftm    INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS fta    INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS oreb   INTEGER;  -- offensive rebounds (OREB%)
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS dreb   INTEGER;  -- defensive rebounds (opp OREB% / your DREB%)
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS steals INTEGER;
ALTER TABLE team_game_stats ADD COLUMN IF NOT EXISTS blocks INTEGER;
