"""Telegram search/today intent must be unambiguous — Start is a scan, /start is not."""

from __future__ import annotations

import pytest

from radar.notify.search_intent import classify, fund_from, slash_rewrite


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Start", "search"),
        ("start", "search"),
        ("search now", "search"),
        ("Search now.", "search"),
        ("please search", "search"),
        ("run a scan", "search"),
        ("/run", "search"),
        ("/run northstar", "search"),
        ("/search", "search"),
        ("/search dsw", "search"),
        ("what's new", "today"),
        ("whats new", "today"),
        ("/today", "today"),
        ("today's list", "today"),
    ],
)
def test_classify_search_and_today(text, kind):
    assert classify(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "/start@founder_radar_bot",
        "reject Acme Ltd",
        "not for me",
        "worth contacting",
        "keep this",
        "/why Acme",
        "/fund northstar",
        "why is Today empty",
        "hello",
        "",
        "Starting a company in Leeds next year",
    ],
)
def test_classify_ignores_non_search(text):
    assert classify(text) is None


def test_slash_start_is_not_rewritten_but_bare_start_is():
    assert slash_rewrite("/start") is None
    assert slash_rewrite("Start") == "/run"
    assert slash_rewrite("search now") == "/run"
    assert slash_rewrite("search now northstar") == "/run northstar"
    assert slash_rewrite("what's new") == "/today"
    assert slash_rewrite("/run") is None
    assert slash_rewrite("/today") is None


def test_fund_from_message():
    assert fund_from("search now for Outward") == "outward"
    assert fund_from("hello") is None
