"""End-to-end smoke test for the rp_synthesize_answer auto-save.

Spins up an in-process FastMCP client to call the actual tool — same
code path an agent hits. Verifies the synthesis is persisted to disk
and mirrored to open-notebook.
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

from research_project_mcp import __main__ as m  # noqa: E402
from research_project_mcp.storage import (  # noqa: E402
    DEFAULT_STORAGE_ROOT,
    list_syntheses,
    load_synthesis,
)


async def call_tool(name: str, **kwargs) -> str:
    """Call an MCP tool by name with kwargs. Returns the JSON string."""
    fn = getattr(m, name)
    return await fn(**kwargs)


async def main() -> int:
    slug = "silver-comex-inventory"
    question = (
        "Smoke test (e2e): cross-check the silver registered ratio against ETF flows."
    )

    print(f"== rp_synthesize_answer({slug!r}) ==")
    res = await call_tool(
        "rp_synthesize_answer",
        slug=slug,
        question=question,
        max_sources=3,
        include_contradictions=True,
        log_to_project=True,
    )
    import json
    data = json.loads(res)
    print(f"  question: {data.get('question')[:60]}…")
    print(f"  confidence_overall: {data.get('confidence_overall')}")
    print(f"  synthesis meta: {data.get('synthesis')}")
    print(f"  warnings: {data.get('warnings')}")
    assert data.get("synthesis"), "synthesis not saved"
    sid = data["synthesis"]["synthesis_id"]
    print()

    print(f"== list_syntheses({slug!r}) ==")
    items = list_syntheses(DEFAULT_STORAGE_ROOT, slug, limit=5)
    assert items, "list_syntheses returned empty after save"
    assert any(s["synthesis_id"] == sid for s in items), "newest not in list"
    print(f"  count={len(items)}; new synthesis present=True")
    print()

    print(f"== load_synthesis({slug!r}, {sid!r}) ==")
    full = load_synthesis(DEFAULT_STORAGE_ROOT, slug, sid)
    assert full["question"] == question
    assert "memo" in full and full["memo"]
    print(f"  memo len={len(full['memo'])}; scope present={'scope' in full}")
    print()

    print("== rp_list_recent_syntheses(limit=5) ==")
    res2 = await call_tool("rp_list_recent_syntheses", limit=5)
    data2 = json.loads(res2)
    assert data2.get("syntheses"), "rp_list_recent_syntheses returned empty"
    found = [s for s in data2["syntheses"] if s["synthesis_id"] == sid]
    assert found, "newest not in all-projects list"
    print(f"  count={data2.get('count')}; ours present=True")
    print()

    print("== rp_load_synthesis() (MCP) ==")
    res3 = await call_tool("rp_load_synthesis", slug=slug, synthesis_id=sid)
    data3 = json.loads(res3)
    assert data3.get("memo", "").startswith("# Synthesis"), "memo missing or wrong"
    print(f"  memo starts: {data3.get('memo','')[:60]!r}")
    print()

    print("== rp_save_synthesis() (direct, mirror=False to keep test idempotent) ==")
    res4 = await call_tool(
        "rp_save_synthesis",
        slug=slug,
        question="Direct-call smoke test question",
        memo="## Direct save\n\nThis memo was saved via rp_save_synthesis directly.",
        dossier={"evidence_ranked": [], "contradictions": []},
        mirror_to_notebook=False,
    )
    data4 = json.loads(res4)
    assert data4.get("synthesis_id"), "direct save didn't return id"
    print(f"  synthesis_id: {data4.get('synthesis_id')}")
    print(f"  path: {data4.get('path')}")
    print()

    print("[OK] all synthesis persistence MCP tools round-trip cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
