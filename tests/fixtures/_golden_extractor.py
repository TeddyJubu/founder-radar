"""The extractor the golden llm-cache keys are recorded against.

`prefilter.extract_text` prefers trafilatura when installed (02-architecture
§9), and the two extractors do not produce byte-identical text — so the llm
cache key, which is a hash of that text, would otherwise depend on which
extras happen to be installed on the machine running the suite. CI installs
only `.[dev]` (no trafilatura); a dev box with the `extract` extra would
compute different keys and fail every golden test while CI stays green.

The committed `tests/fixtures/llm_cache` entries are recorded against the
dependency-free builtin extractor, so that is what this pin forces —
consistently, in the fixture builder, in `rekey_llm_cache.py`, and in the
pytest session (`tests/conftest.py`). Production code is never affected:
nothing here touches `radar.extract` beyond rerouting one function in one
module, and `extract_text` itself is untouched.
"""

from __future__ import annotations

import importlib

# Resolve via sys.modules rather than attribute access: `radar.extract`
# re-exports the `prefilter` *function* into its own namespace, shadowing the
# submodule attribute, so `import radar.extract.prefilter as X` would hand
# back the function, not the module.
_prefilter = importlib.import_module("radar.extract.prefilter")


def pin_builtin_extractor() -> None:
    """Route `prefilter.extract_text` to the builtin extractor, in place.

    Must run before any fixture key is computed or any golden test executes.
    Idempotent: the builtin extractor is the same code `extract_text` falls
    back to when trafilatura is absent, so pinning never changes behaviour —
    it only removes the environment from the equation.
    """
    _prefilter.extract_text = _prefilter._builtin_extract
