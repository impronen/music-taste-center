-- Upcoming releases (releases.py): a cache of what ListenBrainz and Wikipedia list for the next
-- few weeks, replaced per source by each refresh. Kept by name and MusicBrainz id rather than by
-- artist id, so imports, aliases and merges all apply when it's read. No foreign keys, so merges
-- need no handling here.
CREATE TABLE IF NOT EXISTS upcoming_releases (
    source        TEXT NOT NULL,    -- 'listenbrainz' or 'wikipedia'
    release_date  TEXT NOT NULL,    -- YYYY-MM-DD
    artist        TEXT NOT NULL,    -- the artist credit as the source writes it
    title         TEXT NOT NULL,
    release_type  TEXT,             -- Album, EP or Single (ListenBrainz only)
    artist_mbids  TEXT,             -- MusicBrainz artist ids, space-separated (ListenBrainz only)
    artist_parts  TEXT,             -- the linked names of a credit, one per line (Wikipedia only)
    release_group_mbid TEXT,
    cover_url     TEXT,
    source_url    TEXT,             -- the news article Wikipedia cites
    genre         TEXT,
    label         TEXT
);
CREATE INDEX IF NOT EXISTS upcoming_releases_date ON upcoming_releases(release_date);
