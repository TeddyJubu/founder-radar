"""Control room prompt edits (`radar/admin/prompts.py`).

The properties that matter: defaults keep their exact version (recorded LLM
fixtures must keep replaying), an edit changes the version everywhere it is
keyed (extraction cache, Today QA snapshot hash), and a broken database
falls back to the default instead of stopping a run.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from radar.admin import prompts
from radar.extract import ExtractContext, cache_key, extract_html
from radar.extract.llm import PROMPT_VERSION as EXTRACT_VERSION
from radar.extract.llm import SYSTEM_PROMPT
from radar.qa.today import PROMPT_VERSION as QA_VERSION
from radar.qa.today import TodayCard, build_user_prompt, subagent_prompt
from radar.store.db import Db

ARTICLE = Path(__file__).parents[1] / "fixtures" / "articles" / "clean_funding_announcement_1.html"


@pytest.fixture
def db(tmp_path):
    handle = Db(tmp_path / "r.db")
    handle.migrate()
    yield handle
    handle.close()


def test_defaults_keep_their_shipped_versions(db):
    assert prompts.effective(db, "extract.system").version == EXTRACT_VERSION
    assert prompts.effective(db, "extract.system").text == SYSTEM_PROMPT
    assert prompts.effective(db, "today_qa.brief").version == QA_VERSION
    assert prompts.effective(db, "today_qa.brief").text == subagent_prompt()
    assert all(p["source"] == "default" for p in prompts.list_view(db))


def test_missing_table_or_no_db_means_default():
    bare = sqlite3.connect(":memory:")
    assert prompts.effective(bare, "today_qa.brief").source == "default"
    assert prompts.effective(None, "extract.system").version == EXTRACT_VERSION


def test_unknown_key_is_refused(db):
    with pytest.raises(prompts.UnknownPrompt):
        prompts.effective(db, "score.weights")


def test_override_round_trip_keeps_history(db):
    first = prompts.save_override(db, "today_qa.brief", "Be strict.", note="v1")
    assert first.source == "override"
    assert first.version.startswith(QA_VERSION + "+")
    second = prompts.save_override(db, "today_qa.brief", "Be stricter.")
    assert second.version != first.version

    history = prompts.history(db, "today_qa.brief")
    assert [h["active"] for h in history] == [True, False]
    assert history[1]["note"] == "v1"

    back = prompts.reset(db, "today_qa.brief", note="undo")
    assert back.source == "default" and back.version == QA_VERSION
    assert len(prompts.history(db, "today_qa.brief")) == 2   # kept, inactive
    kinds = [r["kind"] for r in db.query("SELECT kind FROM admin_change")]
    assert kinds == ["prompt", "prompt", "prompt"]


def test_saving_the_default_text_is_a_reset(db):
    prompts.save_override(db, "extract.system", "Return JSON.")
    eff = prompts.save_override(db, "extract.system", SYSTEM_PROMPT + "\n")
    assert eff.source == "default"
    assert eff.version == EXTRACT_VERSION


def test_empty_text_is_refused(db):
    with pytest.raises(ValueError):
        prompts.save_override(db, "publish.brief", "   ")


def test_override_reaches_the_extraction_call_and_cache_key(db):
    """The edited prompt is the one sent, and it never shares a cache entry."""
    eff = prompts.save_override(db, "extract.system", "Return one JSON record.")
    seen = {}

    class Recorder:
        def complete(self, *, key, system, user):
            seen.update(key=key, system=system)
            raise ConnectionError("offline")   # → heuristic fallback, run goes on

    ctx = ExtractContext(llm=Recorder(), db=db, system_prompt=eff.text,
                         prompt_version=eff.version)
    extract_html(url="https://example.org/a", title="Seed round",
                 html=ARTICLE.read_text(encoding="utf-8"), ctx=ctx)
    assert seen["system"] == "Return one JSON record."
    from radar.extract.prefilter import prefilter

    text = prefilter("https://example.org/a", "Seed round",
                     ARTICLE.read_text(encoding="utf-8")).text
    assert seen["key"] == cache_key(text, ctx.model_id, prompt_version=eff.version)
    assert seen["key"] != cache_key(text, ctx.model_id)


def test_default_context_is_unchanged():
    ctx = ExtractContext()
    assert ctx.system_prompt == SYSTEM_PROMPT
    assert ctx.prompt_version == EXTRACT_VERSION


def test_today_brief_and_version_follow_the_card():
    card = TodayCard(company_id="c1", name="Acme")
    edited = replace(card, brief="Be strict.", prompt_version="x+1")
    assert build_user_prompt(edited).startswith("Be strict.")
    assert build_user_prompt(card).startswith(subagent_prompt())
    # Same facts, new prompt → a new snapshot: the old approval does not carry over.
    assert edited.blob() == card.blob()
    assert edited.snapshot_hash() != card.snapshot_hash()


def test_preview_never_calls_out(db):
    prompts.save_override(db, "today_qa.brief", "Be strict.")
    for key in prompts.SPECS:
        out = prompts.preview(db, key)
        assert out["preview"] and out["sample"]
    assert prompts.preview(db, "today_qa.brief")["preview"].startswith("Be strict.")
