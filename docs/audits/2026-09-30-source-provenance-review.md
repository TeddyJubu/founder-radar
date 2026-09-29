# Source and provenance repair — 30 September 2026

The local review used bounded read-only public GETs with the shared HTTP
client, respecting robots and noindex. No production database was modified;
source-health history was not reset. Endpoint availability is not proof of a
balanced or high-quality current Today list.

## Actual verified sources

| Source | Actual endpoint result | Parser result after repair |
| --- | --- | --- |
| BusinessCloud | https://businesscloud.co.uk/feed/ — 200 | 10 items |
| UKTN | https://www.uktech.news/ — 200 | 18 unique article cards, 12 stated/slug dates |
| Startups Magazine | https://startupsmagazine.co.uk/articles — 200 | 23 unique dated article cards |
| Converge | https://www.convergechallenge.com/feed/ — 200 | 10 dated feed entries |
| Sheffield | https://www.sheffield.ac.uk/commercialisation/commercialisation-news — 200, redirects to non-www | 44 cards |
| Edinburgh Innovations | https://www.edinburgh-innovations.ed.ac.uk/news — 200, redirects to non-www | 12 cards |
| Carbon13 | https://carbonthirteen.com/feed/ — 200 | 10 entries |
| Bdaily North East | https://bdaily.co.uk/region/north-east/rss — 200 | 13 entries |
| Techstars | https://www.techstars.com/newsroom — 200 | 12 global cards; no UK assumption |
| Founders Factory | https://foundersfactory.com/articles/ — 200 | 10 articles |
| Entrepreneur First | https://www.joinef.com/portfolio/ — 200 | 48 London cards; 22 explicitly funded alumni are denial evidence |
| Bethnal Green Ventures | https://bethnalgreenventures.com/portfolio — 200 | 152 invested portfolio cards; all are denial evidence |
| Northern Accelerator | https://northernaccelerator.org/wp-json/wp/v2/posts — 403 | Unverified; do not mark healthy |

The previous UKTN, Startups Magazine and Converge API endpoints were refused
because their responses carry `X-Robots-Tag: noindex`. Repairs use permitted
public routes, not a policy bypass. Startup Magazine's own explanation of its
redesign is https://startupsmagazine.co.uk/startups-magazine-unveils-new-website.
Feed/listing excerpts are not full articles. Neither UKTN nor Startups Magazine
asserts a UK company location merely because the publication is British.

## Honest Innovate UK evidence

The official publication returned 200:
https://www.ukri.org/publications/innovate-uk-funded-projects-since-2004/.
It currently links the real workbook (independent bounded GET returned 200):
https://www.ukri.org/wp-content/uploads/2026/09/IUK-030926-20260902-Innovate-UK-funded-projects-from-financial-year-2016-17-to-present.xlsx.

New records cite the workbook actually discovered on that publication. The
fragment `#project=REF&participant=NAME` is a human lookup aid, not a claimed
spreadsheet deep-link. Structured evidence also preserves the publication
name, participant and grant reference. Workbook project numbers are not GtR
UUIDs and must never be turned into guessed `gtr.ukri.org/projects?ref=` links.

Preview historical repair against an explicit backed-up database:

```
python -m radar.qa.provenance --db /absolute/path/to/radar.db
```

Review the returned exact row changes and hash. To apply that same plan:

```
python -m radar.qa.provenance --db /absolute/path/to/radar.db --apply --plan-hash REVIEWED_HASH
```

This does not migrate or fetch anything. It only replaces Innovate UK's
fabricated GtR citations in company_source, signal and observation with the
relevant permanent publication plus project/participant lookup. It preserves
evidence values, external IDs and the QA audit trail. It clears affected
snapshot approvals; the changed card hash requires new verification. A changed
plan or mismatched approval hash aborts the entire transaction. Genuine
ukri_gtr citations remain unchanged.

## Verification API and integration

`radar.qa.provenance.verify_source(db, url)` performs GET verification outside
scoring and records a URL-specific outcome. It reads response headers without
downloading a whole workbook, follows at most four redirects, validates public
HTTP(S) destinations before following, and respects robots/rate limits.
`cached_outcome(db, url)` only reads the database; expired/missing means unknown.
Good results expire in 24 hours, other outcomes in one hour.

- reachable: GET returned 2xx; usable HTTP destination, not proof of factual correctness.
- dead: only HTTP 404 or 410; definitive missing-page evidence.
- invalid: malformed, credential-bearing, private or non-web URL; cannot be used.
- blocked: robots, HTTP 401/403/429/451; actual reachability remains unknown.
- timeout/error: verification incomplete, never described as a dead link.

QA must check the link before accepting even a cached company pass. Reject
only definitive dead/invalid links; hold blocked/transient links incomplete.
Render/read paths must use the cached outcome and must not introduce network
calls into deterministic scoring. Integration hooks are owned separately.

## Sector and source quality boundaries

The heuristic reader now recognises explicit legaltech, proptech,
cybersecurity, HR software and data infrastructure product labels within the
named company's own short description. It does not copy investor-sector prose,
assume generic software is SaaS, or invent labels for a balanced-looking list.

Entrepreneur First's live first external links were founder LinkedIn profiles.
Its actual Founded/Funded by fields also identify old invested companies such
as Tractable. The parser now reads those fields, excludes social identity
links, removes an unsupported pre-seed assumption and sends explicit funded
records to the existing denylist path. Historical stored company values need
a separately reviewed re-enrichment/repair; code changes do not rewrite them.

Live Today still needs measurement by distinct company: displayed/eligible
source shares (including any UKRI evidence, not just primary source), sector
and unknown-sector shares, winning fund, and each displayed primary link's
verification result. Adapter counts above do not prove those outcomes.

## Reviewed fingerprint transition

Changing from JSON to HTML/RSS deliberately changes the structural guard.
If production already remembers an API fingerprint, the next fetch will
correctly report `layout_changed` until an operator accepts the reviewed new
shape with `radar.fetch.layout.accept_fingerprint(db, key, fingerprint)`.
Do not delete source-health history or blindly accept a failing response.
These fingerprints came from the successful actual captures above:

| Source | Reviewed new fingerprint |
| --- | --- |
| startups_magazine | 6cca2a698ec97cee |
| uktn | a5033e6dfaf4064a |
| converge | d7250bd328be69a6 |

Re-read a current successful response before applying if the deployment is
later; the page structure may have changed in the meantime.

BGV's official portfolio is an invested portfolio, not an uninvested cohort.
Its own offer says every portfolio company begins with a £60,000 equity
investment for 7%: https://www.bethnalgreenventures.com/our-offer. The active
and exited portfolio therefore both feed denial evidence; no new companies
are created from it. Existing matched companies are flagged on a future scan.

## Follow-up after live run 66

UKTN article bodies now use `div.js-post-content`. Three permitted live article
GETs returned HTTP 200 and extracted 3321, 3215 and 3041 characters on 30 September.
Index selector fingerprint remains `a5033e6dfaf4064a`.

Companies House's official advanced-search specification explicitly documents
HTTP 404 as "No companies found":
https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/advanced-company-search
A read-only request with the configured production key to that exact API host,
for today's active ltd SIC 62012 window, returned empty-body 404. A controlled
MONZO name query to the same endpoint/key returned JSON 200 with 14 hits. Only
advanced-search 404 is now an empty window; other endpoint/status errors remain errors.

Cambridge Enterprise's public `https://www.enterprise.cam.ac.uk/feed/` returned
HTTP 200 without a noindex response directive, through the robots-respecting
client. It parsed 150 dated items. The adapter now reads this permitted RSS
instead of the noindex JSON API and requires individual article extraction.
Its reviewed replacement fingerprint is `d7250bd328be69a6` for adapter ID
`cambridge_enterprise`; accept this reviewed transition before the next scan.
No noindex/robots restriction was bypassed. No live scan or data writes ran.

## Article hydration at the fetch boundary

The `full_text_in_feed=False` flag is now consumed before extraction for news
and spinout sources. Source-specific wrappers collect substantive article text;
there is no whole-page/navigation fallback. Cambridge collects its separate
`main section.block--text div.prose` blocks. Already-complete feed bodies and
already-hydrated items avoid duplicate requests. UKTN now uses this same boundary.

Each source gets at most 20 articles, each GET has an eight-second timeout and
no retry backoff. Up to three redirects are followed explicitly, with public
address validation and robots checks at every destination. Meta/header noindex
remains binding. A bare 304 gets one unconditional retry; a second 304 is
withheld rather than substituting the excerpt. Unreadable/blocked articles are
withheld individually and reported as degraded without losing healthy neighbors.
An exhausted article budget produces a warning, not a broken-source heartbeat.
Other source kinds (registry/grant/portfolio/accelerator) retain their existing
structured extraction path; this change does not invent company facts or add AI calls.

Read-only real article verification on 30 September extracted:

- BusinessCloud: 1790 characters from its Monzo/Nubank takeover article.
- Bdaily: 3746 characters from its Gateshead flyover-demolition article.
- Startups Magazine: 1468 characters from its future-of-the-magazine article.
- Cambridge Enterprise: 4479 characters from its biotechnology-ecosystems article.

These prove text retrieval, not that these particular stories are qualifying
startups. The normal company extraction and fund gates still make that decision.
147 targeted regressions passed across hydration, sources, source registry,
Companies House, provenance, source safety and phase-eight sources.
