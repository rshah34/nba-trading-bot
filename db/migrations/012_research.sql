-- The research agent's record: each run, and every fix it took to the held-back
-- season. The findings are also the ledger that raises the bar for the next one:
-- every confirmation ever run against a season counts against it.
CREATE TABLE IF NOT EXISTS research_runs (
    id          SERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    model       TEXT NOT NULL,
    focus       TEXT,
    status      TEXT NOT NULL,          -- running | done | out_of_budget | out_of_turns | refused | error
    cost_usd    NUMERIC NOT NULL DEFAULT 0,
    turns       INTEGER NOT NULL DEFAULT 0,
    summary     TEXT,
    explored    JSONB                   -- ideas measured but not confirmed: [{idea, outcome}]
);

CREATE TABLE IF NOT EXISTS research_findings (
    id             SERIAL PRIMARY KEY,
    run_id         INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    hypothesis     TEXT NOT NULL,
    rationale      TEXT,
    slice          JSONB NOT NULL,
    stat           TEXT NOT NULL,
    multiplier     NUMERIC NOT NULL,
    explore_season TEXT NOT NULL,
    confirm_season TEXT NOT NULL,
    verdict        TEXT NOT NULL,       -- pass | fail
    reasons        JSONB NOT NULL,
    explore        JSONB NOT NULL,
    confirm        JSONB NOT NULL,
    threshold_z    NUMERIC NOT NULL,
    applied_at     TIMESTAMPTZ          -- set when the fix goes into the model
);
