#!/usr/bin/env python3
"""
autoresearch plot — render a progress.png-style chart from results.tsv.

Reads the tab-separated results log and produces a PNG chart that mirrors
the upstream autoresearch progress.png:

  - x-axis: experiment index (commit order)
  - y-axis: val_bpb (lower is better)
  - blue dots = keep, gray dots = discard, red x = crash
  - horizontal red dashed line = baseline (first run's val_bpb)
  - best-so-far envelope drawn as a thin blue line

The chart is for the dashboard — readable in a browser tab without zoom.

Usage:
  python plot.py                              # writes progress.png in the repo
  python plot.py --out PATH                   # explicit output path
  python plot.py --repo PATH                  # different autoresearch checkout
  python plot.py --results PATH               # explicit results.tsv
  python plot.py --show                       # open the PNG after writing
  python plot.py --json                       # print summary as JSON, no PNG

Requires matplotlib (NOT optional). The autoresearch .venv has it; the
autoresearch.cmd wrapper routes plot through that venv.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path("C:/Data/Hermes/~/autoresearch")


def load_results(tsv: Path) -> list[dict[str, Any]]:
    if not tsv.exists():
        sys.exit(f"[FAIL] {tsv} does not exist. Run autoresearch setup first.")
    lines = tsv.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < len(header):
            parts += [""] * (len(header) - len(parts))
        row = dict(zip(header, parts))
        try:
            row["val_bpb"] = float(row.get("val_bpb", "nan"))
        except ValueError:
            row["val_bpb"] = float("nan")
        try:
            row["memory_gb"] = float(row.get("memory_gb", "nan"))
        except ValueError:
            row["memory_gb"] = float("nan")
        rows.append(row)
    return rows


def render_png(rows: list[dict[str, Any]], out_path: Path) -> dict[str, Any]:
    """Render a matplotlib PNG of the autoresearch progress."""
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")  # no display required
    import matplotlib.pyplot as plt  # noqa: PLC0415

    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=110)

    # Split by status
    keeps = [(i, r) for i, r in enumerate(rows) if r["status"] == "keep" and r["val_bpb"] > 0]
    discards = [(i, r) for i, r in enumerate(rows) if r["status"] == "discard" and r["val_bpb"] > 0]
    crashes = [(i, r) for i, r in enumerate(rows) if r["status"] == "crash"]

    # Plot scatter
    if keeps:
        xk, yk = zip(*[(i, r["val_bpb"]) for i, r in keeps])
        ax.scatter(xk, yk, s=42, c="#2b6cb0", marker="o",
                   edgecolors="white", linewidths=0.6, label="keep", zorder=3)
    if discards:
        xd, yd = zip(*[(i, r["val_bpb"]) for i, r in discards])
        ax.scatter(xd, yd, s=32, c="#a0aec0", marker="o",
                   edgecolors="white", linewidths=0.4, label="discard", zorder=2)
    if crashes:
        xc = [i for i, _ in crashes]
        # Plot crashes at the bottom of the chart so they're visible
        if keeps:
            y_floor = min(r["val_bpb"] for _, r in keeps) - 0.05
        else:
            y_floor = 1.0
        yc = [y_floor] * len(xc)
        ax.scatter(xc, yc, s=48, c="#c53030", marker="x",
                   linewidths=1.6, label="crash", zorder=4)

    # Best-so-far envelope
    if keeps:
        best_so_far: list[tuple[int, float]] = []
        cur_best = float("inf")
        for i, r in keeps:
            if r["val_bpb"] < cur_best:
                cur_best = r["val_bpb"]
            best_so_far.append((i, cur_best))
        bx, by = zip(*best_so_far)
        ax.plot(bx, by, color="#2b6cb0", linewidth=1.4, alpha=0.7,
                label="best so far", zorder=2.5)

    # Baseline = first keep val_bpb
    if keeps:
        baseline = keeps[0][1]["val_bpb"]
        ax.axhline(baseline, color="#c53030", linestyle="--", linewidth=1.0,
                   alpha=0.6, label=f"baseline ({baseline:.4f})", zorder=1.5)

    # Titles + axes
    total = len(rows)
    n_keep, n_discard, n_crash = len(keeps), len(discards), len(crashes)
    best = min((r["val_bpb"] for _, r in keeps), default=None)
    best_str = f"   best val_bpb: {best:.6f}" if best is not None else ""
    ax.set_title(
        f"autoresearch — {total} experiments   "
        f"keep={n_keep}  discard={n_discard}  crash={n_crash}{best_str}",
        fontsize=12, fontweight="bold", loc="left",
    )
    ax.set_xlabel("experiment index")
    ax.set_ylabel("val_bpb  (lower is better)")
    ax.grid(True, alpha=0.25, linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper right", framealpha=0.92, fontsize=9)

    # Footer: best commit
    if keeps:
        best_row = min(keeps, key=lambda kv: kv[1]["val_bpb"])[1]
        ax.text(
            0.99, -0.12,
            f"best commit: {best_row['commit']}   "
            f"\"{best_row['description'][:80]}\"",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=8, color="#4a5568", family="monospace",
        )

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)

    return {
        "out_path": str(out_path),
        "total": total,
        "keeps": n_keep,
        "discards": n_discard,
        "crashes": n_crash,
        "best_val_bpb": best,
        "baseline_val_bpb": keeps[0][1]["val_bpb"] if keeps else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Render autoresearch progress PNG")
    p.add_argument("--repo", default=str(DEFAULT_REPO),
                   help="path to autoresearch checkout")
    p.add_argument("--results", default=None, help="explicit results.tsv path")
    p.add_argument("--out", default=None, help="output PNG path")
    p.add_argument("--show", action="store_true",
                   help="open the PNG after writing (best-effort)")
    p.add_argument("--json", action="store_true", help="emit JSON summary")
    args = p.parse_args()

    if args.results:
        tsv = Path(args.results).expanduser()
    else:
        tsv = Path(args.repo).expanduser() / "results.tsv"
    if args.out:
        out_path = Path(args.out).expanduser()
    else:
        out_path = Path(args.repo).expanduser() / "progress.png"

    rows = load_results(tsv)
    info = render_png(rows, out_path)

    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"[OK] wrote {info['out_path']}")
        print(f"     experiments: {info['total']}  "
              f"keep={info['keeps']}  discard={info['discards']}  "
              f"crash={info['crashes']}")
        if info.get("best_val_bpb") is not None:
            print(f"     best val_bpb: {info['best_val_bpb']:.6f}  "
                  f"(baseline {info['baseline_val_bpb']:.6f})")

    if args.show:
        try:
            import os
            if sys.platform == "win32":
                os.startfile(info["out_path"])  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{info["out_path"]}"')
            else:
                os.system(f'xdg-open "{info["out_path"]}" >/dev/null 2>&1')
        except Exception as e:
            print(f"[WARN] could not open PNG: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())