---
name: tradingview-desktop
description: "Use when TradingView Desktop needs to be launched with Chrome DevTools Protocol (CDP) enabled for the `mcp__tradingview__desktop__*` tools to work — the `mcp__tradingview__tv_launch` MCP tool does NOT auto-detect the Microsoft Store (UWP) install on this host, and the path changes on every Store update. Use this skill when chart automation, replay, or Pine Script injection from the agent is needed, or when `tv_launch` reports it can't find the binary."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [tradingview, cdp, chrome-devtools, uwp, microsoft-store, mcp, trading, charting, pine-script]
    related_skills: [mcp-server-setup]
---

# TradingView Desktop (CDP launch procedure)

## Overview

The `mcp__tradingview__desktop__*` tools (`chart_get_state`, `quote_get`, `pine_compile`, replay, alert management, etc.) drive a running TradingView Desktop instance over Chrome DevTools Protocol. The `mcp__tradingview__tv_launch` tool exists to start that instance — but on this host it fails to auto-detect the **Microsoft Store (UWP)** install location and returns a "binary not found" error.

This skill is the manual launch procedure: find the current exe, start it with `--remote-debugging-port=9222`, verify the CDP endpoint is reachable, then the MCP tools work as designed.

## When to use

- `mcp_tradingview_desktop_tv_health_check` reports "no CDP connection" → start TV manually first
- `mcp_tradingview_desktop_tv_launch` returns "TradingView Desktop not found" → the auto-detect failed; do it manually
- First automation task of the day — TV isn't always running on this host
- After a TradingView Store update (every few weeks) — the versioned directory name changes, re-list and update your launcher

## The recipe

### 1. Find the current exe

The MS Store install lives under `C:\Program Files\WindowsApps\` with a versioned directory name. The version changes on every Store update, so **always re-list** rather than caching the path:

```bash
ls "C:/Program Files/WindowsApps/" | grep -i TradingView.Desktop
```

You'll see one entry like `TradingView.Desktop_2.4.0.5077_x64__mhcfefhfbdmnd`. The exe is at:

```
C:\Program Files\WindowsApps\TradingView.Desktop_<VERSION>_x64__mhcfefhd\hfbdmnd\TradingView.exe
```

Note: the directory is read-locked to most users; you may need to `dir` from an elevated shell or use `Get-ChildItem` from PowerShell. `cmd /c dir` and `ls` from git-bash can usually read it because the path is canonicalized at the OS level.

### 2. Launch with CDP enabled

```bash
"C:/Program Files/WindowsApps/TradingView.Desktop_<VERSION>_x64__mhcfefhfbdmnd/TradingView.exe" \
    --remote-debugging-port=9222 &
```

Or from PowerShell, if a child window is preferred:

```powershell
Start-Process "C:\Program Files\WindowsApps\TradingView.Desktop_<VERSION>_x64__mhcfefhfbdmnd\TradingView.exe" `
    -ArgumentList "--remote-debugging-port=9222"
```

This opens TradingView Desktop. The `--remote-debugging-port` flag tells the embedded Chromium to expose a CDP endpoint on `127.0.0.1:9222` — exactly what the MCP server connects to.

### 3. Verify CDP is reachable

```bash
curl -s http://127.0.0.1:9222/json/version | head -20
```

You should see Chromium/CDP version info. If you get connection refused, the launch didn't bind the port (most often: antivirus stripping the flag, or another process grabbed 9222). Check `netstat -ano | grep 9222` for the listener PID.

### 4. The MCP tools now work

Any call to `mcp__tradingview__desktop__chart_set_symbol`, `mcp__tradingview__desktop__pine_compile`, `mcp__tradingview__desktop__replay_start`, etc. will drive the running instance. Start with `mcp_tradingview_desktop_tv_health_check` to confirm connectivity.

## Common pitfalls

### `tv_launch` MCP tool does NOT auto-detect MS Store installs

The auto-detect logic in the MCP server looks in `%LocalAppData%` and standard Program Files locations, then falls back to a list of known registry entries. The MS Store UWP sandboxed install path doesn't match any of those, so it returns "binary not found" even though the app is installed and working. **Don't waste time debugging the MCP tool — just use the manual launch above.**

### Path changes on every Store update

The `TradingView.Desktop_<VERSION>_x64__...` directory name is updated whenever Microsoft Store ships a new version (every few weeks, sometimes more). Never hardcode the path in scripts; always `ls` it. If a previously-working script suddenly fails, the first thing to check is "did TV update?".

### CDP port conflicts

Port 9222 is the Chromium default; some other dev tools (Chrome itself, Edge, certain VS Code extensions) may also try to bind it. If the launch silently fails to bind, pick another port (`--remote-debugging-port=9333`) and update the MCP config to match — or kill the conflicting listener.

### Antivirus stripping the `--remote-debugging-port` flag

Some endpoint-protection products strip unfamiliar Chromium flags. If CDP never binds even though the launch appears to succeed, check the Windows Defender / CrowdStrike application control logs. Whitelist the flag or use a launcher wrapper script.

### The exe lives in a locked-down directory

`C:\Program Files\WindowsApps\` is ACL-restricted to UWP system components. You can READ it but typically can't write or modify anything inside. That's fine for our use case (we only need to launch the exe), but it means you can't symlink the binary into a more convenient location without admin.

### UAC prompts on first launch

The MS Store app may show a UAC prompt the first time you launch it from a non-Store launcher. Click yes once; subsequent launches won't prompt. If the prompt is being suppressed (e.g. headless), launch from an elevated context.

## Verification checklist (run before trusting the launch)

- [ ] `ls "C:/Program Files/WindowsApps/" | grep TradingView.Desktop` returns exactly one entry
- [ ] The exe in that directory exists and is >50MB (sanity check: not a stub)
- [ ] `<that exe> --remote-debugging-port=9222` starts TradingView Desktop and the process stays running
- [ ] `curl -s http://127.0.0.1:9222/json/version` returns Chromium/CDP JSON
- [ ] `netstat -ano | grep 9222` shows the TV PID listening on 127.0.0.1:9222
- [ ] `mcp_tradingview_desktop_tv_health_check` returns `{connected: true, ...}`
- [ ] A trivial tool call works: `mcp__tradingview__desktop__chart_get_state` returns the current chart info

## Related

- **`mcp-server-setup`** — for debugging the MCP server connection itself (if the tools return errors even though CDP is up)
- **`hermes-agent-skill-authoring`** — frontmatter spec this skill follows
- **`memory-curator`** — the skill that extracted this stub from `MEMORY.md` on 2026-06-18; the curator can be re-run as the TradingView procedure evolves
