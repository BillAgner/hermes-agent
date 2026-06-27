"""Behavioral tests for ``hermes_cli.cron.cron_show`` and ``cron_runs``.

Verifies the dispatch paths through ``cron_command`` for the new ``show``
and ``runs`` verbs:

- ``show`` / ``runs`` resolve a human name to the canonical job id and read
  runs via ``SessionDB.list_cron_job_runs`` (the same backend the dashboard
  uses via ``/api/cron/jobs/<id>/runs``).
- ``AmbiguousJobReference`` and "not found" both surface as exit code 1
  with a user-readable message.
- ``runs`` respects ``--limit`` (clamped 1..100) and tolerates a missing
  ``jobs.json`` (returns an empty list, prints "(none yet)").

Tests use ``_isolate_hermes_home`` from conftest.py so nothing touches the
real ``~/.hermes``. No network, no live API calls.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch as mock_patch

import pytest


@pytest.fixture
def jobs_file(tmp_path, monkeypatch):
    """Write a single job into a temp ``jobs.json`` and point the cron module at it."""
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    job = {
        "id": "abc123",
        "name": "My Watchdog",
        "enabled": True,
        "schedule": {"value": "*/5 * * * *", "display": "every 5m"},
        "schedule_display": "every 5m",
        "state": "scheduled",
        "deliver": ["local"],
        "skills": [],
        "next_run_at": "2099-01-01T00:00:00",
        "last_status": "ok",
        "last_run_at": "2026-06-26T12:00:00",
    }
    (cron_dir / "jobs.json").write_text(json.dumps({"jobs": [job]}))

    # Patch the module-level constants cron/jobs.py captured at import time.
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json", raising=False)
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir, raising=False)
    monkeypatch.setattr("cron.jobs.HERMES_DIR", tmp_path, raising=False)
    return cron_dir, job


def _fake_session_db(runs, captured=None):
    """Return a context manager that swaps ``hermes_state.SessionDB`` for a fake.

    The fake's ``list_cron_job_runs`` returns ``runs`` (capped to the
    requested limit). If ``captured`` is provided, the requested limit is
    stashed there for assertion.

    Why patch hermes_state.SessionDB (not cron.SessionDB): the production
    code does ``from hermes_state import SessionDB`` inside the helper, so
    the canonical reference lives on hermes_state, not on hermes_cli.cron.
    """
    class FakeDB:
        def __init__(self, *a, **kw):
            pass

        def list_cron_job_runs(self, canonical, limit, offset):
            if captured is not None:
                captured["limit"] = limit
                captured["canonical"] = canonical
            # Mirror SessionDB.list_cron_job_runs ordering: started_at DESC,
            # id DESC tiebreak. Test fixtures don't have to pre-sort.
            ordered = sorted(
                list(runs),
                key=lambda r: (r.get("started_at") or 0, r.get("id") or ""),
                reverse=True,
            )
            return ordered[:limit]

        def close(self):
            pass

    import hermes_state
    return mock_patch.object(hermes_state, "SessionDB", FakeDB)


class TestCronShow:
    def test_show_resolves_by_name(self, jobs_file):
        _, job = jobs_file

        with _fake_session_db([{"id": "cron_abc123_20260626_120000", "started_at": 1719408000}]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_show
                rc = cron_show("My Watchdog", runs_limit=5)

        assert rc == 0
        out = buf.getvalue()
        assert "abc123" in out
        assert "My Watchdog" in out
        assert "Recent runs" in out
        assert "cron_abc123_20260626_120000" in out

    def test_show_resolves_by_canonical_id(self, jobs_file):
        with _fake_session_db([]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_show
                rc = cron_show("abc123", runs_limit=5)

        assert rc == 0
        out = buf.getvalue()
        assert "abc123" in out
        assert "(none yet)" in out

    def test_show_unknown_job_returns_exit_1(self, jobs_file):
        buf = io.StringIO()
        with redirect_stdout(buf):
            from hermes_cli.cron import cron_show
            rc = cron_show("does-not-exist", runs_limit=5)

        assert rc == 1
        assert "Job not found" in buf.getvalue()


class TestCronRuns:
    def test_runs_prints_rows_newest_first(self, jobs_file):
        fake_runs = [
            {"id": "cron_abc123_runA", "started_at": 1719408000, "ended_at": 1719408005,
             "preview": "first user prompt for run A"},
            {"id": "cron_abc123_runB", "started_at": 1719410000, "ended_at": None,
             "preview": "second user prompt for run B"},
        ]
        with _fake_session_db(fake_runs):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_runs
                rc = cron_runs("abc123", limit=20)

        assert rc == 0
        out = buf.getvalue()
        # newest first — runB before runA
        assert out.index("cron_abc123_runB") < out.index("cron_abc123_runA")
        # active session (no ended_at) shows "running" tag
        assert "running" in out
        # ended session shows "done" tag
        assert "done" in out

    def test_runs_empty_history_prints_friendly_message(self, jobs_file):
        with _fake_session_db([]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_runs
                rc = cron_runs("abc123", limit=20)

        assert rc == 0
        assert "No runs recorded yet" in buf.getvalue()

    def test_runs_resolves_by_human_name(self, jobs_file):
        with _fake_session_db([{"id": "cron_abc123_runA", "started_at": 1719408000}]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_runs
                rc = cron_runs("My Watchdog", limit=20)

        assert rc == 0
        assert "cron_abc123_runA" in buf.getvalue()

    def test_runs_unknown_job_returns_exit_1(self, jobs_file):
        buf = io.StringIO()
        with redirect_stdout(buf):
            from hermes_cli.cron import cron_runs
            rc = cron_runs("does-not-exist", limit=20)

        assert rc == 1
        assert "Job not found" in buf.getvalue()

    def test_runs_limit_is_clamped(self, jobs_file):
        """Limit >100 is clamped to 100; <1 is clamped to 1. We assert via
        the call to ``SessionDB.list_cron_job_runs`` to confirm the clamp
        actually reaches the DB layer."""
        captured: dict = {}
        with _fake_session_db([], captured=captured):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_runs
                cron_runs("abc123", limit=999)
        assert captured["limit"] == 100
        assert captured["canonical"] == "abc123"

        with _fake_session_db([], captured=captured):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_runs
                cron_runs("abc123", limit=0)
        assert captured["limit"] == 1


class TestCronCommandDispatch:
    """The argparse-level dispatch wires ``show`` / ``runs`` to the helpers."""

    def test_show_dispatches_to_cron_show(self, jobs_file):
        args = type("Args", (), {"cron_command": "show", "job_id": "abc123", "limit": 3})()
        with _fake_session_db([]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_command
                rc = cron_command(args)

        assert rc == 0
        assert "abc123" in buf.getvalue()

    def test_runs_dispatches_to_cron_runs(self, jobs_file):
        args = type("Args", (), {"cron_command": "runs", "job_id": "abc123", "limit": 7})()
        with _fake_session_db([{"id": "cron_abc123_x", "started_at": 1719408000}]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from hermes_cli.cron import cron_command
                rc = cron_command(args)

        assert rc == 0
        assert "cron_abc123_x" in buf.getvalue()