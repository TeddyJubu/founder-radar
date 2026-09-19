"""The Hermes plugin must register /run /search /today and rewrite Start."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

PLUGIN = (
    Path(__file__).resolve().parents[2]
    / "hermes"
    / "plugins"
    / "founder-radar-telegram"
    / "__init__.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("founder_radar_telegram_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_plugin_registers_slash_commands_and_hooks():
    mod = _load()
    seen = SimpleNamespace(hooks=[], commands=[])

    class Ctx:
        def register_hook(self, name, _fn):
            seen.hooks.append(name)

        def register_command(self, name, handler=None, **_kw):
            seen.commands.append(name)

    mod.register(Ctx())
    assert "pre_gateway_dispatch" in seen.hooks
    assert "pre_llm_call" in seen.hooks
    assert "pre_tool_call" in seen.hooks
    assert "transform_llm_output" in seen.hooks
    assert seen.commands == ["run", "search", "today"]


def test_plugin_rewrites_start_to_run():
    mod = _load()
    event = SimpleNamespace(text="Start")
    result = mod._on_pre_gateway_dispatch(event=event)
    assert result == {"action": "rewrite", "text": "/run"}
    assert mod._on_pre_gateway_dispatch(event=SimpleNamespace(text="/start")) is None
    assert mod._on_pre_gateway_dispatch(event=SimpleNamespace(text="/run")) is None
