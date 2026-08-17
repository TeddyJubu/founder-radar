"""The Today screen, driven in a real browser — prototype/TESTING.md.

Test names carry the plan's IDs so a failure maps straight back to the
document. Everything is addressed by `data-testid`: CSS classes are for
styling and may be renamed freely, the testids are the contract (§1).

**Suite D is the one that matters.** This interface has one architectural
rule — it computes nothing — so every number on screen must equal the value in
SQLite exactly. A visual defect is a defect; a D failure means the scoring has
two sources of truth and stops being defensible.

    pytest -m browser
"""

from __future__ import annotations

import json
import re
import urllib.request

import pytest

from tests.browser.conftest import tid

pytestmark = pytest.mark.browser

PLACEHOLDERS = ("undefined", "null", "NaN", "[object Object]", "(None)")

VIEWPORTS = [
    ("desktop", 1440, 900),
    ("laptop", 1280, 800),
    ("tablet", 834, 1112),
    ("phone", 393, 852),
    ("small-phone", 375, 667),
]


def _post(server: str, payload: dict):
    req = urllib.request.Request(
        server + "/api/verdict", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# ═══════════════════════════════════════════════════════════ A — API contract


def test_a1_root_serves_html(server):
    with urllib.request.urlopen(server + "/", timeout=5) as r:
        assert r.status == 200
        assert "text/html" in r.headers["Content-Type"]


def test_a3_top_level_keys(api):
    assert {"date", "companies", "totals", "run"} <= set(api)


def test_a3b_eligibility_diagnostics_are_reconcilable_aggregates(api):
    diagnostics = api["eligibility_diagnostics"]
    assert {"scored_companies", "reviewable_companies", "eligible_before_review",
            "display_limit", "shown", "excluded", "reasons"} <= set(diagnostics)
    assert diagnostics["shown"] == len(api["companies"])
    assert diagnostics["excluded"] == (
        diagnostics["scored_companies"] - diagnostics["shown"]
    )
    assert sum(row["count"] for row in diagnostics["reasons"]) == diagnostics["excluded"]
    for row in diagnostics["reasons"]:
        assert set(row) == {"key", "label", "count"}
        assert row["key"] and row["label"] and isinstance(row["count"], int)


def test_a4_company_keys(api):
    required = {"company_id", "name", "fit", "edge", "coverage", "priority",
                "explanation", "flags", "signals", "also_fits", "fund_scores",
                "vehicle", "tier"}
    assert api["companies"], "no companies to review"
    for c in api["companies"]:
        assert required <= set(c), f"{c.get('name')} missing {required - set(c)}"
        assert len(c["fund_scores"]) == 4
        assert {score["fund_key"] for score in c["fund_scores"]} == {
            "outward", "dsw", "northstar", "anticus"
        }


def test_a5_rejects_never_reach_today(api):
    assert all(c["tier"] != "reject" for c in api["companies"])


def test_a6_ordered_by_priority(api):
    p = [c["priority"] for c in api["companies"]]
    assert p == sorted(p, reverse=True)


def test_a7_ties_break_on_coverage(api):
    """Among companies the scoring cannot separate, review the one we know
    something about first."""
    cs = api["companies"]
    for a, b in zip(cs, cs[1:]):
        if a["priority"] == b["priority"]:
            assert a["coverage"] >= b["coverage"], f"{a['name']} then {b['name']}"


def test_a8_types(api):
    for c in api["companies"]:
        assert isinstance(c["flags"], list) and isinstance(c["signals"], list)
        assert all(isinstance(c[k], (int, float)) for k in ("fit", "edge", "coverage"))


def test_a9_unknown_route_404s(server):
    try:
        urllib.request.urlopen(server + "/api/nonsense", timeout=5)
        pytest.fail("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


@pytest.mark.parametrize("payload", [
    {"company_id": "x", "verdict": "banana"},          # A10
    {"verdict": "worth contacting"},                   # A11 — no company_id
])
def test_a10_a11_bad_verdicts_rejected(server, payload):
    status, body = _post(server, payload)
    assert status == 400 and "error" in body


# ═══════════════════════════════════════════════════════════════ B — rendering


def test_b1_b2_single_card_with_a_name(today):
    assert today.locator(tid("card")).count() == 1
    assert today.locator(tid("company-name")).inner_text().strip()


def test_b3_b4_both_tiles_named_not_positional(today):
    """Named tiles: a reordering can never make a test compare Fresh to Match."""
    for testid, label in ((tid("score-fit"), "Match"), (tid("score-edge"), "Fresh")):
        tile = today.locator(testid)
        assert tile.count() == 1
        assert tile.locator(tid("score-label")).text_content() == label
    hint = today.locator(tid("score-hint"))
    assert hint.count() == 1
    assert "fund fit" in hint.inner_text().lower()
    assert today.locator(tid("score-fit")).get_attribute("title")
    assert today.locator(tid("score-edge")).get_attribute("title")


def test_b5_scores_render_as_integers(today):
    for n in today.locator(tid("score-value")).all_inner_texts():
        assert re.fullmatch(r"\d+", n.strip()), n


def test_b6_b7_route_and_reasoning_present(today):
    """The route, and a visible account of why this company scored as it did.

    That account used to be the `score.explanation` sentence rendered as
    clauses. It is now the criteria ledger, one row per scored rule, and the
    sentence sits verbatim inside a shut `<details>` — so asserting on the
    explanation's *visible* text would assert the disclosure is open, which is
    not what B6/B7 are about. The claim being pinned is unchanged: the card
    shows the route and says why, above the fold, without being touched.
    """
    assert today.locator(tid("route-fund")).inner_text().strip()

    rows = today.locator(tid("criterion"))
    assert rows.count() >= 3, "the ledger is the reasoning; it must not be empty"
    # Visible without opening anything — the point of the redesign.
    assert rows.first.is_visible()
    assert len(today.locator(tid("criteria")).inner_text()) > 20

    # The sentence is still carried, verbatim, for anyone who wants it.
    assert len(today.locator(tid("explanation")).get_attribute("data-text")) > 20


def test_b14_every_card_has_a_direct_primary_source_link(today, api):
    link = today.locator(f'{tid("evidence-link")}['
                         'data-primary-source="true"]')
    assert link.count() == 1
    assert link.get_attribute("href") == api["companies"][0]["source_url"]


def test_b15_four_fund_match_scores_are_visible(today, api):
    scores = today.locator(tid("fund-score"))
    assert scores.count() == 4

    displayed = {}
    for i in range(scores.count()):
        raw = scores.nth(i).get_attribute("data-value")
        if raw:
            displayed[scores.nth(i).get_attribute("data-fund")] = float(raw)
    expected = {
        score["fund_key"]: score["fit"]
        for score in api["companies"][0]["fund_scores"]
        if score["fit"] is not None
    }
    assert displayed == expected


def test_b16_eligibility_diagnostics_are_visible_without_company_rows(today, api):
    panel = today.locator(tid("eligibility-diagnostics"))
    assert panel.count() == 1
    assert "scored" in panel.locator(tid("eligibility-summary")).inner_text()
    assert panel.locator(tid("eligibility-reason")).count() == len(
        api["eligibility_diagnostics"]["reasons"]
    )
    for company in api["companies"]:
        assert company["name"] not in panel.inner_text()


def test_b8_b9_progress_dots(today, api):
    dots = today.locator(tid("progress-dot"))
    assert dots.count() == min(len(api["companies"]), 12)
    assert today.locator(f'{tid("progress-dot")}[data-state="now"]').count() == 1


def test_b10_b11_verdict_bar(today):
    for name in ("verdict-worth-contacting", "verdict-unsure", "verdict-not-for-me"):
        assert today.locator(tid(name)).count() == 1
    assert not today.locator(tid("verdict-bar")).is_hidden()


def test_b12_no_placeholder_leakage(today):
    """`undefined`, `[object Object]` or `(None)` on screen is a rendering bug
    reaching the client. `(None)` was a real `explain.py` defect on 8 of 11
    cards until the signal-date fix; it is in the sweep now that it cannot
    recur silently."""
    text = today.locator(tid("card")).inner_text()
    found = [p for p in PLACEHOLDERS if p in text]
    assert not found, f"placeholder leaked into the card: {found}"


def test_b13_no_tofu_glyphs(today):
    """SF Symbols codepoints only resolve on Apple platforms with the font
    installed; everywhere else they render as boxes. This regressed once."""
    text = today.locator(tid("card")).inner_text()
    bad = [c for c in text if c == "�" or ord(c) >= 0xF0000]
    assert not bad, f"unrenderable glyphs: {[hex(ord(c)) for c in bad]}"


# ═════════════════════════════════════════════════════════════ C — interaction


def test_c1_c2_c3_arrow_navigation(today):
    first = today.locator(tid("company-name")).inner_text()
    today.keyboard.press("ArrowLeft")                       # C3 — no-op on card 1
    assert today.locator(tid("company-name")).inner_text() == first

    today.keyboard.press("ArrowRight")                      # C1
    second = today.locator(tid("company-name")).inner_text()
    assert second != first

    today.keyboard.press("ArrowLeft")                       # C2
    assert today.locator(tid("company-name")).inner_text() == first


@pytest.mark.parametrize("key,toast_bit", [
    # Keep-worthy keys confirm the company landed on Kept, not the raw verdict
    # string — matching the shortlist UX (`saved to Kept`).
    ("1", "saved to Kept"),
    ("2", "saved to Kept"),
    ("3", "not for me"),
])
def test_c4_c5_c6_keyboard_verdicts(today, key, toast_bit):
    name = today.locator(tid("company-name")).inner_text()
    today.keyboard.press(key)
    today.wait_for_selector(f'{tid("toast")}.show')
    toast = today.locator(tid("toast")).inner_text()
    assert toast_bit in toast and name.split()[0] in toast
    assert today.locator(tid("company-name")).inner_text() != name


def test_c7_buttons_match_keyboard(today):
    name = today.locator(tid("company-name")).inner_text()
    today.locator(tid("verdict-unsure")).click()
    today.wait_for_selector(f'{tid("toast")}.show')
    assert "saved to Kept" in today.locator(tid("toast")).inner_text()
    assert today.locator(tid("company-name")).inner_text() != name


def test_c8_undo_returns_to_the_decided_company(today):
    name = today.locator(tid("company-name")).inner_text()
    today.keyboard.press("1")
    today.wait_for_timeout(300)
    today.keyboard.press("Meta+z" if today.context.browser.browser_type.name != "chromium"
                         else "Control+z")
    today.wait_for_timeout(300)
    assert today.locator(tid("company-name")).inner_text() == name


def test_c9_toast_auto_hides(today):
    today.keyboard.press("2")
    today.wait_for_selector(f'{tid("toast")}.show')
    today.wait_for_selector(f'{tid("toast")}.show', state="detached", timeout=3000)


def test_c10_c11_reviewing_everything_reaches_the_done_state(today, api):
    for _ in range(len(api["companies"])):
        today.keyboard.press("2")
        today.wait_for_timeout(120)
    today.wait_for_selector(tid("done-state"))
    assert "reviewed" in today.locator(tid("done-state")).inner_text()
    assert today.locator(tid("verdict-bar")).is_hidden()


def test_c14_refresh_does_not_requeue_a_decided_company(today):
    company_id = today.locator(tid("card")).get_attribute("data-company-id")

    today.keyboard.press("3")
    today.wait_for_timeout(300)
    today.reload(wait_until="networkidle")
    today.wait_for_selector(tid("card"))

    assert today.locator(tid("card")).get_attribute("data-company-id") != company_id


def test_c15_review_again_restores_the_daily_queue(today, server):
    with urllib.request.urlopen(server + "/api/today", timeout=5) as response:
        current_api = json.loads(response.read())
    first_company_id = current_api["companies"][0]["company_id"]

    for _ in range(len(current_api["companies"])):
        today.keyboard.press("3")
        today.wait_for_timeout(120)

    today.wait_for_selector(tid("done-state"))
    today.locator(tid("review-again")).click()
    today.wait_for_selector(tid("card"))

    assert today.locator(tid("card")).get_attribute("data-company-id") == first_company_id


def test_c16_back_navigation_does_not_reopen_a_decided_company(today):
    first_company_id = today.locator(tid("card")).get_attribute("data-company-id")

    today.keyboard.press("3")
    today.wait_for_timeout(250)
    second_company_id = today.locator(tid("card")).get_attribute("data-company-id")
    today.keyboard.press("ArrowLeft")

    assert second_company_id != first_company_id
    assert today.locator(tid("card")).get_attribute("data-company-id") == second_company_id


def test_c13_modifier_keys_never_record_a_verdict(today, server):
    """`Cmd+1` switches browser tabs. A handler that ignored modifiers would
    file a verdict every time the user changed tab."""
    before = json.loads(urllib.request.urlopen(server + "/api/today").read())
    name = today.locator(tid("company-name")).inner_text()
    for key in ("Control+1", "Control+2", "Control+3", "Meta+1"):
        today.keyboard.press(key)
    today.wait_for_timeout(300)
    assert today.locator(tid("company-name")).inner_text() == name
    assert not today.locator(f'{tid("toast")}.show').count()
    after = json.loads(urllib.request.urlopen(server + "/api/today").read())
    assert before["totals"] == after["totals"]


# ═══════════════════════════════════════════════════════ D — data integrity ★


def test_d1_name_is_verbatim(today, api):
    assert today.locator(tid("company-name")).inner_text() == api["companies"][0]["name"]


def test_d2_d3_scores_are_the_raw_database_values(today, api):
    """The strict form. Comparing rounded text would pass even if the UI were
    off by half a point; `data-value` carries the unrounded number."""
    c = api["companies"][0]
    assert float(today.locator(tid("score-fit")).get_attribute("data-value")) == c["fit"]
    assert float(today.locator(tid("score-edge")).get_attribute("data-value")) == c["edge"]


def test_d4_explanation_is_character_for_character(today, api):
    """The spec's sentence, verbatim. Layout may split it into clauses for
    scanning, but every character still comes from `score.explanation` —
    the UI must not re-derive or paraphrase a claim it does not own."""
    expected = api["companies"][0]["explanation"]
    why = today.locator(tid("explanation"))
    assert why.get_attribute("data-text") == expected
    # All clauses (including any collapsed preview tail) reconstruct the
    # template sentence when joined with a single space — the same delimiter
    # `explain.py` used between parts.
    joined = today.evaluate(
        """() => [...document.querySelectorAll('[data-testid="explanation-clause"]')]
                   .map(el => el.textContent).join(' ')"""
    )
    assert joined == expected


def test_d4c_ledger_shows_every_scored_rule_and_never_calls_unknown_a_failure(today, api):
    """One row per component, with the status the engine would give it.

    The thresholds are `POSITIVE_AT` / `NEGATIVE_AT` from `radar/score/explain.py`.
    They are restated in the page, so this test is the thing that notices if
    the two drift apart and the ledger starts calling a criterion a match
    while the sentence below it says "Against:".

    The `unknown` case is the one that matters most. The full-model percentage
    keeps an unconfirmed fact in the denominator without treating it as a
    failure; a UI that draws `sub_score = None` with the same mark as
    `sub_score = 0` puts that back, visually, on every card.
    """
    components = api["companies"][0]["components"]
    rows = today.locator(tid("criterion"))
    assert rows.count() == len(components)

    for comp in components:
        row = today.locator(f'{tid("criterion")}[data-key="{comp["key"]}"]')
        assert row.count() == 1, f"{comp['key']} is scored but not shown"
        status = row.get_attribute("data-status")
        sub = comp["sub_score"]
        if sub is None:
            expected = "unknown"
        elif sub >= 0.6:
            expected = "met"
        elif sub <= 0.34:
            expected = "missed"
        else:
            expected = "partial"
        assert status == expected, f"{comp['key']}: sub_score={sub} drawn as {status}"

    assert today.locator(f'{tid("criterion")}[data-status="unknown"]').count() == sum(
        1 for c in components if c["sub_score"] is None)


def test_d4d_gloss_explains_only_what_is_not_already_obvious(today, api):
    """A met rule needs no sentence; an unknown or a miss is exactly where
    Aryan asked what the rule was even testing. Glossing every row would put
    the wall of text back in a new shape."""
    met = today.locator(f'{tid("criterion")}[data-status="met"]')
    if met.count():
        assert met.first.locator(".led-gloss").count() == 0

    for status in ("unknown", "missed"):
        rows = today.locator(f'{tid("criterion")}[data-status="{status}"]')
        if rows.count():
            assert rows.first.locator(".led-gloss").count() == 1, status


def test_d4b_one_liner_is_honest_when_absent_and_verbatim_when_present(today, api):
    """Registry companies have no description. Fabricating one from a SIC or
    sector would fail the client's "say unknown rather than guess" rule. When
    the extractor did write a one-liner, the card must show that string
    verbatim — and above the explanation, so the company describes itself
    before the scoring prose does."""
    # The demo shortlist is mostly registry-derived: no one-liner may appear.
    if not api["companies"][0].get("one_liner"):
        assert today.locator(tid("one-liner")).count() == 0

    blurb = "Turns brewery waste into packaging foam."
    today.evaluate(
        """(blurb) => {
             data.companies[i].one_liner = blurb;
             render();
           }""",
        blurb,
    )
    assert today.locator(tid("one-liner")).inner_text() == blurb
    blurb_y = today.locator(tid("one-liner")).bounding_box()["y"]
    why_y = today.locator(tid("explanation")).bounding_box()["y"]
    assert blurb_y < why_y


def test_d5_display_text_is_the_rounding_of_its_own_tile(today):
    """`Math.round` ties away from zero; Python's `round` ties to even, so
    `round(62.5)` is 62 here and 63 in the browser — and 62.5 is exactly what
    this dataset contains. Match the JS semantics, not Python's."""
    import math

    for testid in (tid("score-fit"), tid("score-edge")):
        tile = today.locator(testid)
        raw = float(tile.get_attribute("data-value"))
        shown = int(tile.locator(tid("score-value")).inner_text())
        assert shown == math.floor(raw + 0.5), f"{testid}: shows {shown}, raw {raw}"


def test_d6_d7c_card_state_matches_the_database(today, api):
    c = api["companies"][0]
    card = today.locator(tid("card"))
    assert float(card.get_attribute("data-coverage")) == c["coverage"]
    assert card.get_attribute("data-tier") == c["tier"] != "reject"


def test_d7_amber_tint_iff_below_the_coverage_floor(today, api):
    """`min_coverage` made visible instead of silently suppressing a
    shortlist. Asks the card what it *is*, not what it looks like."""
    c = api["companies"][0]
    thin = today.locator(tid("card")).get_attribute("data-thin") == "true"
    assert thin is (c["coverage"] < 0.5)


def test_d7b_coverage_note_counts_what_we_know(today, api):
    c = api["companies"][0]
    if c["coverage"] >= 0.5:
        pytest.skip("no thin card in this dataset")
    note = today.locator(tid("coverage-note")).inner_text()
    assert f"we know {round(c['coverage'] * 5)} of 5" in note


def test_d8_verdict_round_trip_upserts(today, server, demo_db):
    """The highest-value check in the plan: the one write path, and it must
    update rather than duplicate."""
    import sqlite3

    name = today.locator(tid("company-name")).inner_text()
    cid = today.locator(tid("card")).get_attribute("data-company-id")

    today.keyboard.press("1")
    today.wait_for_timeout(400)

    conn = sqlite3.connect(str(demo_db))
    rows = conn.execute(
        "SELECT value FROM user_field WHERE field='verdict' AND company_id=?",
        (cid,)).fetchall()
    assert rows == [("worth contacting",)], f"{name}: {rows}"

    today.keyboard.press("Control+z")
    today.wait_for_timeout(300)
    today.keyboard.press("3")
    today.wait_for_timeout(400)

    rows = conn.execute(
        "SELECT value FROM user_field WHERE field='verdict' AND company_id=?",
        (cid,)).fetchall()
    conn.close()
    assert rows == [("not for me",)], f"expected one upserted row, got {rows}"


def test_d9_the_sweep_consumes_the_verdicts(today, demo_db, api):
    """Proves the UI's only write reaches the tuning engine. Without this the
    threshold sweep has no labels and every precision column stays blank."""
    from radar.score.tune import sweep
    from radar.store.db import Db

    for _ in range(3):
        today.keyboard.press("1")
        today.wait_for_timeout(250)

    result = sweep(Db(str(demo_db)))
    assert sum(result["labels"].values()) >= 3
    assert "No verdicts yet" not in result["recommendation"]


# ══════════════════════════════════════════════════════ V — visual/responsive


@pytest.mark.parametrize("name,w,h", VIEWPORTS)
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_v1_v3_v4_v5_layout_holds(page, server, name, w, h, scheme):
    page.set_viewport_size({"width": w, "height": h})
    page.emulate_media(color_scheme=scheme)
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))

    # V1 — nothing pushes the document sideways
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    ), f"horizontal scroll at {name}/{scheme}"

    # V3 — verdict labels stay on one line
    for b in ("verdict-worth-contacting", "verdict-unsure", "verdict-not-for-me"):
        assert page.locator(tid(b)).evaluate(
            "e => e.scrollHeight <= e.offsetHeight + 2"
        ), f"{b} wrapped at {name}/{scheme}"

    # V4 — the date is one line, or deliberately hidden below 460px
    assert page.locator(tid("today-date")).evaluate(
        "e => e.offsetParent === null || e.scrollHeight <= e.offsetHeight + 2"
    ), f"date wrapped at {name}/{scheme}"

    # V5 — the company name is not clipped
    assert page.locator(tid("company-name")).evaluate(
        "e => e.scrollWidth <= e.clientWidth + 1"
    ), f"name clipped at {name}/{scheme}"


def test_v2_verdict_bar_never_covers_the_explanation(page, server):
    """The explanation is the point of the card. A sticky bar sitting on top
    of it was a real defect once."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(200)
    why = page.locator(tid("explanation")).bounding_box()
    bar = page.locator(tid("verdict-bar")).bounding_box()
    overlap = why["y"] + why["height"] > bar["y"] and why["y"] < bar["y"] + bar["height"]
    assert not overlap, f"the verdict bar covers the explanation: {why} vs {bar}"


def test_v6_dark_mode_is_a_designed_peer(page, server):
    seen = {}
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.goto(server + "/", wait_until="networkidle")
        seen[scheme] = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert seen["light"] != seen["dark"], seen


@pytest.mark.parametrize("width", [725, 393])
def test_v9_header_navigation_links_do_not_overlap(page, server, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))

    names = ("nav-dashboard", "nav-kept", "nav-onboarding", "progress")
    boxes = [page.locator(tid(name)).bounding_box() for name in names]
    for i, left in enumerate(boxes):
        for right, right_name in zip(boxes[i + 1:], names[i + 1:]):
            horizontal = left["x"] < right["x"] + right["width"]
            horizontal &= right["x"] < left["x"] + left["width"]
            vertical = left["y"] < right["y"] + right["height"]
            vertical &= right["y"] < left["y"] + left["height"]
            assert not (horizontal and vertical), (
                f"header controls overlap at {width}px: {names[i]} and {right_name}"
            )


# ═════════════════════════════════════════════════════════ X — accessibility


def test_x1_x6_landmarks_and_language(today):
    assert today.locator(tid("today-main")).get_attribute("aria-live") == "polite"
    assert today.locator("html").get_attribute("lang") == "en-GB"


def test_x2_x3_keyboard_reaches_every_control(today):
    reachable = set()
    for _ in range(25):
        today.keyboard.press("Tab")
        t = today.evaluate("document.activeElement?.dataset?.testid || ''")
        if t:
            reachable.add(t)
    assert {"verdict-worth-contacting", "verdict-unsure",
            "verdict-not-for-me"} <= reachable, reachable


def test_x4_external_links_are_safe(today):
    links = today.locator(f'{tid("evidence-link")}, {tid("company-domain")}')
    for i in range(links.count()):
        a = links.nth(i)
        assert a.get_attribute("target") == "_blank"
        assert "noopener" in (a.get_attribute("rel") or "")


# ══════════════════════════════════════════════════════════════════ K — kept


def test_k1_a_kept_company_appears_on_the_kept_page(page, server, api):
    """"If I see a company I like and want to keep it, where does that go?"

    It goes to `user_field`, and this page is the answer to the question — the
    only place he can see what he has chosen without opening the spreadsheet.
    """
    company_id = api["companies"][0]["company_id"]
    name = api["companies"][0]["name"]
    assert _post(server, {"company_id": company_id, "verdict": "worth contacting"})[0] == 200

    page.goto(server + "/kept", wait_until="networkidle")
    page.wait_for_selector(tid("kept"))

    row = page.locator(f'{tid("kept-row")}[data-company-id="{company_id}"]')
    assert row.count() == 1, f"{name} was kept but is not on the kept page"
    assert row.get_attribute("data-verdict") == "worth contacting"
    assert name in row.inner_text()


def test_k2_not_for_me_is_not_a_kept_company(page, server, api):
    """Saying no is the point of saying no — it must not come back as a pick."""
    company_id = api["companies"][1]["company_id"]
    assert _post(server, {"company_id": company_id, "verdict": "not for me"})[0] == 200

    page.goto(server + "/kept", wait_until="networkidle")
    page.wait_for_selector(tid("kept"))

    assert page.locator(
        f'{tid("kept-row")}[data-company-id="{company_id}"]').count() == 0
    assert "not for me" not in page.locator(tid("kept")).inner_text().lower()


def test_k3_kept_page_reaches_today_and_back(page, server, api):
    """The two screens have to be reachable from each other, or the list may as
    well not exist."""
    assert _post(server, {"company_id": api["companies"][0]["company_id"],
                          "verdict": "worth contacting"})[0] == 200
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    page.locator(tid("nav-kept")).click()
    page.wait_for_selector(tid("kept"))
    page.locator(tid("nav-today")).click()
    page.wait_for_selector(tid("card"))



def test_k4_kept_badge_counts_on_today(page, server, api):
    """The header badge is how Kept stays visible without opening the list."""
    # Session-scoped DB accumulates verdicts from earlier interaction tests.
    # Force a known "not for me" first so the next keep must raise the count.
    company_id = api["companies"][-1]["company_id"]
    assert _post(server, {"company_id": company_id, "verdict": "not for me"})[0] == 200

    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    before = int((page.locator(tid("kept-badge")).get_attribute("data-count") or "0"))

    status, body = _post(server, {"company_id": company_id, "verdict": "worth contacting"})
    assert status == 200
    payload = json.loads(body)
    assert payload["kept_count"] == before + 1

    page.reload(wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    badge = page.locator(tid("kept-badge"))
    assert badge.get_attribute("data-count") == str(before + 1)
    assert badge.inner_text().strip() == str(before + 1)


def test_k5_help_page_is_reachable_from_kept(page, server, api):
    assert _post(server, {"company_id": api["companies"][0]["company_id"],
                          "verdict": "worth contacting"})[0] == 200
    page.goto(server + "/kept", wait_until="networkidle")
    page.wait_for_selector(tid("kept"))
    page.locator(tid("nav-help")).click()
    page.wait_for_selector(tid("help"))
    text = page.locator(tid("help")).inner_text()
    assert "Kept" in text
    assert "user_field" in text


def test_k6_dashboard_route_renders_calendar_and_kept_history(page, server, api):
    """The dashboard route is real, navigable, and renders its server data."""
    company_id = api["companies"][0]["company_id"]
    assert _post(server, {"company_id": company_id,
                          "verdict": "worth contacting"})[0] == 200

    page.goto(server + "/", wait_until="networkidle")
    page.locator(tid("nav-dashboard")).click()
    page.wait_for_selector(tid("dashboard"))

    assert page.locator(tid("section-calendar")).count() == 1
    assert page.locator(tid("calendar")).count() == 1
    assert page.locator(tid("kept-table")).count() == 1
    assert page.locator(f'{tid("kept-table-row")}['
                       f'data-company-id="{company_id}"]').count() == 1

    page.locator(tid("nav-kept")).click()
    page.wait_for_selector(tid("kept"))


def test_x4b_hostile_source_text_cannot_run_script(today):
    """Every string on this card is scraped off somebody else's site.

    `render()` builds one innerHTML string, so an unescaped company name or
    article headline is script execution — and this page can read the whole
    queue and POST verdicts. A source page only has to put markup in its
    <title> for that headline to reach `signal.headline` verbatim.

    Proved exploitable before `esc()` existed: the payload below set
    `document.body.dataset.pwned` on a real card.
    """
    payload = '<img src=x onerror="window.__pwned = 1">Acme'
    today.evaluate(
        """(payload) => {
             const c = data.companies[i];
             c.name = payload;
             c.explanation = payload;
             c.signals = [{kind: "news_mention", headline: payload,
                           source_url: "https://example.test/a"}];
             render();
           }""",
        payload,
    )

    assert today.evaluate("window.__pwned ?? null") is None, "scraped markup executed"
    assert today.locator(f'{tid("card")} img').count() == 0, "scraped markup became an element"
    # Escaped, not stripped: the reader still sees exactly what the source said.
    assert payload in today.locator(tid("company-name")).inner_text()


def test_x4c_a_javascript_url_never_becomes_a_link(today):
    """Escaping does not help an href — `javascript:` needs no angle bracket.

    `_common.absolute_url` drops the scheme, but only the HTML adapters go
    through it; the wp-json ones take `entry["link"]` as printed. So the
    allowlist has to exist here, at the point of use.
    """
    today.evaluate(
        """() => {
             const c = data.companies[i];
             c.signals = [{kind: "news_mention", headline: "Anything",
                           source_url: "javascript:window.__jsurl = 1"}];
             c.sources = [];
             render();
           }"""
    )

    hrefs = today.locator(tid("evidence-link")).evaluate_all(
        "els => els.map(e => e.getAttribute('href'))")
    assert not any((h or "").lower().startswith("javascript:") for h in hrefs), hrefs
    assert today.evaluate("window.__jsurl ?? null") is None


def test_x5_reduced_motion_is_honoured(page, server):
    page.emulate_media(reduced_motion="reduce")
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    duration = page.locator(tid("card")).evaluate(
        "e => getComputedStyle(e).animationDuration")
    seconds = (float(duration[:-2]) / 1000 if duration.endswith("ms")
               else float(duration.rstrip("s")))
    assert seconds <= 0.001, f"animation not suppressed: {duration}"


def test_x7_colour_is_never_the_only_cue(today, api):
    """The amber tint always has words beside it."""
    if api["companies"][0]["coverage"] >= 0.5:
        pytest.skip("no thin card in this dataset")
    assert today.locator(tid("coverage-note")).count() == 1


# ═══════════════════════════════════════════════════════════ E — resilience


def test_e4_double_keypress_writes_one_verdict(today, server, demo_db):
    import sqlite3

    cid = today.locator(tid("card")).get_attribute("data-company-id")
    today.keyboard.press("1")
    today.keyboard.press("1")            # the second lands on the next company
    today.wait_for_timeout(500)

    conn = sqlite3.connect(str(demo_db))
    n = conn.execute(
        "SELECT COUNT(*) FROM user_field WHERE field='verdict' AND company_id=?",
        (cid,)).fetchone()[0]
    conn.close()
    assert n == 1, f"{n} rows written for one company"


def test_e6_console_is_clean(page, server):
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    for _ in range(4):
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(100)
    assert not errors, errors


def test_e7_network_is_clean(page, server):
    bad: list[str] = []
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.goto(server + "/", wait_until="networkidle")
    page.wait_for_selector(tid("card"))
    page.keyboard.press("1")
    page.wait_for_timeout(400)
    assert not bad, bad



# ═══════════════════════════════════════════════════════════ O — onboarding


ONBOARDING_SECTIONS = [
    "section-problem", "section-morning", "section-scores",
    "section-coverage", "section-funds", "section-expect", "section-feedback",
]


@pytest.fixture
def onboarding(page, server: str):
    page.goto(server + "/onboarding", wait_until="networkidle")
    page.wait_for_selector(tid("onboarding"))
    return page


def test_o1_every_section_is_present(onboarding):
    for name in ONBOARDING_SECTIONS:
        assert onboarding.locator(tid(name)).count() == 1, name


def test_o2_diagrams_render_as_svg_not_images(onboarding):
    """Inline SVG inherits the design tokens, so the diagrams follow dark mode
    and the type scale. A screenshot would go stale the first time a colour
    changed."""
    svgs = onboarding.locator("figure svg")
    assert svgs.count() >= 5
    assert onboarding.locator("figure img").count() == 0


def test_o3_diagrams_are_described_for_screen_readers(onboarding):
    svgs = onboarding.locator("figure svg")
    for i in range(svgs.count()):
        label = svgs.nth(i).get_attribute("aria-label")
        assert label and len(label) > 25, f"svg {i} has no useful aria-label"
        assert svgs.nth(i).get_attribute("role") == "img"


def test_o4_navigation_between_the_two_pages(onboarding, server):
    onboarding.locator(tid("nav-today")).click()
    onboarding.wait_for_selector(tid("card"))
    onboarding.locator(tid("nav-onboarding")).click()
    onboarding.wait_for_selector(tid("onboarding"))
    assert onboarding.locator(tid("cta-today")).count() == 1


def test_o5_shares_one_design_system(onboarding, server):
    """Both pages load the same tokens file. Two definitions would drift into
    two design systems."""
    hrefs = onboarding.eval_on_selector_all(
        "link[rel=stylesheet]", "els => els.map(e => new URL(e.href).pathname)")
    assert "/tokens.css" in hrefs
    onboarding.goto(server + "/", wait_until="networkidle")
    hrefs = onboarding.eval_on_selector_all(
        "link[rel=stylesheet]", "els => els.map(e => new URL(e.href).pathname)")
    assert "/tokens.css" in hrefs


@pytest.mark.parametrize("w,h", [(1440, 900), (393, 852)])
def test_o6_no_horizontal_scroll(page, server, w, h):
    page.set_viewport_size({"width": w, "height": h})
    page.goto(server + "/onboarding", wait_until="networkidle")
    page.wait_for_selector(tid("onboarding"))
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    ), f"horizontal scroll at {w}x{h}"


def test_o7_the_page_actually_scrolls(page, server):
    """`height: 100%` on body pinned the box to the viewport while the content
    ran to 5000px inside it. Today never noticed because it fits on one
    screen."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server + "/onboarding", wait_until="networkidle")
    page.wait_for_selector(tid("onboarding"))
    metrics = page.evaluate(
        "({doc: document.documentElement.scrollHeight,"
        "  body: parseFloat(getComputedStyle(document.body).height)})")
    assert metrics["doc"] > 1500, "the page is unexpectedly short"
    assert metrics["body"] > 1500, (
        f"body box is {metrics['body']}px but content is {metrics['doc']}px — "
        "height:100% is clipping the document")


def test_o8_reading_level_stays_plain(onboarding):
    """It is written for a student at 7am, not for an engineer. Jargon from the
    spec must not leak into the page a non-technical reader sees."""
    text = onboarding.locator(tid("onboarding")).inner_text().lower()
    for term in ("sic code", "sh01", "config_hash", "coverage floor",
                 "discovery_route", "min_coverage", "pydantic", "sqlite"):
        assert term not in text, f"jargon leaked into onboarding: {term!r}"
