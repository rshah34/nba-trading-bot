-- The roster list's names, so news about a player who hasn't played yet (a rookie,
-- a new signing) can still be matched to him.
ALTER TABLE rosters ADD COLUMN IF NOT EXISTS player_name TEXT;
