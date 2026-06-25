# autoresearch protocol — distilled from upstream `program.md`

This is the verbatim distillation of the rules the upstream `program.md` gives
the research agent. Hermes enforces these as a behavioral contract — they
are not optional. If a future user wants to relax them, they edit `program.md`
in the upstream repo, not this skill.

## Setup (one time per run)

1. **Pick a tag.** Usually today's date (e.g. `mar5`). The branch
   `autoresearch/<tag>` must NOT already exist — this is a fresh run.
2. **Branch off master:** `git checkout -b autoresearch/<tag>`.
3. **Read the in-scope files:**
   - `README.md` — repo context.
   - `prepare.py` — fixed constants, data prep, tokenizer, dataloader,
     evaluation. **Do not modify.**
   - `train.py` — the file the agent edits. Model, optimizer, training loop.
4. **Verify data exists.** Check `~/.cache/autoresearch/` for shards and
   tokenizer. If absent, tell the human to run `uv run prepare.py`.
5. **Initialize `results.tsv`** with the header row only. The baseline is
   recorded after the first run.
6. **Confirm and go.**

## Experiment loop (runs forever until interrupted)

1. Look at git state — current branch/commit.
2. Tune `train.py` with an experimental idea. Hack the code directly.
3. `git commit`.
4. Run: `uv run train.py > run.log 2>&1` (redirect — do NOT flood context).
5. Read results: `grep "^val_bpb:\|^peak_vram_mb:" run.log`.
6. Empty grep = crash. `tail -n 50 run.log` to read the stack trace.
   - Obvious fix (typo, missing import): fix and re-run.
   - Fundamentally broken idea: log as `crash`, move on.
7. **Do NOT commit `results.tsv`** — leave it untracked.
8. If `val_bpb` improved (lower) → **advance** the branch (keep commit).
9. If equal or worse → `git reset` back to where you started.

## Hard constraints

| Constraint | Reason |
|---|---|
| Only `train.py` is editable | Keeps diffs reviewable |
| `prepare.py` is read-only | Contains the ground-truth metric `evaluate_bpb` |
| `pyproject.toml` is fixed | No new packages; agent uses what's there |
| 5-minute wall-clock budget | Comparable across hardware |
| Kill runs > 10 minutes | Treat as failure, revert |
| VRAM is soft | Some increase OK for meaningful gains |
| Simplicity criterion | 0.001 val_bpb + 20 lines hack = not worth it |

## What the agent CAN do

- Modify `train.py` — anything: architecture, optimizer, hyperparameters,
  batch size, model size, training loop, attention pattern, normalization,
  initialization, etc.

## What the agent CANNOT do

- Modify `prepare.py` (read-only).
- Install new packages or add dependencies.
- Modify the evaluation harness (`evaluate_bpb` in `prepare.py`).
- Modify `pyproject.toml`.
- Modify `program.md` (the human owns this file).

## Output format

`train.py` prints a summary to stdout at end:

```
---
val_bpb:          0.997900
training_seconds: 300.1
total_seconds:    325.9
peak_vram_mb:     45060.2
mfu_percent:      39.80
total_tokens_M:   499.6
num_steps:        953
num_params_M:     50.3
depth:            8
```

Extract metric with: `grep "^val_bpb:" run.log`

## results.tsv schema

```
commit	val_bpb	memory_gb	status	description
```

- **commit**: short git hash (7 chars).
- **val_bpb**: 6 decimals; `0.000000` for crashes.
- **memory_gb**: `peak_vram_mb / 1024`, rounded to .1f; `0.0` for crashes.
- **status**: `keep`, `discard`, or `crash`.
- **description**: short text of what was tried.

Example:

```
commit	val_bpb	memory_gb	status	description
a1b2c3d	0.997900	44.0	keep	baseline
b2c3d4e	0.993200	44.2	keep	increase LR to 0.04
c3d4e5f	1.005000	44.0	discard	switch to GeLU activation
d4e5f6g	0.000000	0.0	crash	double model width (OOM)
```

## Stop conditions

- The human interrupts.
- The agent crashes 3+ times in a row with no obvious fix.
- A run exceeds the 10-minute hard kill threshold (kill, revert, log `discard`).

## NEVER STOP rule

Once the experiment loop has begun (after initial setup), do NOT pause to ask
the human if they should continue. Do NOT ask "should I keep going?" The human
might be asleep or away. The agent is autonomous. If you run out of ideas,
think harder — re-read in-scope files, read papers referenced in code, try
combining previous near-misses, try more radical changes. The loop runs until
the human interrupts, period.

## Estimated throughput

- ~12 experiments/hour (5 min/run + a few seconds startup/eval).
- ~100 experiments per typical human sleep (8 hours).