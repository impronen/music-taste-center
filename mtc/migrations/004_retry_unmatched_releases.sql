-- The MusicBrainz matcher now retries a title without edition markers ("(Deluxe Edition)",
-- "- Remastered", "- EP"). Albums whose lookup found nothing and whose title has such a part
-- are looked up once more by the next release-date fetch.
UPDATE album_info SET mb_fetched_at = NULL
WHERE status = 'ok' AND mb_status = 'not_found' AND release_group_mbid IS NULL
  AND album_id IN (SELECT id FROM albums WHERE title LIKE '%(%' OR title LIKE '%[%' OR title LIKE '% - %');
