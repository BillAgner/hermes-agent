# Understand-Anything — Hermes Integration Notes

This directory is a Windows directory **junction** pointing to
`C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin\skills`.
Hermes walks the skills tree recursively, so each UA subfolder registers as
a separate skill whose `name` is taken from each `SKILL.md`'s frontmatter.

Do **not** add a `SKILL.md` at this root level — it would register as a
ninth fake skill. Other filenames (`README.md`, `INTEGRATION.md`) are ignored
by Hermes skill discovery.

---

## Skills available

| Skill name | What it does |
|---|---|
| `understand` | Run the full multi-agent analysis on a codebase (or path), produce `.understand-anything/knowledge-graph.json` |
| `understand-chat` | Q&A against the knowledge graph (asks "how does X work?") |
| `understand-dashboard` | Open the interactive web dashboard for the graph |
| `understand-diff` | Analyze impact of git diffs / PRs on the graph |
| `understand-domain` | Extract business domains, flows, process steps |
| `understand-explain` | Deep-dive explanation of a specific file or function |
| `understand-knowledge` | Analyze a Karpathy-pattern LLM wiki into a knowledge graph |
| `understand-onboard` | Generate an onboarding guide for new team members |

Use them by name: e.g. `/understand`, `/understand-diff`, `/understand-explain src/foo.py`.

---

## Prerequisites (already met on this host)

1. **Node.js ≥ 22** — verified `v24.15.0`
2. **pnpm ≥ 10** — installed at `C:\Users\bobup\AppData\Roaming\npm\pnpm` (10.6.2).
   Not on `PATH` by default. The UA skill assumes `pnpm` is on `PATH`; if it
   isn't when the agent runs `/understand`, the skill will error at Phase 0.5.
3. **Plugin core built** — `packages/core/dist/` exists with 49 files. Built
   once via `NODE_ENV=development pnpm install --frozen-lockfile` (the
   `prepare` script triggers the build).
4. **Plugin-root junction** — `C:\Users\bobup\.understand-anything-plugin`
   → `C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin`.
   The UA skill's Phase 0.5 plugin-root resolver checks `$HOME/.understand-anything-plugin`
   as one of several fallbacks; this junction makes that path resolve.

---

## Install layout (Bill's host, non-canonical HERMES_HOME)

```
C:\Data\Hermes\~\Understand-Anything\                            ← repo clone (Bill's fork / local checkout)
   └─ understand-anything-plugin/                                 ← plugin root (package.json + pnpm-workspace.yaml)
      ├─ agents/, hooks/, skills/, packages/, src/, ...          ← upstream plugin content
      └─ packages/core/dist/                                     ← built artifacts (do not commit; gitignored)

C:\Data\Hermes\skills\understand-anything\                       ← JUNCTION → ../~/.../understand-anything-plugin/skills
   ├─ understand/SKILL.md                                        ← registered as skill "understand"
   ├─ understand-chat/SKILL.md                                   ← registered as skill "understand-chat"
   ├─ ... (8 total)
   └─ INTEGRATION.md                                             ← this file (NOT a skill)

C:\Users\bobup\.understand-anything-plugin\                      ← JUNCTION → ../Data/Hermes/~/.../understand-anything-plugin
                                                                ← (so $HOME/.understand-anything-plugin resolves for UA's plugin-root lookup)
```

---

## Reinstall / update

**Refresh from upstream** (when Egonex-AI pushes new commits):

```powershell
cd C:\Data\Hermes\~\Understand-Anything
git pull --ff-only
$env:NODE_ENV = 'development'
pnpm install --frozen-lockfile    # re-runs prepare script, rebuilds core
```

Skill discovery picks up new `SKILL.md` files automatically (junctions are
followed). No re-junctioning needed.

**Re-run the install script** (idempotent; verifies existing junction):

```powershell
powershell -ExecutionPolicy Bypass -File C:\Data\Hermes\scripts\install_understand_anything_link.ps1
```

**If you ever delete the junction by mistake:**

```powershell
cmd /c mklink /J "C:\Data\Hermes\skills\understand-anything" "C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin\skills"
cmd /c mklink /J "C:\Users\bobup\.understand-anything-plugin" "C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin"
```

---

## What each UA skill writes where

The `/understand` skill writes to `<project>/.understand-anything/`:

```
.understand-anything/
├── knowledge-graph.json       ← the main graph (commit this — others can skip the pipeline)
├── meta.json                  ← last git commit hash + scan timestamps
├── config.json                ← autoUpdate, outputLanguage
├── .understandignore          ← exclusions (start with .gitignore + UA defaults)
├── intermediate/              ← scratch (gitignore)
├── tmp/                       ← scratch (gitignore)
└── .trash-<ts>/               ← phase-7 cleanup scratch (auto-purged after 7 days)
```

To share the graph with collaborators, commit everything **except**
`intermediate/` and `diff-overlay.json`. Large graphs (10 MB+) → use git-lfs.

---

## Known quirks (also captured as lessons)

1. **`pnpm` global via corepack needs admin on this host** (`C:\Program Files\nodejs\`
   is system-owned). The pnpm install at
   `C:\Users\bobup\AppData\Roaming\npm\` is the user-local workaround. Add it to
   the system PATH (`%AppData%\npm`) to make `/understand` happy without per-session
   PATH exports.

2. **First `/understand` run on a new project auto-creates `.understandignore`**
   and **prompts the user to review it before continuing** (Phase 0.5 step 2).
   This is intentional — confirms the exclusion list before scanning.

3. **Worktree redirect** — `/understand` detects git worktrees and redirects
   `.understand-anything/` output to the main repo root (so the graph survives
   worktree teardown). Set `UNDERSTAND_NO_WORKTREE_REDIRECT=1` to opt out.

4. **MSYS bash + cmd `mklink`** — passing the target path as a single quoted
   string through `cmd /c "..."` from MSYS bash produces a `C:\C:\...`
   double-prefixed target that resolves to nothing. Either pass args as an
   array (PowerShell `& cmd.exe /c mklink /J arg1 arg2`), or invoke cmd from
   a Windows-native shell. See lesson L-20260618-220100.

---

## Related

- Repo: https://github.com/Egonex-AI/Understand-Anything
- Local checkout: `C:\Data\Hermes\~\Understand-Anything`
- Install script: `C:\Data\Hermes\scripts\install_understand_anything_link.ps1`
- Complement: `liteparse` skill (spatial PDF), `markitdown` skill (broad format conversion)
