"""End-to-end smoke test for research-project-mcp.

Spawns the installed ``research-project-mcp.exe`` as a subprocess MCP server,
talks to it over stdio via the FastMCP ClientSession, and exercises the
full lifecycle:

    rp_list_projects → rp_create_project → rp_add_evidence → rp_query_project
    → rp_archive_project

Also verifies graceful degradation when the open-notebook mirror is
unreachable (pointed at a closed port).

Usage::

    python tests/smoke_mcp.py

Run from the package root.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Allow running without install.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))


EXE_PATH = Path(
    r"C:\Data\Hermes\hermes-agent\venv\Scripts\research-project-mcp.exe"
)


def _imports():
    """Lazy-import the MCP client (so the test can print a useful error
    if the venv doesn't have mcp)."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    return ClientSession, StdioServerParameters, stdio_client


def _parse_tool_text(res) -> dict:
    """Extract the first text content block from a CallToolResult and parse JSON."""
    if not res.content:
        return {"_raw": None}
    block = res.content[0]
    text = getattr(block, "text", str(block))
    try:
        return json.loads(text)
    except Exception:
        return {"_raw_text": text}


async def _run_subtest(
    label: str,
    *,
    env: dict[str, str] | None = None,
    body,
) -> dict:
    """Spawn the server with custom env vars, run ``body(session)``,
    tear down, return whatever ``body`` returns. Raises on failure."""
    ClientSession, StdioServerParameters, stdio_client = _imports()
    params = StdioServerParameters(
        command=str(EXE_PATH),
        args=[],
        env={**os.environ, **(env or {})},
    )
    print(f"\n--- {label} ---")
    print(f"env: RESEARCH_PROJECTS_DIR={env.get('RESEARCH_PROJECTS_DIR') if env else '<unset>'}, "
          f"OPEN_NOTEBOOK_URL={env.get('OPEN_NOTEBOOK_URL') if env else '<default>'}")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await body(session)


async def _main_online(root: Path) -> None:
    """Run against the real open-notebook service (assumed reachable)."""
    ClientSession, StdioServerParameters, stdio_client = _imports()
    params = StdioServerParameters(
        command=str(EXE_PATH),
        args=[],
        env={**os.environ, "RESEARCH_PROJECTS_DIR": str(root)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            slug = "smoke-online-001"

            # 1. rp_list_projects (should be empty)
            res = await session.call_tool("rp_list_projects", {"status": "active"})
            data = _parse_tool_text(res)
            assert "projects" in data, f"missing 'projects' in {data}"
            print(f"[OK] rp_list_projects → count={data['count']} (expect 0)")

            # 2. rp_create_project — should reach open-notebook and create a notebook
            res = await session.call_tool(
                "rp_create_project",
                {
                    "slug": slug,
                    "title": "Smoke test project (online)",
                    "scope": "Verify end-to-end MCP pipeline against real open-notebook",
                    "tags": ["smoke", "test"],
                    "initial_hypotheses": [
                        {"id": "H1", "claim": "Pipeline works end-to-end", "confidence": 0.9}
                    ],
                    "initial_questions": ["Does the mirror notebook actually exist?"],
                },
            )
            data = _parse_tool_text(res)
            assert data.get("notebook_id"), f"no notebook_id; got {data}"
            assert "project" in data, f"no project; got {data}"
            project_id = data["project"]["id"]
            assert project_id == slug
            print(f"[OK] rp_create_project → notebook_id={data['notebook_id']}")
            # state.json must exist
            state_file = root / slug / "state.json"
            assert state_file.exists(), f"missing state.json at {state_file}"
            print(f"[OK] state.json exists at {state_file}")

            # 3. rp_add_evidence — should mirror the URL via create_source_link
            res = await session.call_tool(
                "rp_add_evidence",
                {
                    "slug": slug,
                    "claim": "Open-notebook MCP returned 200 OK",
                    "sources": ["https://example.com/smoke-test"],
                    "weight": 0.7,
                    "source_types": ["primary"],
                    "note": "smoke-test evidence",
                },
            )
            data = _parse_tool_text(res)
            assert "evidence" in data, f"missing 'evidence' in {data}"
            assert data["evidence"]["id"] == "E1"
            assert data["evidence"]["weight"] == 0.7
            assert "mirror_warnings" not in data or not data["mirror_warnings"], (
                f"unexpected mirror warnings: {data.get('mirror_warnings')}"
            )
            print(f"[OK] rp_add_evidence → E1 weight=0.7, no mirror warnings")

            # 4. rp_query_project — compact summary
            res = await session.call_tool(
                "rp_query_project", {"slug": slug, "max_evidence": 5}
            )
            data = _parse_tool_text(res)
            assert data["slug"] == slug
            assert data["open_question_count"] == 1
            assert len(data["hypotheses"]) == 1
            assert data["hypotheses"][0]["id"] == "H1"
            assert data["evidence_count"] == 1
            print(
                f"[OK] rp_query_project → open_q={data['open_question_count']}, "
                f"evidence={data['evidence_count']}, conf={data['confidence_overall']}"
            )

            # 5. rp_render_report — markdown memo
            res = await session.call_tool(
                "rp_render_report", {"slug": slug, "format": "markdown"}
            )
            data = _parse_tool_text(res)
            assert data["format"] == "markdown"
            assert "Smoke test project" in data["report"]
            assert "E1" in data["report"]
            assert "Q1" in data["report"]
            print(f"[OK] rp_render_report → {len(data['report'])} chars of markdown")

            # 6. rp_manual_override — bump H1 confidence
            res = await session.call_tool(
                "rp_manual_override",
                {
                    "slug": slug,
                    "field_path": "hypotheses.H1.confidence",
                    "new_value": 0.95,
                    "reason": "smoke test — bumping confidence after successful run",
                },
            )
            data = _parse_tool_text(res)
            assert "project" in data, f"missing 'project' in {data}"
            assert data["new_value"] == 0.95
            tl = data["project"]["timeline"]
            assert any("manual override" in t["event"] for t in tl), (
                f"manual override not in timeline: {[t['event'] for t in tl]}"
            )
            print(f"[OK] rp_manual_override → hypotheses.H1.confidence = 0.95 (manual event logged)")

            # 7. rp_archive_project
            res = await session.call_tool("rp_archive_project", {"slug": slug})
            data = _parse_tool_text(res)
            assert data["project"]["status"] == "archived"
            print(f"[OK] rp_archive_project → status=archived")

            # 8. rp_list_projects — confirm it's now archived
            res = await session.call_tool(
                "rp_list_projects", {"status": "archived"}
            )
            data = _parse_tool_text(res)
            slugs = [p["slug"] for p in data["projects"]]
            assert slug in slugs, f"archived list missing {slug}: {slugs}"
            print(f"[OK] rp_list_projects(status=archived) → {len(slugs)} project(s)")


async def _main_isolated(root: Path) -> None:
    """Run with OPEN_NOTEBOOK_URL pointing at a closed port — must still
    write canonical state.json and return a warning."""
    ClientSession, StdioServerParameters, stdio_client = _imports()
    params = StdioServerParameters(
        command=str(EXE_PATH),
        args=[],
        env={
            **os.environ,
            "RESEARCH_PROJECTS_DIR": str(root),
            "OPEN_NOTEBOOK_URL": "http://localhost:1",  # closed port
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            slug = "smoke-isolated-002"

            res = await session.call_tool(
                "rp_create_project",
                {
                    "slug": slug,
                    "title": "Isolated smoke (mirror down)",
                    "scope": "Confirm graceful degradation when open-notebook is unreachable",
                    "tags": ["smoke", "isolated"],
                },
            )
            data = _parse_tool_text(res)
            assert "project" in data, f"no project: {data}"
            assert data.get("warning"), f"expected a warning, got: {data}"
            assert data["notebook_id"] is None, (
                f"notebook_id should be None when mirror is down, got {data['notebook_id']}"
            )
            print(f"[OK] rp_create_project (mirror down) → warning present, "
                  f"notebook_id=None, project still created")

            # state.json must still exist.
            state_file = root / slug / "state.json"
            assert state_file.exists(), f"missing state.json at {state_file}"
            print(f"[OK] state.json exists despite mirror failure: {state_file}")

            # _registry.json must also be written.
            registry = root / "_registry.json"
            assert registry.exists(), f"missing _registry.json at {registry}"
            reg_data = json.loads(registry.read_text(encoding="utf-8"))
            assert slug in reg_data, f"registry missing {slug}: {list(reg_data)}"
            assert reg_data[slug]["status"] == "active"
            print(f"[OK] _registry.json has the project: status={reg_data[slug]['status']}")

            # add_evidence with a URL — must succeed (canonical write) and produce
            # mirror_warnings.
            res = await session.call_tool(
                "rp_add_evidence",
                {
                    "slug": slug,
                    "claim": "Even without the mirror, evidence is recorded",
                    "sources": ["https://example.com/isolated"],
                    "weight": 0.5,
                },
            )
            data = _parse_tool_text(res)
            assert "evidence" in data, f"missing evidence: {data}"
            assert "mirror_warnings" in data, f"expected mirror_warnings, got {data}"
            assert len(data["mirror_warnings"]) >= 1, (
                f"expected ≥1 mirror warning, got {data['mirror_warnings']}"
            )
            print(f"[OK] rp_add_evidence (mirror down) → evidence saved, "
                  f"{len(data['mirror_warnings'])} mirror warning(s)")

            # archive — pure storage, should still work.
            res = await session.call_tool("rp_archive_project", {"slug": slug})
            data = _parse_tool_text(res)
            assert data["project"]["status"] == "archived"
            print(f"[OK] rp_archive_project (mirror down) → status=archived")


async def _main() -> int:
    if not EXE_PATH.exists():
        print(f"[FAIL] exe not found at {EXE_PATH}")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="rp_smoke_mcp_"))
    print(f"Using temp storage root: {tmp}")
    try:
        # Run online (open-notebook reachable on localhost:5055)
        try:
            await _main_online(tmp / "online")
        except AssertionError as exc:
            print(f"[FAIL] online subtest: {exc}")
            return 1
        except BaseException as exc:
            # Surface the full ExceptionGroup traceback.
            if hasattr(exc, "exceptions"):
                for sub in exc.exceptions:
                    import traceback
                    print(f"[FAIL] online subtest sub-exception: {type(sub).__name__}: {sub}")
                    traceback.print_exception(type(sub), sub, sub.__traceback__)
            else:
                import traceback
                traceback.print_exception(type(exc), exc, exc.__traceback__)
            return 1

        # Run isolated (open-notebook pointed at a closed port)
        try:
            await _main_isolated(tmp / "isolated")
        except AssertionError as exc:
            print(f"[FAIL] isolated subtest: {exc}")
            return 1
        except Exception as exc:
            print(f"[FAIL] isolated subtest raised {type(exc).__name__}: {exc}")
            return 1

        print("\nAll MCP smoke tests passed.")
        return 0
    finally:
        # Clean up.
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"Cleaned up temp root: {tmp}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))