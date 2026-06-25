#!/usr/bin/env python3
"""Fix the broken line in api.ts caused by a patch-tool mishap.

The patch tool stripped the `\\` escape for `\` from the comment, leaving
the bytes:  `Hermes\\r\\nesearch_projects\\<slug>...`  (CR/LF split across
two lines, with the second line starting mid-path). Rolldown parses
`\\<` as an invalid Unicode escape.
"""
from pathlib import Path
import sys

p = Path(r"C:\Data\Hermes_0.17.0\hermes-agent\web\src\lib\api.ts")
src_bytes = p.read_bytes()

# Find the broken sequence in raw bytes
broken = b"    // C:\\Data\\Hermes_0.17.0\r\nesearch_projects\\<slug>\\state.json and is mirrored"
fixed = b"    // C:\\Data\\Hermes_0.17.0\\research_projects\\<slug>\\state.json and is mirrored"

if broken in src_bytes:
    new_bytes = src_bytes.replace(broken, fixed)
    p.write_bytes(new_bytes)
    print(f"[OK] fixed broken line in {p}")
else:
    print(f"[FAIL] broken pattern not found in {p}")
    # Show lines around 1152
    lines = src_bytes.split(b"\n")
    for i in range(1148, 1160):
        if i < len(lines):
            print(f"  line {i+1}: {lines[i][:120]!r}")
    sys.exit(1)