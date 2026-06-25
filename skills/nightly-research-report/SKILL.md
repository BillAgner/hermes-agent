---
name: nightly-research-report
description: "Recurring overnight-research-plus-morning-report cron pipeline for Hermes Agent. Use when the user wants scheduled autonomous web research (e.g. 'research agentic developments every night and report at 6am'). Two-job pattern: short research ticks overnight + single synthesis job at delivery time. State handoff via dated markdown file."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cron, recurring, research, pipeline, scheduled, overnight]
    related_skills: [arxiv, blogwatcher, hermes-agent]
---

# Nightly Research → Morning Report Pipeline

A two-job cron pattern for autonomous overnight research followed by a human-readable morning summary delivered to Telegram (or any channel). Built for the user's 1am-4am research / 6am report request on 2026-06-15.

## Why two jobs, not one

Hermes cron has a **3-minute hard interrupt per run**. A single overnight session would be killed almost immediately. The solution: many short research ticks (3 min each, ~6/night) that share a dated state file, plus one synthesis job at delivery time.

## Architecture

| Job | Schedule | Time | Delivery | Purpose |
|-----|----------|------|----------|---------|
| Research tick | `*/30 1-3 * * *` | 1:00–3:30am | `local` | 3-min focused research slice, appends to state file |
| Morning report | `0 6 * * *` | 6:00am | `telegram` | Reads state file, synthesizes, delivers to user |

**State handoff:** `C:\Data\Hermes\cron\output\nightly-research\YYYY-MM-DD.md`
Each tick appends; morning job reads the file matching today's date. State persists across ticks; auto-stops when 2 idle ticks in a row add `## RESEARCH COMPLETE`.

**Chaining:** morning job has `context_from: ["<research_job_id>"]` so it receives the last tick's summary as context. The state file is the durable source of truth.

## Setup steps

1. Create output dir: `mkdir -p $HERMES_HOME/cron/output/nightly-research`
2. Create research job via `cronjob create`:
   - schedule: `*/30 1-3 * * *`
   - enabled_toolsets: `web, search, terminal, file, session_search, skills`
   - skills: `arxiv` (and any other research-specific skills)
   - deliver: `local`
3. Note the returned `job_id` (e.g. `44ce1bfc5939`)
4. Create morning job via `cronjob create`:
   - schedule: `0 6 * * *`
   - context_from: `["<research_job_id>"]`
   - enabled_toolsets: `terminal, file`
   - deliver: `telegram`
5. Verify with `cronjob list` — both jobs should show `enabled: true` and a sane `next_run_at`

## Research tick prompt template

See `references/research-tick-prompt.md` in this skill. The prompt enforces:
- 3-min hard budget — stop researching by the 2:30 mark
- Read state file first, append (don't overwrite)
- Pick ONE lane per tick from a menu (arXiv, GitHub trending, AI labs, HN, Reddit, topical deep-dives)
- 1-3 NEW findings per tick, each with: title, URL, description, "why for Hermes" (ties to actual Hermes architecture), Status (New/Repeat/Already-available), Action (install/configure/monitor-only/no-action)
- 2-idle-tick stop condition writes `## RESEARCH COMPLETE` to the state file
- No installs, no config changes, research only

## Morning report prompt template

See `references/morning-report-prompt.md`. The prompt enforces:
- Read today's state file; handle missing/empty/`## RESEARCH COMPLETE` cases gracefully
- Bill's preferred synthesis style: TL;DR with decisions → verified facts → tier/architecture breakdown → install commands → phased kill-switchable plan → risk register → open questions → "Want me to proceed with Step X, or review the plan first?"
- Telegram constraints: <4096 chars per message, no `###` headers, no tables, no `hermes send --file`
- **Split chunks under 4000 chars** to dodge the Telegram dupe-send bug (>4096 chars sent twice — `stream_consumer.py` L510+L585)
- Don't fabricate; drop findings with missing source URLs
- Always deliver, even "no signal" gets a Telegram message

## Toolsets

- **Research tick:** `web, search, terminal, file, session_search, skills` — needs web fetch, search, shell for `date`/`ls`, file ops, prior session check, skill awareness
- **Morning report:** `terminal, file` — needs shell for `hermes send` + `date` + `ls`, file ops for reading state

Keep these minimal to control token cost. Don't load `messaging` toolset on the report job — `hermes send` CLI is the actual mechanism.

## Telegram delivery (the gotcha)

The `send_message` tool is NOT registered in cron sessions. Use the CLI:

```bash
hermes send --to telegram "message body"
hermes send --list   # verify Telegram is configured
```

If `hermes send --list` reports "No messaging platforms configured", Telegram isn't wired up. Surface the gap and dump the report content to stdout.

## Verification

```bash
# Confirm both jobs scheduled
hermes cron list

# Confirm output dir exists and is writable
ls -la $HERMES_HOME/cron/output/nightly-research/

# Confirm context_from is persisted (the list view doesn't show it)
grep -A 2 "morning_job_id" $HERMES_HOME/cron/jobs.json

# Manually trigger a single research tick for a smoke test
hermes cron run <research_job_id>
```

## Pitfalls

**Don't run the research job as a single long agent session.** The 3-min cap will kill it. The whole point of the tick pattern is to fit inside the cap.

**Don't have the research tick do installs or write to other files.** It's research only. The morning job proposes installs for the user to approve.

**Don't `hermes skills install` from the research tick.** Burns tokens on install setup, may not be reversible, and the user might not want it. The morning report proposes; the user decides.

**Don't fabricate findings to fill out a thin night.** "No signal" is a valid report. Honest > padded.

**Don't read full arxiv PDFs.** Abstracts only — 5x faster, 10x cheaper tokens.

**Don't go down rabbit holes.** The prompt caps at 10-15 papers / 20 web pages per tick. If you're tempted to read more, stop and write what you have.

**Telegram 4096-char limit + dupe bug.** Always split into chunks <4000 chars. The gateway sends anything >4096 twice.

**The morning job's `context_from` carries only the LAST tick's final assistant message, not the full state file.** The state file is the durable handoff; context_from is just a "research ran" signal. The prompt must `ls` + `read_file` the state file directly.

**Cron schedule is local time.** Bill is in PDT (UTC-7). `0 1 * * *` is 1am PDT. Verify with `hermes cron list` → `next_run_at` field.

**Each night creates a fresh state file** (`YYYY-MM-DD.md`). State doesn't carry across nights.

## Variations

- **Different research window:** change `*/30 1-3 * * *` to e.g. `*/30 2-5 * * *` for 2am-5:30am
- **More research time per night:** drop the `1-3` constraint to `0-5` and let it run 0:00-5:30am
- **Daily summary instead of research:** same pattern, but the tick writes a state file of accomplishments and the morning job summarizes what got done
- **Different delivery channel:** swap `telegram` for `discord:#channel`, `slack:#channel`, etc. in the morning job's `deliver` field
- **Multi-day research projects:** add a job that runs every few days to summarize a week of state files
