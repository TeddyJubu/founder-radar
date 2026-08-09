"""Today screen prototype — a read layer over the real database.

Not part of the shipped system. `radar/` does not import it and it is not in
the package; it exists so the interface can be judged against real rows
instead of a mockup.

Two rules it inherits from 02-architecture §2, and they are the whole reason
this is thin:

* **It computes nothing.** Fit, edge, coverage, tier and the explanation
  sentence are read straight out of `score`. The moment a surface recomputes a
  number, the number has two sources of truth and the scoring stops being
  defensible.
* **One write path.** Verdicts go to `user_field`, the same table the Google
  Sheet writes and `radar/score/tune.py` reads — so marking a company here
  improves the threshold sweep, and nothing else in the database is touched.

Stdlib only: no FastAPI, no npm, no build step.

    .venv/bin/python prototype/server.py --db /tmp/demo.db --port 8787
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Tiers a human is asked to look at. `reject` never reaches Today: the gates
# already decided, and re-litigating them is what the Companies tab is for.
REVIEWABLE = ("shortlist", "watchlist")


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _vehicles() -> dict[str, dict]:
    """`vehicle_key -> display name and cheque range`, from the seeded config.

    The score table stores the key, not the label. Resolving it here rather
    than denormalising into the row keeps the config the single owner of what
    a vehicle is called.
    """
    from radar.config.defaults import default_config

    out: dict[str, dict] = {}
    for fund in default_config().funds:
        for v in fund.vehicles:
            out[v.vehicle_key] = {
                "name": v.vehicle_name,
                "fund": fund.name,
                "cheque_min": v.cheque_min,
                "cheque_max": v.cheque_max,
            }
    return out


def _age_phrase(incorporated_on: str | None, today: date) -> tuple[str, str | None]:
    """Prose first, exact date on hover. The complaint was about age, so it
    reads as a sentence rather than as an ISO string in a table cell."""
    if not incorporated_on:
        return "incorporation date not confirmed", None
    try:
        d = datetime.fromisoformat(str(incorporated_on)[:10]).date()
    except ValueError:
        return "incorporation date not confirmed", None
    months = (today.year - d.year) * 12 + (today.month - d.month)
    exact = d.strftime("%-d %B %Y")
    if months < 1:
        return "incorporated this month", exact
    if months == 1:
        return "incorporated 1 month ago", exact
    if months < 24:
        return f"incorporated {months} months ago", exact
    return f"incorporated {months // 12} years ago", exact


def build_today(conn: sqlite3.Connection, limit: int = 20) -> dict:
    vehicles = _vehicles()
    today = date.today()

    # One row per company: the fund it fits best. The others become "also
    # fits", because the decision Aryan is making is "who do I send this to",
    # and four rows for one company is the version-1 complaint about scanning.
    rows = conn.execute(
        """SELECT s.id sid, s.company_id, s.fund_key, s.vehicle_key, s.tier,
                  s.fund_fit_pct, s.discovery_edge, s.coverage, s.priority,
                  s.explanation, s.flags,
                  c.canonical_name, c.domain, c.website_url, c.hq_city,
                  c.hq_region, c.incorporated_on, c.sector, c.stage,
                  c.one_liner, c.discovery_route, c.companies_house_no
             FROM score s
             JOIN company c ON c.id = s.company_id
             JOIN (SELECT company_id, MAX(priority) best
                     FROM score WHERE tier IN (?, ?) GROUP BY company_id) t
               ON t.company_id = s.company_id AND t.best = s.priority
            WHERE s.tier IN (?, ?) AND c.merged_into IS NULL
            GROUP BY s.company_id
            -- Ties break on coverage: among companies the scoring cannot
            -- separate, review the one we actually know something about
            -- first. Without a Companies House key every registry company
            -- has unknown age, so hundreds land on an identical priority and
            -- the tie-break is doing all the ordering work.
            ORDER BY s.priority DESC, s.coverage DESC, c.canonical_name
            LIMIT ?""",
        (*REVIEWABLE, *REVIEWABLE, limit),
    ).fetchall()

    verdicts = {
        r["company_id"]: r["value"]
        for r in conn.execute(
            "SELECT company_id, value FROM user_field WHERE field = 'verdict'")
    }

    out = []
    for r in rows:
        vehicle = vehicles.get(r["vehicle_key"] or "", {})
        phrase, exact = _age_phrase(r["incorporated_on"], today)

        signals = [dict(s) for s in conn.execute(
            """SELECT kind, headline, source_url, occurred_on, source_key
                 FROM signal WHERE company_id = ?
                ORDER BY COALESCE(occurred_on, first_seen) DESC LIMIT 5""",
            (r["company_id"],))]

        components = [dict(c) for c in conn.execute(
            """SELECT key, label, sub_score, weight, contribution, evidence
                 FROM score_component WHERE score_id = ? ORDER BY contribution DESC""",
            (r["sid"],))]

        also = [dict(a) for a in conn.execute(
            """SELECT fund_key, tier, fund_fit_pct FROM score
                WHERE company_id = ? AND fund_key != ? AND tier != 'reject'
                ORDER BY fund_fit_pct DESC""",
            (r["company_id"], r["fund_key"]))]

        out.append({
            "company_id": r["company_id"],
            "name": r["canonical_name"],
            "domain": r["domain"],
            "website": r["website_url"],
            "city": r["hq_city"],
            "region": r["hq_region"],
            "sector": r["sector"],
            "stage": r["stage"],
            "one_liner": r["one_liner"],
            "route": r["discovery_route"],
            "ch_number": r["companies_house_no"],
            "age_phrase": phrase,
            "age_exact": exact,
            "fund": r["fund_key"],
            "fund_name": vehicle.get("fund"),
            "vehicle": vehicle.get("name") or r["vehicle_key"],
            "cheque_min": vehicle.get("cheque_min"),
            "cheque_max": vehicle.get("cheque_max"),
            "tier": r["tier"],
            "fit": r["fund_fit_pct"],
            "edge": r["discovery_edge"],
            "coverage": r["coverage"],
            "priority": r["priority"],
            "explanation": r["explanation"],
            "flags": json.loads(r["flags"]) if r["flags"] else [],
            "signals": signals,
            "components": components,
            "also_fits": also,
            "verdict": verdicts.get(r["company_id"]),
        })

    counts = dict(conn.execute(
        "SELECT tier, COUNT(*) FROM score GROUP BY tier").fetchall())
    run = conn.execute(
        "SELECT started_at, items_fetched, companies_new, shortlisted, status "
        "FROM run ORDER BY id DESC LIMIT 1").fetchone()

    return {
        "date": today.isoformat(),
        "companies": out,
        "totals": {
            "reviewable": len(out),
            "shortlist": counts.get("shortlist", 0),
            "watchlist": counts.get("watchlist", 0),
            "rejected": counts.get("reject", 0),
        },
        "run": dict(run) if run else None,
    }


def set_verdict(conn: sqlite3.Connection, company_id: str, verdict: str) -> None:
    """The single write. Same table and field name the Sheet uses, so the two
    surfaces cannot disagree and `tune.py` picks it up unchanged."""
    conn.execute(
        """INSERT INTO user_field(company_id, field, value, updated_at)
           VALUES (?, 'verdict', ?, ?)
           ON CONFLICT(company_id, field)
           DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (company_id, verdict, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def make_handler(conn: sqlite3.Connection):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:          # noqa: N802 - stdlib naming
            if self.path in ("/", "/index.html"):
                self._send(200, (HERE / "index.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif self.path in ("/onboarding", "/onboarding.html"):
                self._send(200, (HERE / "onboarding.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif self.path == "/tokens.css":
                self._send(200, (HERE / "tokens.css").read_bytes(),
                           "text/css; charset=utf-8")
            elif self.path.startswith("/api/today"):
                payload = json.dumps(build_today(conn), default=str).encode()
                self._send(200, payload, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:         # noqa: N802
            if not self.path.startswith("/api/verdict"):
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            company_id, verdict = body.get("company_id"), body.get("verdict")
            if not company_id or verdict not in (
                    "worth contacting", "not for me", "unsure"):
                self._send(400, b'{"error":"bad verdict"}', "application/json")
                return
            set_verdict(conn, company_id, verdict)
            self._send(200, b'{"ok":true}', "application/json")

        def log_message(self, *args) -> None:      # quiet
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/demo.db")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    conn = _conn(args.db)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(conn))
    print(f"Today prototype on http://127.0.0.1:{args.port}  (db: {args.db})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
