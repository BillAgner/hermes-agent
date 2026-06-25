#!/usr/bin/env python3
"""
autoresearch analyze — summarize results.tsv.

Reads the tab-separated results log and produces:
  - Total experiment count + status breakdown (keep/discard/crash).
  - Best val_bpb (lowest non-crash) + which commit.
  - Most recent keep + its description.
  - Per-status averages + improvement rate.
  - Recent trend (last 10 keeps, sliding-window delta).
  - Optional simple ASCII chart of keep val_bpb over time (no matplotlib needed).

Usage:
  python analyze.py                      # human-readable
  python analyze.py --json               # machine-readable
  python analyze.py --top 20             # top N keeps
  python analyze.py --repo PATH          # different autoresearch checkout
  python analyze.py --chart              # ASCII chart of keeps
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

DEFAULT_REPO = Path("C:/Data/Hermes/~/autoresearch")


def load_results(tsv: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if not tsv.exists():
        sys.exit(f"[FAIL] {tsv} does not exist. Run autoresearch setup first.")
    lines = tsv.read_text(encoding="utf-8").splitlines()
    if not lines:
        return [], []
    header = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < len(header):
            # Pad missing columns (last description may have tabs)
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
    return header, rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    by_status: dict[str, list[dict[str, Any]]] = {"keep": [], "discard": [], "crash": []}
    for r in rows:
        s = r.get("status", "").strip().lower()
        if s in by_status:
            by_status[s].append(r)
        else:
            by_status.setdefault(s, []).append(r)

    keeps = by_status["keep"]
    crashes = by_status["crash"]
    discards = by_status["discard"]

    valid_keeps = [r for r in keeps if r["val_bpb"] > 0]
    best = min(valid_keeps, key=lambda r: r["val_bpb"]) if valid_keeps else None
    last_keep = keeps[-1] if keeps else None

    improvements = []
    prev_bpb: float | None = None
    for r in keeps:
        if r["val_bpb"] > 0:
            if prev_bpb is not None:
                improvements.append(prev_bpb - r["val_bpb"])
            prev_bpb = r["val_bpb"]

    summary: dict[str, Any] = {
        "total": total,
        "keeps": len(keeps),
        "discards": len(discards),
        "crashes": len(crashes),
        "keep_rate": round(len(keeps) / total, 3) if total else 0.0,
        "crash_rate": round(len(crashes) / total, 3) if total else 0.0,
        "best": best,
        "last_keep": last_keep,
        "improvements": {
            "count": len(improvements),
            "mean": round(mean(improvements), 6) if improvements else 0.0,
            "median": round(median(improvements), 6) if improvements else 0.0,
            "total": round(sum(improvements), 6) if improvements else 0.0,
        },
        "by_status": {
            s: {
                "count": len(rs),
                "mean_val_bpb": round(mean(r["val_bpb"] for r in rs), 6) if rs else 0.0,
                "mean_memory_gb": round(mean(r["memory_gb"] for r in rs), 2) if rs else 0.0,
            }
            for s, rs in by_status.items()
            if rs
        },
    }

    # Recent trend: last 10 keeps
    recent = keeps[-10:]
    if len(recent) >= 2:
        first = recent[0]["val_bpb"]
        last = recent[-1]["val_bpb"]
        summary["recent_trend"] = {
            "window": len(recent),
            "first_val_bpb": first,
            "last_val_bpb": last,
            "delta": round(first - last, 6),
            "improving": last < first,
        }
    else:
        summary["recent_trend"] = None
    return summary


def ascii_chart(rows: list[dict[str, Any]], width: int = 60, height: int = 14) -> str:
    """Render keeps as ASCII scatter/line. No matplotlib required."""
    keeps = [r for r in rows if r["status"] == "keep" and r["val_bpb"] > 0]
    if not keeps:
        return "(no keep rows to chart)"
    xs = list(range(len(keeps)))
    ys = [r["val_bpb"] for r in keeps]
    y_min, y_max = min(ys), max(ys)
    if y_max == y_min:
        y_max = y_min + 1e-9

    grid = [[" "] * width for _ in range(height)]
    for i, y in zip(xs, ys):
        col = int(i * (width - 1) / max(len(keeps) - 1, 1))
        row = int((y_max - y) * (height - 1) / (y_max - y_min))
        row = max(0, min(height - 1, row))
        grid[row][col] = "●"

    header = f"val_bpb over keeps (n={len(keeps)})  y={y_min:.4f}..{y_max:.4f}"
    body = "\n".join("│" + "".join(r) + "│" for r in grid)
    footer = "└" + "─" * width + "┘"
    return f"{header}\n{body}\n{footer}"


def render_text(summary: dict[str, Any], top: list[dict[str, Any]], chart: str | None) -> str:
    L: list[str] = []
    L.append("autoresearch analyze")
    L.append("=" * 60)
    s = summary
    L.append(f"  total experiments: {s['total']}")
    L.append(
        f"  keeps: {s['keeps']}   discards: {s['discards']}   crashes: {s['crashes']}"
    )
    L.append(f"  keep rate: {s['keep_rate']:.1%}   crash rate: {s['crash_rate']:.1%}")
    L.append("")

    if s["best"]:
        b = s["best"]
        L.append(
            f"  BEST:       commit {b['commit']}  val_bpb={b['val_bpb']:.6f}  "
            f"mem={b['memory_gb']:.1f}GB"
        )
        L.append(f"              \"{b['description']}\"")
    if s["last_keep"]:
        k = s["last_keep"]
        L.append(
            f"  LAST KEEP:  commit {k['commit']}  val_bpb={k['val_bpb']:.6f}  "
            f"mem={k['memory_gb']:.1f}GB"
        )
        L.append(f"              \"{k['description']}\"")
    L.append("")

    imp = s["improvements"]
    L.append(f"  improvements across keeps: {imp['count']}")
    L.append(
        f"     mean: {imp['mean']:+.6f}   median: {imp['median']:+.6f}   "
        f"total: {imp['total']:+.6f}"
    )
    if s["recent_trend"]:
        t = s["recent_trend"]
        arrow = "↓ improving" if t["improving"] else "↑ regressing"
        L.append(
            f"  recent trend (last {t['window']} keeps): {t['first_val_bpb']:.6f} → "
            f"{t['last_val_bpb']:.6f}  Δ={t['delta']:+.6f}  {arrow}"
        )
    L.append("")

    if s["by_status"]:
        L.append("  by status:")
        for status, info in s["by_status"].items():
            L.append(
                f"     {status:8s}  n={info['count']:3d}  "
                f"mean val_bpb={info['mean_val_bpb']:.6f}  "
                f"mean mem={info['mean_memory_gb']:.1f}GB"
            )
        L.append("")

    if top:
        L.append(f"  TOP {len(top)} KEEPS (lowest val_bpb):")
        L.append("    commit    val_bpb    mem_gb   description")
        for r in top:
            L.append(
                f"    {r['commit']:7s}  {r['val_bpb']:.6f}  {r['memory_gb']:>5.1f}    "
                f"{r['description'][:60]}"
            )
        L.append("")

    if chart:
        L.append(chart)
        L.append("")
    return "\n".join(L)


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize autoresearch results.tsv")
    p.add_argument("--repo", default=str(DEFAULT_REPO), help="path to autoresearch checkout")
    p.add_argument("--results", default=None, help="explicit path to results.tsv")
    p.add_argument("--top", type=int, default=10, help="show top N keeps (default 10)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--chart", action="store_true", help="add ASCII chart of keeps")
    args = p.parse_args()

    if args.results:
        tsv = Path(args.results).expanduser()
    else:
        tsv = Path(args.repo).expanduser() / "results.tsv"
    _, rows = load_results(tsv)
    summary = summarize(rows)

    keeps = [r for r in rows if r["status"] == "keep" and r["val_bpb"] > 0]
    keeps_sorted = sorted(keeps, key=lambda r: r["val_bpb"])
    top = keeps_sorted[: args.top]

    payload = {
        "results_tsv": str(tsv),
        "summary": summary,
        "top_keeps": top,
    }
    chart = ascii_chart(rows) if args.chart else None
    if chart:
        payload["chart_ascii"] = chart

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_text(summary, top, chart))
    return 0


if __name__ == "__main__":
    sys.exit(main())