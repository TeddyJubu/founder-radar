# Source evidence supplied to the final company check

The final checker now sees the stored Companies House number and raw SIC activity codes. Unknown values stay unknown. A link to the official record lets the checker verify identity; it does not claim the record was freshly fetched. A registered office does not establish the company's headquarters. SIC codes are not guessed product categories.

For Innovate UK, older cards contain only the saved signal label, amount and date, plus the project/participant lookup fragment from their citation. Those fragments help find a row; they do not prove that the full workbook row was preserved. The payload says this explicitly.

New ingestion preserves a small receipt from the official adapter's actual parsed participant row. The project, normalized participant name and Companies House number must all match the existing company/source association. The receipt is at most 2 KB. It keeps the real workbook URL, row fields and missing values, with the observation time when saved. Project descriptions are bounded project-level context, possibly describing a consortium; they are never assigned to a company's product, sector, stage, funding or headquarters. The checker remains free to reject the wrong entity.

The current card citation and historical workbook download URL are separate. An old workbook URL can be replaced by UKRI; storing it is provenance, not a claim that it remains reachable. The normal source-link verifier still checks the chosen card citation.

An owner can refresh receipts for existing cards using one official current workbook download, parsed by the existing standard-library adapter:

```sh
founder-radar refresh-source-evidence
founder-radar refresh-source-evidence --company-id EXISTING_ID
```

Both commands preview matches without writing. Review the project, participant, company number and workbook URL first. Then explicitly apply the same targeted operation:

```sh
founder-radar refresh-source-evidence --company-id EXISTING_ID --apply
```

Without explicit IDs the command targets up to 500 current Innovate UK candidate cards. It reads the entire workbook stream but constructs items only for the targeted identities. It matches the selected card's project, so another legitimate grant for that company does not create a false ambiguity. It never imports the workbook's other companies. Zero matches or duplicate rows for the selected project leave the receipt unknown. Apply adds evidence observations only; it does not change company facts, scores, signals or saved decisions. A changed receipt changes the card snapshot, so an earlier approval cannot authorize it. Run the normal genuine `today-qa` afterward before publishing. This operation downloads source evidence and may take time; it is not a daily review action.
