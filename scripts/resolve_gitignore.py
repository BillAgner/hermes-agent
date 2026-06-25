#!/usr/bin/env python3
"""Resolve rebase conflicts by taking HEAD's version + appending commit's added lines.

For .gitignore in this rebase: Bill's commit adds `venv312/` to the end.
HEAD has different content above. Resolution: keep HEAD's full content, append venv312/.
"""
from pathlib import Path

target = Path(r"C:\Data\Hermes_0.17.0\hermes-agent\.gitignore")
src = target.read_text(encoding="utf-8").splitlines(keepends=True)

# Find conflict regions
out = []
i = 0
while i < len(src):
    line = src[i]
    if line.startswith("<<<<<<< "):
        # Find =======
        j = i + 1
        while j < len(src) and not src[j].startswith("======="):
            j += 1
        # j is at =======
        # Find >>>>>>>
        k = j + 1
        while k < len(src) and not src[k].startswith(">>>>>>>"):
            k += 1
        # k is at >>>>>>> (inclusive)
        # HEAD content is i+1 to j-1, commit content is j+1 to k-1
        head_content = src[i+1:j]
        commit_content = src[j+1:k]
        # For .gitignore: take HEAD's content + the commit's added lines (which is just "venv312/\n")
        # Strategy: union the two — keep all unique lines
        combined = head_content + [c for c in commit_content if c.strip() and c not in head_content]
        out.extend(combined)
        i = k + 1
    else:
        out.append(line)
        i += 1

target.write_text("".join(out), encoding="utf-8")
print(f"OK: resolved, {len(out)} lines (was {len(src)})")
print(f"Last 5 lines:")
for line in out[-5:]:
    print(f"  {line.rstrip()}")