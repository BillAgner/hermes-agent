---
name: model-router
description: "Routing decisions for promoting mature, well-understood tasks from the primary (cloud) model to the local ollama model. Maintains a routing table, runs shadow validation against cloud output, and writes approved decisions into config.yaml."
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [routing, cost, ollama, local-models, self-improvement, maturity, validation]
    related: [autonomous-ai-agents, hermes-agent]
    requires_env: [OPENROUTER_API_KEY]
    requires_models: [hf.co/gpustack/bge-m3-GGUF:Q8_0]
---

# model-router

A self-improvement mechanism for the ollama fallback model.

The idea: as tasks repeat and prove themselves stable ("mature"), validate
that the local model can handle them at parity with the cloud model. When
parity is demonstrated, route that task to local — free, private, no rate
limits. The cloud model stays primary for novel or hard work.

This is a routing LAYER on top of `fallback_providers`. `fallback_providers`
fires on **failure**; this fires on **observed equivalence** with a human
gate (`/routing promote`).

## Quick reference — `/routing` slash commands

When the user says `/routing <verb> [args]`, run the corresponding
`promote.py` subcommand. Storage is `$HERMES_HOME/routing/table.json` and
`$HERMES_HOME/routing/validation_log.jsonl`. The CLI is at
`$HERMES_HOME/routing/promote.py`.

| Slash           | CLI                                                                | Purpose                                          |
|-----------------|--------------------------------------------------------------------|--------------------------------------------------|
| `/routing`      | `python $HERMES_HOME/routing/promote.py list`                       | Show all tracked tasks                           |
| `/routing show <id>` | `python $HERMES_HOME/routing/promote.py show <id>`             | Full record for one task                         |
| `/routing status`   | `python $HERMES_HOME/routing/promote.py status`                | One-line summary                                 |
| `/routing mature <id>` | `python $HERMES_HOME/routing/promote.py mature <id>`         | Force-mark a task as mature                      |
| `/routing validate <id>` | `python $HERMES_HOME/routing/promote.py validate <id>`     | Run shadow validation now (cloud vs local)       |
| `/routing validate-all` | `python $HERMES_HOME/routing/promote.py validate-all`        | Validate every mature task                       |
| `/routing promote <id>` | `python $HERMES_HOME/routing/promote.py promote <id>`        | Promote a task to local (writes config.yaml)     |
| `/routing demote <id>`  | `python $HERMES_HOME/routing/promote.py demote <id>`         | Send a task back to cloud                        |
| `/routing auto-promote` | `python $HERMES_HOME/routing/promote.py auto-promote`        | Promote every task that passes the gate          |
| `/routing reset <id>`   | `python $HERMES_HOME/routing/promote.py reset <id>`          | Clear validation history (keeps success count)   |
| `/routing log [N]`      | `python $HERMES_HOME/routing/promote.py log [N]`             | Last N validation log entries                    |
| `/routing apply`        | `python $HERMES_HOME/routing/promote.py apply`               | Write all routing decisions into config.yaml     |

## Architecture

```
                   ┌──────────────────┐
                   │ user/main agent  │
                   └────────┬─────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
    ┌─────────────────┐         ┌─────────────────┐
    │  cloud (M3)     │  <────  │  local (Qwen)   │
    │  openrouter     │  shadow │  ollama         │
    └─────────────────┘   run   └─────────────────┘
              │                           ▲
              │  equivalence check        │
              └───────────┬───────────────┘
                          ▼
                 ┌────────────────┐
                 │ routing table  │
                 │ table.json     │
                 └────────┬───────┘
                          │ `apply` writes into
                          ▼
                 ┌────────────────┐
                 │  config.yaml   │
                 │  auxiliary.*   │
                 └────────────────┘
```

The comparison step uses **bge-m3** (already on the local ollama) for
embedding cosine similarity. Threshold: 0.95 (configurable via
`EMBED_PASS_THRESHOLD`). Falls back to `difflib.SequenceMatcher` at 0.85 if
ollama is unreachable.

## Maturity model

A task is `new` until it has `ROUTING_MATURITY_THRESHOLD=5` successful
runs, at which point it flips to `mature`. Once mature, it becomes a
candidate for shadow validation. A mature task with 3+ shadow runs all
above the threshold auto-promotes (with `auto-promote`).

The success counter is meant to be incremented by the agent after each
successful task completion. A simple convention:

```python
from router import record_success
record_success("task-id")
```

You can also pass `--reason` to `promote`/`demote` for an audit trail.

## Safety

- **Auto-promote is gated.** Default thresholds: 3 validation runs, all
  above 0.95, with the worst run still above 0.90. Below that, `auto-promote`
  is a no-op and the task stays on cloud.
- **`apply` is explicit.** Until you run `promote.py apply`, the routing
  table and the config.yaml are decoupled. You can experiment with
  promotions and roll back without touching the live agent config.
- **Demote is one command away.** If a promoted task starts misbehaving,
  `promote.py demote <id>` flips it back. `promote.py apply` then writes
  the change. The fallback chain (openrouter as primary, ollama as
  fallback) is unchanged and stays as the safety net.
- **No silent re-quantization.** The model used for validation is whatever
  is currently in `fallback_providers[0]` (the first `provider: custom`
  entry). If you change that, run `validate-all` to re-establish parity.

## What the skill expects from the agent

When the user invokes `/routing`, the agent should:
1. Run the corresponding CLI command.
2. Format the output for chat.
3. For `/routing promote <id>`, confirm with the user before running
   `apply` (or run `apply` automatically — see the user's preference).

## Tool install: check ollama before letting the tool download its own GGUF

When evaluating a new tool that needs an embedding, reranker, or small-LLM model, **check `ollama list` first**. If ollama already has a model that fits the role, configure the tool to call ollama's HTTP API instead of letting it download its own GGUF (usually 0.5–2 GB).

Reasons:
- Avoids ~2 GB of duplicate model downloads (model-router already routes cloud ↔ local; adding more local models via other tools just wastes disk + bandwidth).
- Stops GPU VRAM contention. The fallback chain already pulls 14B/32B into VRAM; a second set of GGUF loaders from a different process races for the same memory.
- Single source of truth. The `ollama list` inventory is what's audited; tooling outside that inventory is invisible to it.

How to apply:
- Run `ollama list` early when researching a tool's deps.
- If the role maps to an existing ollama model, plan the install to route through `http://localhost:11434` (embedding: `/v1/embeddings`, chat: `/api/chat`, rerank: `/api/rerank` if supported else `/api/chat` with yes/no + logprobs).
- If no ollama model fits, fall back to the tool's bundled GGUF and **add the model to the ollama pull list** as a follow-up so it joins the central inventory.

This pattern was identified during QMD (Query Markup Documents) planning — QMD ships its own node-llama-cpp trio, but ollama already had `bge-m3` + `bge-reranker-v2-m3` + `qwen3:8b` on disk.

For user-initiated tasks (Pattern A), the agent is encouraged to:
- After a task succeeds, call `record_success("<descriptive-id>")` to
  accumulate maturity.
- When the user says "this works the same every time", call
  `promote.py mature <id>` to short-circuit maturity.
- When delegating work, check the table first:
  ```python
  from router import get_task
  if (t := get_task("task-id")) and t["routing"]["current"] == "local":
      # delegate to local
  ```

## Files

| Path | Purpose |
|------|---------|
| `$HERMES_HOME/routing/table.json` | Routing decisions + maturity + validation history |
| `$HERMES_HOME/routing/validation_log.jsonl` | Append-only shadow-run log |
| `$HERMES_HOME/routing/promote.py` | CLI for everything |
| `$HERMES_HOME/skills/model-router/compare.py` | Embedding + text similarity |
| `$HERMES_HOME/skills/model-router/router.py` | Table CRUD + log appender |
| `$HERMES_HOME/skills/model-router/SKILL.md` | This file |

## Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `OLLAMA_URL` | `http://localhost:11434` | ollama base URL |
| `EMBED_MODEL` | `hf.co/gpustack/bge-m3-GGUF:Q8_0` | local embedding model |
| `EMBED_TIMEOUT` | `30` | seconds |
| `EMBED_PASS_THRESHOLD` | `0.95` | cosine sim to pass |
| `TEXT_PASS_THRESHOLD` | `0.85` | SequenceMatcher ratio to pass |
| `ROUTING_MATURITY_THRESHOLD` | `5` | successes before a task is mature |
| `OLLAMA_CHAT_TIMEOUT` | `600` | seconds for local chat |
| `CLOUD_CHAT_TIMEOUT` | `300` | seconds for cloud chat (validation only) |

## Limitations

- Shadow validation runs the cloud and local models on the **same** prompt
  in isolation. It does not capture the full conversation context, tool
  state, or system-prompt variance. Treat the similarity score as
  "is the local model competent for this prompt shape", not "is it a
  drop-in replacement for the full agent loop".
- Embedding similarity is a proxy. Two outputs can mean the same thing and
  have low cosine similarity (paraphrase), or differ subtly in fact and
  have high cosine similarity. For high-stakes tasks, escalate to
  LLM-as-judge (TODO: add a `--method llm_judge` path).
- The current seed set is hand-crafted. As you discover which auxiliary
  tasks fire most often, add more samples with
  `promote.py sample-add <id> --prompt "..." --system "..."`.
