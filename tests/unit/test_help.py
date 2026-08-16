"""Client-issues plan §3.7 (G19-G21, F18) — the /help handover page.

The prototype server serves `prototype/help.html` byte-for-byte
(`server.py`: `self._send(200, (HERE / "help.html").read_bytes(), ...)`), so a
content test on the file is a content test on the page.

Aryan asked to be walked through exactly these things: where the data comes
from and how the three surfaces connect, where his shortlist lives, how to
update funds, edit criteria, add/remove sources — and (F18) how to change the
login himself. If any of those sections silently disappears from the page,
this test names it.
"""

from __future__ import annotations

from pathlib import Path

HELP = Path(__file__).resolve().parents[2] / "prototype" / "help.html"

#: The sections he asked for, in the words the page actually uses.
REQUIRED_SECTIONS = [
    "How the parts connect",
    "Data flow",
    "Shortlist vs Kept",
    "Where Kept is stored",
    "Update funds later",
    "Edit fund criteria",
    "Add or remove sourcing channels",
    "Change the login password",
]


def test_help_covers_the_handover_sections():
    text = HELP.read_text(encoding="utf-8").lower()
    missing = [s for s in REQUIRED_SECTIONS if s.lower() not in text]
    assert not missing, f"/help no longer explains: {missing}"
