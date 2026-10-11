-- Artists the user hid from Rediscover and Forgotten favourites ("forgotten for a reason", or music
-- they no longer have). Only those two lists look at it; plays, stats and every other page are
-- untouched. maintenance.merge_artists repoints a hidden source to the target.
CREATE TABLE IF NOT EXISTS hidden_artists (
    artist_id   INTEGER PRIMARY KEY REFERENCES artists(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL
);
