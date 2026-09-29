"""Isolated, executable settings lesson; never opens a production sheet or DB.

Run from the repository with its Python environment:
python videos/founder-radar-client-walkthrough/prepare_demo.py OUTPUT_DIRECTORY
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright
from radar.config.defaults import default_config
from tests.factories import C, score_one

OUTPUT = Path(sys.argv[1]).resolve()
ASSETS = OUTPUT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
cfg = default_config()
company = C(canonical_name="DEMO River Finance", age_months=12, sector="fintech",
            funding=2_000_000, prior_total_gbp=2_000_000,
            last_round_gbp=500_000, uk_exec_pct=100)
baseline = cfg.model_copy(deep=True)
history = []

HTML = """<!doctype html><html><head><meta charset=utf-8><style>
body{background:#f6f3ec;color:#133e3b;font:22px Arial;padding:12px 55px;margin:0}header{font-size:17px;color:#b6532e}h1{font-size:32px;margin:12px 0}p{font-size:18px;color:#58635e}.tabs{padding:13px;background:#e1e9df;border-radius:9px;margin:12px 0}label{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #ced5ca;padding:7px 10px}input{width:170px;font-size:20px;padding:6px;border:1px solid #8fa497;border-radius:7px}input[type=checkbox]{height:25px}button{background:#164c43;color:white;padding:14px 25px;font:20px Arial;border:0;border-radius:8px;margin:12px 0}.result{padding:14px;background:white;border:2px solid #b6532e;border-radius:12px;font-size:24px}small{font-size:16px}
</style></head><body><header>DEMO ONLY · local configuration replica · fictional company</header>
<h1>Make a change. Calculate again.</h1><p>Baseline: repository default configuration, not a live Google Sheet. Edits below are teaching examples.</p>
<div class=tabs>Sources &nbsp; · &nbsp; Fund Criteria &nbsp; · &nbsp; Settings</div>
<label>Sources → northern_accelerator → Enabled<input id=enabled type=checkbox checked></label>
<label>Outward fund_ii → prior_total_max (£)<input id=prior type=number value=20000000></label>
<label>Settings → shortlist_fit<input id=threshold type=number value=70></label>
<label>Settings → weight_fit / weight_edge<input id=weight type=number step=.1 value=.6></label>
<button id=apply>Save demo settings + rescore</button><div class=result id=result></div><p>DEMO River Finance: UK · fintech · 12 months old · £2m previously raised</p>
<script>async function send(){const r=await fetch('/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enabled.checked,prior:+prior.value,threshold:+threshold.value,weight:+weight.value})});const s=await r.json();result.innerHTML=`Match <b>${s.fund_fit_pct}</b> · Fresh <b>${s.discovery_edge}</b> · Priority <b>${s.priority}</b> · <b>${s.tier.toUpperCase()}</b><br><small>${s.explanation}</small>`;}apply.onclick=send;send();</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        values = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        changed = baseline.model_copy(deep=True)
        changed.sources = [s.model_copy(update={"enabled": values["enabled"]})
                           if s.key == "northern_accelerator" else s
                           for s in changed.sources]
        changed.fund("outward").vehicles[0].hard_rejects["prior_total_max"] = values["prior"]
        changed.settings = changed.settings.model_copy(update={
            "shortlist_fit": values["threshold"], "weight_fit": values["weight"],
            "weight_edge": round(1-values["weight"], 5)})
        result = score_one(company, "outward", changed).model_dump()
        history.append({"input": values, "source_enabled": changed.sources[1].enabled,
                        "config_hash": changed.hash(), "score": result})
        (OUTPUT / "demo-settings-results.json").write_text(json.dumps(history, indent=2))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width":1280,"height":720})
    page.goto(f"http://127.0.0.1:{server.server_port}")
    page.wait_for_function("result.textContent.includes('WATCHLIST')")
    page.screenshot(path=str(ASSETS / "settings-baseline.png"))
    page.locator("#enabled").uncheck()
    page.locator("#apply").click()
    page.wait_for_timeout(150)
    page.screenshot(path=str(ASSETS / "settings-source-off.png"))
    page.locator("#prior").fill("1000000")
    page.locator("#apply").click()
    page.wait_for_function("result.textContent.includes('REJECT')")
    page.screenshot(path=str(ASSETS / "settings-rule-reject.png"))
    page.locator("#prior").fill("20000000")
    page.locator("#threshold").fill("60")
    page.locator("#apply").click()
    page.wait_for_function("result.textContent.includes('SHORTLIST')")
    page.screenshot(path=str(ASSETS / "settings-threshold.png"))
    page.locator("#threshold").fill("70")
    page.locator("#weight").fill("0.8")
    page.locator("#apply").click()
    page.wait_for_function("result.textContent.includes('62.5')")
    page.screenshot(path=str(ASSETS / "settings-weight.png"))
    browser.close()
server.shutdown()
assert history[0]["score"]["tier"] == "watchlist"
assert history[1]["source_enabled"] is False
assert history[2]["score"]["tier"] == "reject"
assert history[3]["score"]["tier"] == "shortlist"
assert history[4]["score"]["priority"] == 62.5
print("Five real configuration/scoring states captured; production untouched.")
