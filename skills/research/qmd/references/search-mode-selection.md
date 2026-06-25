# Search-Mode Selection Guide

Choosing between `qmd_search`, `qmd_vector_search`, and `qmd_deep_search`.

## Decision tree

```
Is your query an exact string, code symbol, or error message?
├─ YES -> qmd_search (BM25)
└─ NO
   ├─ Is the query a concept or paraphrase ("how does X work")?
   │  └─ YES -> qmd_vector_search
   └─ Do you need best-effort recall across a large KB for agent context?
      └─ YES -> qmd_deep_search
```

## When each mode shines

### qmd_search (BM25)

**Strengths:**
- Exact phrase matching
- Code symbols (`auth.login`, `mcp__qmd__search`)
- Error strings ("ECONNREFUSED 127.0.0.1:11434")
- Fastest mode (<100ms typically)
- Doesn't need vector index (works on FTS-only too)

**Weaknesses:**
- Misses synonyms ("login" doesn't match "authentication")
- Misses paraphrases ("how to deploy" doesn't match "shipping to production")
- Misses semantically related but lexically different content

**Examples:**
- `qmd search "auth.login" --json`
- `qmd search "OBSIDIAN_VAULT_PATH" --json`
- `qmd search "ECONNREFUSED" --json`

### qmd_vector_search (sqlite-vec)

**Strengths:**
- Semantic similarity ("login" matches "authentication")
- Paraphrase tolerance ("how to deploy" matches "shipping to production")
- Works even with vague / underspecified queries

**Weaknesses:**
- Slower than BM25 (embedding computation)
- Can return false positives (semantically similar but topically unrelated)
- Misses exact strings if the embedding model tokenizes them weirdly

**Examples:**
- `qmd vector_search "how do I deploy to staging" --json`
- `qmd vector_search "what's the best way to debug a slow query" --json`

### qmd_deep_search (hybrid + rerank)

**Strengths:**
- Best recall across the KB
- Query expansion catches related concepts the original query missed
- Reranking sorts by true relevance, not just lexical/semantic distance
- Position-aware blending preserves exact matches in top results

**Weaknesses:**
- Slowest mode (multi-step pipeline, LLM calls)
- 5-15s typical per query (depends on KB size and reranker speed)
- Overkill for simple lookups

**Examples:**
- `qmd deep_search "agentic memory patterns" --json` (recalls docs about memory even if they don't say "agentic")
- `qmd deep_search "skill authoring conventions" --json` (recalls docs about SKILL.md format)

## Score thresholds

| Score | Meaning | Use |
|---|---|---|
| 0.8 - 1.0 | Highly relevant | Use directly |
| 0.5 - 0.8 | Moderately relevant | Use, but verify |
| 0.2 - 0.5 | Somewhat relevant | Inspect, often noise |
| 0.0 - 0.2 | Low relevance | Filter out |

Apply thresholds with `--min-score 0.3` (or higher for strict mode).

## Practical patterns

### Pattern 1: Find a note you know exists
```sh
qmd search "exact phrase from the note" --json -n 1
# if 0 hits -> qmd vector_search with a paraphrase
# if 1 hit -> qmd get "<path>" to retrieve full content
```

### Pattern 2: Build agent context for a task
```sh
# Step 1: cast a wide net with deep search
qmd deep_search "task description" --files --min-score 0.4
# Step 2: fetch the full content of the top hits
qmd multi-get "path1.md,path2.md,path3.md" --max-bytes 20480
```

### Pattern 3: Discover what's in the KB
```sh
# See collection overview
qmd status
# List all files in a collection
qmd ls <collection-name>
# Get a docid->path mapping for a search
qmd search "anything" --files -n 20
```