-- External metadata (last.fm, MusicBrainz), filled by `python -m mtc enrich`.
-- status: 'ok' | 'not_found' | 'error'. Rows double as the fetch cache: refetched after
-- config.METADATA_TTL_DAYS, or immediately for 'error'.

CREATE TABLE artist_info (
    artist_id    INTEGER PRIMARY KEY REFERENCES artists(id),
    status       TEXT NOT NULL,
    lastfm_name  TEXT,              -- name after last.fm autocorrect
    mbid         TEXT,
    url          TEXT,
    image_url    TEXT,
    listeners    INTEGER,
    playcount    INTEGER,           -- global last.fm plays
    bio_summary  TEXT,
    error        TEXT,
    fetched_at   INTEGER NOT NULL,
    tags_fetched_at INTEGER
);

CREATE TABLE album_info (
    album_id     INTEGER PRIMARY KEY REFERENCES albums(id),
    status       TEXT NOT NULL,
    lastfm_name  TEXT,
    mbid         TEXT,              -- MusicBrainz release id as given by last.fm
    url          TEXT,
    image_url    TEXT,
    listeners    INTEGER,
    playcount    INTEGER,
    n_tracks     INTEGER,
    error        TEXT,
    fetched_at   INTEGER NOT NULL,
    tags_fetched_at INTEGER,
    -- release date: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'
    release_date        TEXT,
    release_date_source TEXT,       -- 'musicbrainz' | 'tag'
    release_group_mbid  TEXT,
    release_type        TEXT,       -- MusicBrainz primary type: Album, EP, Single...
    mb_status           TEXT,       -- 'ok' | 'not_found' | 'error'
    mb_fetched_at       INTEGER
);

CREATE TABLE tags (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,     -- lowercased, whitespace-collapsed
    kind  TEXT NOT NULL             -- 'genre' | 'year' | 'decade' | 'place' | 'other' (personal/noise)
);

-- weight: last.fm's tag count, 0-100 relative to the item's top tag.
CREATE TABLE artist_tags (
    artist_id  INTEGER NOT NULL REFERENCES artists(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    weight     INTEGER NOT NULL,
    PRIMARY KEY (artist_id, tag_id)
);
CREATE INDEX artist_tags_tag ON artist_tags(tag_id);

CREATE TABLE album_tags (
    album_id  INTEGER NOT NULL REFERENCES albums(id),
    tag_id    INTEGER NOT NULL REFERENCES tags(id),
    weight    INTEGER NOT NULL,
    PRIMARY KEY (album_id, tag_id)
);
CREATE INDEX album_tags_tag ON album_tags(tag_id);
