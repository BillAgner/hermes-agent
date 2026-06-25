#!/usr/bin/env python3
"""
autoresearch status — preflight check before any run.

Reports:
  - Current branch + git status (clean? ahead/behind?)
  - results.tsv: row count, last commit, last val_bpb
  - GPU: detected adapter + CUDA available?
  - Cache: ~/.cache/autoresearch/ populated?
  - Python: version + torch available?

Exit code 0 = ready (with caveats), 1 = blocker.

Usage:
  python status.py                  # human-readable
  python status.py --json           # machine-readable
  python status.py --repo PATH      # point at a different autoresearch checkout
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = Path("C:/Data/Hermes/~/autoresearch")
CACHE_DIR = Path.home() / ".cache" / "autoresearch"


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 10) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def check_repo(repo: Path) -> dict:
    if not repo.exists():
        return {"exists": False, "path": str(repo)}
    if not (repo / ".git").exists():
        return {"exists": True, "is_git": False, "path": str(repo)}
    rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    rc2, sha, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    rc3, status, _ = run(["git", "status", "--porcelain"], cwd=repo)
    rc4, remotes, _ = run(["git", "remote", "-v"], cwd=repo)
    return {
        "exists": True,
        "is_git": True,
        "path": str(repo),
        "branch": branch,
        "sha": sha,
        "clean": status == "",
        "uncommitted": status.splitlines() if status else [],
        "remotes": remotes,
    }


def check_results(repo: Path) -> dict:
    tsv = repo / "results.tsv"
    if not tsv.exists():
        return {"exists": False, "path": str(tsv)}
    try:
        lines = tsv.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return {"exists": True, "error": str(e), "path": str(tsv)}
    if not lines:
        return {"exists": True, "rows": 0, "header": "", "last": None}
    header = lines[0]
    data = [l for l in lines[1:] if l.strip()]
    last = data[-1].split("\t") if data else None
    last_dict = None
    if last and len(last) >= 5:
        last_dict = {
            "commit": last[0],
            "val_bpb": last[1],
            "memory_gb": last[2],
            "status": last[3],
            "description": last[4],
        }
    return {
        "exists": True,
        "path": str(tsv),
        "header": header,
        "rows": len(data),
        "last": last_dict,
    }


def check_gpu() -> dict:
    info = {"cuda_available": False, "torch_installed": False}
    try:
        import torch  # noqa: PLC0415
        info["torch_installed"] = True
        info["torch_version"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["device_count"] = torch.cuda.device_count()
            info["device_name"] = torch.cuda.get_device_name(0)
            try:
                info["compute_capability"] = ".".join(
                    str(x) for x in torch.cuda.get_device_capability(0)
                )
            except Exception:
                pass
        return info
    except ImportError:
        info["error"] = "torch not installed"
        return info
    except Exception as e:
        info["error"] = str(e)
        return info


def check_cache() -> dict:
    if not CACHE_DIR.exists():
        return {"exists": False, "path": str(CACHE_DIR)}
    data = CACHE_DIR / "data"
    tok = CACHE_DIR / "tokenizer"
    info: dict = {
        "exists": True,
        "path": str(CACHE_DIR),
        "data_dir_exists": data.exists(),
        "tokenizer_dir_exists": tok.exists(),
    }
    if data.exists():
        shards = list(data.glob("shard_*.parquet"))
        info["data_shard_count"] = len(shards)
    if tok.exists():
        info["tokenizer_files"] = sorted(p.name for p in tok.iterdir())[:10]
    return info


def check_python() -> dict:
    return {
        "version": sys.version.split()[0],
        "executable": sys.executable,
        "uv_available": shutil.which("uv") is not None,
    }


def render_text(report: dict) -> str:
    lines: list[str] = []
    repo = report["repo"]
    lines.append("autoresearch status")
    lines.append("=" * 60)
    if not repo["exists"]:
        lines.append(f"[FAIL] repo not found at {repo['path']}")
        return "\n".join(lines)
    if not repo["is_git"]:
        lines.append(f"[FAIL] {repo['path']} is not a git repo")
        return "\n".join(lines)
    lines.append(f"  repo:       {repo['path']}")
    lines.append(f"  branch:     {repo['branch']}  ({repo['sha']})")
    lines.append(f"  clean:      {'yes' if repo['clean'] else 'NO — uncommitted changes:'}")
    for u in repo["uncommitted"][:5]:
        lines.append(f"               {u}")
    if len(repo["uncommitted"]) > 5:
        lines.append(f"               ... and {len(repo['uncommitted']) - 5} more")
    lines.append("")
    res = report["results"]
    if not res["exists"]:
        lines.append(f"  results.tsv: missing (run setup to initialize)")
    else:
        lines.append(f"  results.tsv: {res['rows']} rows")
        if res.get("last"):
            last = res["last"]
            lines.append(
                f"               last: {last['commit']}  "
                f"val_bpb={last['val_bpb']}  "
                f"status={last['status']}"
            )
            lines.append(f"                      \"{last['description']}\"")
    lines.append("")
    gpu = report["gpu"]
    if not gpu["torch_installed"]:
        lines.append("  torch:       not installed (train.py will fail to import)")
    else:
        lines.append(f"  torch:       {gpu.get('torch_version', '?')}")
        if gpu["cuda_available"]:
            cap = gpu.get("compute_capability", "?")
            lines.append(
                f"  GPU:         {gpu.get('device_name', '?')} "
                f"(sm_{cap.replace('.', '_') if cap != '?' else '?'})  "
                f"x{gpu.get('device_count', 1)}"
            )
            lines.append("               CUDA OK — train.py will run")
        else:
            lines.append(
                "  GPU:         CUDA not available "
                f"({gpu.get('error', 'no CUDA device')})"
            )
            lines.append("               train.py will FAIL on this host")
    lines.append("")
    cache = report["cache"]
    if not cache["exists"]:
        lines.append(f"  cache:       missing ({cache['path']})")
        lines.append("               run: uv run prepare.py  (one-time, ~2 min)")
    elif not cache.get("data_dir_exists"):
        lines.append(f"  cache:       {cache['path']} (empty — no data)")
        lines.append("               run: uv run prepare.py")
    else:
        n = cache.get("data_shard_count", 0)
        tok = "yes" if cache.get("tokenizer_dir_exists") else "no"
        lines.append(f"  cache:       {n} data shard(s), tokenizer: {tok}")
    lines.append("")
    py = report["python"]
    lines.append(f"  python:      {py['version']}  ({py['executable']})")
    lines.append(f"  uv:          {'yes' if py['uv_available'] else 'no'}")
    lines.append("")
    if repo["clean"] and report["gpu"]["cuda_available"] and res.get("rows", 0) >= 1:
        verdict = "[OK]   ready to continue the experiment loop"
    elif not report["gpu"]["cuda_available"]:
        verdict = "[WARN] no CUDA — analysis/setup work fine, train.py will fail"
    elif not res["exists"]:
        verdict = "[WARN] no results.tsv yet — run setup first"
    else:
        verdict = "[OK]   partial — see notes above"
    lines.append(verdict)
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="autoresearch preflight check")
    p.add_argument("--repo", default=str(DEFAULT_REPO), help="path to autoresearch checkout")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = p.parse_args()

    repo = Path(args.repo).expanduser()
    report = {
        "repo": check_repo(repo),
        "results": check_results(repo),
        "gpu": check_gpu(),
        "cache": check_cache(),
        "python": check_python(),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))

    if not report["repo"]["exists"] or not report["repo"].get("is_git"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())