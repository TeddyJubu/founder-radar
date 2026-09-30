# Techscaler Catalyst: discovery source audit

Checked 30 September 2026 against official public institutional pages:

- Resources index: https://www.techscaler.co.uk/resources — HTTP 200, 948,865 bytes.
- Current introduction: https://www.techscaler.co.uk/resources/introducing-the-2026-autumn-catalyst-cohort — HTTP 200, 274,438 bytes; publication **29 September 2026**.
- https://www.techscaler.co.uk/robots.txt — HTTP 200; sitemap declaration, no disallow rules. This is an access check, not a blanket licence to reproduce the website.

The article explicitly names participants under **Meet the cohort:**, after an alumni section and before **Up next**. The adapter reads only that named list. Alumni Husu, Nuuri and XYNQ are excluded; duplicate MADGenesis Limited is emitted once; N/a and Nil are rejected. Ambiguous displays such as `AURA / Windsor Brain`, `FutureTherma Labs (SAHP HUB LTD)` and the proposed Baltic Comfort rename remain exactly as stated. They are not silently converted into legal names.

Each item represents a company, retaining the actual article URL and publication date plus program/cohort metadata. A matching company-labelled anchor inside that cohort section can be retained as link evidence. The September article's current-cohort list has no such company links; alumni links must not be borrowed. Programme membership does not prove Scotland/UK headquarters, Companies House identity, funding status, trading age, stage or sector. These remain unknown. There is no name-only Companies House lookup in this adapter.

The index parser considers dated introductions whose titles begin `Introducing` or `Meet` and explicitly contain `Catalyst` then `cohort`. It does not treat application announcements, alumni stories or programme recaps as current company lists. Requests use the existing polite, robots-aware HTTP client. At most five introductions are read per run, with a 1 MB streaming transport cap and a second parse-size guard, eight-second request timeout, no retries and no automatic redirects. Malformed articles record a degraded source result rather than a healthy zero; missing index structure raises a layout-change result. Recent-list coverage is bounded to the public resources index, not a promise of historical completeness.

## Activation and verification

The registry key is `techscaler_catalyst`, Track **A**, accelerator, weekly. New default configurations include its Sources row enabled. Existing production Sheets are durable user configuration and are not changed by this commit: the owner must add the exact key with Track `A` and Enabled `TRUE` to the Sources tab, then load/validate settings through the normal workflow. An existing false toggle remains the owner's choice.

Use `founder-radar sources --test techscaler_catalyst` for a bounded source check. Then run the supported scan/resolve workflow and completed Today QA before publication. This feed increases genuine discovery inputs; it cannot guarantee all listed ventures resolve, qualify for a fund or produce a balanced Today quota. It is Scottish programme discovery, not a replacement for independently evidenced North East/Yorkshire eligibility.

Fixtures are minimal real HTML excerpts retaining the current header/date, named section, alumni and boundaries. Offline regressions cover those facts, placeholders/duplicates, exact anchor association, since filtering, unknown attributes and degraded malformed layouts. No production data, credentials, decisions or candidate admissions are changed by the source implementation.
