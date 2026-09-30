# Verify signal-first company identities

A news source may name a company without supplying its Companies House number.
The enrichment stage now checks an explicitly linked company website before the
ordinary registry enrichment queue. It does not search the web or match a common
brand against registry search results.

The source's company anchor label must match the named company. The company's
own site must explicitly state its UK legal registration number and identity;
an active Companies House profile must confirm the same number and legal name.
A different brand requires an explicit trading-name relationship on that site.
VAT numbers, telephone numbers, third-party/client examples and conflicting
registrations are not accepted. Existing company IDs and saved decisions remain
unchanged. A number already attached to another company is left for manual review.

Verified registration establishes incorporation age, not operating headquarters.
Country/HQ fields, funding, stage and sectors are preserved, including unknowns.
Registered-office evidence is stored separately. Companies missing real UK-HQ
proof may therefore remain ineligible after a successful identity verification.

Website requests respect robots/noindex and public-address checks on each
same-domain redirect. Each company reads at most its linked page and three legal
pages; each download is capped at 1 MB with eight-second request timeouts and no
retries. The shared enrichment request budget counts those GETs and the registry
profile GET. Failed attempts remain unknown, retain a reason and retry after
24 hours; at most 20 companies are attempted in a normal run. Optional unavailable
legal pages are recorded without discarding a valid primary identity statement.

For existing news companies, preview a bounded collection of source links and
verification without changing the database:

```sh
founder-radar --json verify-identities --collect-links --company COMPANY_ID --limit 1 --request-budget 20
```

The preview reads HTTP evidence into an in-memory database copy. After reviewing
its result and taking the normal database backup, add `--apply` to persist the
same scoped operation. Read the returned `verified_company_ids`; scoring/QA and
publication are separate operations and are not triggered by this command. Only
currently reviewed news article wrappers are used for historical link collection,
with at most two cited articles per company. Older pages lacking a correctly
labelled company link remain unknown.

No new cohort source is activated by this change. Techscaler participant names
alone are not sufficient registry identity evidence, and programme participation
is not operating-HQ proof.
