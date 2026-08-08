# Privacy Notice — UK Founder Radar

**Last updated: 7 August 2026**

This notice explains what personal data the UK Founder Radar crawler collects, why,
and how to have it removed. The URL of this page is published in the crawler's
User-Agent string, so anyone who sees our requests in their server logs can find it.

> **Contact for any question or request below:** *(set this to a monitored address
> before launch — the same address that appears in the User-Agent.)*

---

## Who we are

UK Founder Radar is a private research tool operated by a single individual. It
identifies newly formed UK technology companies for the purpose of introducing them
to UK venture capital funds. It is not a public service, a published database, or a
product sold to third parties.

## What we collect

We collect information about **companies**, and a deliberately minimal amount about
the **people who found them**.

**About a person, we store only:**

| Field | Why |
|---|---|
| Name | To identify who founded the company |
| Role (e.g. "Co-founder", "CTO") | To distinguish founders from other officers |
| Public professional profile URL | Only where it is already publicly linked |
| Whether they are a Person with Significant Control | A matter of public record at Companies House |
| Date of appointment | Establishes when the company was formed |
| Count of prior directorships | A number only — never the underlying company list |
| The source URL the information came from | So every fact can be traced |

**We never store**, and the database has no column for:

- email address
- telephone number
- home address or correspondence address
- date of birth — **including the partial month-and-year date of birth that
  Companies House returns on its officers endpoint**, which we discard at the point
  of ingestion rather than hiding at display time
- nationality
- country of residence

## Where it comes from

Public sources only: the Companies House public register and its API, UK Research
and Innovation grant award data, GOV.UK announcements, university technology-transfer
office announcements, accelerator cohort pages, and publicly published UK technology
news. We do not scrape behind a login, bypass a paywall, or circumvent an anti-bot
challenge. Where a site's `robots.txt` disallows a path, we do not fetch it.

## Our lawful basis

**Legitimate interests** (UK GDPR Article 6(1)(f)). Our assessment is that
introducing early-stage companies to investors is a legitimate commercial activity;
that the processing is limited to business-context information about people acting
in a professional capacity; and that the minimal fields listed above have a low
impact on the individuals concerned. The full assessment is published alongside this
notice at [legitimate-interests.md](legitimate-interests.md).

## How long we keep it

Company records are retained while the company remains relevant. Records about
founders of companies that have been rejected for more than **12 months** are purged
automatically.

## How we protect it

The database sits on a single access-controlled server. Credentials are held in a
file readable only by the service account. The derived spreadsheet is restricted to
named accounts with link-sharing switched off.

## Your rights

You may ask us to confirm what we hold about you, correct it, restrict how we use
it, object to the processing, or erase it.

**To be removed, contact the address at the top of this notice.** Erasure is applied
immediately and is recorded in a suppression list, so a later crawl of the same
article will not reinstate your details.

You may also complain to the Information Commissioner's Office at
[ico.org.uk](https://ico.org.uk).

## Automated decision-making

Companies are scored automatically. The scoring is arithmetic over company
attributes — sector, stage, location, age, funding status. **No score is computed
about a person, and no decision with a legal or similarly significant effect on an
individual is made by this system.**
