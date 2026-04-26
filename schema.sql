-- Turso schema for the news agent.
-- Apply with: turso db shell newsagentdb < schema.sql

CREATE TABLE IF NOT EXISTS seen_urls (
  url_hash TEXT PRIMARY KEY,
  first_seen TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_urls_first_seen ON seen_urls(first_seen);

CREATE TABLE IF NOT EXISTS votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  story_id TEXT NOT NULL,
  story_title TEXT,
  story_source TEXT,
  story_topic TEXT,
  vote INTEGER NOT NULL,  -- 1 = up, -1 = down
  voted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_votes_voted_at ON votes(voted_at);

CREATE TABLE IF NOT EXISTS sent_stories (
  story_id TEXT PRIMARY KEY,
  title TEXT,
  source TEXT,
  topic TEXT,
  url TEXT,
  sent_at TEXT NOT NULL
);
