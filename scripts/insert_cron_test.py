#!/usr/bin/env python3
"""Insert the cron env-sanitize test from da7253215 into Bill's test file.

Bill's test_script_nonzero_exit method ends with:
    assert success is False
    assert "exited with code 1" in output
    assert "error info" in output

Then test_script_empty_output follows. We insert the new test between them.
"""
from pathlib import Path

target = Path(r"C:\Data\Hermes_0.17.0\hermes-agent\tests\cron\test_cron_script.py")
src = target.read_text(encoding="utf-8")

# Locate the insertion point: the line right before `def test_script_empty_output`
# that follows the test_script_nonzero_exit block.
needle = '''        success, output = _run_job_script(str(script))
        assert success is False
        assert "exited with code 1" in output
        assert "error info" in output

    def test_script_empty_output(self, cron_env):
        from cron.scheduler import _run_job_script'''

replacement = '''        success, output = _run_job_script(str(script))
        assert success is False
        assert "exited with code 1" in output
        assert "error info" in output

    def test_script_subprocess_env_sanitized(self, cron_env, monkeypatch):
        """Cron scripts must not inherit Hermes provider env (SECURITY.md \\u00a72.3)."""
        from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST
        from cron.scheduler import _run_job_script

        blocked_var = next(iter(_HERMES_PROVIDER_ENV_BLOCKLIST))
        monkeypatch.setenv(blocked_var, "must_not_leak")

        script = cron_env / "scripts" / "env_probe.py"
        script.write_text(
            textwrap.dedent(
                f"""\\
                import os
                key = {blocked_var!r}
                print("PRESENT" if os.environ.get(key) else "ABSENT")
                """
            )
        )

        success, output = _run_job_script("env_probe.py")
        assert success is True
        assert output == "ABSENT"

    def test_script_empty_output(self, cron_env):
        from cron.scheduler import _run_job_script'''

if needle not in src:
    print(f"FAIL: needle not found in {target}")
    print("First 200 chars after 'assert \"error info\" in output':")
    idx = src.find('assert "error info" in output')
    if idx >= 0:
        print(repr(src[idx:idx+500]))
    raise SystemExit(1)

new_src = src.replace(needle, replacement, 1)
if new_src == src:
    print("FAIL: replace did nothing")
    raise SystemExit(1)

target.write_text(new_src, encoding="utf-8")
print(f"OK: inserted 23 lines, new total = {len(new_src.splitlines())} lines (was {len(src.splitlines())})")