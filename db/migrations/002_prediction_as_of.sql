-- Add an explicit "as-of" timestamp to predictions: the moment the prediction
-- was made, and the cutoff that all context (injuries, news, odds) is filtered to.
-- This makes late-breaking-news handling honest and lets us store more than one
-- prediction per game (e.g. an early "preview" and a "final" near tip-off).

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS as_of TIMESTAMPTZ;
UPDATE predictions SET as_of = created_at WHERE as_of IS NULL;
ALTER TABLE predictions ALTER COLUMN as_of SET NOT NULL;

-- Replace UNIQUE(game_id, model_version) with one that includes as_of.
ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_game_id_model_version_key;
ALTER TABLE predictions ADD CONSTRAINT predictions_game_model_asof_key
    UNIQUE (game_id, model_version, as_of);
