-- Picks are now priced at the bettor's own books, and carry how far the price (and,
-- for props, the line) can move before the bet stops being worth making.
ALTER TABLE bets ADD COLUMN IF NOT EXISTS sportsbook TEXT;
ALTER TABLE bets ADD COLUMN IF NOT EXISTS playable_odds NUMERIC;
ALTER TABLE bets ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE prop_bets ADD COLUMN IF NOT EXISTS playable_odds NUMERIC;
ALTER TABLE prop_bets ADD COLUMN IF NOT EXISTS playable_line NUMERIC;

-- Late-scratch guard: the update texted when an injury change after the pick
-- undercut it. NULL = the pick stands.
ALTER TABLE bets ADD COLUMN IF NOT EXISTS flag TEXT;
ALTER TABLE prop_bets ADD COLUMN IF NOT EXISTS flag TEXT;
