-- Tracks loved on last.fm (user.getLovedTracks), replaced as a whole by each updater run.
-- Kept by name rather than by track id, so a loved track that hasn't been scrobbled yet, a later
-- import and an artist merge (whose name rule lands in artist_aliases) all resolve when read,
-- through the `loved` view. No foreign keys, so merges need no handling here.
CREATE TABLE IF NOT EXISTS loved_tracks (
    artist_key  TEXT NOT NULL,            -- ingest.key() of the artist name
    title_key   TEXT NOT NULL,            -- ingest.key() of the track title
    artist      TEXT NOT NULL,            -- as last.fm sent them
    title       TEXT NOT NULL,
    loved_at    INTEGER NOT NULL,         -- unix seconds
    PRIMARY KEY (artist_key, title_key)
);

-- One row per loved track, with the track it resolves to (NULL when it isn't in the library).
-- A name rule wins over an artist of the same name, as in ingest.
CREATE VIEW IF NOT EXISTS loved AS
SELECT l.artist, l.title, l.loved_at, t.id AS track_id, t.artist_id
FROM loved_tracks l
LEFT JOIN artist_aliases x ON x.name_key = l.artist_key
LEFT JOIN artists a ON a.name_key = l.artist_key
LEFT JOIN tracks t ON t.artist_id = COALESCE(x.artist_id, a.id) AND t.title_key = l.title_key;
