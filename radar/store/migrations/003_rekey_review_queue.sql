-- 003: rekey legacy review-queue entries to the deterministic pair key.
--
-- `enqueue_review` used to store each queued pair under a random key
-- ("review:<ulid>") and deduplicate by scanning the whole queue. The
-- deterministic key ("review:<winner>|<loser>", the two ids sorted) made that
-- scan unnecessary — but an entry written before the change lives under a key
-- the O(1) idempotency check never looks for, so a pair re-found on a later
-- run would be queued a second time. This migration rewrites the survivors
-- and drops the duplicates, in place.
--
-- Two statements, both scoped to legacy keys (the `review:` prefix with no
-- `|` separator — ULIDs never contain one, so the discriminator is exact):
--
-- 1. A pair already re-queued under the new scheme has TWO entries. The
--    deterministic one is the newer decision (enqueue_review writes the fresh
--    MatchResult and `created_at`), so the random-keyed one is dropped.
-- 2. The survivors move to their deterministic key. `json_extract` reads the
--    pair straight out of the payload; MIN/MAX over the two ids sorts them
--    exactly like `_pair_key` does (ULIDs are ASCII, so BINARY collation
--    matches Python's `sorted()`). `json_valid` comes first because
--    `json_extract` *raises* on malformed JSON rather than returning NULL —
--    entries that are not a JSON object with both ids are left alone rather
--    than corrupted or crashed on.

DELETE FROM _meta
WHERE key LIKE 'review:%' AND key NOT LIKE '%|%'
  AND json_valid(value)
  AND json_extract(value, '$.winner_id') IS NOT NULL
  AND json_extract(value, '$.loser_id') IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM _meta target
    WHERE target.key = 'review:'
        || MIN(json_extract(_meta.value, '$.winner_id'),
               json_extract(_meta.value, '$.loser_id'))
        || '|'
        || MAX(json_extract(_meta.value, '$.winner_id'),
               json_extract(_meta.value, '$.loser_id'))
  );

UPDATE _meta
SET key = 'review:'
       || MIN(json_extract(value, '$.winner_id'),
              json_extract(value, '$.loser_id'))
       || '|'
       || MAX(json_extract(value, '$.winner_id'),
              json_extract(value, '$.loser_id'))
WHERE key LIKE 'review:%' AND key NOT LIKE '%|%'
  AND json_valid(value)
  AND json_extract(value, '$.winner_id') IS NOT NULL
  AND json_extract(value, '$.loser_id') IS NOT NULL;
