# Hardware prereqs and Bill's host reality

## Upstream requirements (from autoresearch README)

> **Requirements:** A single NVIDIA GPU (tested on H100), Python 3.10+,
> [uv](https://docs.astral.sh/uv/).

- **GPU:** single NVIDIA. Tested on H100 (Hopper, sm_90). FA3 is the
  preferred attention kernel.
  - On Hopper (sm_90): uses `varunneal/flash-attention-3`.
  - On non-Hopper NVIDIA: uses `kernels-community/flash-attn3`.
  - On AMD: not supported upstream. See "Notable forks" in upstream README.
- **Python:** ≥ 3.10.
- **Package manager:** `uv`.
- **Disk:** `~/.cache/autoresearch/` holds data shards + tokenizer. Each
  parquet shard is multi-MB; full corpus is 6,542 shards. Realistic disk
  for a quick test: 1–5 GB; for a full prep: tens of GB.
- **PyTorch:** `torch==2.9.1` with cu128 (CUDA 12.8 build).

## Bill's host (this machine)

| Component | Value |
|---|---|
| OS | Windows 10 |
| Python in Hermes venv | 3.11.15 |
| Python on system | 3.12.10 |
| `uv` | 0.11.21 (installed) |
| GPU | AMD Radeon 8060S Graphics |
| NVIDIA driver / `nvidia-smi` | not present |
| `torch` (CUDA) | not installed |
| Disk free on C: | 112 GB |
| RAM | (sufficient for tokenizer/data prep) |

## Verdict

| Task | Can run here? |
|---|---|
| Read `train.py` / `prepare.py` | ✅ |
| Generate custom `program.md` | ✅ |
| `uv sync` | ✅ (CPU-only pip resolution) |
| `uv run prepare.py` (tokenizer + data download) | ✅ (CPU-only, no CUDA) |
| Run `analysis.ipynb` against existing `results.tsv` | ✅ (CPU-only, needs pandas + matplotlib in chosen env) |
| **Run `uv run train.py`** | ❌ Fails on `torch.cuda.get_device_capability()` — no NVIDIA GPU |

## Workarounds

### Option 1: Remote CUDA host

If Bill has access to a remote box with an NVIDIA GPU (H100 ideal, anything
with CUDA + a modern PyTorch works):

1. `git clone https://github.com/karpathy/autoresearch.git` on the remote.
2. `cd autoresearch && uv sync && uv run prepare.py` (~2 min one-time).
3. Spawn an agent in that directory:
   ```bash
   claude -p "Read program.md and kick off a new autoresearch run. \
       Use tag $(date +%b%d). Run autonomously until interrupted." \
       --workdir ~/autoresearch \
       --allowedTools 'Bash,Read,Edit,Write,Grep,Glob' \
       --max-turns 9999
   ```
4. Pull `results.tsv` back to this host for analysis.

### Option 2: Notable forks for non-NVIDIA

From upstream README "Notable forks":

- **MacOS** (Apple Silicon): `miolini/autoresearch-macos`,
  `trevin-creator/autoresearch-mlx`.
- **Windows NVIDIA RTX**: `jsegov/autoresearch-win-rtx`.
- **AMD ROCm**: `andyluo7/autoresearch`.

If Bill's AMD Radeon is powerful enough (8060S is integrated RDNA 3.5,
modest VRAM), the AMD ROCm fork is the most realistic local option — but
expect slow training (small `DEPTH`, low `TOTAL_BATCH_SIZE`).

### Option 3: Read-only mode

The agent can still be useful here without ever touching CUDA:

- Compare two `train.py` commits on paper (line count, parameter count from
  `num_params_M`, complexity heuristics).
- Generate experiment proposals as custom `program.md` text.
- Summarize past `results.tsv` files for the user.
- Render the upstream `progress.png` style chart from any results.tsv with
  matplotlib.

### Option 4: CPU-only training

If the agent is willing to wait: `train.py` will not run on CPU without
patches. The kernels package and FA3 require CUDA. This is a non-starter
without an NVIDIA (or ROCm fork) GPU.

## Recommendation

For Bill's actual workflow on this host:

1. **Use this skill for analysis and orchestration.** Reading train.py,
   generating custom program.md, summarizing past runs — all work.
2. **For overnight runs, route to a remote CUDA host.** Wire it once, and
   the agent can launch and monitor from here.
3. **If no remote host is available, run the ROCm fork on the AMD GPU**
   with `DEPTH=4`, `MAX_SEQ_LEN=256`, `TOTAL_BATCH_SIZE=2**14` per the
   upstream README's "tuning for smaller platforms" guidance. Expect
   `val_bpb` in the 1.5+ range (vs ~1.0 on H100).