-- Loved-title rules: link a loved track whose title is spelled differently from the library's
-- ("Song (Remastered 2011)" vs "Song") to a chosen track. Keyed by the loved entry's own names, so
-- the updater replacing loved_tracks wholesale leaves them in place. The track id is moved by
-- maintenance.merge_artists when a merge combines tracks; deleting a track drops its rules.
CREATE TABLE IF NOT EXISTS loved_title_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reused: a stale Undo can't hit a newer rule
    artist_key  TEXT NOT NULL,            -- loved_tracks.artist_key
    title_key   TEXT NOT NULL,            -- loved_tracks.title_key
    artist      TEXT NOT NULL,            -- the loved entry's names, for display once it's unloved
    title       TEXT NOT NULL,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    UNIQUE (artist_key, title_key)
);
CREATE INDEX IF NOT EXISTS idx_loved_title_rules_track ON loved_title_rules(track_id);

-- The `loved` view of 005, now consulting the rules first. Same columns.
DROP VIEW IF EXISTS loved;
CREATE VIEW loved AS
SELECT l.artist, l.title, l.loved_at,
       COALESCE(rt.id, t.id) AS track_id, COALESCE(rt.artist_id, t.artist_id) AS artist_id
FROM loved_tracks l
LEFT JOIN loved_title_rules r ON r.artist_key = l.artist_key AND r.title_key = l.title_key
LEFT JOIN tracks rt ON rt.id = r.track_id
LEFT JOIN artist_aliases x ON x.name_key = l.artist_key
LEFT JOIN artists a ON a.name_key = l.artist_key
LEFT JOIN tracks t ON t.artist_id = COALESCE(x.artist_id, a.id) AND t.title_key = l.title_key;
