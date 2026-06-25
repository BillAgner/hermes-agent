"""Smoke test for the schema and storage layer.

Runs against a temp directory so it doesn't touch the real research_projects/.
Exercises the full lifecycle: create → add hypothesis → add evidence →
mark question answered → mark dead-end → save → reload → verify.

Invoke from the package root with::

    python tests/smoke_schema_storage.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Allow running as ``python tests/smoke_schema_storage.py`` without install.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from research_project_mcp import (  # noqa: E402
    Evidence,
    Hypothesis,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    Question,
    ResearchProject,
    create_project,
    load_project,
    save_project,
    schema,
    storage,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rp_smoke_"))
    try:
        # 1. Create a project.
        p = create_project(
            tmp,
            slug="silver-comex-inventory",
            title="COMEX silver registered vs eligible",
            scope="Why is registered falling? Who's buying? Implications for spot?",
            tags=["commodities", "silver", "comex"],
            initial_hypotheses=[
                {
                    "id": "H1",
                    "claim": "Commercial banks are draining physical via registered drain",
                    "confidence": 0.6,
                }
            ],
            initial_questions=[
                "Is the drain commercial or retail?",
                "Are futures spreads moving with inventory?",
            ],
        )
        assert p.status == "active"
        assert p.notebook_id is None, "notebook_id is set later by the MCP via open-notebook"
        assert len(p.hypotheses) == 1
        assert len(p.questions) == 2
        print(f"[OK] created project with id={p.id!r}, status={p.status!r}")

        # 2. Add evidence.
        p.evidence.append(
            Evidence(
                id="E1",
                claim="Registered fell 4.2% week-over-week as of 2026-06-19",
                sources=["https://silverdata.io/comex-silver"],
                source_types=["primary", "scraper"],
                weight=0.9,
            )
        )
        p.touch("added E1")
        save_project(tmp, p)
        print("[OK] added E1 evidence, weight=0.9")

        # 3. Answer a question.
        q1 = next(q for q in p.questions if q.id == "Q1")
        q1.status = "answered"
        q1.answer = "Initial reading: drain appears retail (SLV outflows dominant)."
        q1.answered = schema._utcnow_iso()
        p.touch("answered Q1")
        save_project(tmp, p)
        print("[OK] answered Q1")

        # 4. Mark a dead-end.
        p.dead_ends.append(
            schema.DeadEnd(
                id="DE1",
                description="SLV GLD filings for retail demand proxy — too lagged (45-day).",
            )
        )
        p.touch("recorded dead-end DE1")
        save_project(tmp, p)
        print("[OK] recorded DE1 dead-end")

        # 5. Add a contradiction.
        p.evidence.append(
            Evidence(
                id="E3",
                claim="Eligible rose 1.1% same week",
                sources=["https://silverdata.io/comex-silver"],
                weight=0.9,
            )
        )
        p.contradictions.append(
            schema.Contradiction(
                id="C1",
                claim_a_id="E1",
                claim_b_id="E3",
                interpretation="drain from registered → eligible, not total loss",
            )
        )
        p.touch("added E3 + contradiction C1")
        save_project(tmp, p)
        print("[OK] added E3 + contradiction C1")

        # 6. Reload from disk and verify everything round-trips.
        loaded = load_project(tmp, "silver-comex-inventory")
        assert loaded.title == p.title
        assert len(loaded.hypotheses) == 1
        assert loaded.hypotheses[0].confidence == 0.6
        assert len(loaded.questions) == 2
        assert loaded.questions[0].id == "Q1"
        assert loaded.questions[0].status == "answered"
        assert loaded.questions[1].id == "Q2"
        assert loaded.questions[1].status == "open"
        assert loaded.questions[0].answer.startswith("Initial reading:")
        assert len(loaded.evidence) == 2
        assert len(loaded.contradictions) == 1
        assert loaded.contradictions[0].interpretation.startswith("drain")
        assert len(loaded.dead_ends) == 1
        assert len(loaded.timeline) >= 5, "timeline should have grown"
        print(f"[OK] reloaded project; timeline has {len(loaded.timeline)} events")

        # 7. Confidence aggregate.
        agg = loaded.confidence_overall()
        assert agg == 0.6
        print(f"[OK] overall confidence = {agg}")

        # 8. Registry round-trip.
        from research_project_mcp.storage import list_projects, save_registry, load_registry
        registry = load_registry(tmp)
        assert "silver-comex-inventory" in registry
        assert registry["silver-comex-inventory"]["status"] == "active"
        summaries = list_projects(tmp, status="active")
        assert len(summaries) == 1
        print(f"[OK] registry has {len(registry)} entry; active list has {len(summaries)}")

        # 9. Duplicate creation should raise.
        try:
            create_project(tmp, "silver-comex-inventory", "dup", "dup")
        except ProjectAlreadyExistsError as e:
            print(f"[OK] duplicate create rejected: {e}")
        else:
            print("[FAIL] duplicate create did NOT raise")
            return 1

        # 10. Missing project load should raise.
        try:
            load_project(tmp, "does-not-exist")
        except ProjectNotFoundError as e:
            print(f"[OK] missing project load rejected: {e}")
        else:
            print("[FAIL] missing project load did NOT raise")
            return 1

        # 11. Archive.
        from research_project_mcp.storage import archive_project
        archived = archive_project(tmp, "silver-comex-inventory")
        assert archived.status == "archived"
        active_after = list_projects(tmp, status="active")
        archived_after = list_projects(tmp, status="archived")
        assert len(active_after) == 0
        assert len(archived_after) == 1
        print(f"[OK] archive: active={len(active_after)}, archived={len(archived_after)}")

        # 12. Atomic-write: ensure no .tmp files leak.
        tmpfiles = list(tmp.rglob("*.tmp"))
        assert not tmpfiles, f"leaked tmp files: {tmpfiles}"
        print("[OK] no leaked .tmp files (atomic writes clean)")

        # 13. State.json is valid JSON.
        state = json.loads((tmp / "silver-comex-inventory" / "state.json").read_text())
        assert state["id"] == "silver-comex-inventory"
        assert state["status"] == "archived"
        print("[OK] state.json is valid JSON, status=archived")

        print("\nAll smoke tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())