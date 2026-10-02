-- Core entities. name_key is the normalized (casefolded, whitespace-collapsed) identity;
-- the display name is whatever spelling was seen first.
CREATE TABLE artists (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    name_key  TEXT NOT NULL UNIQUE,
    mbid      TEXT
);

CREATE TABLE albums (
    id         INTEGER PRIMARY KEY,
    artist_id  INTEGER NOT NULL REFERENCES artists(id),
    title      TEXT NOT NULL,
    title_key  TEXT NOT NULL,
    mbid       TEXT,
    UNIQUE (artist_id, title_key)
);

CREATE TABLE tracks (
    id         INTEGER PRIMARY KEY,
    artist_id  INTEGER NOT NULL REFERENCES artists(id),
    title      TEXT NOT NULL,
    title_key  TEXT NOT NULL,
    mbid       TEXT,
    UNIQUE (artist_id, title_key)
);

CREATE TABLE imports (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,          -- 'csv', later 'lastfm-api'
    label         TEXT,                   -- file name or user name
    encoding      TEXT,
    started_at    INTEGER NOT NULL,
    rows_read     INTEGER NOT NULL DEFAULT 0,
    rows_added    INTEGER NOT NULL DEFAULT 0,
    rows_skipped  INTEGER NOT NULL DEFAULT 0,  -- unparseable (e.g. "now playing" without a date)
    min_ts        INTEGER,
    max_ts        INTEGER
);

-- One row per listen. ts is unix UTC; l* columns are local time (config.TZ), filled at insert
-- and recomputed by `python -m mtc rebuild` if the zone changes.
CREATE TABLE scrobbles (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    artist_id   INTEGER NOT NULL REFERENCES artists(id),
    track_id    INTEGER NOT NULL REFERENCES tracks(id),
    album_id    INTEGER REFERENCES albums(id),
    import_id   INTEGER REFERENCES imports(id),
    lday        TEXT NOT NULL,     -- YYYY-MM-DD
    lhour       INTEGER NOT NULL,  -- 0-23
    lwday       INTEGER NOT NULL,  -- 0 = Monday
    session_id  INTEGER,           -- derived
    UNIQUE (ts, artist_id, track_id)
);
CREATE INDEX scrobbles_artist_ts ON scrobbles(artist_id, ts);
CREATE INDEX scrobbles_track ON scrobbles(track_id);
CREATE INDEX scrobbles_album ON scrobbles(album_id);
CREATE INDEX scrobbles_lday ON scrobbles(lday);
CREATE INDEX scrobbles_session ON scrobbles(session_id);

-- Derived tables: fully rebuilt by mtc.derive after every import.
CREATE TABLE artist_stats (
    artist_id      INTEGER PRIMARY KEY REFERENCES artists(id),
    plays          INTEGER NOT NULL,
    first_ts       INTEGER NOT NULL,
    last_ts        INTEGER NOT NULL,
    first_lday     TEXT NOT NULL,
    n_tracks       INTEGER NOT NULL,
    n_days         INTEGER NOT NULL,
    n_years        INTEGER NOT NULL,
    n_sessions     INTEGER NOT NULL,
    prehistory     INTEGER NOT NULL,  -- 1 = already in rotation when tracking began
    gateway_id     INTEGER REFERENCES artists(id),  -- artist played right before the first listen
    first_track_id INTEGER REFERENCES tracks(id)
);
CREATE INDEX artist_stats_plays ON artist_stats(plays DESC);
CREATE INDEX artist_stats_gateway ON artist_stats(gateway_id);

-- Co-listening links: a < b. score is the Ochiai coefficient over sessions.
CREATE TABLE artist_links (
    a       INTEGER NOT NULL REFERENCES artists(id),
    b       INTEGER NOT NULL REFERENCES artists(id),
    shared  INTEGER NOT NULL,
    score   REAL NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE INDEX artist_links_b ON artist_links(b);

CREATE TABLE meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
