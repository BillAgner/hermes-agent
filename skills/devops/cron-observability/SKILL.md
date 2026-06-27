---
name: cron-observability
description: Find, view, and aggregate outputs of Hermes cron jobs.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cron, scheduled-jobs, observability, logs, dashboards]
    related_skills: [nightly-research-report, daily-news-digest, trade-vision]
---

# Cron Observability in Hermes

## Overview

Hermes cron jobs (`cron/scheduler.py` + `cron/jobs.py`) deliver outputs through
**three access paths**, but no single path answers "what did job X report over
its last N runs?" — there's a per-job history gap. This skill maps the
landscape so the agent can answer cron-output questions fast and propose
concrete fixes when the gap hurts.

Use this skill when the user asks to **see**, **review**, **compare**, or
**debug** the outputs of any cron job — whether a one-shot check ("what did
the watchdog report at 3am?") or a recurring need ("show me trade-vision's
last week of daily analyses side-by-side").

## When to Use

- "Show me the output of cron job X"
- "What did the watchdog / daily-research / market-snapshot report yesterday?"
- "Compare the last N runs of job X"
- "Why didn't I get the cron output in Telegram?"
- "Where do cron logs live?"
- "I need a dashboard view of cron runs"
- Any debugging task where a scheduled job is the suspected culprit

**Don't use for:** live-running agent sessions (those go through the normal
session UI); one-off `terminal` background processes (those use a different
watcher and don't appear in `cron list`).

## Three Access Paths (as of v0.17.0)

### 1. Telegram delivery — the canonical channel

Every cron run's final output auto-delivers to the configured channel. Default
is the current chat. The cron scheduler wraps the output in a header/footer
frame so the run is identifiable in the chat history.

**If the user says they're not seeing cron outputs:** the problem is delivery
config, not the cron job itself. Check:

- `hermes cron list` → `last_delivery_error` field per job
- Gateway log: `~/.hermes/logs/gateway.log`
- The job's `deliver` field in `~/.hermes/cron/jobs.json`

### 2. CLI: `hermes cron list`

```bash
hermes cron list
```

Shows per job: `last_run_at`, `last_status` (ok / error), `last_error`,
`last_delivery_error`. Only the **most recent run** is exposed — there is no
"last 10 runs" view in the CLI today.

**To get more than one run:** fall through to path 3 (session logs) and look
up session IDs manually, or read `~/.hermes/cron/jobs.json` (it carries
`run_history` in some versions but is not a stable public schema).

### 3. Session logs: `hermes logs --session <id>`

Cron runs create sessions with ID pattern:

```
cron_<job_short_id>_<timestamp>
```

Where `<job_short_id>` is the first 8 chars of the job UUID and
`<timestamp>` is the run start in `YYYYMMDDTHHMMSSZ` form.

To pull a run's full transcript:

```bash
hermes logs --session cron_a1b2c3d4_20260626T143000Z -f
```

Session IDs are **not surfaced back** from `cron list`, so the discovery
loop today is:

1. User reports "I got a Telegram message from the watchdog at 3am"
2. Scan `~/.hermes/cron/jobs.json` or recent gateway log for the matching
   job_id → timestamp → session_id
3. `hermes logs --session <id>` to read the actual run

This is the friction point the dashboard / new CLI verbs are meant to fix.

## Dashboard `/cron` Page (built-in)

The dashboard SPA at `http://127.0.0.1:9119/cron` (loopback) has full job
management. Confirmed endpoints (from the built bundle
`hermes_cli/web_dist/assets/index-*.js`):

| Endpoint verb        | Purpose                                  |
|----------------------|------------------------------------------|
| `getCronJobs`        | List all jobs (richer than CLI list)     |
| `createCronJob`      | New job from form                        |
| `updateCronJob`      | Edit existing job                        |
| `deleteCronJob`      | Remove job                               |
| `pauseCronJob`       | Pause scheduling                         |
| `resumeCronJob`      | Resume scheduling                        |
| `triggerCronJob`     | Run-now (immediate execution)            |
| `getCronDeliveryTargets` | List channels a job can deliver to  |

**What the dashboard does NOT show:** per-run history. Clicking a job in the
UI shows its config + last-run status, same as `cron list`. To see actual
run output, you still drop down to session logs (path 3).

## The Per-Job Run History Gap

Today's architecture only stores the most-recent run's status per job. The
following questions are **not directly answerable** with the built-in tools:

- "What did job X report at 3pm yesterday vs. 3pm today?"
- "Show me the last 10 outputs of trade-vision-daily side-by-side"
- "Has watchdog recovered more or fewer services this week vs. last?"

### Closing the gap (options, ascending effort)

| Option | Effort | What you get |
|---|---|---|
| **A. CLI: `hermes cron show <id>` + `hermes cron runs <id>`** | ~1 hr | New verbs. `runs` prints last N runs with start/end, status, session ID, error. Pairs with `hermes logs --session <id>` to pull each run's content. Requires appending to a `runs.jsonl` on every tick. |
| **B. Dashboard cron panel with run history** | ~2-3 hr | New `/cron/<job_id>` view showing last N runs with timestamps + status, click a run to see the full session output inline. Same `runs.jsonl` backend as A. |
| **C. Cross-job aggregation feed** | ~3-4 hr | "Recent cron activity" panel — one feed across all jobs (like `/api/cron/runs?limit=50`), grouped by job. Best for at-a-glance health. |
| **D. Plain-file logs at `~/.hermes/cron/<job_id>.log`** | ~30 min | Each run appends full output + metadata to a plain file. View with `tail -f` or any editor. No UI. Lowest-overhead observability. |

**Recommended path: A + B.** Build the `runs.jsonl` backend once (small
change to `cron/scheduler.py`'s completion path), wire it into `cron show`
and `cron runs` CLI verbs (A), then add a dashboard panel (B) that reads the
same backend via a new `/api/cron/runs/<job_id>` endpoint. Dashboard adds no
extra state — pure view on top of the same JSONL.

**Deliberately not in scope:** changing Telegram delivery semantics, the
scheduler itself, or how jobs are stored. This is a read-side observability
problem.

## On-Disk Layout

```
~/.hermes/
├── cron/
│   ├── jobs.json            # Job definitions + last_run_* state (mutable)
│   ├── .tick.lock           # Scheduler lock file (do not edit)
│   └── runs.jsonl           # NOT YET BUILT — option A/B backend target
└── logs/
    ├── agent.log            # All session activity, including cron_ sessions
    ├── errors.log           # WARNING+ across all sessions
    └── gateway.log          # Telegram delivery + cron scheduling events
```

## Quick Recipes

### "Did the watchdog fire in the last hour?"

```bash
hermes cron list | grep -A2 watchdog
# Look at last_run_at + last_status
```

### "Why didn't the daily-research-report deliver to Telegram?"

```bash
# 1. Check delivery error
hermes cron list | grep -A4 daily-research

# 2. If last_delivery_error is set, read it
# 3. Check gateway.log for that timestamp
tail -200 ~/.hermes/logs/gateway.log | grep -i 'delivery\|telegram'
```

### "Pull the last 24h of cron runs across all jobs"

```bash
# Today's session DB includes everything; use FTS5 or filter by role
session_search(query="cron", limit=10)
```

Or, if the dashboard is up:

```
http://127.0.0.1:9119/cron
```

### "Show me the actual output of a specific past run"

1. Find the session ID — search `~/.hermes/cron/jobs.json` for the job's
   `last_session_id` or grep gateway.log for the cron start event
2. Pull the transcript:

   ```bash
   hermes logs --session <id> --level INFO
   ```

3. If you need the raw agent log file:

   ```bash
   grep -A1000 'session_id=<id>' ~/.hermes/logs/agent.log | head -500
   ```

## Common Pitfalls

1. **Confusing background `terminal` processes with cron jobs.** They are
   different. `terminal(background=True, notify_on_complete=True)` runs
   inside the agent session and delivers via the gateway watcher, not the
   cron scheduler. They will NOT appear in `hermes cron list`.

2. **"Where did the cron output go?"** First check Telegram — that's the
   default delivery channel. If you set up a cron job but didn't configure
   `deliver`, the output went to the agent log + your default chat.

3. **Session ID format varies by job type.** Standard pattern is
   `cron_<short_id>_<ts>`. One-shot jobs may have a different prefix. Don't
   assume — search `session_search` if the canonical pattern doesn't match.

4. **`hermes cron list` only shows the most recent run.** If you need a
   week of history today, you have to grep `agent.log` or scan the session
   DB. Don't promise the CLI can show a run history it cannot.

5. **The dashboard's `/cron` page is for management, not history.** Job
   config + last-run status, not past outputs. If the user is asking for
   output history, route them to session logs (path 3) or propose building
   option A/B.

6. **`runs.jsonl` doesn't exist yet.** If a future agent claims there's a
   `runs.jsonl` in `~/.hermes/cron/`, that's stale info — option A/B
   haven't been built as of v0.17.0.

## Verification Checklist

- [ ] Confirmed `~/.hermes/cron/jobs.json` exists and parses
- [ ] Confirmed `hermes cron list` returns at least one job (else there's
  nothing to observe)
- [ ] Confirmed dashboard `/cron` is reachable at `http://127.0.0.1:9119/cron`
- [ ] Confirmed at least one cron session ID appears in `session_search`
- [ ] If proposing option A/B: confirmed `runs.jsonl` does NOT yet exist
  (otherwise the gap is already closed)

## Related Skills

- `nightly-research-report` — example cron pipeline using the two-job pattern
- `daily-news-digest` — example cron pipeline using YouTube source
- `trade-vision` — daily CC digest as a cron job (uses last_run for state)