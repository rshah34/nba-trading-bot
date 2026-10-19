-- Runs can be started from the dashboard: the mode (explore only, or explore and
-- confirm), the budget asked for, the step-by-step log the page shows while a run
-- works, and an explore-only run's candidate fixes for a later run to confirm.
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'confirm';
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS budget_usd NUMERIC;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS log JSONB NOT NULL DEFAULT '[]';
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS candidates JSONB;
