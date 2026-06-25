"""Smoke test for the new synthesis persistence tools.

Run from the project root: ``python tests/smoke_synthesis.py``

Exercises the full path:
  1. save_synthesis → file on disk
  2. list_syntheses → sees the new entry
  3. load_synthesis → returns the full memo
  4. list_all_syntheses → sees the new entry

Uses the silver-comex-inventory project (a known-existing project).
"""
import asyncio
import sys
from pathlib import Path

# Make src importable without install.
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

from research_project_mcp.storage import (  # noqa: E402
    DEFAULT_STORAGE_ROOT,
    list_all_syntheses,
    list_syntheses,
    load_synthesis,
    save_synthesis,
)


async def main() -> int:
    slug = "silver-comex-inventory"
    question = "Smoke test: is the registered ratio still above the 0.25 trigger?"

    print(f"== Saving synthesis to {slug!r} ==")
    meta = save_synthesis(
        DEFAULT_STORAGE_ROOT,
        slug,
        question=question,
        memo="# Test synthesis\n\nThis is a smoke-test memo. Should round-trip cleanly.",
        dossier={
            "focus_hypotheses": ["H1", "H2"],
            "evidence_ranked": [
                {"evidence_id": "E1", "claim": "Test evidence 1"},
                {"evidence_id": "E2", "claim": "Test evidence 2"},
            ],
            "contradictions": [],
            "open_questions": [{"id": "Q1", "text": "Test question"}],
            "follow_up_suggestions": ["Test follow-up"],
        },
        confidence_overall=0.42,
    )
    print(meta)
    sid = meta["synthesis_id"]
    print()

    print(f"== list_syntheses({slug!r}, limit=3) ==")
    items = list_syntheses(DEFAULT_STORAGE_ROOT, slug, limit=3)
    assert items, "list_syntheses returned empty"
    assert items[0]["synthesis_id"] == sid, "newest first invariant failed"
    print(f"  count={len(items)}; newest={items[0]['synthesis_id']}")
    print(f"  question={items[0]['question'][:60]}…")
    print()

    print(f"== load_synthesis({slug!r}, {sid!r}) ==")
    full = load_synthesis(DEFAULT_STORAGE_ROOT, slug, sid)
    assert full["question"] == question
    assert full["memo"].startswith("# Test synthesis")
    assert full["confidence_overall"] == 0.42
    assert len(full["dossier"]["evidence_ranked"]) == 2
    print(f"  memo len={len(full['memo'])}; dossier keys={list(full['dossier'].keys())}")
    print()

    print("== list_all_syntheses(limit=5) ==")
    all_items = list_all_syntheses(DEFAULT_STORAGE_ROOT, limit=5)
    assert all_items, "list_all_syntheses returned empty"
    found = [s for s in all_items if s["synthesis_id"] == sid]
    assert found, "newest entry not in all-projects list"
    print(f"  count={len(all_items)}; our entry present={bool(found)}")
    print()

    print("[OK] all synthesis persistence helpers round-trip cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
