# SessionDB mocking patterns

When testing CLI handlers or API endpoints that read from `SessionDB`, you need to fake the DB layer. Two specific patterns come up repeatedly.

## Pattern 1: Fake SessionDB that mirrors real ordering

`SessionDB.list_cron_job_runs(canonical_id, limit, offset)` runs this query:

```sql
SELECT s.*, ... 
FROM sessions s
WHERE s.source = 'cron' AND s.id >= ? AND s.id < ?
ORDER BY s.started_at DESC, s.id DESC
LIMIT ? OFFSET ?
```

It returns rows newest-first, with `id` as the tiebreaker when `started_at` ties. A naive fake that returns `list(runs)[:limit]` will pass tests but disagree with the real DB on ordering.

**Fix:** have the fake sort by `(started_at, id) DESC`:

```python
class FakeDB:
    def __init__(self, *a, **kw):
        pass

    def list_cron_job_runs(self, canonical, limit, offset):
        ordered = sorted(
            list(runs),
            key=lambda r: (r.get("started_at") or 0, r.get("id") or ""),
            reverse=True,
        )
        return ordered[:limit]

    def close(self):
        pass
```

And patch via `hermes_state`, not via the consumer module (see Trap 4 in windows-tooling-gotchas.md):

```python
import hermes_state
with mock_patch.object(hermes_state, "SessionDB", FakeDB):
    ...
```

## Pattern 2: Capture what the call asked for

For tests that assert "limit was clamped" or "the right canonical id was passed", capture the args inside the fake:

```python
def test_runs_limit_is_clamped(jobs_file):
    captured: dict = {}
    with _fake_session_db([], captured=captured):
        cron_runs("abc123", limit=999)
    assert captured["limit"] == 100  # clamped from 999
    assert captured["canonical"] == "abc123"
```

The fake records whatever the caller passed and the test reads it back. Cleaner than inspecting call_args on a Mock.

## Pattern 3: Resolve-by-name fixtures

`resolve_job_ref(ref)` accepts either a canonical id (`"abc123"`) or a human name (`"My Watchdog"`). It returns `None` for unknown refs and raises `AmbiguousJobReference` when multiple jobs share a name. Always test both paths.

```python
def test_runs_resolves_by_human_name(jobs_file):
    """Lookup by name works the same as lookup by canonical id."""
    with _fake_session_db([{"id": "cron_abc123_runA", "started_at": 1719408000}]):
        rc = cron_runs("My Watchdog", limit=20)
    assert rc == 0

def test_runs_unknown_job_returns_exit_1(jobs_file):
    """Unknown refs surface as exit 1 with a user-readable message."""
    rc = cron_runs("does-not-exist", limit=20)
    assert rc == 1
    assert "Job not found" in captured_stdout
```

The `jobs_file` fixture writes a single job to a temp `jobs.json` and patches the module-level `cron.jobs.JOBS_FILE` so `list_jobs` / `resolve_job_ref` see it without touching `~/.hermes/`.

## Pattern 4: Full fake-DB template

```python
from contextlib import redirect_stdout
from unittest.mock import patch as mock_patch
import io

def _fake_session_db(runs, captured=None):
    class FakeDB:
        def __init__(self, *a, **kw): pass

        def list_cron_job_runs(self, canonical, limit, offset):
            if captured is not None:
                captured["limit"] = limit
                captured["canonical"] = canonical
            ordered = sorted(
                list(runs),
                key=lambda r: (r.get("started_at") or 0, r.get("id") or ""),
                reverse=True,
            )
            return ordered[:limit]

        def close(self): pass

    import hermes_state
    return mock_patch.object(hermes_state, "SessionDB", FakeDB)


@pytest.fixture
def jobs_file(tmp_path, monkeypatch):
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
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json", raising=False)
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir, raising=False)
    monkeypatch.setattr("cron.jobs.HERMES_DIR", tmp_path, raising=False)
    return cron_dir, job
```

Use this when you need a CLI handler that reads both `cron.jobs` (for `resolve_job_ref`) and `SessionDB` (for run history) — the fixture gives you a controlled `jobs.json`, and the fake DB gives you controlled run rows.