"""Rebuild llm-cache keys after a prompt change — offline, no provider.

The cache key is `sha256(prompt_version | model | normalise_ws(text))`, so
bumping `PROMPT_VERSION` moves every key while the hand-authored payloads
stay valid. This script recomputes each fixture's key with the *real* code
path (prefilter → cache_key) and rewrites the recorded entries at their new
keys, exactly as the fixture builder's write step does — no provider call, no
network, no payload editing.

    .venv/bin/python tests/fixtures/rekey_llm_cache.py

It is the tool the `prefilter.extract_text` ponytail, the `ReplayLLM`
cache-miss message and `build_extraction_fixtures.py` all point to. It is
*only* for the case where the keys moved and the payloads are still valid.
For any other drift — a new article, an edited payload, an extractor swap
that changes which articles pass the prefilter — run the builder instead:

    .venv/bin/python tests/fixtures/build_extraction_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import build_extraction_fixtures as b  # noqa: E402 — same directory, sys.path[0]
from _golden_extractor import pin_builtin_extractor  # noqa: E402


def main() -> int:
    pin_builtin_extractor()
    before = {p.stem for p in b.LLM_CACHE.glob("*.json")}
    live: set[str] = set()
    for fx in b.FIXTURES:
        key = fx.write()  # article/expected rewrite to identical bytes; cache at the current key
        if key:
            live.add(key)
    for path in sorted(b.LLM_CACHE.glob("*.json")):
        if path.stem not in live:
            path.unlink()
            print(f"  rekeyed {path.stem[:12]}…")
    moved = len(before - live)
    print(f"{len(live)} live cache entries"
          + (f", {moved} key(s) moved" if moved else " — nothing to do"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
