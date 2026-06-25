# Ollama shim pattern for tools that wrap node-llama-cpp

## Problem

Tools like QMD wrap `node-llama-cpp` for local LLM inference. Default path downloads ~2GB of GGUF models and tries to compile llama.cpp from source — fails on hosts without CUDA Toolkit, slow on CPU, and conflicts with other models loaded in ollama.

Callers in these tools (e.g. `qmd.ts`, `store.ts`) use **two different access paths** to the LLM:

1. **`withLLMSession(fn, options)`** — the "blessed" path with lifecycle management
2. **`getDefaultLlamaCpp()`** — direct access, bypassing the session abstraction

A naive shim that only replaces `withLLMSession` leaves the second path untouched, causing it to trigger the bundled GGUF download even when the env var is set. Example blocker: `store.ts:2313` calls `getDefaultLlamaCpp().expandQuery(query)` for query expansion, so `qmd query` (deep search) still fails in ollama mode unless that call site is patched.

## Solution: branch `getDefaultLlamaCpp()` itself

Make `getDefaultLlamaCpp()` return an ollama-backed facade when the env var is set. The facade has the same method signatures as the bundled `LlamaCpp` class. **No call-site changes needed** in any caller.

### Pattern

```typescript
// src/llm.ts
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

```typescript
// src/qmd.ts (only the conditional-import pattern for withLLMSession callers)
import { ..., pullModels, ... } from "./llm.js";
const llmModule = await (process.env.QMD_USE_OLLAMA === "1"
  ? import("./llm_ollama.js")
  : import("./llm.js"));
const withLLMSession = llmModule.withLLMSession;
```

The facade in `llm_ollama.ts` exposes both `getDefaultLlamaCpp()` (singleton) and `withLLMSession()` (per-call session), so both access paths route through ollama when the env var is set.

## Full facade code

```typescript
// src/llm_ollama.ts — drop-in OllamaLlamaCpp facade
import type {
  ILLMSession,
  EmbeddingResult,
  EmbedOptions,
  RerankDocument,
  RerankResult,
  RerankOptions,
  Queryable,
} from "./llm.js";

const OLLAMA_BASE = process.env.OLLAMA_HOST || "http://127.0.0.1:11434";
const EMBED_MODEL = process.env.QMD_OLLAMA_EMBED_MODEL || "bge-m3:latest";
const EXPAND_MODEL = process.env.QMD_OLLAMA_EXPAND_MODEL || "qwen3:8b";

async function ollamaEmbed(text: string, model: string): Promise<number[]> {
  const resp = await fetch(`${OLLAMA_BASE}/v1/embeddings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, input: text }),
  });
  if (!resp.ok) throw new Error(`ollama embed HTTP ${resp.status}`);
  const data = await resp.json() as { data: Array<{ embedding: number[] }> };
  return data.data[0].embedding;
}

async function ollamaExpand(query: string, context?: string): Promise<Queryable[]> {
  const prompt = [
    "You generate query variations for a hybrid BM25 + vector search engine.",
    "Return your answer as a JSON array of objects with keys 'type' and 'text'.",
    "Allowed types: 'lex' (keyword variant), 'vec' (paraphrase).",
    "Generate at most 2 entries. Output ONLY the JSON array.",
    "",
    context ? `Collection context: ${context}` : "",
    `Original query: ${query}`,
    "",
    "JSON array:",
  ].filter(Boolean).join("\n");

  const resp = await fetch(`${OLLAMA_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: EXPAND_MODEL,
      messages: [{ role: "user", content: prompt }],
      stream: false,
      options: { temperature: 0.3, num_predict: 200 },
    }),
  });
  const data = await resp.json() as { message: { content: string } };
  return parseExpansion(data.message?.content || "", query);
}

async function ollamaRerank(query: string, docs: RerankDocument[]): Promise<RerankResult> {
  // Cosine-similarity rerank via bge-m3 (workaround for ollama's BERT crash bug)
  const queryEmb = await ollamaEmbed(query, EMBED_MODEL);
  const results: Array<{file: string; score: number; index: number}> = [];
  for (let i = 0; i < docs.length; i++) {
    const doc = docs[i];
    const docEmb = await ollamaEmbed(
      doc.title ? `${doc.title}\n\n${doc.text}` : doc.text,
      EMBED_MODEL
    );
    results.push({ file: doc.file, score: cosineSim(queryEmb, docEmb), index: i });
  }
  results.sort((a, b) => b.score - a.score);
  return { results, model: `${EMBED_MODEL} (cosine similarity)` };
}

function cosineSim(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i]; na += a[i] ** 2; nb += b[i] ** 2;
  }
  const denom = Math.sqrt(na) * Math.sqrt(nb);
  return denom === 0 ? 0 : dot / denom;
}

class OllamaLlamaCpp {
  async embed(text: string, options?: EmbedOptions) {
    return withLLMSession(s => s.embed(text, options));
  }
  async embedBatch(texts: string[]) {
    return withLLMSession(s => s.embedBatch(texts));
  }
  async expandQuery(query: string, options?: { context?: string; includeLexical?: boolean }) {
    return withLLMSession(s => s.expandQuery(query, options));
  }
  async rerank(query: string, documents: RerankDocument[], options?: RerankOptions) {
    return withLLMSession(s => s.rerank(query, documents, options));
  }
}

let defaultFacade: OllamaLlamaCpp | null = null;
export function getDefaultLlamaCpp(): OllamaLlamaCpp {
  if (!defaultFacade) defaultFacade = new OllamaLlamaCpp();
  return defaultFacade;
}

class OllamaLLMSession implements ILLMSession {
  // ... implements embed, embedBatch, expandQuery, rerank, release ...
}

export async function withLLMSession<T>(fn: (s: ILLMSession) => Promise<T>) {
  const session = new OllamaLLMSession();
  try { return await fn(session); } finally { session.release(); }
}
```

## Why this pattern works for any `node-llama-cpp` wrapper

- **Single-branch dispatch** — `getDefaultLlamaCpp()` becomes the choke point. Every code path (session-managed OR direct) routes through it.
- **Method-signature compatible** — the facade has the same async method names as the bundled class, so callers like `await llm.expandQuery(q)` work unchanged.
- **Stateless facade, stateful session** — `OllamaLlamaCpp` methods each spin up a short-lived `OllamaLLMSession` via `withLLMSession`. The facade itself is a singleton; sessions are per-call.
- **Loose JSON parsing** — ollama chat models often emit prose around the JSON. The expansion parser strips code fences, extracts the `[...]` block, retries with single→double quotes, and falls back to the original query if parsing fails.

## What doesn't work (validated failures)

- **`bge-reranker-v2-m3-GGUF` via ollama** — crashes with `GGML_ASSERT(n_outputs_max <= cparams.n_outputs_max)`. The GGUF is encoder-only BERT but ollama's llama.cpp wrapper treats it as a causal LM and tries to allocate output buffers for the full 8192-token context.
- **`Qwen3-Reranker-4B` (DevQuasar variant) as a yes/no classifier** — top token is "Okay" not "yes"/"no". Doesn't behave as a clean reranker fine-tune.
- **Top-level `await import()` in non-ESM files** — qmd's package.json says `"type": "module"` so it works, but if you try this in a CJS file you'll get `ERR_AMBIGUOUS_MODULE_SYNTAX`.
- **`eval('require("node:module")')` to get `createRequire` synchronously** — same ERR_AMBIGUOUS issue if top-level await is present in the file.
- **`bun --cwd PATH run X` syntax** — the `--cwd` flag gets interpreted as a `bun run` option. Use `cd /d PATH && bun run X` in .cmd wrappers.
- **`bun run X -- Y` to pass positional args** — the `--` separator confuses `bun.cmd`'s argv parsing. Use `bun run X Y` directly.

## When to consider the cosine-similarity rerank workaround

The bge-m3 cosine-similarity rerank is a pragmatic fallback when:
- A real cross-encoder reranker isn't available via your HTTP backend
- The signal loss vs a true reranker is acceptable (modest improvement over vector search alone)
- Latency matters — cosine-similarity via the existing embed model is ~400ms per chunk, fast enough for top-30 rerank in ~12s

If a real cross-encoder is needed, the alternative paths are:
1. Run a separate rerank service (vLLM, TGI, llama-server) outside of ollama
2. Wait for ollama to fix the BERT encoder support and use `bge-reranker-v2-m3-GGUF` directly
3. Fine-tune a generative LM specifically as a yes/no classifier (the Qwen3-Reranker fine-tune, not the base Qwen3-8B)