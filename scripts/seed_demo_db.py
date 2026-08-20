#!/usr/bin/env python3
"""CLI for the demo-DB recipe in ``tests.demo_db.build``.

Codifies prototype/TESTING.md §0.2. Usage:

    python scripts/seed_demo_db.py [path]      # defaults to /tmp/demo.db
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.demo_db import build


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/demo.db"
    count = build(path)
    print(f"seeded {count} companies into {path}", flush=True)


if __name__ == "__main__":
    main()
