---
name: research-project
description: "Persistent structured epistemic state for multi-session research. Track hypotheses with confidence, accumulate evidence with source weight, record contradictions and dead-ends. State lives at C:\\Data\\Hermes\\research_projects\\<slug>\\state.json and mirrors to open-notebook for source browsing. Backed by an MCP server with 17 rp_* tools. Use when research will span multiple sessions, when you need to remember hypotheses/evidence/contradictions across context, or when Bill explicitly asks to start or update a research project."
version: 0.1.0
author: Bill Agner
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, knowledge-base, hypothesis-tracking, mcp, long-running]
    related_skills: [open-notebook, arxiv, research-paper-writing, last30days]
---

# research-project

Persistent **structured epistemic state** for research that will outlive
a single session. NOT content storage — that's what open-notebook does.
This primitive tracks *what I currently believe, why I believe it, what
evidence backs it, what contradicts it, and what I've ruled out.*

Concrete example: a silver-COMEX investigation. The state file holds
H1 ("registered inventory is overstated vs physical availability"),
confidence 0.35, with reasoning; E1-E3 as evidence items with source
URLs and weights; Q1-Q2 as open sub-questions; and a chronological
timeline of every update. Next session, the agent loads this state and
picks up exactly where it left off — no re-derivation, no lost
reasoning, no duplicate searches.

The MCP server is registered as `research_project` and exposes 17
tools (all prefixed `rp_`). State is canonical on disk; open-notebook
is a browseable mirror for source URLs.

## When to use

Reach for this primitive when:

- Bill says "let's research X", "investigate Y", "track Z over time"
- the topic benefits from accumulating evidence across sessions
  (commodity prices, company due diligence, technical deep-dives,
  scientific topics, market monitoring, regulatory watch)
- you find yourself re-deriving the same context every session
- the answer is provisional and confidence should evolve with evidence

Do **not** use this for:

- One-shot Q&A, transient lookups, simple web searches,
  single-session debugging — just answer.
- Replacing open-notebook. open-notebook stores **curated content**
  (articles, notes, sources); research-project tracks **epistemic
  state** (beliefs, evidence, confidence). They complement each other
  — every evidence URL added here is mirrored to open-notebook
  automatically.
- Long-term knowledge bases. This is for **active investigations**.
  After a project concludes, archive it (`rp_archive_project`);
  don't keep adding evidence to a dead project.

## The data model

One JSON file at `C:\Data\Hermes\research_projects\<slug>\state.json`.
Stable ids (`H1`, `E1`, `Q1`, `C1`, `DE1`) are reused across updates.
Confidence and weight are floats in `[0.0, 1.0]`. The timeline grows
monotonically. Source URLs are mirrored to open-notebook.

```yaml
# Silver-COMEX inventory project (illustrative)
id: silver-comex-inventory
title: "Registered silver inventory at COMEX vs physical market"
scope: "Is registered silver inventory overstated? Track COMEX registered stocks vs physical availability and reconcile against ETF flows and COT data."
status: active
notebook_id: "notebook:abc123"          # open-notebook mirror

hypotheses:
  - id: H1
    claim: "Registered silver inventory is overstated vs physical availability."
    confidence: 0.35
    reasoning: "COMEX registered has held steady while premiums diverged in 2026."
  - id: H2
    claim: "Discrepancy is reporting lag, not fraud."
    confidence: 0.45
    reasoning: "COT data suggests normal settlement timing."

evidence:
  - id: E1
    claim: "Registered silver fell 12% Q1 2026 while prices rose 8%."
    sources: ["https://www.cmegroup.com/.../cmet-cot-report.pdf"]
    source_types: ["primary", "cme-bulletin"]
    weight: 1.0                       # primary regulatory filing
    note: "Direct from CME warehouse report."

questions:
  - id: Q1
    text: "What is the open-interest-to-registered ratio for May 2026?"
    status: open
  - id: Q2
    text: "Are ETFs also reporting divergences?"
    status: answered
    answer: "SLV holdings are flat; that argues against a coordinated squeeze narrative."

contradictions:
  - id: C1
    claim_a_id: H1
    claim_b_id: H2
    interpretation: "Both could be true if reporting lag dominates short-term but secular overstatement accumulates."

dead_ends:
  - id: DE1
    description: "Tried to scrape ComexBuster daily export logs — site uses JS rendering, not scrapable."

timeline:
  - timestamp: "2026-06-20T14:30:00+00:00"
    event: "project created"
  - timestamp: "2026-06-21T16:00:00+00:00"
    event: "updated H2 (confidence 0.40 → 0.45)"

related_projects: []
tags: ["commodities", "metals", "comex"]
```

## Tool reference

### Discovery

| Tool | When to call |
|------|-------------|
| `rp_health` | First call in a session, or when tool calls start failing. Probes both the storage root and the open-notebook mirror. |
| `rp_list_projects(status?)` | Cheap registry read. Run this before creating a project to check for an existing one on the same topic. |
| `rp_get_project(slug)` | Fetch the full canonical state of a single project by slug. |

### Lifecycle

| Tool | When to call |
|------|-------------|
| `rp_create_project(slug, title, scope, tags?, initial_hypotheses?, initial_questions?)` | Bootstrap a new project. Returns the project + `notebook_id`. |
| `rp_archive_project(slug)` | Mark a project concluded. State.json is preserved — never deleted. |
| `rp_link_session(slug, session_id)` | At end of a session that touched the project, record the Hermes session id so future sessions can find the conversation that produced the update. |

### State updates

| Tool | When to call |
|------|-------------|
| `rp_add_hypothesis(slug, claim, confidence, reasoning?)` | Add a new hypothesis. Auto-assigns the next `H<n>` id. |
| `rp_update_hypothesis(slug, hypothesis_id, confidence?, reasoning?, claim?)` | Confidence shifts after new evidence. Logs the change to the timeline. |
| `rp_open_question(slug, text)` | Record a new sub-question to investigate. Auto-assigns `Q<n>`. |
| `rp_answer_question(slug, question_id, answer)` | Mark a question answered with the resolved answer. |
| `rp_mark_dead_end(slug, description)` | Record a path that was tried and abandoned — saves future re-work. |
| `rp_add_contradiction(slug, claim_a_id, claim_b_id, interpretation)` | When two claims/hypotheses disagree, record it explicitly rather than burying it. |
| `rp_add_evidence(slug, claim, sources, weight, source_types?, note?, evidence_id?)` | Add a piece of evidence with provenance. URLs are mirrored to open-notebook automatically. |

### Read / report

| Tool | When to call |
|------|-------------|
| `rp_query_project(slug, max_evidence?, max_questions?)` | Compact structured summary for cheap context injection mid-session. |
| `rp_sync_into_context(max_projects?, status?)` | The auto-load magic — returns a plain-text block for system-prompt injection that lists every active project. Called by the Hermes loader at session start. |
| `rp_render_report(slug, format?)` | Full markdown memo (or JSON dump) of a project. Format = `"markdown"` (default) or `"json"`. |

### Manual override

| Tool | When to call |
|------|-------------|
| `rp_manual_override(slug, field_path, new_value, reason)` | Bill explicitly corrects any field by dot-path. Logs a `kind="manual"` timeline event with the reason. Validates the new value against the field's Pydantic type before writing. |

## Workflow patterns

### Pattern A — "Start a research project"

Bill says "let's research silver-COMEX". Run `rp_list_projects` first
to check for duplicates. If none, call `rp_create_project`:

```json
{
  "slug": "silver-comex-inventory",
  "title": "Registered silver inventory at COMEX vs physical market",
  "scope": "Is registered silver inventory overstated? Track COMEX registered stocks vs physical availability and reconcile against ETF flows and COT data.",
  "tags": ["commodities", "metals", "comex"],
  "initial_hypotheses": [
    {
      "id": "H1",
      "claim": "Registered silver inventory is overstated vs physical availability.",
      "confidence": 0.30,
      "reasoning": "Initial reading; will revise with COT data."
    },
    {
      "id": "H2",
      "claim": "Discrepancy is reporting lag, not fraud.",
      "confidence": 0.45,
      "reasoning": "Plausible alternative explanation; will falsify."
    }
  ],
  "initial_questions": [
    "What is the open-interest-to-registered ratio for May 2026?",
    "Are ETFs also reporting divergences?"
  ]
}
```

Response includes `"notebook_id": "notebook:abc123"` (or a
`"warning"` if the mirror was unreachable — project still exists,
just no browseable mirror yet).

### Pattern B — "Add evidence during a session"

Mid-session, I find a CME COT report. Add it as evidence; URLs are
mirrored to open-notebook so Bill can click through:

```json
{
  "slug": "silver-comex-inventory",
  "claim": "Registered silver fell 12% Q1 2026 while COMEX-monitored prices rose 8%.",
  "sources": [
    "https://www.cmegroup.com/market-data/reports/cmet-cot-report.pdf"
  ],
  "source_types": ["primary", "cme-bulletin"],
  "weight": 1.0,
  "note": "Direct from CME warehouse report; archived copy in /downloads."
}
```

`weight=1.0` reflects a primary regulatory filing. The MCP writes
canonical state first, then mirrors the URL to the project's
open-notebook notebook. If the mirror fails, canonical state still
saves and `mirror_warnings` lists which URLs failed.

### Pattern C — "Update hypothesis after new evidence"

After Pattern B, H1 should shift up. Update with reasoning so the
timeline stays useful:

```json
{
  "slug": "silver-comex-inventory",
  "hypothesis_id": "H1",
  "confidence": 0.35,
  "reasoning": "Up from 0.30 — CME COT report shows registered fell 12% vs price rise 8% (E1, weight 1.0). Not conclusive but shifts the prior."
}
```

The timeline appends: `"updated H1 (confidence 0.30 → 0.35)"`. Future
sessions can read the timeline and reconstruct how confidence evolved
without re-deriving it.

## The auto-load magic (`rp_sync_into_context`)

At session start, the Hermes loader calls `rp_sync_into_context`
(defaults: top 5 active projects) and prepends the returned block to
the system prompt. The block looks like:

```
# Active research projects (2)

## silver-comex-inventory — Registered silver inventory at COMEX vs physical market
tags: commodities, metals, comex
last active: 2026-06-22T09:15:00+00:00
hypotheses: H1=0.35, H2=0.45
open questions: 1; evidence items: 3

## company-acme-due-diligence — ACME Corp pre-investment review
tags: equities, due-diligence
last active: 2026-06-21T18:00:00+00:00
hypotheses: H1=0.60, H2=0.40, H3=0.20
open questions: 4; evidence items: 12
```

This means: even if Bill asks a vague question ("anything new on
silver?"), I can immediately call `rp_query_project(slug)` on the
relevant project and know where we left off — open questions,
hypotheses that need updating, what's been ruled out. The investigation
resumes mid-flight instead of restarting from zero.

## Manual override path

Three doors to correct the agent's state — pick whichever is convenient:

### 1. Chat to me

```
Bill: "set H1 to confidence 0.7"
```

I call:

```json
{
  "slug": "silver-comex-inventory",
  "field_path": "hypotheses.H1.confidence",
  "new_value": 0.7,
  "reason": "Bill manually corrected"
}
```

`field_path` uses dot-notation and must resolve to a real Pydantic
field or the call is rejected. Supported forms include:

- `scope` — top-level scalar
- `tags` — top-level list (replaced in place)
- `hypotheses.H1.confidence` — list item by id
- `evidence.E2.weight` — list item by id
- `questions.Q3.answer` — list item by id
- `hypotheses.1.claim` — list item by numeric index (fallback)

The override logs a `kind="manual"` timeline event with the reason,
so the audit trail distinguishes agent writes from human corrections.

### 2. Edit JSON state file directly

Open `C:\Data\Hermes\research_projects\<slug>\state.json` in VSCode,
edit, save. The MCP validates against the Pydantic schema on next
read — bad edits warn but don't silently corrupt.

### 3. Edit the open-notebook mirror note

Each hypothesis / evidence item / question is also a note in the
project's notebook (one notebook per project, named `[rp] <slug>`).
Edit the note in the open-notebook UI. On the next read, the MCP
detects the divergence and asks which side wins (canonical JSON or
mirror note). Default behaviour: canonical JSON wins; mirror is
best-effort.

## When to create a new project vs continue an existing one

Before creating, always run `rp_list_projects` and search by title or
tag for a similar active project. If one exists, add an entry to its
`related_projects` rather than creating a duplicate. If the topic is
genuinely a different question (e.g. "silver spot price drivers" vs
"silver-COMEX inventory"), create a new one and link them.

Slug rules: must be unique, kebab-case (lowercase + hyphens +
underscores + alphanumerics, no spaces). Examples:
`silver-comex-inventory`, `company-acme-due-diligence`,
`arxiv-llm-wiki-2026`.

## Source weight guidelines

| Weight | Use for |
|--------|---------|
| **1.0** | Peer-reviewed paper, primary regulatory filing (10-K, COT report, SEC EDGAR), court record |
| **0.8-0.9** | Established news source (Reuters, FT, WSJ, Bloomberg), official company press release, government statistical release |
| **0.6-0.7** | Industry publication (TrendForce, CoinDesk, S&P Capital IQ), secondary news, established analyst note |
| **0.4-0.5** | Blog post, opinion piece, expert commentary, niche trade press |
| **0.2-0.3** | Social media (Reddit, X), forum rumor, unverified wire copy |
| **0.0-0.1** | Anonymous, no provenance, single-source claim with no attribution |

When stacking evidence, the weighted average of `weight × claim` is a
useful informal indicator of how strong the case is for a hypothesis.

## Operational notes

- **Storage location**: `C:\Data\Hermes\research_projects\<slug>\state.json`
  is canonical; `_registry.json` is the slug-to-metadata index.
  Override root via `RESEARCH_PROJECTS_DIR` env var.
- **open-notebook is the mirror.** If it's down, the MCP degrades
  gracefully — canonical state still writes, with a `warning` field
  in the response. Re-syncing is automatic on next reachable call.
- **`rp_manual_override` uses dot-notation field paths** that must
  resolve to a real schema field, or the call is rejected. No silent
  typos.
- **Timeline is auto-logged.** Every state change appends a
  `TimelineEvent`. Manual overrides are logged with `kind="manual"`
  and the reason — the timeline is the audit trail.
- **Archive, don't delete.** `rp_archive_project` marks a project
  concluded; `state.json` is preserved. There is no `rp_delete_project`.
- **Session linking**: end any session that touched a project with
  `rp_link_session(slug, session_id)` so future sessions can find the
  conversation that produced the update.
- **State writes first.** Updates write canonical state to disk
  *before* the mirror. If the disk write fails, the mirror is never
  touched.
- **Reads are tolerant.** `rp_sync_into_context` swallows per-project
  load failures so a single corrupt file can't blank the system
  prompt.

## Files

- Source: `C:\Data\Hermes\~\research-project-mcp\packages\research-project-mcp\`
- Skill junction: `C:\Data\Hermes\skills\research\research-project` →
  `C:\Data\Hermes\~\research-project-mcp\skills\research-project`
- MCP binary: `C:\Data\Hermes\hermes-agent\venv\Scripts\research-project-mcp.exe`
- Hermes config: `mcp_servers.research_project` in `C:\Data\Hermes\config.yaml`
- Storage: `C:\Data\Hermes\research_projects\`
- Schema: `packages\research-project-mcp\src\research_project_mcp\schema.py`
  (`Hypothesis`, `Question`, `Evidence`, `Contradiction`, `DeadEnd`,
  `TimelineEvent`, `ResearchProject`)

## Anti-patterns

- **Don't create a project for every question.** Reserve projects for
  investigations that will span sessions or accumulate evidence.
- **Don't update hypotheses without reasoning.** Empty `reasoning`
  makes the timeline useless — future-you can't tell *why* confidence
  shifted.
- **Don't weight everything 0.9.** Distinguish primary (1.0), tier-1
  press (0.8-0.9), industry (0.6-0.7), opinion (0.4-0.5), social
  (0.2-0.3). Flat weights make the evidence stack meaningless.
- **Don't add evidence without sources.** `sources` can be empty in a
  pinch, but unsourced claims are rumors.
- **Don't use this as a replacement for memory.** Memory is for stable
  facts ("Bill prefers kebab-case slugs"); research-project is for
  active investigations.
- **Don't add evidence to a concluded/archived project.** Reopen to
  `active`, or start a follow-up project with a new slug.