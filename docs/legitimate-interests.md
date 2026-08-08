# Legitimate Interests Assessment — UK Founder Radar

**Completed: 7 August 2026 · Review: annually, or on any material change to the sources**

A legitimate interests assessment is the three-part test the UK GDPR requires before
Article 6(1)(f) can be relied on. It is short because the processing is narrow.

---

## Part 1 — Purpose test: is there a legitimate interest?

**The interest.** Identifying newly incorporated UK technology companies and
introducing them to venture capital funds whose published mandates they match.

**Who benefits.**

- *The operator*, who is establishing themselves as a scout for four UK funds.
- *The funds*, whose stated business is finding early-stage companies, and several
  of whose vehicles are publicly funded with a regional development mandate.
- *The companies and their founders*, who are seeking investment. Being surfaced to
  a matching investor is the outcome an early-stage founder is actively pursuing.

**Would anything be lost if we did not do this?** Yes — the specific value of the
system is surfacing companies that funds have *not* already seen. The alternative,
reading funds' own portfolio pages, produces only companies that already raised.

**Assessment: legitimate.** This is ordinary commercial activity, lawful, and not
contrary to any published expectation of the sources used.

---

## Part 2 — Necessity test: is the processing necessary?

The purpose is to tell a fund about a company. A company is not a useful
introduction without knowing **who founded it** and **whether they have built
anything before** — a repeat founder is one of the strongest signals in early-stage
investing, and it is precisely the signal a fund asks about first.

**Could we achieve the purpose with less personal data?** We tested that question
field by field, and the answer shaped the schema:

| Field | Necessary? | Decision |
|---|---|---|
| Name | Yes — an unnamed founder cannot be introduced | Stored |
| Role | Yes — distinguishes a founder from a company secretary | Stored |
| Public profile URL | Yes — lets the fund verify the person themselves | Stored, only where already public |
| PSC status | Yes — distinguishes a founder from a nominee director | Stored (public register fact) |
| Prior directorship **count** | Yes — this is the repeat-founder signal | Stored as a **number only** |
| The prior companies themselves | **No** — the count carries the signal | **Not stored** |
| Date of birth (incl. partial month/year) | **No** — contributes nothing to the purpose | **Discarded at ingest** |
| Correspondence address | **No** | **Discarded at ingest** |
| Email, phone | **No** — outreach is the operator's own manual step | **Never collected** |
| Nationality, country of residence | **No** | **Never collected** |

Discarding happens **in the source adapter, before the database write**, not at
display time. The `founder` table has no column for any of the rejected fields, and
the sanctioned insert function has no parameter for them, so this is enforced by
the schema rather than by anyone remembering.

**Assessment: necessary, and minimised.** Five fields, each with a stated reason,
and the most sensitive item Companies House offers is refused.

---

## Part 3 — Balancing test: do the individual's rights override the interest?

**Reasonable expectations.** Every field comes from a source the individual, or
their organisation, published deliberately: a filing on the public register, a
university announcing its own spinout, an accelerator announcing its cohort, a news
article about a funding round. A founder who has announced their company expects
investors to read that announcement.

**Nature of the data.** Business-context information about people acting in a
professional capacity. No special-category data. No financial, health, location or
behavioural data about any individual.

**Likely impact.** Low. The plausible outcome is an investor introduction — which is
what an early-stage founder is seeking. There is no profiling of the individual, no
score attached to a person, no automated decision affecting them, and no
disclosure to any party beyond the four named funds.

**Would they object?** Some might prefer not to be contacted, which is why erasure
is one message and is permanent (see below). We consider the residual objection risk
low and adequately mitigated.

**Assessment: the interest is not overridden.**

---

## Safeguards actually implemented

These are not intentions; each is a line of code or a published file.

1. **Field minimisation enforced by the schema.** `tests/unit/test_schema_privacy.py`
   fails the build if a forbidden column ever appears on the `founder` table.
2. **Discard at ingest.** Dates of birth and correspondence addresses returned by
   the Companies House officers endpoint are dropped in the adapter.
3. **One-command erasure that survives re-ingestion.** `founder-radar forget "<name>"`
   deletes the rows and writes a suppression entry that every ingest path checks, so
   tomorrow's crawl of the same article cannot reinstate the person.
   `tests/unit/test_privacy.py::test_forget_removes_and_suppresses` proves it.
4. **Retention limit.** Founders of companies rejected more than 12 months ago are
   purged.
5. **Published privacy notice**, whose URL is carried in the crawler's User-Agent so
   any site operator seeing our traffic can find it.
6. **Honest crawler conduct.** `robots.txt` obeyed via Protego, `Crawl-delay`
   honoured with a one-second floor, no login bypass, no paywall bypass, no anti-bot
   circumvention. A site presenting a challenge page has stated that automation is
   unwelcome, and the response is to drop that source.
7. **Access control.** Database on a single access-controlled server; the derived
   spreadsheet restricted to named accounts with link-sharing off.

---

## Outcome

**Article 6(1)(f) may be relied on for this processing**, subject to the safeguards
above remaining in place.

**Re-run this assessment if any of these change:** a new source category is added
(particularly anything behind authentication), any additional personal data field is
introduced, the data is shared beyond the four named funds, or the output is
published rather than shared privately.
