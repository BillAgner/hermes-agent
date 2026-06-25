"""Seed the WARM and COLD memory tiers with the curated MEMORY.md and USER.md content.

Phase E of the memory migration plan (2026-06-15). Parses the two source
files (read-only) into discrete facts, derives a stable content-hash ID
for each, and upserts into both tiers:

  - COLD tier (SQLite FTS5 at C:/Data/Hermes_0.17.0/cold_tier.db) for lexical
    search.
  - WARM tier (Qdrant local at C:/Data/Hermes_0.17.0/warm_tier.qdrant) for
    semantic vector search.

The script is idempotent: re-running it does NOT duplicate rows, because
the fact_id is content-hash-based. A "fact_id already in COLD" check
short-circuits the second run.

Source files are NOT modified. Smoke-test fixtures (C-1..C-5, W-1..W-5)
are NOT touched — new facts use a 'seed-mem-' / 'seed-usr-' prefix.

Usage:
    C:/Data/Hermes_0.17.0/hermes-agent/.venv/Scripts/python.exe \\
        C:/Data/Hermes_0.17.0/scripts/seed_memory_corpus.py
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time

# HERMES_HOME must be set BEFORE we import any agent modules, so
# that path resolution inside cold_tier / retrieval uses the right
# locations. The script targets the production tier locations
# (C:/Data/Hermes_0.17.0/cold_tier.db and C:/Data/Hermes_0.17.0/warm_tier.qdrant).
os.environ['HERMES_HOME'] = 'C:/Data/Hermes_0.17.0'

# Add the hermes-agent checkout to sys.path so the script can be run
# from anywhere (e.g. a cron-style invocation) without needing the
# venv activated with cwd = hermes-agent.
_AGENT_ROOT = 'C:/Data/Hermes_0.17.0/hermes-agent'
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

from agent.cold_tier import ColdTier  # noqa: E402
from agent.retrieval import WarmTier  # noqa: E402


# --- Constants ---------------------------------------------------------------

COLD_DB = 'C:/Data/Hermes_0.17.0/cold_tier.db'
WARM_QDRANT = 'C:/Data/Hermes_0.17.0/warm_tier.qdrant'
PROFILE_ID = 'default'
SOURCE_DIR = 'C:/Data/Hermes_0.17.0/memories'

DELIM = '§'

# Common stopwords for tag derivation. We want retrieval-friendly
# technical terms, not grammatical glue.
_TAG_STOPWORDS = {
    'the', 'a', 'an', 'is', 'in', 'on', 'of', 'to', 'for', 'and', 'or',
    'not', 'be', 'with', 'at', 'as', 'by', 'this', 'that', 'it', 'its',
    'if', 'from', 'into', 'about', 'use', 'using', 'used', 'so', 'do',
    'does', 'did', 'but', 'than', 'then', 'when', 'where', 'how', 'why',
    'what', 'which', 'who', 'whom', 'are', 'was', 'were', 'has', 'have',
    'had', 'will', 'would', 'should', 'could', 'can', 'may', 'might',
    'must', 'shall', 'one', 'two', 'three', 'all', 'any', 'some', 'no',
    'yes', 'also', 'just', 'only', 'very', 'more', 'most', 'less',
    'least', 'other', 'another', 'such', 'same', 'own', 'because',
    'before', 'after', 'above', 'below', 'up', 'down', 'out', 'over',
    'under', 'again', 'further', 'once', 'here', 'there', 'each',
    'every', 'both', 'few', 'many', 'much', 'own', 'still', 'now',
    'new', 'old', 'get', 'got', 'set', 'see', 'know', 'make', 'made',
    'run', 'runs', 'running', 'via', 'per', 'i', 'you', 'we', 'they',
    'he', 'she', 'his', 'her', 'their', 'our', 'your', 'my', 'me',
    'them', 'us', 'him',
}


# --- Parsing ----------------------------------------------------------------

def split_into_facts(path: str) -> list[str]:
    """Read a memory file and split on '§' delimiters, returning non-empty
    chunks with leading/trailing whitespace stripped.
    """
    with open(path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    chunks = text.split(DELIM)
    out: list[str] = []
    for c in chunks:
        c = c.strip()
        if c:
            out.append(c)
    return out


def extract_title(chunk: str, max_chars: int = 100) -> str:
    """Extract a short title from the first sentence of a chunk.

    Splits on '.', '!', '?', or ':' (colon is common in tech-speak),
    takes the first segment, and trims to max_chars.
    """
    # Split on the first sentence-end punctuation
    m = re.split(r'[.!?]\s|\n|:\s', chunk, maxsplit=1)
    first = m[0].strip() if m else chunk.strip()
    if len(first) > max_chars:
        first = first[: max_chars - 1].rstrip() + '…'
    return first or chunk[:max_chars].strip()


def derive_tags(title: str, max_tags: int = 8) -> list[str]:
    """Derive keyword tags from the title.

    Lowercases, splits on non-alphanumeric chars, drops stopwords and
    tokens shorter than 3 chars, dedupes, returns up to max_tags.
    """
    tokens = re.split(r'[^a-z0-9]+', title.lower())
    seen: list[str] = []
    for tok in tokens:
        if not tok or tok in _TAG_STOPWORDS or len(tok) < 3:
            continue
        if tok not in seen:
            seen.append(tok)
        if len(seen) >= max_tags:
            break
    return seen


def make_fact_id(prefix: str, content: str) -> str:
    """Stable content-hash ID. Same content -> same ID. 8-char SHA256
    prefix is plenty (16^8 = 4B distinct IDs) and keeps the ID readable.
    """
    h = hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]
    return f'{prefix}-{h}'


# --- Tier interaction -------------------------------------------------------

def existing_cold_ids(cold: ColdTier) -> set[str]:
    """Return all fact_ids currently stored in the COLD tier for this
    profile. Used to skip re-inserts on idempotent re-runs.

    We iterate via a low-level SQL query because ColdTier has no
    ``list_all()`` method. Cost is O(N) for N rows; the corpus is small.
    """
    import sqlite3
    conn = sqlite3.connect(str(cold.db_path))
    try:
        rows = conn.execute(
            'SELECT id FROM facts_fts WHERE profile_id = ?',
            (cold.profile_id,),
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


# --- Main -------------------------------------------------------------------

def main() -> int:
    print('=== Hermes memory corpus seeder ===')
    print(f'COLD: {COLD_DB}')
    print(f'WARM: {WARM_QDRANT}')
    print(f'PROFILE: {PROFILE_ID}')
    print()

    # 1) Build the list of facts from both source files.
    mem_facts = split_into_facts(os.path.join(SOURCE_DIR, 'MEMORY.md'))
    usr_facts = split_into_facts(os.path.join(SOURCE_DIR, 'USER.md'))
    print(f'Source: MEMORY.md -> {len(mem_facts)} facts')
    print(f'Source: USER.md   -> {len(usr_facts)} facts')
    total_in = len(mem_facts) + len(usr_facts)
    print(f'Total to consider: {total_in}')
    print()

    # 2) Open both tiers.
    cold = ColdTier(db_path=COLD_DB, profile_id=PROFILE_ID)
    warm = WarmTier(
        qdrant_path=WARM_QDRANT,
        profile_id=PROFILE_ID,
        reranker_gguf=None,
    )

    # 3) Build the set of already-known fact_ids so we can skip.
    known = existing_cold_ids(cold)
    print(f'Already in COLD: {len(known)} ids')

    # 4) Iterate facts and seed.
    new_count = 0
    skip_count = 0
    err_count = 0
    now_ts = int(time.time())
    for prefix, content in (
        *((('seed-mem', c) for c in mem_facts)),
        *((('seed-usr', c) for c in usr_facts)),
    ):
        fact_id = make_fact_id(prefix, content)
        title = extract_title(content)
        tags = derive_tags(title)

        if fact_id in known:
            skip_count += 1
            print(f'  SKIP  {fact_id}  {title[:60]}')
            continue

        # COLD: id-stable insert. add() is delete+insert by id within
        # the profile; safe to call on a fresh row.
        try:
            cold.add(
                fact_id,
                content,
                kind='observation',
                context_prefix=title,
                ts=now_ts,
            )
        except Exception as e:
            err_count += 1
            print(f'  ERR  COLD  {fact_id}  {e}')
            continue

        # WARM: vector upsert. add_fact() is idempotent on the
        # (profile, fact_id) -> uuid5 mapping, so re-running the
        # script is safe. importance 0.7 matches the curated-memory
        # default; tag bonus helps retrieval surface the right fact.
        try:
            warm.add_fact(
                fact_id=fact_id,
                content=content,
                context_prefix=title,
                importance=0.7,
                tags=tags,
                ts=now_ts,
            )
        except Exception as e:
            err_count += 1
            print(f'  ERR  WARM  {fact_id}  {e}')
            # The COLD insert is already done; we still count this as
            # a partial success. Continue to next fact.
            continue

        new_count += 1
        print(f'  ADD   {fact_id}  [{",".join(tags[:4])}]  {title[:60]}')

    print()
    total_after = total_in  # i.e. new + skip
    print(
        f'Seeded {new_count + skip_count} facts '
        f'({new_count} new, {skip_count} already existed)'
    )
    if err_count:
        print(f'  WARN: {err_count} errors during seeding (see above)')
    return 0 if err_count == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
