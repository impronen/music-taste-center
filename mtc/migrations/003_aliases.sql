-- Name rules: a normalized artist name that always means another artist. Created when two
-- artists are merged; ingest applies them, so later imports of a misspelling land on the
-- right artist.
CREATE TABLE artist_aliases (
    id          INTEGER PRIMARY KEY,
    name_key    TEXT NOT NULL UNIQUE,     -- ingest.key() of the alternative spelling
    name        TEXT NOT NULL,            -- that spelling as it was seen
    artist_id   INTEGER NOT NULL REFERENCES artists(id),
    scrobbles   INTEGER NOT NULL DEFAULT 0,  -- moved when the rule was created
    created_at  INTEGER NOT NULL
);
CREATE INDEX artist_aliases_artist ON artist_aliases(artist_id);

-- Duplicate suggestions the user said are different artists.
CREATE TABLE dismissed_duplicates (
    group_key   TEXT PRIMARY KEY,         -- sorted name_keys of the group, newline-separated
    created_at  INTEGER NOT NULL
);
