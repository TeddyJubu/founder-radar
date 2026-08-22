# Code-fix workflow — worktree, review, test, then ship

The person messaging you is **not an engineer**. They will not SSH, paste
logs, pick a branch, or merge anything. You do the whole loop. Talk to them
in short plain English.

Read this file before you edit a single line of application code.

Production checkout: `/opt/founder-radar/app` (branch `main`, owned by
`deploy/update-from-main.sh`).
Worktrees: `/opt/founder-radar/worktrees/` (never inside `app/`).
Ship gate: `sudo bash /opt/founder-radar/app/deploy/hermes-ship.sh <worktree>`.

---

## Talk like this

First message, immediately:

> I'm on it. I'll message you when it's done — you don't need to do anything.

When it worked:

> It's fixed and live. [one sentence of what was wrong, no file paths]

When you cannot finish (missing key, anti-bot, scoring would have to change):

> I couldn't finish this one. [one sentence a non-tech person can forward]
> Nothing on the live site was changed.

Never send pytest output, git hashes, worktree paths, or "please pull".

---

## The loop (do it in this order, not in parallel)

Memory: this is a 4 GB box. **One sub-agent at a time.** Never review and
test together. Never while `founder-radar.service` is active.

### 1. Ops first

`cd /opt/founder-radar` and run `founder-radar repair --apply`.
If that made it healthy, tell them it's sorted. Stop. No worktree.

### 2. Worktree

```bash
sudo -u radar mkdir -p /opt/founder-radar/worktrees
sudo -u radar git -C /opt/founder-radar/app fetch origin main
# Catch up if GitHub is ahead so the fix is not based on stale live code.
# If this fails because the VPS is already ahead, continue from live HEAD.
sudo -u radar git -C /opt/founder-radar/app merge --ff-only origin/main || true
slug=fix-$(date -u +%Y%m%dT%H%M%S)
wt=/opt/founder-radar/worktrees/$slug
sudo -u radar git -C /opt/founder-radar/app worktree add \
  -b "hermes/$slug" "$wt"
```

All edits happen in `$wt`. Never edit `/opt/founder-radar/app` directly.
If `merge --ff-only origin/main` failed *and* `git status` on live `main`
says the histories diverged, stop. Tell the human you couldn't finish.

### 3. Implement (you, in the worktree)

Adapter layout, parse, or crash only. Match neighbouring adapters.
Commit on `hermes/fix-*`. Do not merge yet.

### 4. Review sub-agent (fresh pair of eyes — must not be you)

Pass **everything** the child needs. It has no memory of this chat.

`delegate_task` (preferred) or a nested one-shot if delegation returns
before the child finishes:

```text
hermes chat --yolo -s founder-radar -t terminal --query-file /tmp/review.txt
```

Fill `references/review-prompt.md` with `$wt` and the commit range.
The child must output a line `VERDICT: APPROVE` or `VERDICT: REJECT`.
It must **not** edit files, merge, or ship.

REJECT → fix in the worktree, review again. Two review rounds max.

### 5. Test sub-agent (only after APPROVE)

Same isolation. Fill `references/test-prompt.md`.
The child must output `VERDICT: PASS` or `VERDICT: FAIL`.
It must **not** merge or ship.

FAIL → fix in the worktree, then **review again** (the diff changed), then
test again. Two test rounds max.

Run these **sequentially**. Wait for each VERDICT before the next step.
If `delegate_task` backgrounds, do not ship until the result is in.

### 6. Ship — only if review APPROVE **and** test PASS

```bash
sudo bash /opt/founder-radar/app/deploy/hermes-ship.sh "$wt"
```

That script is the machine gate. It re-runs pytest, refuses a `radar/score/`
diff, fast-forwards live `main`, tries to push, reinstalls, and removes the
worktree. If it exits non-zero, **the live site is unchanged** — tell the
human you couldn't finish, do not bypass the script.

### 7. Tell the human

One short message. Then stop.

---

## How to call the sub-agents

Sub-agents start blank. The prompt file must contain: worktree path,
commit SHA, files changed (`git diff --stat origin/main`), the hard limits,
and the exact VERDICT line you will parse.

Do **not** set `delegation.worktree_isolation` for review/test — they must
inspect **this** worktree, not a new empty one.

One child at a time (`max_concurrent_children` effectively 1 for this
workflow). Review and test are `role="leaf"`.

---

## Hard limits (ship.sh enforces these too)

- No edits under `radar/score/` (gates, fit, edge, tiering).
- No `.env`, tokens, `google-sa.json`.
- No `git push --force`, no `git reset --hard` on `/opt/founder-radar/app`.
- No dropping `data/radar.db`.
- No merging if pytest did not PASS.
- No merging while the daily scan is running.
