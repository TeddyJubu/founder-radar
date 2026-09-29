-- Compatibility for databases created by the unreleased snapshot prototype.
ALTER TABLE score_snapshot ADD COLUMN approved_snapshot_hash TEXT;
