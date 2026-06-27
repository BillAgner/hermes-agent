# When to use `--no-agent --script` for hermes cron jobs

## TL;DR

If the task can be expressed as a single shell command or short Python script that always does the same thing, use `--no-agent --script`. Don't burn an LLM call on it.

## What `--no-agent --script` gives you

- **Zero tokens per tick.** The script's stdout is delivered verbatim (or silenced if empty).
- **No drift.** The LLM can't reinterpret the task each run, can't "helpfully" spawn extra Windows Scheduled Tasks, can't add new tool calls.
- **Instant execution.** No LLM round-trip — the script runs as soon as the gateway tick fires.
- **Same observability.** `hermes cron show <id>` shows last run, last status, last error just like prompt-based jobs.

## What it costs

- **No reasoning.** If the inputs vary in ways a script can't anticipate, you need a prompt-based job.
- **You write the script.** A few dozen lines of Python wrapping your PowerShell/shell command.

## Existing no-agent jobs on this host

These are the proven reference implementations — read at least one before writing your own:

| Script | Cadence | Purpose |
|---|---|---|
| `stall-detector.py` | every 1m | Detect when the main agent has stalled |
| `auto_rag_sync.py` | daily 02:00 | Sync open-notebook / RAG embeddings |
| `daily_briefing.py` | daily 07:00 | Morning summary |
| `hermes_watchdog.py` | every 1m | Process / memory health probe |
| `cleanup-rdclient-trace.py` | daily 03:00 | Delete RDP trace ETLs (this session's job) |

All of them follow the same shape: idempotent, silent on "nothing to do", one-line stdout on success, stderr on failure. The `templates/no_agent_script.py` skill file is a stripped-down clone of `cleanup-rdclient-trace.py` ready to be copied and modified.

## Decision tree

```
Can the task be one PowerShell / shell command?
├── Yes → --no-agent --script
└── No, but the steps are deterministic
    ├── Yes → --no-agent --script (longer Python wrapper)
    └── No, judgment needed at runtime
        └── Prompt-based job (the prompt itself is the work product)
```

## When NOT to use `--no-agent --script`

- Multi-step research that requires querying the web, picking sources, weighing claims (use a research skill).
- Trade analysis that needs live OHLCV + sentiment + scoring (use trade-vision skill).
- Daily briefings that need to read calendar/email/inbox and synthesize (use a prompt with a knowledge-base skill).
- Anything where "what to do" depends on "what we found" — that's reasoning, scripts don't do reasoning.

## Hard-won pattern: PowerShell-via-Python wrapper

For Windows tasks where the user gave you a literal PowerShell command, the cleanest pattern is a small Python script that:

1. Computes the target path with `Path(os.environ["LOCALAPPDATA"]) / ...` (avoids hardcoded `C:\Users\<name>\`).
2. Snapshots file count + bytes before.
3. Calls PowerShell via `subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ...])` — same flags as `hermes-cleanup.py`.
4. Snapshots file count + bytes after.
5. Prints one line: `deleted N file(s), freed X MiB from <path> (took Ys, K remain)`.
6. Returns 0 on success, 1 on PowerShell failure (with stderr written so `hermes cron show` captures it).

The `-NoProfile -NonInteractive` flags matter: `-NoProfile` skips loading the user's `$PROFILE` (faster, no surprises), `-NonInteractive` blocks interactive prompts (matters because cron has no TTY).

See `templates/no_agent_script.py` for the full starter — copy it, change `TARGET` and the `ps` command, ship it.
