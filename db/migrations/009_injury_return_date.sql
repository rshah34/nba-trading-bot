-- The date the injury feed expects a player back. A player listed day-to-day who has
-- been ruled out of tonight's game carries a return date after it; that date, not the
-- status word, is what says he is out.
ALTER TABLE injuries ADD COLUMN IF NOT EXISTS return_date DATE;
