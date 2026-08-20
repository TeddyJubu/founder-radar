"""The register-derived demo database (prototype/TESTING.md §0.2).

One recipe, three callers: the Cloud Agent Today server, the browser suite,
and a human rebuilding `/tmp/demo.db`. All of them go through `build()` so a
fixture or pipeline change cannot make CI pass while the demo terminal serves
a different dataset.

No pytest, no network, no API key. The HTTP double serves the committed
Companies House payloads; `scripts/seed_demo_db.py` is the CLI wrapper.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from radar.config.defaults import default_config
from radar.pipeline import enrich_stage, resolve_item, score_company
from radar.sources.base import FetchContext
from radar.sources.companies_house import CompaniesHouseAdapter
from radar.store.db import Db

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api"

#: The fixtures are a coherent set built around 06-scoring §11's worked
#: example: METZERO LIMITED, 15021884, incorporated 2026-06-14 in Newcastle,
#: SIC 62012, SH01 filed 2026-07-30.
TODAY = date(2026, 8, 8)


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
        self.text = json.dumps(payload)

    @property
    def ok(self):
        return 200 <= self.status < 400

    def json(self):
        return self._payload


class MockCompaniesHouse:
    """Every Companies House and postcodes.io endpoint, served from fixtures.

    Routing by URL rather than by call order, because the enrichment passes
    interleave: officers, PSC, prior appointments and filing history are
    fetched per company, and asserting an order would pin an implementation
    detail rather than a contract.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, **kw):  # noqa: ARG002 - the adapter's http protocol
        self.calls.append(url)
        if "/advanced-search/companies" in url:
            return FakeResponse(_fixture("ch_advanced_search_page.json"))
        if "postcodes.io" in url:
            outcode = url.rstrip("/").rsplit("/", 1)[-1].upper()
            table = _fixture("postcodes_io_outcodes.json")
            body = table.get(outcode)
            if body is None:
                return FakeResponse({"status": 404, "error": "not found"}, status=404)
            return FakeResponse(body)
        if "/persons-with-significant-control" in url:
            return FakeResponse(_fixture("ch_psc.json"))
        if "/appointments" in url:
            return FakeResponse(_fixture("ch_officer_appointments.json"))
        if "/officers" in url:
            return FakeResponse(_fixture("ch_officers_with_dob.json"))
        if "/filing-history" in url:
            return FakeResponse(_fixture("ch_filing_history_sh01.json"))
        return FakeResponse({}, status=404)

    def count(self, fragment: str) -> int:
        return sum(1 for c in self.calls if fragment in c)


def build(path: str) -> int:
    """Sweep, enrich and score the fixture companies into `path`. Returns count.

    Closes the connection before returning so a caller can open the same WAL
    database from another process (the Today server, Playwright).
    """
    db = Db(path)
    try:
        db.migrate()
        cfg = default_config()
        http = MockCompaniesHouse()
        adapter = CompaniesHouseAdapter(api_key="demo", days_back=90, window_days=90)
        for item in adapter.fetch(FetchContext(http=http, config=cfg, db=db, now=TODAY)):
            resolve_item(db, item, cfg)
        enrich_stage(db, cfg, http, api_key="demo")
        for row in db.query("SELECT id FROM company"):
            score_company(db, row["id"], cfg, today=TODAY)
        return db.scalar("SELECT COUNT(*) FROM company")
    finally:
        db.close()
