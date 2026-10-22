# Founder Radar in n8n

This integration makes the orchestration visible in a real n8n canvas while keeping
source adapters, scoring, QA, the database, and Sheet sync in the existing Python
application. It does not replace Founder Radar or split one scan into pretend
executable stages.

**Default safety:** both exports are inactive, the London schedule is disabled,
and `send_telegram` is false. The real template has a deliberately invalid host
and a credential placeholder. Only the separate loopback fixture was designed to
run without credentials. Nothing here deploys, changes systemd, or enables a
production schedule.

## Files

- [`founder-radar.workflow.json`](../integrations/n8n/founder-radar.workflow.json):
  authenticated real-API template
- [`founder-radar.mock.workflow.json`](../integrations/n8n/founder-radar.mock.workflow.json):
  same visible orchestration against synthetic loopback fixtures
- [`generate_workflows.py`](../integrations/n8n/generate_workflows.py): deterministic
  generator; edit this, regenerate, then review the JSON diff
- [`mock_actions_server.py`](../integrations/n8n/mock_actions_server.py): standalone
  Python-standard-library fixture; never imports or executes Radar
- [`test_workflows.py`](../integrations/n8n/test_workflows.py): offline graph, JavaScript
  condition, safe-default, and fixture-contract tests
- [`run_mock_demo.py`](../integrations/n8n/run_mock_demo.py): import and execute with
  an installed real n8n engine, assert request traces, and save evidence

## What the canvas actually executes

1. Manual Trigger, or the currently disabled 06:30 Europe/London Schedule Trigger
2. Edit Fields (`Configure`) and an explicit safe-configuration check
3. HTTP `POST /v1/jobs/search`, using a per-operation idempotency key
4. Validate the accepted job ID, save its deadline, Wait, then HTTP poll the same
   `GET /v1/jobs/{id}`
5. Branch on the returned job state:
   - `done` **and** exit code 0: optionally continue to publish
   - `partial`: preserve output and stop for operator review
   - `failed` or `interrupted`: preserve output and stop for operator review
   - `queued`/`running`: poll again only within the count and time limits
   - unknown/malformed state, or exhausted polling budget: stop with an honest
     “polling stopped” result, without calling the job successful
6. If `publish_enabled` is true, HTTP `POST /v1/jobs/publish` with
   `{"send": false}` by default; the existing CLI performs the publish gate
7. Wait/poll the publish job with the same terminal-state handling
8. A result node retains the actual job payload and returned dashboard URL

All executable nodes are built in: Manual Trigger, Schedule Trigger, Edit Fields,
If, HTTP Request, Wait, and Stop And Error. Sticky Notes explain the Python
pipeline and failure handling. There is no Execute Command, Code, community node,
Telegram node, or embedded secret.

A successful `send:false` run reports `publish_checks_passed_no_telegram`. It does
not claim that a Telegram message was sent, a new company was found, or the
contents of the dashboard are verified beyond the API output.

## Important side effects

`search` already runs the full pipeline, writes SQLite, runs Today QA, and syncs
the configured Google Sheet. Calling the real search endpoint is a real scan,
not a dry run. It can contact source sites and paid services according to the
application's configuration.

`publish` runs pre-publish checks/repair, Today QA, a second gate, and renders the
dashboard ping. Even `send:false` can heal/rescore, change database state, sync
Sheet changes, or call Hermes. Only `send:true` requests the Telegram ping.
`no_llm` controls the search CLI's extraction setting; it is not a guarantee that
all other parts of the real pipeline are free or side-effect-free.

The n8n workflow deliberately does not call `/v1/today-qa` or
`/v1/publish-check` separately, because those responsibilities are already inside
search/publish. Failures are never papered over by bypassing the CLI gate.

## Versions and schema

Prepared against the official npm package **n8n 2.42.3**, on Node **24.19.0**.
Its installed built-in node definitions were inspected for these supported
versions: Manual Trigger 1, Schedule Trigger 1.2, Set 3.4, If 2.2, HTTP Request
4.2, Wait 1.1, Stop And Error 1, and Sticky Note 1. Explicit supported versions
keep these exports stable even when n8n's newest node defaults change.

The exports contain no instance-specific workflow ID, owner, production URL,
credential value, or pinned result data. Deterministic node IDs make regeneration
reviewable. n8n's [import/export documentation](https://docs.n8n.io/build/manage-workflows/export-and-import.md)
explains JSON workflow import and warns that exports can contain credential
references or sensitive node parameters. These exports have only an obvious
placeholder credential reference.

## Run the isolated mock

The verified local installation used the official npm package pinned to 2.42.3
and Node 24.19.0. To reproduce in a separate local directory (replace these
absolute paths with directories on the chosen computer):

```bash
npm install --prefix /absolute/path/to/n8n-local --save-exact n8n@2.42.3
/absolute/path/to/n8n-local/node_modules/.bin/n8n --version
```

The CLI harness below is sufficient for automated fixture validation and does
not require an owner password. For an optional interactive canvas demonstration,
start n8n in its own separate data directory, bound only to loopback:

```bash
N8N_USER_FOLDER=/absolute/path/to/n8n-demo-data \
N8N_LISTEN_ADDRESS=127.0.0.1 N8N_HOST=localhost \
N8N_DIAGNOSTICS_ENABLED=false N8N_VERSION_NOTIFICATIONS_ENABLED=false \
N8N_TEMPLATES_ENABLED=false N8N_PUBLIC_API_DISABLED=true \
  /absolute/path/to/n8n-local/node_modules/.bin/n8n start
```

Open `http://localhost:5678` in the browser on that same computer. The operator
completes n8n's owner-account setup themselves; do not put passwords into chat,
workflow JSON, or this repository. Start the loopback fixture described below,
import only the mock export, and use the Manual Trigger. Stop the local n8n
process when finished. No public service or reverse-proxy setup is needed.

The delivered version was validated through the CLI, not the interactive editor:
no persistent n8n UI server, owner login, UI screenshot, or interactive UI QA is
claimed. See the [validation record](n8n-validation-2026-10-06.md).

The fixture binds only `127.0.0.1`. It accepts the search/publish job shapes, returns
synthetic job status, requires an idempotency key, and rejects any publish without
explicit JSON `send:false`. It has no outbound HTTP client, Radar invocation,
Google integration, Telegram integration, or provider credentials.

From the repository root:

```bash
python integrations/n8n/generate_workflows.py
python integrations/n8n/test_workflows.py

# Use a separately installed official n8n executable. State/logs stay outside the repo.
python integrations/n8n/run_mock_demo.py \
  --n8n /absolute/path/to/n8n \
  --output-dir /absolute/path/to/disposable-n8n-evidence
```

The harness imports before executing by workflow ID, uses an isolated n8n user
folder, starts only loopback fixtures, and records results and request traces.
It does not install software or create a real API credential. Use `--scenarios
done partial failed publish-failed stuck` for a smaller targeted check; the
default covers all fixture scenarios.

For a manual canvas demonstration, run this server on the **same host/network
namespace as n8n**, import `founder-radar.mock.workflow.json`, leave its schedule
disabled, and execute the Manual Trigger:

```bash
python integrations/n8n/mock_actions_server.py \
  --port 18790 --scenario done --trace /tmp/founder-radar-mock-requests.jsonl
```

If n8n is in a different container, its `127.0.0.1` refers to that container. Put
the fixture in that namespace for this demo; do not loosen the mock's loopback
or send guard or expose the fixture publicly. If an instance's SSRF protection
blocks the loopback fixture, use a permitted isolated local instance rather than
disabling protection on a shared or production instance.

Fixtures: `done`, `partial`, `failed`, `interrupted`, `stuck`, `unknown`,
`publish-failed`, `publish-stuck`, `start-conflict`. Normally the first poll is
running and the second terminal. The mock workflow waits one second, with a
five-poll/30-second stage budget, so failures can be demonstrated quickly.

The offline tests validate structure, exported expressions, and the fixture
contract. The real-n8n harness supplies separate engine evidence. Neither proves
production authentication, a production scan, Sheet writes, real Hermes behavior,
Telegram delivery, or durable crash recovery.

## Configure the real template later

Production setup, credentials, real execution, and scheduling require a separate
approved cutover. Do not execute these steps as part of the fixture demonstration.

1. Reconcile the branch with the running production version first. The checkout
   started at `60f2067`, dated September 30, 2026. The reported October 3
   production Today-QA cache fix may not be included; do not overwrite it with
   this earlier base. Inspect and test the actual diff before any rollout.
2. Confirm the hardened Actions API includes `POST /v1/jobs/publish`, partial and
   interrupted job states, and idempotency handling. The unmodified September 30
   API does not have this complete contract. See [ChatGPT Actions](chatgpt-actions.md)
   and the API changes shipped alongside this integration.
3. Import `founder-radar.workflow.json` into the intended n8n instance, inactive.
   Set `Configure.api_base` to the authorized HTTPS origin with no trailing slash,
   path, query, or embedded credentials. Do not use the mock export for real work.
4. In n8n's secure credential UI, the authorized operator supplies/selects a
   **Bearer Auth** credential for the existing Actions API. Select that credential
   on all four HTTP Request nodes. The JSON contains only a placeholder, and
   provides no token. Never put the token into a Set node, header text field,
   exported JSON, command-line argument, or committed file. n8n documents this
   under [HTTP Request credentials](https://docs.n8n.io/integrations/builtin/credentials/httprequest/).
5. Keep TLS validation enabled. Redirect following is disabled on all HTTP nodes.
   Keep the existing real API authentication in place. The unauthenticated mock
   fixture is a different program, not an auth-bypass switch in the real service.
6. Keep `send_telegram=false`. Set `publish_enabled=false` if the approved run
   should stop after the scan. Change notification behavior only after explicitly
   approving the Telegram send, destination, and intended message category.
7. Run an approved manual production check and inspect its actual job results
   before considering a schedule. A credential or host change is not permission
   to run a scan.

Workflow execution records retain job output for diagnosis. Limit n8n access and
retention appropriately; stdout/stderr and business data may be sensitive even
though the workflow export contains no secrets. Back up n8n state/encryption
material according to the chosen deployment's runbook.

## Polling, retries, and recovery

- Every API call has a 15-second HTTP timeout, at most three attempts, and a
  two-second retry delay. Non-2xx responses fail the HTTP node. Authentication,
  conflict, and server errors never become a success result.
- Search polls every 30 seconds, at most 140 polls and a 3,900-second stage
  deadline. Publish polls every 15 seconds, at most 60 polls and a 900-second
  deadline. The deadline is checked after a response and is therefore not an
  exact cancellation timer. HTTP latency/retries add overhead. A workflow-level
  9,000-second timeout is a final guard, subject to the n8n instance's limits.
- The per-stage poll counter is the pending-condition node's execution index.
  The first nonterminal response counts as poll 1. A terminal response never
  needs another poll.
- **Stopping n8n polling does not cancel the API job.** Save the job ID and check
  that same job. Never report a timeout as proof the scan did nothing.
- These Wait intervals are under 65 seconds. n8n documents that such waits stay
  in-process; this workflow does not promise durable sleep/resume after process
  failure. `saveExecutionProgress` helps inspection, but is not an end-to-end
  restart-recovery guarantee. See the [Wait node's time behavior](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.wait/).
- Default `Configure.execution_key` is
  `founder-radar:<workflow-id>:<execution-id>`. Search adds `:search`, publish adds
  `:publish`. HTTP retries in one execution keep the same key and body.
- **A fresh Manual Trigger run recomputes the key and can start a new scan.**
  In the inspected n8n 2.42.3 implementation, Retry failed execution copies
  previous execution data, including completed `Configure` output. A downstream
  retry of this exact graph therefore normally retains the original key despite
  getting a new execution ID. Reexecuting Configure or changing the graph can
  change that behavior: inspect the actual retained key and original job first.
  A retry of `Stop - operator review` simply stops again; it does not resume a
  failed CLI command. For an approved fresh manual execution that should
  reconcile the same logical operation, replace the `execution_key` expression
  with its exact previous value and preserve the request body. Reusing a
  terminal job's key returns its existing outcome; it does not rerun the CLI.
  A genuinely new approved scan needs a fresh key.
- The API accepts 1–128 ASCII characters from `A-Z a-z 0-9 . _ : -`. The workflow
  limits the base key to 120 characters, leaving room for the operation suffix.
  Keep job-store records across restarts; deleting them forgets their deduplication
  history. Do not change a key's request body: that correctly returns
  `409 idempotency_conflict`.
- `409 mutation_in_progress` means another API mutation owns the lock. Do not
  clear it blindly or cycle through fresh keys. Inspect the returned job/poll
  reference where present and review the service state.
- The server's mutation lock protects Actions API mutations, not direct CLI,
  systemd, or every possible writer. Its local POSIX filesystem and same-service-UID
  execution assumptions matter; a `sudo` transition may close inherited lock
  descriptors. Confirm deployment behavior before relying on that lock.
- `interrupted` means the former worker stopped without a trusted final outcome.
  There may already be database/Sheet changes or a notification. Review before
  any retry; this workflow does not resubmit interrupted jobs automatically.

The API response contract is:

```json
{
  "ok": true,
  "accepted": true,
  "job_id": "32 lowercase hexadecimal characters",
  "status": "queued",
  "poll": "/v1/jobs/<job-id>",
  "reused": false
}
```

New starts return 202. Repeating an identical key/body returns the same job, 202
while active or 200 if terminal. Polling returns `{"ok":true,"job":{...}}`.
The job's `status`, `exit_code`, `error`, `stdout`, `stderr`, and `dashboard_url`
are authoritative. `job.result` is present only if CLI stdout parsed as JSON;
search's structured run summary may be under `job.result.run`. Publish often
emits text instead. The workflow does not invent a top-level run ID or require
structured publish stdout.

## Keep one scheduler

The repository's `deploy/founder-radar.timer` already owns a 06:30 Europe/London
scan with systemd's restart catch-up and randomized delay. The n8n Schedule Trigger
is **disabled**, and both exports are **inactive**. Do not enable/publish it while
that timer still schedules scans. A later approved cutover must choose one owner,
address missed-run/restart behavior, test the overlap protection, and document
rollback. The API lock alone does not prevent systemd overlap.

The workflow explicitly sets `Europe/London`, so a future enabled n8n schedule
would follow UK DST. See the official [Schedule Trigger documentation](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/)
and [workflow settings](https://docs.n8n.io/build/manage-workflows/configure-workflow-settings.md).

## Maintenance

Regenerate and test after changing the source generator:

```bash
python integrations/n8n/generate_workflows.py
python integrations/n8n/test_workflows.py
# Then rerun the real-n8n mock harness against the final exported bytes.
git diff -- integrations/n8n docs/n8n-integration.md
```

Pinned node versions were checked against the installed official package; node
behavior and import format should be rechecked when upgrading n8n. Further
references: [HTTP Request](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/),
[If](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.if/), and
[Edit Fields](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.set/).
