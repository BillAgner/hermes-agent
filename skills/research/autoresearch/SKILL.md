---
name: autoresearch
version: "1.1.0"
description: "Drive Karpathy's autoresearch — an autonomous LLM pretraining research loop that edits train.py, runs a fixed 5-minute CUDA training, and keeps/discards experiments based on val_bpb. Includes local analysis (status, analyze, plot) and remote-CUDA delegation (delegate) for hosts without an NVIDIA GPU. Use this skill when the user wants autonomous pretraining research, when asked to drive an experiment overnight, when reading or analyzing results.tsv from an autoresearch run, when rendering the progress.png dashboard, or when explaining the autoresearch protocol."
homepage: https://github.com/karpathy/autoresearch
repository: https://github.com/karpathy/autoresearch
author: Hermes Agent (wrapper); Karpathy (project)
license: MIT
platforms: [linux, windows]
metadata:
  hermes:
    tags: [research, llm, pretraining, gpu, cuda, autonomous, ml, training, dashboard]
    related_skills: [claude-code, codex, opencode]
---

# autoresearch — Hermes Orchestration Guide

Karpathy's `autoresearch` is a deliberately tiny repo (~3 files) that lets an AI
agent run an autonomous LLM pretraining research org overnight. The agent
edits `train.py`, trains for a fixed 5-minute budget on a single GPU, checks
`val_bpb` (validation bits-per-byte — lower is better), keeps or discards, and
repeats. You wake up to a `results.tsv` of every experiment and (hopefully) a
better model.

This skill teaches Hermes:

1. **The protocol** — branch → modify train.py → run → log → keep/discard.
2. **The metric** — `val_bpb` is the ground-truth objective; lower is better.
3. **The constraints** — single NVIDIA GPU with CUDA (Hopper ideal for FA3),
   `prepare.py` is read-only, `pyproject.toml` is fixed, 5-minute wall-clock
   budget per run, kill anything > 10 minutes.
4. **How to drive it from Hermes** — analyze `results.tsv`, summarize runs,
   compare experiments, and (when a GPU host is available) delegate the actual
   loop to Claude Code / Codex CLI running inside the autoresearch directory.

## When to use this skill

- The user mentions **autoresearch, pretraining research, val_bpb, train.py
  tuning, "overnight training", or an autonomous research loop**.
- The user asks to **read or analyze a results.tsv** from an autoresearch run.
- The user asks to **set up a new autoresearch branch/tag** (e.g.
  `autoresearch/mar5`).
- The user wants to **compare two experiments** by val_bpb and complexity.
- The user asks to **explain the autoresearch protocol** or write a custom
  `program.md`.

## When NOT to use this skill

- General LLM fine-tuning (HuggingFace `Trainer`, LoRA, etc.) — out of scope.
- Inference-only work — autoresearch is a *training* research loop.
- For multi-day training runs — autoresearch is deliberately capped at 5 min/run.

## Critical environment facts (this host)

| Fact | Value | Source of truth |
|---|---|---|
| Repo path | `C:\Data\Hermes\~\autoresearch` | This host |
| Python in venv | 3.11.15 | `python --version` |
| Python on system | 3.12.10 (`C:\Users\bobup\AppData\Local\Programs\Python\Python312\python.exe`) | system install |
| autoresearch `.python-version` | 3.10 | upstream repo |
| `uv` | 0.11.21 (installed) | `uv --version` |
| GPU | **AMD Radeon 8060S** (no NVIDIA) | `Get-WmiObject Win32_VideoController` |
| `nvidia-smi` | not found | PATH |
| `torch` (CUDA) | not installed | `python -c "import torch"` |

**Bottom line:** The 5-minute CUDA training loop **cannot run on this host**
(no NVIDIA GPU). What CAN run here:

- Reading `train.py` / `prepare.py` and explaining them.
- Generating custom `program.md` research instructions.
- Reading and analyzing `results.tsv` from runs done elsewhere.
- Comparing two `train.py` commits by their reported metrics.
- Setting up the repo (`uv sync`) and running `prepare.py` data/tokenizer prep.
- Delegating the actual GPU loop to a remote host via SSH or Claude Code CLI.

When the user asks to "run autoresearch overnight" on this host, surface the
GPU constraint immediately and propose one of:

1. **Delegate to a remote CUDA host** (SSH tunnel, `codex`, or `claude -p`
   running on the remote machine). The remote host has the GPU; this host has
   the orchestration.
2. **Read-only mode** — analyze past runs, summarize `results.tsv`, propose
   next-experiment hypotheses without committing changes.
3. **Set up only** — run `uv sync`, `prepare.py`, initialize `results.tsv`
   with the baseline row so the GPU host can take over.

## The protocol (distilled from `program.md`)

These are the exact rules the upstream repo tells the research agent to
follow. Honor them — the protocol IS the benchmark.

### Setup phase (one time per run)

1. **Pick a tag** — usually today's date, e.g. `mar5`. The branch
   `autoresearch/<tag>` must NOT already exist.
2. **Branch off master:** `git checkout -b autoresearch/<tag>` from current
   `master`.
3. **Read the in-scope files** in full:
   - `README.md` — repo context.
   - `prepare.py` — fixed constants, data prep, tokenizer, dataloader,
     evaluation. **DO NOT MODIFY.**
   - `train.py` — the only file the agent edits. Model, optimizer, loop.
4. **Verify `~/.cache/autoresearch/`** contains data shards and a tokenizer.
   If not, tell the human to run `uv run prepare.py` (one-time, ~2 min).
5. **Initialize `results.tsv`** with the header row only:
   ```
   commit	val_bpb	memory_gb	status	description
   ```
   Baseline is recorded after the first run.
6. **Confirm with the human**, then start the loop.

### Experiment loop (runs forever until interrupted)

1. Look at git state (current branch / commit).
2. Tune `train.py` with an experimental idea by hacking code directly.
3. `git commit` (short hash recorded in results.tsv).
4. Run: `uv run train.py > run.log 2>&1` (redirect — DO NOT flood context).
5. Read results: `grep "^val_bpb:\|^peak_vram_mb:" run.log`.
6. Empty grep = crash. `tail -n 50 run.log` to read the stack trace. If
   obviously fixable (typo, missing import), fix and retry. If fundamentally
   broken, log as `crash` and move on.
7. **DO NOT commit `results.tsv`** — leave it untracked by git.
8. If `val_bpb` improved (lower): **advance** the branch (keep the commit).
9. If equal or worse: `git reset` back to where you started.
10. **NEVER STOP.** The loop runs until the human interrupts. If you run out
    of ideas, re-read `train.py`, read papers referenced in code, try
    combinations of previous near-misses, try more radical changes.

### Constraints

| Rule | Reason |
|---|---|
| Only `train.py` is editable | Keeps diffs reviewable |
| `prepare.py` is read-only | Contains the evaluation harness — the ground-truth metric |
| `pyproject.toml` is fixed | No new packages; agent must use what's there |
| 5-minute wall-clock budget | Makes experiments comparable across hardware |
| Kill runs > 10 min | Treat as failure, revert |
| VRAM is soft | Some increase OK for meaningful gains, no blow-up |
| Simplicity wins | 0.001 val_bpb improvement + 20 lines hacky code = not worth it |

### Output format

`train.py` prints a summary like:

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

Extract with: `grep "^val_bpb:\|^peak_vram_mb:" run.log`

### results.tsv columns

```
commit   val_bpb   memory_gb   status       description
a1b2c3d  0.997900  44.0        keep         baseline
b2c3d4e  0.993200  44.2        keep         increase LR to 0.04
c3d4e5f  1.005000  44.0        discard      switch to GeLU activation
d4e5f6g  0.000000  0.0         crash        double model width (OOM)
```

- Tab-separated, NOT comma-separated (commas break in descriptions).
- `val_bpb` to 6 decimals; `0.000000` for crashes.
- `memory_gb` = `peak_vram_mb / 1024`, rounded to .1f; `0.0` for crashes.
- `status`: `keep`, `discard`, or `crash`.
- `description`: short text of what was tried.

## How to drive from Hermes

### 1. Analyze a past run

```bash
python "C:\Data\Hermes\skills\research\autoresearch\scripts\analyze.py" \
    --results "C:\Data\Hermes\~\autoresearch\results.tsv" \
    --json
```

Returns: total experiments, keep/discard/crash counts, best val_bpb, mean
improvement per keep, recent trend. `--json` for machine-readable output
(good for dashboards).

### 2. Check repo status

```bash
python "C:\Data\Hermes\skills\research\autoresearch\scripts\status.py"
```

Returns: current branch, git status, results.tsv row count, GPU check, and
whether `~/.cache/autoresearch/` is populated. Fast pre-flight before any
run.

### 3. Delegate the autonomous loop to a remote CUDA host

If Bill has access to a remote CUDA box, the cleanest path is:

```bash
# On the remote host, after `git clone` and `uv sync && uv run prepare.py`:
claude -p "Read program.md and kick off a new autoresearch run. \
    Use tag $(date +%b%d). Run autonomously until interrupted." \
    --workdir ~/autoresearch \
    --allowedTools 'Bash,Read,Edit,Write,Grep,Glob' \
    --max-turns 9999
```

Or spawn `codex` / `opencode` instead — the protocol is agent-agnostic. The
upstream README says: *"Simply spin up your Claude/Codex or whatever you want
in this repo (and disable all permissions), then you can prompt something
like: `Hi have a look at program.md and let's kick off a new experiment!
let's do the setup first.`"*

### 4. Local-only options (no CUDA)

On this AMD host, the useful work is:

- **Read `train.py`** and propose experiments on paper.
- **Run `analysis.ipynb`** (the upstream has one) against an existing
  `results.tsv` if `pandas` and `matplotlib` are installed.
- **Generate custom `program.md`** variations focused on Bill's domain
  (small-models, TinyStories, low VRAM, etc.).
- **Run `uv sync && uv run prepare.py`** for the tokenizer + data prep
  (CPU-only step that downloads shards and trains BPE — no CUDA needed).

### 5. The clean command: autoresearch.cmd

This is the one-line entry point Bill asked for. From any shell:

```
C:\Data\Hermes\skills\research\autoresearch\scripts\autoresearch.cmd status
C:\Data\Hermes\skills\research\autoresearch\scripts\autoresearch.cmd analyze
C:\Data\Hermes\skills\research\autoresearch\scripts\autoresearch.cmd plot
C:\Data\Hermes\skills\research\autoresearch\scripts\autoresearch.cmd delegate
C:\Data\Hermes\skills\research\autoresearch\scripts\autoresearch.cmd setup
C:\Data\Hermes\skills\research\autoresearch\scripts\autoresearch.cmd train
```

| Subcommand | What it does | Works on this host? |
|---|---|---|
| `status` | Preflight check (branch, git, GPU, cache, Python) | ✅ |
| `analyze` | Summarize `results.tsv` (text + optional ASCII chart) | ✅ |
| `plot` | Render `progress.png` from `results.tsv` (matplotlib) | ✅ (uses autoresearch `.venv`) |
| `delegate` | Print the remote-CUDA launch command for Claude/Codex/OpenCode | ✅ |
| `setup` | `uv sync + uv run prepare.py` (downloads data + trains tokenizer) | ✅ (CPU only, ~2 min) |
| `train` | `uv run train.py` | ❌ (no NVIDIA GPU on this host) |
| `update` | `git pull --ff-only` in the repo | ✅ |

## Filesystem layout

```
C:\Data\Hermes\~\autoresearch\                      ← upstream repo (read-write)
├── prepare.py                                       ← READ-ONLY per protocol
├── train.py                                         ← only file the agent edits
├── program.md                                       ← agent instructions (this skill distills them)
├── README.md
├── pyproject.toml                                   ← fixed deps
├── uv.lock
├── analysis.ipynb                                   ← upstream analysis notebook
├── progress.png                                     ← (regenerated by plot.py)
├── .python-version                                  ← 3.10
└── .git\                                            ← branched per-run (autoresearch/<tag>)

C:\Data\Hermes\skills\research\autoresearch\         ← THIS SKILL (Hermes wrapper)
├── SKILL.md                                         ← (you are here)
├── scripts\
│   ├── status.py                                    ← branch / git / GPU / cache check
│   ├── analyze.py                                   ← results.tsv summary + ASCII trend
│   ├── plot.py                                      ← results.tsv → progress.png (matplotlib)
│   ├── delegate.py                                  ← print the remote-CUDA launch command
│   ├── autoresearch.cmd                             ← Windows entry point (all 7 subcommands)
│   └── autoresearch.sh                              ← bash entry point
└── references\
    ├── protocol.md                                  ← verbatim program.md distillation
    ├── hardware.md                                 ← prereqs + Bill's host reality
    └── upstream → C:\Data\Hermes\~\autoresearch      ← junction
```

## The dashboard: `plot.py`

`analyze.py` prints text (or an ASCII chart in a terminal). `plot.py` is the
**browser dashboard** — it renders the upstream-style `progress.png`:

```bash
python "C:\Data\Hermes\skills\research\autoresearch\scripts\plot.py"
# writes C:\Data\Hermes\~\autoresearch\progress.png
```

The chart shows:
- x-axis: experiment index (commit order)
- y-axis: `val_bpb` (lower is better)
- **blue dots** = `keep`, **gray dots** = `discard`, **red ×** = `crash`
- a **blue line** tracing the best-so-far envelope (lower envelope = improving)
- a **red dashed horizontal line** at the baseline (first run's `val_bpb`)
- a footer with the best commit + its description

`autoresearch.cmd plot` auto-routes through the autoresearch `.venv` where
matplotlib 3.10.8 lives (the system Python 3.12 doesn't have it).

Flags:
- `--results PATH` / `--repo PATH` — point at a different run
- `--out PATH` — write somewhere other than `progress.png`
- `--show` — best-effort `os.startfile` to open in the default viewer
- `--json` — print a JSON summary instead of human text

## Remote delegation: `delegate.py`

This AMD host can't run the CUDA training loop. `delegate.py` prints the
exact command to launch the autonomous research loop on a remote CUDA host
(SSH target via `--host` or `AUTORESEARCH_REMOTE_HOST` env var). It **never
runs anything by default** — by design. Pass `--exec` to actually SSH.

```bash
# print mode: see the command first
python "C:\Data\Hermes\skills\research\autoresearch\scripts\delegate.py"
# → prints the claude-code command for the remote host

# pick a different agent CLI
python delegate.py --agent codex --tag mar5
python delegate.py --agent opencode

# execute (requires ssh in PATH + host key set up)
AUTORESEARCH_REMOTE_HOST=user@box.example.com python delegate.py --exec
```

Supported agents:
- `claude-code` (default) — `claude -p "..." --workdir ~/autoresearch ...`
- `codex` — `codex --cd ~/autoresearch -q '...'`
- `opencode` — `opencode --directory ~/autoresearch`

The default tag is today's date (e.g. `jun21`); override with `--tag mar5`.

## Pitfalls

- **Running `train.py` on this host will crash** — `train.py:21` calls
  `torch.cuda.get_device_capability()` which raises `AssertionError` /
  `RuntimeError` when no CUDA device is present. Do not retry.
- **`prepare.py` is read-only** — the evaluation harness
  (`evaluate_bpb`) lives there and is the ground-truth metric. Modifying it
  invalidates the benchmark.
- **`results.tsv` must NOT be committed** — keep it in `.gitignore`. The
  upstream `.gitignore` already excludes it.
- **5-minute budget is wall-clock training time, not total runtime** —
  startup, compilation, and eval overhead are excluded. A run that takes
  6 minutes total but 5 minutes of training is fine.
- **VRAM comparisons across hardware are meaningless** — only compare runs on
  the same machine. Upstream says: *"your runs (and results) become not
  comparable to other people running on other compute platforms."*
- **`prepare.py` downloads ~400B-token corpus shards** — first-time setup is
  ~2 min plus data download. Use `--num-shards 8` for a quick test.

## Verify the install

After the installer runs, expect:

```
[OK] junction: C:\Data\Hermes\skills\research\autoresearch\references\upstream -> C:\Data\Hermes\~\autoresearch
[OK] autoresearch.cmd at <path> resolves Python 3.12+
[OK] status.py reports AMD Radeon 8060S (no CUDA — train.py will fail locally)
```

The references/upstream junction lets `analyze.py` and `status.py` find
`results.tsv` and the in-scope files even if the repo path moves — but the
canonical path is still `C:\Data\Hermes\~\autoresearch`.

## Related

- **Upstream repo:** https://github.com/karpathy/autoresearch
- **Parent project:** https://github.com/karpathy/nanochat (full multi-GPU
  implementation; autoresearch is a single-GPU distillation)
- **Bill's host reality:** AMD Radeon 8060S — needs remote CUDA for the loop.
- **Hermes delegation skills:** `claude-code`, `codex`, `opencode` (all in
  `autonomous-ai-agents/`) — these are the agents that drive the loop when a
  CUDA host is available.