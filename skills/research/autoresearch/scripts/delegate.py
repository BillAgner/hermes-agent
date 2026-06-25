#!/usr/bin/env python3
"""
autoresearch delegate — emit the exact command to launch the autonomous
research loop on a remote CUDA host.

The autoresearch skill on this Windows box cannot itself run the 5-minute
CUDA training loop (no NVIDIA GPU). The protocol in `references/protocol.md`
expects the loop to be driven by a coding agent (Claude Code, Codex, etc.)
that edits `train.py`, runs `uv run train.py`, and reads `val_bpb`.

This script produces the launch command for that remote agent. By default
it PRINTS the command and exits — Hermes never silently SSHs or forks
background work without showing the human exactly what would run. Pass
--exec to actually run it (requires SSH access to be configured).

Usage:
  python delegate.py                                    # print the claude command
  python delegate.py --agent codex                      # use Codex CLI instead
  python delegate.py --agent claude-code --tag mar5     # custom branch tag
  python delegate.py --host user@box.example.com        # explicit SSH target
  python delegate.py --remote-dir ~/autoresearch       # custom remote path
  python delegate.py --exec                             # actually SSH and run

The script does NOT do any of the GPU work itself. The remote host does.
This host is the orchestrator — it prints the command, captures results,
and surfaces them via the dashboard.

Exit codes:
  0 = command printed (or executed successfully)
  1 = bad arguments
  2 = --exec failed (ssh missing or unreachable)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = Path("C:/Data/Hermes/~/autoresearch")
DEFAULT_REMOTE_DIR = "~/autoresearch"


AGENT_COMMANDS = {
    "claude-code": {
        "bin": "claude",
        "flags": [
            "--workdir {remote_dir}",
            "--allowedTools 'Bash,Read,Edit,Write,Grep,Glob'",
            "--max-turns 9999",
        ],
        "prompt": (
            "Read program.md and kick off a new autoresearch run. "
            "Use tag {tag}. Run autonomously until interrupted."
        ),
    },
    "codex": {
        "bin": "codex",
        "flags": [
            "--cd {remote_dir}",
            "-q 'Read program.md and kick off a new autoresearch run. "
            "Use tag {tag}. Run autonomously until interrupted.'",
        ],
        "prompt": "",
    },
    "opencode": {
        "bin": "opencode",
        "flags": ["--directory {remote_dir}"],
        "prompt": (
            "Read program.md and kick off a new autoresearch run. "
            "Use tag {tag}. Run autonomously until interrupted."
        ),
    },
}


def build_remote_command(agent: str, tag: str, remote_dir: str) -> str:
    """Build the command to run on the remote host."""
    cfg = AGENT_COMMANDS[agent]
    if cfg["prompt"]:
        flags = " ".join(f.replace("{remote_dir}", remote_dir)
                         for f in cfg["flags"])
        return f'{cfg["bin"]} -p "{cfg["prompt"].format(tag=tag)}" {flags}'
    # codex-style: prompt is already inside -q
    return (
        f'{cfg["bin"]} '
        + " ".join(f.replace("{remote_dir}", remote_dir).replace("{tag}", tag)
                   for f in cfg["flags"])
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Build (and optionally run) the command to launch "
                    "autoresearch on a remote CUDA host.",
    )
    p.add_argument("--agent", choices=list(AGENT_COMMANDS), default="claude-code",
                   help="which agent CLI to use (default: claude-code)")
    p.add_argument("--tag", default=None,
                   help="branch tag like 'mar5' (default: today's date like jun21)")
    p.add_argument("--host", default=None,
                   help="SSH target like 'user@box.example.com' "
                        "(default: AUTORESEARCH_REMOTE_HOST env var)")
    p.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR,
                   help=f"path on the remote host (default: {DEFAULT_REMOTE_DIR})")
    p.add_argument("--exec", action="store_true",
                   help="actually SSH and run (requires ssh + host configured)")
    args = p.parse_args()

    # Default tag = today's date
    if args.tag is None:
        from datetime import datetime
        args.tag = datetime.now().strftime("%b%d").lower()

    # Resolve host
    host = args.host or __import__("os").environ.get("AUTORESEARCH_REMOTE_HOST", "")

    cmd = build_remote_command(args.agent, args.tag, args.remote_dir)

    if args.exec:
        if not host:
            print("[FAIL] --exec requires --host or AUTORESEARCH_REMOTE_HOST env",
                  file=sys.stderr)
            return 1
        if shutil.which("ssh") is None:
            print("[FAIL] ssh not found in PATH", file=sys.stderr)
            return 2
        ssh_cmd = ["ssh", host, cmd]
        print(f"[delegate] running on {host}:")
        print(f"  {' '.join(ssh_cmd)}")
        try:
            rc = subprocess.call(ssh_cmd)
            return rc
        except KeyboardInterrupt:
            return 130
    else:
        # Print mode: never run anything
        print("# autoresearch delegate — copy/paste onto the remote CUDA host")
        print("# (or run this script with --exec if SSH is configured)")
        print()
        if host:
            print(f"# remote host: {host}")
            print(f"#   ssh {host}  '{cmd}'")
            print()
        print(f"# raw command ({args.agent}):")
        print(f"  {cmd}")
        print()
        print("# one-line launch (once you have ssh configured):")
        if host:
            print(f"  python delegate.py --agent {args.agent} --tag {args.tag} "
                  f"--host {host} --exec")
        else:
            print(f"  AUTORESEARCH_REMOTE_HOST=user@box python delegate.py "
                  f"--agent {args.agent} --tag {args.tag} --exec")
        print()
        print("# after the run, pull results.tsv back to this host:")
        print(f"  scp {host or 'user@box'}:{args.remote_dir}/results.tsv "
              f"{DEFAULT_REPO}/")
        return 0


if __name__ == "__main__":
    sys.exit(main())