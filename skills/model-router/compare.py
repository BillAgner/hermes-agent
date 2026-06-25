"""
compare.py — equivalence scoring for shadow-validation.

Primary: cosine similarity over embeddings from the local bge-m3 model
(already on ollama at hf.co/gpustack/bge-m3-GGUF:Q8_0).

Fallback: difflib.SequenceMatcher ratio (no model needed, much noisier).

Tertiary: caller can pass method="llm_judge" to defer to a future LLM-as-judge.
"""
from __future__ import annotations

import math
import time
from difflib import SequenceMatcher
from typing import Iterable, Tuple

import requests

from _config import get, get_float, get_int

OLLAMA_URL = get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = get(
    "EMBED_MODEL", "hf.co/gpustack/bge-m3-GGUF:Q8_0"
)
EMBED_TIMEOUT = get_int("EMBED_TIMEOUT", 30)

# Thresholds. Embedding cosine above this is considered "no meaningful difference"
# for most tasks. Text-similarity threshold is lower because SequenceMatcher is
# stricter (character-level).
EMBED_PASS_THRESHOLD = get_float("EMBED_PASS_THRESHOLD", 0.95)
TEXT_PASS_THRESHOLD = get_float("TEXT_PASS_THRESHOLD", 0.85)


# ---------- Embeddings ----------

def _embed_once(text: str, model: str = EMBED_MODEL) -> list[float]:
    """Single-shot embedding call. ollama returns {"embedding": [...]}."""
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=EMBED_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    emb = data.get("embedding")
    if not emb:
        raise RuntimeError(f"ollama returned no embedding: {data}")
    return emb


def get_embedding(text: str, model: str = EMBED_MODEL) -> list[float]:
    """Public entry point. bge-m3 has an 8K-token context; truncate safely."""
    if not text:
        # Match a zero vector for empty input so callers can compare uniformly.
        return [0.0] * 1024
    if len(text) > 6000:  # conservative, leaves headroom for tokenizer overhead
        text = text[:6000]
    return _embed_once(text, model)


# ---------- Math ----------

def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity in [-1, 1] over equal-length vectors."""
    a = list(a)
    b = list(b)
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def text_similarity(a: str, b: str) -> float:
    """Character-level SequenceMatcher ratio in [0, 1]."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ---------- Public API ----------

def compare(
    a: str,
    b: str,
    method: str = "auto",
) -> Tuple[float, str]:
    """Return (similarity, method_used). method in {auto, embedding, text}.

    auto: try embedding first, fall back to text if the embedding call fails
          (e.g., ollama not running, model not pulled). Never raises for the
          embedding backend being unavailable.
    """
    if method == "auto":
        try:
            score = cosine_similarity(get_embedding(a), get_embedding(b))
            return score, "embedding"
        except Exception as e:  # noqa: BLE001 — we want any backend failure to fall through
            # One retry with a tiny sleep helps if ollama was just busy loading the embed model
            time.sleep(1)
            try:
                score = cosine_similarity(get_embedding(a), get_embedding(b))
                return score, "embedding"
            except Exception:
                score = text_similarity(a, b)
                return score, f"text (embedding fallback: {type(e).__name__})"
    if method == "embedding":
        ea, eb = get_embedding(a), get_embedding(b)
        return cosine_similarity(ea, eb), "embedding"
    if method == "text":
        return text_similarity(a, b), "text"
    raise ValueError(f"unknown method: {method!r}")


def passed(score: float, method: str) -> bool:
    """Whether `score` (from `compare`) clears the bar for the given method."""
    if method == "embedding":
        return score >= EMBED_PASS_THRESHOLD
    # text (or text fallback)
    return score >= TEXT_PASS_THRESHOLD
