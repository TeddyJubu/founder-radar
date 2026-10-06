# Local n8n validation — October 6, 2026

## Scope

Prepared on the isolated dot cloud computer from Founder Radar commit
`60f20673d1690a6be95144bc8a18e54f797d7a89` (September 30), local branch
`dot/n8n-local-orchestration-20261006`. Nothing was pushed, deployed, scheduled,
or run on the production VPS or user's Mac. The reported October 3 production
cache patch was not available in this GitHub base and must be reconciled before
any later rollout.

Official npm n8n **2.42.3** was installed separately from the repository and
verified with Node **24.19.0**. The real n8n CLI imported and executed the mock
workflow. No UI owner password or real API credential was provisioned. The
workflow exports remain inactive with the schedule disabled.
No persistent n8n web server was started or left running; there was no interactive
editor QA or UI screenshot. The local validation processes exit after each run.

## Checks

- Original repository baseline: **1,041 passed, 1 skipped, 132 deselected**
- Modified repository offline suite: **1,115 passed, 1 skipped, 132 deselected**
- Focused Actions API/reliability tests: **92 passed** (included above)
- Separate workflow graph, expression, and fixture tests: **13 passed**
- CI guards, Python compilation, OpenAPI contract inspection, and whitespace
  diff checks passed
- No browser suite was run: the Today/Kept UI was not changed
- No live, paid-LLM, production integration, or performance suites were run

The real n8n engine passed these nine synthetic scenarios:

| Scenario | Observed outcome | Publish starts |
| --- | --- | ---: |
| Successful scan | Gated publish fixture completed; explicit no-Telegram result | 1 |
| Partial scan | Stopped for operator review | 0 |
| Failed scan | Stopped for operator review | 0 |
| Interrupted scan | Stopped for operator review; no resubmission | 0 |
| Scan never finishes | Stopped at bounded polling limit | 0 |
| Unknown scan status | Stopped with invalid/polling-stopped result | 0 |
| Publish gate failure | Stopped for operator review | 1 |
| Publish never finishes | Stopped at bounded polling limit | 1 |
| Start conflict | HTTP step failed after three same-key attempts | 0 |

Every publish request contained JSON `send:false`. Normal scenarios made one
scan-start request; the conflict case reused the same idempotency key for all
three HTTP attempts. The fixture is a separate loopback-only program that never
imports Radar or calls its CLI. All data and job outcomes in this table are
synthetic. This validates orchestration, not the production scan or QA quality.

The authenticated real-API template also imported successfully. Attempting to
execute it unconfigured was rejected by n8n's credential preflight because the
placeholder credential does not exist. No workflow nodes ran. This is the
expected setup boundary, not an authenticated production integration test.

## Resource observations

Two complete nine-scenario mock runs sampled approximately **432–475 MiB peak
process-tree RSS**, including their local n8n runners. Runtime per execution was
about **7–13 seconds**, excluding import and first-time schema setup. Installed
npm dependencies used about **2.9 GiB disk**; the installation cache used another
**1.3 GiB**.

These are small synthetic local measurements, not steady-state server load,
production scans, concurrent workflows, or a guarantee of fit on an 8 GB VPS.
No production capacity conclusion was drawn.

## Reproduce and limitations

See [setup and mock harness instructions](n8n-integration.md). Raw n8n state and
execution logs were kept outside the repository and are not part of the export
or source bundle. They can contain internal execution/resume material.

The API's lock serializes API mutations only. Direct CLI/systemd writers are not
covered. Job records must be retained for retry deduplication. Interrupted work
is never automatically replayed, but prior external effects may be uncertain.
Short n8n Wait nodes do not provide a promised crash-resume mechanism. Real
credentials, production-version reconciliation, service permissions, scheduler
cutover, live QA/Sheet behavior, and Telegram delivery remain untested and require
a separately authorized rollout.
