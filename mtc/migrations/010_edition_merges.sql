-- Name rules for tracks and albums merged by editions.py ("Song - Remastered 2011" into "Song",
-- "Album (Deluxe Edition)" into "Album"): ingest applies them, so later scrobbles of the old title
-- land on the kept track or album, like artist_aliases do for artists. maintenance.merge_artists
-- repoints them; deleting the target drops its rules.
CREATE TABLE IF NOT EXISTS track_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id   INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    title_key   TEXT NOT NULL,           -- ingest.key() of the title that was merged away
    title       TEXT NOT NULL,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    scrobbles   INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    UNIQUE (artist_id, title_key)
);
CREATE INDEX IF NOT EXISTS idx_track_aliases_track ON track_aliases(track_id);
CREATE TABLE IF NOT EXISTS album_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id   INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    title_key   TEXT NOT NULL,
    title       TEXT NOT NULL,
    album_id    INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    scrobbles   INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    UNIQUE (artist_id, title_key)
);
CREATE INDEX IF NOT EXISTS idx_album_aliases_album ON album_aliases(album_id);

-- The `loved` view of 006, now also resolving a loved title that was merged into another track.
DROP VIEW IF EXISTS loved;
CREATE VIEW loved AS
SELECT l.artist, l.title, l.loved_at,
       COALESCE(rt.id, t.id, tat.id) AS track_id, COALESCE(rt.artist_id, t.artist_id, tat.artist_id) AS artist_id
FROM loved_tracks l
LEFT JOIN loved_title_rules r ON r.artist_key = l.artist_key AND r.title_key = l.title_key
LEFT JOIN tracks rt ON rt.id = r.track_id
LEFT JOIN artist_aliases x ON x.name_key = l.artist_key
LEFT JOIN artists a ON a.name_key = l.artist_key
LEFT JOIN tracks t ON t.artist_id = COALESCE(x.artist_id, a.id) AND t.title_key = l.title_key
LEFT JOIN track_aliases ta ON ta.artist_id = COALESCE(x.artist_id, a.id) AND ta.title_key = l.title_key
LEFT JOIN tracks tat ON tat.id = ta.track_id;
