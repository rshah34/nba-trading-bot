-- The opening message each research run gave Claude (focus, mode, earlier ideas), so
-- a run's results can be read against exactly what it was asked.
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS prompt TEXT;
