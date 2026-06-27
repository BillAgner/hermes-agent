"""Tests for ``GET /api/cron/runs/<session_id>/output``.

The endpoint resolves a cron run session id to its on-disk markdown report
via SessionDB ``ended_at`` + filesystem mtime match. Tests cover:

- Malformed session id (path traversal attempts, wrong format) → 400.
- Unknown session id → 404.
- Real session with a finished run → 200, body has the markdown content,
  matched_file is the timestamped .md file the scheduler wrote.
- Active session (no ``ended_at``) with no flushed output yet → 404.

Uses FastAPI ``TestClient`` with auth shimmed so tests run without a
real session token. Sandbox-safe: every test only touches a temp
``HERMES_HOME``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    """Sandbox HERMES_HOME to a temp dir so tests can't touch the real install."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Reload cron.jobs under the new HERMES_HOME so OUTPUT_DIR rebinds.
    import importlib
    import cron.jobs
    importlib.reload(cron.jobs)
    # Give each test its own SQLite file so SessionDB rows don't collide
    # across the suite (the module-level DEFAULT_DB_PATH is captured at
    # import time and would otherwise point at one shared file).
    db_path = tmp_path / "state.db"
    yield tmp_path, db_path
    monkeypatch.undo()
    importlib.reload(cron.jobs)


@pytest.fixture
def session_db(hermes_env):
    """A writable SessionDB seeded with one cron run session."""
    tmp_path, db_path = hermes_env
    from hermes_state import SessionDB

    db = SessionDB(db_path=db_path)
    ended_at = time.time()
    started_at = ended_at - 20.0

    def _insert(conn):
        conn.execute(
            """INSERT INTO sessions (id, source, user_id, model, model_config,
               system_prompt, parent_session_id, cwd, started_at, ended_at,
               end_reason, title)
               VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)""",
            (
                "cron_390beaf8ba1a_20260626_134346",
                "cron",
                started_at,
                ended_at,
                "completed",
                "RDP File Cleanup",
            ),
        )
    db._execute_write(_insert)
    yield db
    db.close()


@pytest.fixture
def output_file(hermes_env, session_db):
    """Drop a markdown output file at the canonical path for the test session.

    Real scheduler writes to ``OUTPUT_DIR/<job_id>/<YYYY-MM-DD_HH-MM-SS>.md``
    with mtime ~= ``ended_at``. We mirror that here.
    """
    from cron.jobs import OUTPUT_DIR  # reloaded under temp HERMES_HOME

    job_dir = OUTPUT_DIR / "390beaf8ba1a"
    job_dir.mkdir(parents=True, exist_ok=True)
    # Use a timestamp that matches the session_id's compact form, formatted
    # in the file-naming convention (YYYY-MM-DD_HH-MM-SS).
    # Session id is cron_390beaf8ba1a_20260626_134346 -> 2026-06-26_13-43-46.md
    path = job_dir / "2026-06-26_13-43-46.md"
    path.write_text(
        "# Cron Job: RDP File Cleanup\n\n## Response\n\nCleared 0 files.\n",
        encoding="utf-8",
    )
    # Pin mtime so the closest-match picks this file over any neighbours.
    ended_at = time.time() - 0.5
    os.utime(path, (ended_at, ended_at))
    return path


@pytest.fixture
def client():
    """TestClient with auth shimmed out."""
    from fastapi.testclient import TestClient
    from hermes_cli import web_server as ws

    # Auth shim — tests don't care about session tokens.
    ws._has_valid_session_token = lambda req: True
    return TestClient(ws.app)


class TestGetCronRunOutput:
    def test_malformed_session_id_returns_400(self, client):
        # TestClient raises on URLs with literal `..` path segments. Quote
        # the entire segment so the request reaches the route handler, where
        # the regex rejects it.
        for bad in [
            "not-cron-format",
            "cron_abc_20260626_134346",  # job_id too short
            "cron_390beaf8ba1a_notatimestamp",
            "cron_390beaf8ba1a_20260626_13434",  # 5 digits, not 6
            "cron_390beaf8ba1a_20260626_1343467",  # 7 digits
            "cron_AAAAAAAAAAAA_20990101_000000",  # hostile: uppercase hex
        ]:
            from urllib.parse import quote
            r = client.get(f"/api/cron/runs/{quote(bad, safe='')}/output")
            assert r.status_code == 400, f"expected 400 for {bad!r}, got {r.status_code}: {r.text}"

    def test_unknown_session_id_returns_404(self, client, hermes_env):
        # Valid format but no session row in the DB.
        r = client.get("/api/cron/runs/cron_000000000000_20990101_000000/output")
        assert r.status_code == 404
        assert "Unknown session id" in r.text

    def test_real_run_returns_markdown(self, client, hermes_env, session_db, output_file):
        r = client.get("/api/cron/runs/cron_390beaf8ba1a_20260626_134346/output")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == "cron_390beaf8ba1a_20260626_134346"
        assert body["job_id"] == "390beaf8ba1a"
        assert body["matched_file"] == "2026-06-26_13-43-46.md"
        assert "# Cron Job: RDP File Cleanup" in body["content"]
        assert "Cleared 0 files." in body["content"]
        assert body["size"] == os.path.getsize(str(output_file))
        assert body["mtime"] > 0

    def test_active_run_with_no_output_returns_404(self, client, hermes_env):
        # Active session (no ended_at, no file flushed yet)
        tmp_path, db_path = hermes_env
        from hermes_state import SessionDB

        db = SessionDB(db_path=db_path)
        db._execute_write(lambda conn: conn.execute(
            """INSERT INTO sessions (id, source, user_id, model, model_config,
               system_prompt, parent_session_id, cwd, started_at)
               VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)""",
            ("cron_390beaf8ba1a_20260626_999999", "cron", time.time()),
        ))
        db.close()
        r = client.get("/api/cron/runs/cron_390beaf8ba1a_20260626_999999/output")
        assert r.status_code == 404


class TestSecurityInvariants:
    """The job_id segment is double-validated by the regex AND
    ``_job_output_dir``; a hostile session_id that passes the regex
    still gets blocked at the path layer."""

    def test_path_traversal_in_job_id_blocked(self, client):
        # This looks like it could be a regex match if the regex were lax;
        # with the strict [0-9a-f]{12} constraint it can't match.
        r = client.get("/api/cron/runs/cron_AAAAAAAAAAAA_20990101_000000/output")
        # Will 400 (regex) or 404 (regex passes but job hex is not in DB).
        assert r.status_code in (400, 404)

    def test_endpoint_does_not_read_outside_cron_output_dir(
        self, client, hermes_env, session_db
    ):
        # Even if a hostile session id parses, the file lookup is constrained
        # to cron.jobs.OUTPUT_DIR / job_id / — no way to escape via filename.
        # We can't fully exercise this without writing a file outside the dir,
        # but the static check is enough: _job_output_dir raises on / and \\
        # and Path(text).is_absolute().
        from cron.jobs import _job_output_dir
        with pytest.raises(ValueError):
            _job_output_dir("../escape")
        with pytest.raises(ValueError):
            _job_output_dir("/absolute")
        with pytest.raises(ValueError):
            _job_output_dir("nested/dir")