# Finished client walkthrough

The editable source for the complete narrated owner handover is `scenes.json`.
It contains the original 13 product chapters plus 12 practical owner chapters.
The original 6 minute 38 second export remains preserved in `baseline-6m38/`.
`SCRIPT.md` contains the measured, chapter-timed transcript. This replaces
the September 29 outline with an actual narrated, captioned video and worked settings edits.

Final media and editable HyperFrames project are kept outside Git at:

`/Users/teddyburtonburger/.codex/visualizations/2026/09/29/01a0eef3-b0b5-7222-a689-370c90349c6b/client-walkthrough/`

This directory contains `founder-radar-client-walkthrough.mp4`, the measured
transcript, SRT captions, sentence timing, local narration WAV, screenshots,
settings/scoring evidence, HTML compositions and validation reports.
`OWNER-CARDS.md` supplies copyable owner prompts and reference commands.
The full written [owner handover](../../docs/owner-handover.md) provides the
detailed recovery steps. `owner-scenes.json` keeps the owner chapters separately
for review; `scenes.json` is the canonical full timeline input.

No upload or client delivery is implied by creating the export.

## Demonstration boundaries

- Screenshots come from the real Today, Kept and Dashboard code against a
  disposable local database. Approval records are explicitly simulated in
  that fixture. They are not evidence of a real Hermes or production QA run.
- The settings screen is an isolated local configuration replica, not Google
  Sheets. Its save action really changes the configuration passed to the
  product's deterministic scorer. Five states are checked and recorded.
- The original Outward `fund_ii.prior_total_max` value is taken from repository
  defaults. Tightening £20m to £1m is a fictional teaching edit, not a claim
  that Outward changed policy. Live Sheet values may differ from defaults.
- No production Sheet, database, credentials, decisions or services are used.
- Source disabled affects future collection; it does not erase stored companies.
- Score threshold changes do not invent evidence or alter the score arithmetic.
- Incomplete final checks withhold companies from publication.

## Owner-operation scope

The added chapters use the current owner runbook and the parent agent’s
verified VPS service/timer inventory. They explain the actual login addresses,
Hermes requests, saved decisions, daily/weekly checks, read-only diagnosis,
server services and account boundaries, trusted updates, conditional reboot,
backup/restore, failure recovery, Sheet sync, passwords and key rotation.
They do not claim account access was transferred or an off-server backup exists.
The current Hermes launcher must be verified before using its version-specific
maintenance/setup commands; none are invented in this recording.

## Rebuild

Run from the repository root using the project's Python environment:

```sh
python videos/founder-radar-client-walkthrough/capture_ui.py "$OUTPUT"
python videos/founder-radar-client-walkthrough/prepare_demo.py "$OUTPUT"
```

`capture_ui.py` refuses to overwrite an existing demo database. Supply a fresh
output directory. `prepare_demo.py` needs Playwright and validates the five
settings states. The narrative dates and examples are deliberately not live.

Run `narrate.py` with an existing Python voice environment containing
`kokoro_onnx`, numpy and soundfile, and the cached Kokoro model and voice paths
shown in that file. It uses British `bf_emma`, speed 1.08, without remote
providers. It measures every sentence, trims excess silence and builds SRT
caption chunks of up to eleven words. Chunk timing is proportional within
each measured sentence, rather than forced word alignment.

```sh
python videos/founder-radar-client-walkthrough/narrate.py "$OUTPUT"
python videos/founder-radar-client-walkthrough/compose.py "$OUTPUT"
cd "$OUTPUT"
npx hyperframes@0.8.93 check --snapshots --json > check.json
npx hyperframes@0.8.93 snapshot --frames 13 --describe false
npx hyperframes@0.8.93 render --fps 24 --quality delivery \
  --output founder-radar-client-walkthrough.mp4
```

The project also needs local GSAP 3.14.2 in `assets/gsap.min.js`, plus the
locally available Arial and Georgia font files. The renderer makes no remote
media requests. The project was authored through the HyperFrames
`general-video` route. Voice, typography and timing remain editable.

Validate the completed export with `ffprobe` for streams and duration and
`ffmpeg -v error -i VIDEO -f null -` for complete decoding. Inspect chapter
frames and listen to the voice before sharing. Publishing or client messaging
requires the user's explicit request.
