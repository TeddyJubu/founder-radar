#!/usr/bin/env python3
"""Record a captioned morning-pass tutorial against the live Today prototype.

Drives the real pages (onboarding → Today → Kept → Dashboard → Help) so the
video is the system, not a mock. Captions are injected as a top banner and
also written to WebVTT / SRT next to the video.

    DISPLAY=:1 .venv/bin/python scripts/record_usage_tutorial.py \\
        --base-url http://127.0.0.1:8788 \\
        --out-dir /tmp/tutorial-out
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

CAPTION_CSS = """
#fr-tutorial-caption {
  position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
  display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: start;
  padding: 11px 22px 13px;
  background: rgba(11, 18, 32, 0.94);
  color: #fff;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  box-shadow: 0 10px 28px rgba(0,0,0,0.22);
  pointer-events: none;
}
#fr-tutorial-caption .kicker {
  font-size: 11px; font-weight: 700; letter-spacing: 0.14em;
  text-transform: uppercase; color: #8ec8ff; padding-top: 5px; white-space: nowrap;
}
#fr-tutorial-caption .line {
  font-size: 19px; line-height: 1.35; font-weight: 550; letter-spacing: -0.02em;
  max-width: 58rem;
}
body { padding-top: 74px !important; }
.nav[style], nav.nav { top: 74px !important; }
[data-testid="card"],
[data-testid="company-name"],
[data-testid="kept"],
[data-testid="dashboard"],
[data-testid="help"] { scroll-margin-top: 88px; }
"""

CAPTION_JS = """
([css, text]) => {
  if (!document.getElementById('fr-tutorial-caption-style')) {
    const style = document.createElement('style');
    style.id = 'fr-tutorial-caption-style';
    style.textContent = css;
    document.head.appendChild(style);
  }
  let bar = document.getElementById('fr-tutorial-caption');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'fr-tutorial-caption';
    bar.setAttribute('role', 'status');
    bar.innerHTML = '<div class="kicker">How to use</div><div class="line"></div>';
    document.body.appendChild(bar);
  }
  bar.querySelector('.line').textContent = text;
}
"""

HIGHLIGHT_JS = """
el => {
  const prev = el.getAttribute('data-fr-prev-shadow') || el.style.boxShadow;
  el.setAttribute('data-fr-prev-shadow', prev);
  el.style.transition = 'box-shadow 160ms ease, transform 160ms ease';
  el.style.boxShadow = '0 0 0 3px #3b82f6, 0 0 0 7px rgba(59,130,246,0.28)';
  el.style.transform = 'translateY(-1px)';
}
"""


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _vtt_stamp(seconds: float) -> str:
    return _stamp(seconds).replace(",", ".")


class Tutorial:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.t0 = time.monotonic()
        self.captions: list[tuple[float, float, str]] = []
        self._open: tuple[float, str] | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    def show(self, text: str) -> None:
        if self._open:
            start, prev = self._open
            end = self.now()
            if end <= start:
                end = start + 0.04
            self.captions.append((start, end, prev))
        self.page.evaluate(CAPTION_JS, [CAPTION_CSS, text])
        self._open = (self.now(), text)

    def hold(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def say(self, text: str, seconds: float) -> None:
        self.show(text)
        self.hold(seconds)

    def close(self) -> None:
        if self._open:
            start, prev = self._open
            self.captions.append((start, max(self.now(), start + 0.04), prev))
            self._open = None

    def reveal(self, locator: Locator, block: str = "center") -> None:
        locator.evaluate(
            f"el => el.scrollIntoView({{behavior: 'smooth', block: '{block}'}})"
        )
        self.hold(0.85)

    def scroll_top(self) -> None:
        self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        self.hold(0.7)

    def wait_next_card(self, previous_name: str) -> None:
        self.page.wait_for_function(
            """prev => {
              const done = document.querySelector('[data-testid="done-state"]');
              const el = document.querySelector('[data-testid="company-name"]');
              return Boolean(done) || (el && el.textContent.trim() !== prev);
            }""",
            arg=previous_name,
            timeout=10_000,
        )
        self.hold(0.45)
        if self.page.locator('[data-testid="card"]').count():
            self.scroll_top()
            self.reveal(self.page.locator('[data-testid="company-name"]'), block="center")

    def tap(self, locator: Locator) -> None:
        locator.wait_for(state="visible", timeout=10_000)
        self.reveal(locator)
        locator.evaluate(HIGHLIGHT_JS)
        self.hold(0.45)
        locator.click()
        self.hold(0.35)

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")
        self.hold(0.4)
        if self._open:
            self.page.evaluate(CAPTION_JS, [CAPTION_CSS, self._open[1]])

    def write_tracks(self, dest: Path) -> None:
        self.close()
        dest.parent.mkdir(parents=True, exist_ok=True)
        srt_lines: list[str] = []
        vtt_lines = ["WEBVTT", ""]
        for i, (start, end, text) in enumerate(self.captions, 1):
            srt_lines.extend(
                [str(i), f"{_stamp(start)} --> {_stamp(end)}", text, ""]
            )
            vtt_lines.extend(
                [f"{_vtt_stamp(start)} --> {_vtt_stamp(end)}", text, ""]
            )
        dest.with_suffix(".srt").write_text("\n".join(srt_lines), encoding="utf-8")
        dest.with_suffix(".vtt").write_text("\n".join(vtt_lines), encoding="utf-8")


def run(base_url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    video_dir = out_dir / "raw-video"
    if video_dir.exists():
        shutil.rmtree(video_dir)
    video_dir.mkdir()
    user_data = tempfile.mkdtemp(prefix="fr-tutorial-chrome-")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data,
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 900},
            screen={"width": 1440, "height": 900},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1440, "height": 900},
            locale="en-GB",
            color_scheme="light",
            device_scale_factor=1,
            args=[
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                "--window-size=1440,900",
                "--window-position=40,40",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        tour = Tutorial(page)

        try:
            tour.goto(f"{base_url}/onboarding")
            page.locator('[data-testid="onboarding"]').wait_for()
            tour.say(
                "UK Founder Radar finds young UK startups each morning and tells you which fund to send them to.",
                5.2,
            )
            tour.say(
                "It starts from the UK company register — a legal birthday — so a 90-day-old company cannot look six years old.",
                5.6,
            )
            tour.reveal(page.locator('[data-testid="section-problem"]'))
            tour.say(
                "Old lists come from fund websites. Those companies already raised. You cannot introduce a fund to a company it already owns.",
                5.4,
            )
            tour.reveal(page.locator('[data-testid="section-morning"]'))
            tour.say(
                "Your morning job is short: one company at a time. Press 1 to keep, 2 if you are unsure, or 3 to pass.",
                5.6,
            )
            tour.say(
                "Open Today when you are ready. That is the real morning list — not a practice deck.",
                3.6,
            )
            tour.tap(page.locator('[data-testid="cta-today"]'))
            page.locator('[data-testid="today-header"]').wait_for()
            page.locator('[data-testid="card"]').wait_for()
            tour.scroll_top()

            first = page.locator('[data-testid="company-name"]').inner_text()
            tour.say(
                f"This is Today. First up: {first}. One company at a time — read the card, then decide.",
                5.0,
            )
            if page.locator('[data-testid="eligibility-diagnostics"]').count():
                tour.reveal(page.locator('[data-testid="eligibility-diagnostics"]'))
                tour.say(
                    "The blue box is queue diagnostics: why other scored companies are not on Today. Counts only — not a second list.",
                    5.2,
                )
            tour.reveal(page.locator('[data-testid="company-meta"]'))
            tour.say(
                "The top line is the company, city, and how long ago it was incorporated. Unknown stays unknown.",
                4.8,
            )
            tour.reveal(page.locator('[data-testid="scores"]'))
            tour.say(
                "Match is fund fit for the Send-to fund. Fresh is how new it still looks to them.",
                5.0,
            )
            tour.reveal(page.locator('[data-testid="route"]'))
            tour.say(
                "Send to is the suggested fund and vehicle — including the cheque range. That is who you would email.",
                5.2,
            )
            tour.reveal(page.locator('[data-testid="fund-scores"]'))
            tour.say(
                "The four bars are four separate fund scores. They do not add up to 100%. The tallest bar is not always the route.",
                5.6,
            )
            details = page.locator('[data-testid="explanation-toggle"]')
            if details.count():
                tour.say("Open the full score if you want the evidence, not just the headline.", 3.4)
                tour.tap(details)
                tour.hold(2.2)
            tour.say(
                "This one is worth an email. Press Worth contacting — key 1. It is saved to Kept straight away.",
                4.6,
            )
            tour.tap(page.locator('[data-testid="verdict-worth-contacting"]'))
            tour.wait_next_card(first)

            second = page.locator('[data-testid="company-name"]').inner_text()
            tour.say(
                f"Next: {second}. Same morning pass. Look at Match, Fresh, and who it is sent to.",
                4.8,
            )
            tour.reveal(page.locator('[data-testid="scores"]'))
            tour.hold(1.4)
            tour.reveal(page.locator('[data-testid="fund-scores"]'))
            tour.say(
                "This card also fits another fund. If you want to come back later, press Unsure — key 2. Unsure is still Kept.",
                5.4,
            )
            tour.tap(page.locator('[data-testid="verdict-unsure"]'))
            tour.wait_next_card(second)

            third = page.locator('[data-testid="company-name"]').inner_text()
            tour.say(
                f"Last today: {third}. If it is not for you, press Not for me — key 3.",
                4.6,
            )
            tour.reveal(page.locator('[data-testid="company-name"]'), block="start")
            tour.reveal(page.locator('[data-testid="route"]'))
            tour.say(
                "Rejects are stored for tuning, but they never appear on Kept and they do not come back on Today.",
                5.2,
            )
            tour.tap(page.locator('[data-testid="verdict-not-for-me"]'))
            page.locator('[data-testid="done-state"]').wait_for()
            tour.say(
                "That is the morning list done. The done screen points you to Kept — the durable place for your picks.",
                5.0,
            )
            tour.tap(page.locator('[data-testid="done-kept-link"]'))
            page.locator('[data-testid="kept"]').wait_for()
            tour.say(
                "Kept lists Worth contacting and Unsure only. The company you passed is not here.",
                5.0,
            )
            rows = page.locator('[data-testid="kept-row"]')
            if rows.count():
                tour.reveal(rows.first)
                tour.hold(1.6)
            tour.say(
                "Open Dashboard when you want dates: first seen, incorporation, signals, and the day you decided.",
                4.4,
            )
            tour.tap(page.locator('[data-testid="nav-dashboard"]'))
            page.locator('[data-testid="dashboard"]').wait_for()
            tour.say(
                "The calendar is history, not a second inbox. A quiet day is a real answer, not a missing one.",
                5.2,
            )
            tour.reveal(page.locator('[data-testid="section-calendar"]'))
            tour.hold(2.0)
            if page.locator('[data-testid="section-kept-table"]').count():
                tour.reveal(page.locator('[data-testid="section-kept-table"]'))
                tour.hold(2.0)
            tour.say(
                "Help explains how the sheet, this website, and Telegram share one notebook.",
                4.2,
            )
            # Dashboard has no Help link in the header; Today / Kept do.
            # Use Today first, then the onboarding "?" or Help from Kept.
            tour.tap(page.locator('[data-testid="nav-today"]'))
            page.locator('[data-testid="today-header"]').wait_for()
            # After a finished pass, Today may show the done state. Help lives
            # on /help; the "?" goes to onboarding. Prefer the ops help page.
            page.goto(f"{base_url}/help", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            page.locator('[data-testid="help"]').wait_for()
            if tour._open:
                page.evaluate(CAPTION_JS, [CAPTION_CSS, tour._open[1]])
            tour.say(
                "Decisions only count when they are saved here or through the CLI. A chat-only “no” does not update Today or Kept.",
                5.6,
            )
            tour.hold(1.2)
            tour.tap(page.locator('[data-testid="nav-today"]'))
            page.locator('[data-testid="today-header"]').wait_for()
            tour.say(
                "That is the loop: Today, decide with 1 / 2 / 3, then Kept. Use it each morning — the list gets pickier with your answers.",
                5.8,
            )
            tour.hold(1.6)
        finally:
            tour.write_tracks(out_dir / "how_to_use")
            page.close()
            context.close()

    videos = list(video_dir.glob("*.webm")) + list(video_dir.glob("*.mp4"))
    if not videos:
        raise SystemExit("Playwright did not write a video file")
    raw = videos[0]
    dest = out_dir / "how_to_use_raw.webm"
    shutil.copy2(raw, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8788")
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/tutorial-out"))
    args = parser.parse_args()
    path = run(args.base_url.rstrip("/"), args.out_dir)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
