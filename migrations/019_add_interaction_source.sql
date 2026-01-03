-- Migration 019: Add source tracking to user_interactions
-- Created: 2026-01-03
-- Description: Track where users come from (channel, direct, search, etc.)

-- Add source column to user_interactions
ALTER TABLE user_interactions
ADD COLUMN IF NOT EXISTS source VARCHAR(50);

-- Add index for analytics queries
CREATE INDEX IF NOT EXISTS idx_user_interactions_source
ON user_interactions(source)
WHERE source IS NOT NULL;

-- Comments
COMMENT ON COLUMN user_interactions.source IS 'User source: channel, direct, channel_article, etc.';
