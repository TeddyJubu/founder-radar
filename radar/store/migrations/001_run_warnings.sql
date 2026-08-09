-- 001: `run.warnings` — non-fatal observations a run wants recorded (FR-9.2).
-- The daily run notes degraded sources here so the heartbeat and a human
-- reading `status` can see that a source is being blocked without the run
-- itself being marked `failed` (a block is not an outage).
ALTER TABLE run ADD COLUMN warnings TEXT;
