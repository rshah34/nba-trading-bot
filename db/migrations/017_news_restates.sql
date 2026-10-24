-- Whether a news item only restates something already reported ("remains out with
-- the Achilles tear he suffered in preseason") rather than reporting a development.
-- A restatement doesn't make an old story new again.
ALTER TABLE news_signals ADD COLUMN IF NOT EXISTS restates BOOLEAN NOT NULL DEFAULT FALSE;
