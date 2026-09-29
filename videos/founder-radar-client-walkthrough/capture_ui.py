"""Capture the real UI against an isolated disposable database.

Approval is deliberately simulated ONLY inside this teaching fixture.
No real Hermes call, production credentials, Sheet or client database is used.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from radar.qa.today import _config_for, load_today_cards, record_check, TodayCheckResult
from radar.store.db import Db
from tests.demo_db import build

out = Path(sys.argv[1]).resolve()
assets = out / "assets"
assets.mkdir(parents=True, exist_ok=True)
db_path = out / "demo.sqlite3"
if db_path.exists():
    raise SystemExit("Refusing to overwrite an existing demo database; choose a new output folder.")
build(str(db_path))
db = Db(str(db_path))
cards = load_today_cards(db, _config_for(db, None), limit=10_000)
for card in cards[:2]:
    record_check(db, card, TodayCheckResult(
        "pass", checker="hermes",
        summary="ISOLATED VIDEO FIXTURE: simulated completed check, not a live Hermes result"))
for card in cards[2:]:
    record_check(db, card, TodayCheckResult(
        "incomplete", checker="hermes",
        summary="ISOLATED VIDEO FIXTURE: simulated unfinished check"))
db.close()
repo = Path(__file__).resolve().parents[2]
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
home = out / "demo-home"
home.mkdir(exist_ok=True)
proc = subprocess.Popen([sys.executable, str(repo/"prototype/server.py"),
                         "--db", str(db_path), "--port", str(port)], cwd=repo,
                        env={"PATH": os.environ["PATH"], "PYTHONPATH":str(repo),
                             "HOME":str(home), "RADAR_RULES_ONLY":"1"},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base=f"http://127.0.0.1:{port}"
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(base, timeout=1).read()
            break
        except OSError:
            time.sleep(.1)
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={"width":1280,"height":720})
        def capture(name,path):
            page.goto(base+path)
            if path == "/":
                page.wait_for_function("document.querySelector('#progress').textContent.includes('ready to review')",
                                       timeout=20_000)
            else:
                page.wait_for_load_state("networkidle")
            page.evaluate("""() => {const d=document.createElement('div');d.textContent='DEMO · isolated fixture · approvals simulated';d.style='position:fixed;z-index:999999;top:0;left:0;right:0;background:#bf5b35;color:white;text-align:center;font:16px sans-serif;padding:5px';document.body.append(d)}""")
            page.screenshot(path=str(assets/name))
        capture("today.png","/")
        assert "ready to review" in page.locator("body").inner_text()
        page.locator("button").filter(has_text="Worth contacting").click(timeout=4000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(assets/"saved.png"))
        capture("kept.png","/kept")
        assert "worth contacting" in page.locator("body").inner_text().lower()
        capture("dashboard.png","/dashboard")
        browser.close()
finally:
    proc.terminate()
    proc.wait(timeout=10)
print("Real UI captured using isolated fixture and one isolated saved decision.")
