-- Similar artists (last.fm artist.getSimilar) of the most played artists, and last.fm tags of
-- artists with upcoming releases, for the "new to you" releases on the Upcoming page (releases.py).
-- Caches kept by name key (ingest.key), refreshed after 30 days. No foreign keys, so merges need
-- no handling: a seed that merged away is no longer looked up.
CREATE TABLE IF NOT EXISTS similar_seeds (
    seed_key    TEXT PRIMARY KEY,        -- name key of the library artist whose similar artists were fetched
    fetched_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS similar_artists (
    seed_key    TEXT NOT NULL,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL,
    mbid        TEXT,
    match       REAL NOT NULL,           -- last.fm's similarity, 0-1
    PRIMARY KEY (seed_key, name_key)
);
CREATE INDEX IF NOT EXISTS similar_artists_name ON similar_artists(name_key);
CREATE TABLE IF NOT EXISTS release_artist_tags (
    name_key    TEXT PRIMARY KEY,        -- name key of the credit as the release source writes it
    tags        TEXT NOT NULL,           -- JSON [[tag, weight 0-100], ...], [] when last.fm doesn't know it
    fetched_at  INTEGER NOT NULL
);
