#!/usr/bin/env python3
"""Insert 'qmd:' MCP server block into Hermes config.yaml after the tradingview_desktop: block."""
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
content = config_path.read_text(encoding="utf-8")

qmd_block = (
    "  qmd:\n"
    "    command: C:\\Data\\Hermes_0.17.0\\scripts\\qmd-mcp.cmd\n"
    "    args:\n"
    "    - ''\n"
    "    enabled: true\n"
    "    timeout: 180\n"
    "    connect_timeout: 120\n"
)

if "  qmd:\n" in content and "    command: C:\\Data\\Hermes_0.17.0\\scripts\\qmd-mcp.cmd" in content:
    print("SKIP: qmd block already present")
    sys.exit(0)

# Find the tradingview_desktop: block start
marker = "  tradingview_desktop:\n"
idx = content.find(marker)
if idx == -1:
    print("FAIL: could not find 'tradingview_desktop:' marker")
    sys.exit(1)

# Walk forward to find end of the block: next top-level "  <word>:" entry
end_idx = idx + len(marker)
rest = content[end_idx:]
insert_pos = end_idx
for line in rest.split("\n"):
    if line.startswith("  ") and ":" in line and not line.startswith("    "):
        # next top-level key found
        break
    insert_pos += len(line) + 1

new_content = content[:insert_pos] + qmd_block + content[insert_pos:]
config_path.write_text(new_content, encoding="utf-8")
print("OK: qmd MCP block inserted after tradingview_desktop")