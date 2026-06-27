---
name: hermes-cron
description: "Debug, trigger, inspect, and manage Hermes cron jobs. Use when the user says a cron job 'did nothing' or 'didn't fire', wants to run a job manually, asks why a job isn't delivering to chat, or asks to create/update/pause/remove a scheduled job. Covers the 6 verbs (create/list/update/pause/resume/run/remove), where per-run logs land, the `deliver` field matrix (local vs origin vs telegram), the tick-lag gotcha on manual triggers, and the LLM-creates-Windows-Tasks pitfall for trivial commands."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cron, scheduled, recurring, debugging, automation]
    related_skills: [hermes-agent, nightly-research-report, trade-vision]
---

# Hermes Cron Job Management

Manage, debug, and trigger Hermes scheduled jobs. Built from the recurring
"I triggered X and nothing happened" class of question.

## When to use

- User reports a cron job "didn't fire" / "did nothing" / "nothing happened"
- User wants to manually trigger an existing job to test or run early
- User asks to create, pause, resume, update, or remove a job
- User asks why a scheduled job isn't producing visible output (Telegram, dashboard, etc.)
- A cron job ran but its output didn't reach the user — need to find where it landed
- LLM-driven cron job started creating duplicate Windows Scheduled Tasks

## When NOT to use

- Long-running one-off shell work — just run it via `terminal`
- Background process that should outlive the current turn — use `terminal(background=True, notify_on_complete=True)`
- Recurring work that already has a Windows Scheduled Task / systemd timer / launchd plist and doesn't need Hermes in the loop

## The 6 verbs (one tool, six actions)

All management goes through the `cronjob` tool. Use the matching `action`:

| Action | Purpose | Example |
|---|---|---|
| `list` | See all jobs, statuses, next/last run | `cronjob(action="list")` |
| `create` | New scheduled job | `cronjob(action="create", schedule="0 6 * * *", prompt="...", name="Morning X")` |
| `update` | Change schedule, prompt, deliver, etc. | `cronjob(action="update", job_id="abc", schedule="0 7 * * *")` |
| `run` | Trigger one fire NOW | `cronjob(action="run", job_id="abc")` |
| `pause` / `resume` | Stop scheduling (keeps the job) | `cronjob(action="pause", job_id="abc")` |
| `remove` | Delete the job and its output dir | `cronjob(action="remove", job_id="abc")` |

## Where logs land

Every fired run writes a markdown file to:

```
$HERMES_HOME/cron/output/<job_id>/YYYY-MM-DD_HH-MM-SS.md
```

On this host that's `C:\Data\Hermes_0.17.0\cron\output\<job_id>\…`.

The file has three sections: **Prompt** (what the job was told), **Response**
(agent's final assistant message or script stdout), **Summary** (human report).
This is the FIRST place to look when a job "did nothing" — if the file exists
with a recent timestamp, the job ran; the user's perception of "nothing" is a
visibility problem, not an execution problem.

The job record itself is in `$HERMES_HOME/cron/jobs.json` (or
`$HERMES_HOME/cron/cron/jobs.json` on older installs). Inspect directly for
`last_run_at`, `next_run_at`, `last_status`, `last_error`, `repeat.completed`.

## The `deliver` field matrix

This is the most common source of "the job ran but I saw nothing":

| Value | Where the response goes | Use when |
|---|---|---|
| `local` | `$HERMES_HOME/cron/output/<job_id>/<ts>.md` ONLY | Internal-only jobs, daily digests the agent posts via `hermes send` itself, jobs whose output is consumed by another job |
| `origin` | The platform/chat that created the job | Bill-triggered automations that should report back to his chat |
| `telegram` | A specific Telegram chat | Dedicated Telegram digests |
| `all` | Every connected home channel | Truly cross-channel alerts (rare) |
| `platform:chat_id:thread_id` | One specific destination | Routing to a thread or specific channel |

**If the user says "I triggered it and nothing happened," check `deliver`
first.** `local` is silent to the user by design; the user MUST read the
output directory or change `deliver` to `origin` / `telegram`.

## The tick-lag gotcha (manual triggers)

`cronjob(action="run", job_id=X)` does **NOT** execute the job. It:
1. Sets `next_run_at` to `_hermes_now().isoformat()` (right now)
2. Saves the job record
3. Returns `{success: true, job: ...}`

The next scheduler tick (≤1 min on this host) picks up the due job and runs it.
The CLI even prints "It will run on the next scheduler tick" to remind you.

**Implication for "did nothing" debugging:** wait 60-90 seconds after `run`,
then check `cron/output/<job_id>/` for a new file. If still nothing, check
that the scheduler tick is alive (`ls cron/.tick.lock` mtime, or watch the
gateway log for "N job(s) due").

## The "nothing happened" debug flow

Run this when the user reports a job misfire:

1. **Find the job**: `cronjob list` → note `job_id`, `last_run_at`, `last_status`, `next_run_at`, `deliver`.
2. **Check the output dir**: `ls -la cron/output/<job_id>/` → newest file's mtime vs `last_run_at`. If a recent file exists, the job ran — go to step 4.
3. **Check the scheduler**: `cat cron/jobs.json | python -m json.tool | grep -A 3 <job_id>` → `next_run_at` in the future? Tick frequency OK? Look for `last_error`.
4. **Check the `deliver` field**: `local` means silent to the user. If they want a chat ping, propose changing it.
5. **For LLM-driven jobs (`no_agent: false`)**: read the last response in the output dir. The agent may have decided to do something other than the literal prompt (see pitfall below).
6. **For Windows Scheduled Task leaks**: `Get-ScheduledTask | Where-Object {$_.TaskName -match 'cleanup|trace|rdp'}` — duplicate tasks created by a "creative" LLM cron agent.

## Decision: `no_agent: true` vs agent-driven

For trivial scheduled shell commands (cleanup, sync, kill-stale-process), use:

```python
cronjob(
    action="create",
    schedule="...",
    script="my_script.cmd",   # .cmd/.bat/.sh/.py under $HERMES_HOME/scripts/
    no_agent=True,            # skips LLM entirely
)
```

**Why prefer `no_agent: true` for trivial work:**
- Zero tokens per tick (no model call)
- Runs instantly on tick (no LLM latency)
- Cannot "be creative" — runs the script and delivers stdout verbatim
- Matches Bill's preference for self-healing, low-noise automation
- Won't burn money on a 5-second cleanup

**Use the LLM-driven agent path (`no_agent: false`, default) when:**
- The job needs reasoning (research, summarization, conditional logic)
- The output is prose, not a fixed format
- The job reads other jobs' state files and reasons about them

## Pitfalls

**`cronjob run` is async, not immediate.** It enqueues for the next tick. The MCP call returning success does NOT mean the job ran. Wait ~60s and re-check.

**`deliver: local` is silent to the user.** This is the #1 "nothing happened" cause. If the user expected a Telegram ping, the deliver field is wrong — propose `origin` or `telegram`.

**LLM-driven cron agents create Windows Scheduled Tasks when the prompt says "run this daily".** The model interprets "daily" as "make this durable across reboots" and registers a Windows Task Scheduler entry — usually without asking. This creates duplicate execution paths (cron ticks + Windows task both fire). Symptom: same job appears as both `<name>` cron entry AND a Windows task named after the cleanup target (e.g. `CleanupRdClientAutoTrace` or `Hermes_RdClientAutoTrace_Cleanup`). Fix: tighten the prompt to forbid task creation ("Run this command. Do NOT register any Windows Scheduled Tasks.") OR convert to `no_agent: true` (eliminates the LLM that creates them).

**RDP trace files (`msrdc.exe`) accumulate continuously.** `C:\Users\<user>\AppData\Local\Temp\DiagOutputDir\RdClientAutoTrace\` fills with `MSRDCEventProcessor_*.etl` and `RdClientAutoTrace-WppAutoTrace-*.etl` files WHILE `msrdc.exe` is running. `Remove-Item -Force` skips files with open handles silently (the cmdlet reports success). Run cleanup when no RDP session is active, OR kill the `msrdc` process first.

**24-hour interval jobs fire once a day, period.** A `every 1440m` schedule means once per 24h, and after each fire `next_run_at` jumps +1440m. The user can't manually re-trigger into a "more frequent" cadence without changing the schedule. If the job needs more frequent ticks, edit the schedule — `run` won't change frequency.

**`cronjob run` advances `next_run_at` to the next interval, not "now".** Specifically, after `trigger_job()` sets `next_run_at=now`, the scheduler tick fires the job and `mark_job_run` advances `next_run_at` by one interval. So a `every 1440m` job triggered at 13:43 won't fire again until 13:43 TOMORROW. If the user wants a tight loop, they want `schedule` (5-field cron) not `every Nm`.

**The job record's `last_run_at` lags the actual run.** `last_run_at` is set inside `mark_job_run` AFTER the agent completes. For LLM-driven jobs that take 30-60s, the file in `cron/output/` may exist before `last_run_at` is updated. Don't use `last_run_at` to confirm execution — use the output-dir file mtime.

**Schedule strings are local time, not UTC.** On this host Bill is in PDT (UTC-7). `0 1 * * *` is 1am PDT, not 1am UTC. The scheduler stores `next_run_at` with timezone offset (-07:00 suffix) so verify by looking at the absolute value.

**Reading jobs.json directly is OK.** It's plain JSON under a file lock (`cron/.jobs.lock`). When the MCP tool or CLI misbehaves, `python -c "import json; print(json.dumps(json.load(open('jobs.json'))['jobs'], indent=2))"` is a perfectly valid fallback.

## Verification

After any cron-management action:

```bash
# Confirm the action landed
ls -la $HERMES_HOME/cron/output/<job_id>/              # new log file after a run
python -c "import json; d=json.load(open('$HERMES_HOME/cron/jobs.json')); [print(j['id'], j['name'], j.get('next_run_at'), j.get('last_status')) for j in d['jobs'] if j['id']=='<job_id>']"

# For no_agent jobs, verify the script ran
hermes cron run <job_id>      # then wait 60s, check output dir
cat $HERMES_HOME/cron/output/<job_id>/<newest>.md       # script stdout

# For LLM-driven jobs, check the agent's reasoning
tail -100 $HERMES_HOME/cron/output/<job_id>/<newest>.md # look for [SILENT] markers and actual tool calls

# Check the scheduler tick is alive
ls -la $HERMES_HOME/cron/.tick.lock                     # mtime should be ≤2 min old
```

## Quick reference

| Symptom | First check |
|---|---|
| "I triggered X and nothing happened" | `deliver` field + output dir for new file (after 60s) |
| "X job didn't fire" | `cronjob list` → `last_run_at` + `last_status` + `last_error` |
| "I'm not getting Telegram pings" | Job's `deliver` field — `local` doesn't deliver |
| "Same cleanup runs twice" | `Get-ScheduledTask` for orphans (LLM cron leak) |
| "Job's `next_run_at` is way in the future" | That's correct for `every Nm` intervals |
| "Last response was `[SILENT]`" | Agent decided nothing new to report — check if that's intended |

## Variations

- **External fire provider (Chronos, multi-machine)**: uses `claim_job_for_fire()` which advances `next_run_at` under file lock. Single-machine deployments always win the claim; multi-machine setups get at-most-once.
- **Cron chaining**: `context_from: ["<upstream_job_id>"]` injects the upstream job's last assistant message into this job's prompt. For durable state, write to a shared file in `$HERMES_HOME/cron/output/shared/` — context_from only carries the final message, not the full transcript.
