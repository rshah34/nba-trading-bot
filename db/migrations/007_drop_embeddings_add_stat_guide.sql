-- News embeddings are gone: the game model now reads consolidated news signals
-- (news_signals) instead of semantically retrieved chunks, so the chunk table and
-- the pgvector extension are no longer used.
DROP TABLE IF EXISTS news_chunks;
DROP EXTENSION IF EXISTS vector;

-- Single-stat guidance stored with each prop pick: per stat, the projection, the
-- lines at which the model would take the over / the under, and (after the game)
-- what the player actually did.
ALTER TABLE prop_bets ADD COLUMN IF NOT EXISTS stat_guide JSONB;
