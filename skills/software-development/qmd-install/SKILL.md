---
name: qmd-install
description: Install QMD (Query Markup Documents) MCP server for Hermes with ollama routing on Windows. Use when adding local hybrid BM25+vector search over markdown KBs (Obsidian vault, skill definitions, open-notebook exports). Covers clone, bun install, Windows-friendly build (tsc + shebang), ollama shim with OllamaLlamaCpp facade, MCP registration, vault bootstrap, skill junction, and end-to-end verification.
platforms: [windows, macos, linux]
---

# Installing QMD for Hermes (Windows-first)

QMD is a local hybrid-search MCP for markdown. This skill captures the **validated install pattern** from the 2026-06-25 install on `C:\Data\Hermes_0.17.0\`. The install pivots to **ollama routing** (via an `OllamaLlamaCpp` facade) instead of downloading QMD's bundled GGUF models, because the bundled path requires CUDA Toolkit and downloads ~2GB of model weights the host already has in ollama.

## When to use this skill

- Adding QMD to a new Hermes install (any version)
- Re-doing the install after a QMD upgrade
- Onboarding a new machine with ollama + Hermes

## Architecture: ollama-first, bundled as fallback

The install **uses ollama routing by default** (`QMD_USE_OLLAMA=1`). Bundled node-llama-cpp path stays available as a fallback (env var unset) for hosts without ollama.

Why ollama-first:
- **CUDA Toolkit wasn't installed** on the host -> node-llama-cpp's bundled GGUF build fails (or falls back to slow CPU)
- **2GB of GGUF downloads are wasteful** when ollama already has `bge-m3` + `qwen3:8b` + (ideally) a reranker
- **GPU contention matters** — Hermes agent runs 14B+32B models; the QMD pipeline shouldn't add another 2GB resident model

The shim is a **drop-in `OllamaLlamaCpp` facade** that has the same method signatures as the bundled `LlamaCpp` class (`embed`, `embedBatch`, `expandQuery`, `rerank`). The facade is exposed via `getDefaultLlamaCpp()` so call sites that bypass `withLLMSession` (e.g. `store.ts:2313`) route through ollama too — **no call-site changes needed**. See `references/ollama-shim-pattern.md` for the full facade code and design rationale.

## Single-script install

The validated installer is at `C:\Data\Hermes_0.17.0\scripts\install_qmd.ps1`. Copy it for new installs.

### What it does (12 steps)

1. **Clone QMD** -> `~\qmd\` (skip if `.git` present)
2. **`bun install`** in qmd source (skip if `node_modules\better-sqlite3` present)
3. **Build**: `bunx tsc -p tsconfig.build.json` + prepend shebang via `[System.IO.File]::WriteAllText` (skip if `dist\qmd.js` present)
4. **Smoke test**: `bun run qmd --version` in BOTH bundled and ollama modes
5. **Write launcher**: `scripts\qmd-mcp.cmd` with `QMD_USE_OLLAMA=1` env vars + `cd /d` + `bun run qmd mcp`
6. **Bootstrap vault**: create `~/Documents/Obsidian Vault/` with PARA layout (Daily/Projects/Inbox/Templates/References) + `.obsidian\` config
7. **Set `OBSIDIAN_VAULT_PATH`** in `.env`
8. **Backup config.yaml** with timestamped `.bak.qmdinstall.YYYYMMDD_HHMMSS`
9. **Patch config.yaml** via Python helper (`_patch_config_qmd.py`) to insert `qmd:` MCP block after `tradingview_desktop:`. NEVER use PowerShell regex `-replace` for this — see PowerShell gotchas below.
10. **Stage skill files** from `scripts\qmd_skill_payload\` to `~\qmd\skills\research\qmd\` (idempotent via SHA-256 compare)
11. **Junction** `skills\research\qmd\` -> `~\qmd\skills\research\qmd\` via `cmd.exe /c mklink /J`
12. **Verify**: `qmd --version` + `qmd status` in both modes

### Pre-flight requirements administratively

- `#Requires -RunAsAdministrator` (the junction step needs admin)
- `bun`, `node`, `git`, `python.exe` on PATH
- ollama running at `http://127.0.0.1:11434` with `bge-m3:latest` pulled

## Ollama shim files (the hard part)

The shim is **two files** that make QMD use ollama instead of bundled node-llama-cpp:

### 1. `src/llm_ollama.ts` (~300 lines)

Implements an **`OllamaLlamaCpp` facade** that has the same method signatures as the bundled `LlamaCpp` class, plus the `ILLMSession` interface for `withLLMSession`-style call sites:

- `embed(text)` -> `POST /v1/embeddings` with `bge-m3` (1024-dim)
- `embedBatch(texts)` -> sequential embed calls (ollama `/v1/embeddings` accepts arrays but sequential avoids memory pressure)
- `expandQuery(query)` -> `POST /api/chat` with `qwen3:8b` asking for JSON array of `{type, text}` lex/vec variants; parses loose JSON (tolerates code fences, single quotes, trailing commas)
- `rerank(query, documents)` -> **bge-m3 cosine similarity** between query embedding and each document embedding (fallback when a real cross-encoder isn't available)

Exports `withLLMSession<T>(fn, options)` with the same signature as `llm.ts` AND `getDefaultLlamaCpp()` returning the facade singleton.

### 2. `src/llm.ts` patch (~10 lines)

Add the facade import and branch `getDefaultLlamaCpp()` to return it when `QMD_USE_OLLAMA=1` is set:

```typescript
import * as ollamaFacade from "./llm_ollama.js";

// ... existing LlamaCpp class ...

export function getDefaultLlamaCpp(): LlamaCpp | any {
  if (process.env.QMD_USE_OLLAMA === "1") {
    return ollamaFacade.getDefaultLlamaCpp();
  }
  if (!defaultLlamaCpp) {
    defaultLlamaCpp = new LlamaCpp();
  }
  return defaultLlamaCpp;
}
```

This is the **key insight**: by branching `getDefaultLlamaCpp()` itself (not just `withLLMSession`), every call site — including `store.ts:2313` which calls `getDefaultLlamaCpp()` directly — routes through the ollama facade. **No patches needed in store.ts, qmd.ts, or any caller.**

### 3. `src/qmd.ts` patch (~5 lines) for `withLLMSession` callers

Replace the static `withLLMSession` import with a conditional dynamic import:

```typescript
// Before:
import { ..., withLLMSession, ... } from "./llm.js";

// After:
import { ..., pullModels, ... } from "./llm.js";
const llmModule = await (process.env.QMD_USE_OLLAMA === "1"
  ? import("./llm_ollama.js")
  : import("./llm.js"));
const withLLMSession = llmModule.withLLMSession;
```

Also patch the `status` command to skip the device/GPU section when ollama mode is active (the bundled path tries to build llama.cpp with CUDA and produces noise).

For the full facade code and design rationale, see **`references/ollama-shim-pattern.md`** in this skill.

## Critical gotchas (validated)

### PowerShell

1. **PowerShell `-replace` regex is parsed as commands.** Don't use `$x -replace '^(\s*)port:\s*\d+'` directly — the `\s`, `\d`, parens-with-content get interpreted as command tokens. Use a Python helper for any YAML/regex manipulation in PowerShell.

2. **`$ErrorActionPreference = 'Stop'` makes bun warnings fatal.** `bun install`'s `better-sqlite3` native build emits a warning that's not actually fatal, but PowerShell throws on it. Set `$ErrorActionPreference = 'Continue'` around `& bun install 2>&1 | Out-String` and check `$LASTEXITCODE` manually.

3. **UTF-8 .ps1 files with non-ASCII chars (`✓`, `→`, `—`) silently abort the parser.** Save with UTF-8 BOM if non-ASCII content is required: `[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($true))`. Otherwise strip to ASCII (`[OK]`, `->`, `--`).

4. **`cmd //c` from bash with paths containing spaces splits at the space.** Fix: write a `.bat` wrapper, or use PowerShell's `&` operator.

### Bun on Windows

5. **`bun --cwd PATH run X` is invalid syntax.** The `--cwd` flag is interpreted as a `bun run` option, not a global. Use `cd /d PATH && bun run X` in .cmd wrappers.

6. **`bun run X -- Y` doesn't pass Y through.** The `--` separator confuses `bun.cmd`'s argv parsing. Use `bun run X Y` (positional args after the script name).

### QMD-specific

7. **QMD's `bun run build` uses Unix shell pipes** (`cat -`, `chmod +x`) that don't work on Windows natively. Split into `bunx tsc -p tsconfig.build.json` + prepend shebang via PowerShell.

8. **`store.ts:2313` calls `getDefaultLlamaCpp()` directly** for `expandQuery`, bypassing `withLLMSession`. This is the blocker for ollama mode — **fixed** by branching `getDefaultLlamaCpp()` itself (see shim files above), so the facade catches both call paths.

9. **Ollama 0.30.10 has `/v1/embeddings` but NOT `/api/rerank`.** For cross-encoder reranking via ollama, you'd need `/api/chat` with a yes/no prompt + logprobs parsing — **not viable** because:
   - `bge-reranker-v2-m3-GGUF` (encoder-only BERT) crashes ollama with `GGML_ASSERT(n_outputs_max <= cparams.n_outputs_max)`
   - `Qwen3-Reranker-4B` (DevQuasar variant) doesn't behave as a clean yes/no classifier (responds with "Okay" instead of "yes"/"no")
   - **Workaround**: cosine-similarity rerank via bge-m3. Modest improvement over `qmd_vsearch`; same embedding signal.

10. **The conditional-import pattern in qmd.ts uses top-level await.** That requires ESM context. qmd's package.json already says `"type": "module"` so this works. If bundling to CJS, the dynamic import needs to move into an async IIFE.

## Verification

After install:

```powershell
# Bundled path (no env var)
cd "C:\Data\Hermes_0.17.0\~\qmd"
bun run qmd --version
# -> qmd 1.1.0 (d6f3688)

# Ollama path (the MCP launcher uses this)
cd "C:\Data\Hermes_0.17.0\~\qmd"
$env:QMD_USE_OLLAMA=1
$env:OLLAMA_HOST=http://127.0.0.1:11434
bun run qmd --version
# -> qmd 1.1.0 (d6f3688) + "[qmd] Using ollama-backed LLM shim"

# BM25 search
bun run qmd search "obsidian vault" --json -n 3
# -> returns top hits with scores 0.9+

# Vector search (loads bge-m3 on first call, ~270ms per query after)
bun run qmd vsearch "how to write daily notes" --json -n 3

# Deep search (query expansion + vector + cosine-rerank, ~10-20s typical)
bun run qmd query "skill authoring pattern" --json -n 3

# MCP server
"C:\Data\Hermes_0.17.0\scripts\qmd-mcp.cmd"
# -> starts MCP server on stdin/stdout, hermes mcp restart qmd to register
```

## Bootstrap collections

After install, index the collections:

```powershell
cd "C:\Data\Hermes_0.17.0\~\qmd"
$env:QMD_USE_OLLAMA=1
bun run qmd collection add "C:\Data\Hermes_0.17.0\skills" --name skills --mask "**\SKILL.md"
bun run qmd context add qmd://skills "Hermes Agent skill definitions..."
bun run qmd collection add "C:\Users\bobup\Documents\Obsidian Vault" --name obsidian --mask "**\*.md"
bun run qmd context add qmd://obsidian "Bill's personal Obsidian vault..."
bun run qmd embed  # ~70 seconds for 92 skills + 3 vault docs
```

## Files to back up

- `C:\Data\Hermes_0.17.0\scripts\install_qmd.ps1` (the installer)
- `C:\Data\Hermes_0.17.0\scripts\bootstrap_qmd_collections.ps1` (collection bootstrap)
- `C:\Data\Hermes_0.17.0\scripts\_patch_config_qmd.py` (Python helper)
- `C:\Data\Hermes_0.17.0\scripts\qmd-mcp.cmd` (launcher)
- `C:\Data\Hermes_0.17.0\scripts\qmd_skill_payload\` (skill source)
- `C:\Data\Hermes_0.17.0\~\qmd\src\llm_ollama.ts` (ollama shim)
- Patched `~\qmd\src\llm.ts` (the facade-import branch)
- Patched `~\qmd\src\qmd.ts` (the conditional dynamic import)