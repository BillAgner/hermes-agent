# Windows tooling gotchas

The Hermes repo is cross-platform but most of the dev tooling is shell-tested on Linux/macOS. On Windows you'll hit four recurrent traps. Fixes captured below so you don't rediscover them.

## Trap 1: `scripts/run_tests.sh` does not find the venv

**Symptom:**
```
error: no virtualenv found in /c/Data/Hermes_0.17.0/.venv or /c/Data/Hermes_0.17.0/venv
```

**Why:** the wrapper checks for `$candidate/bin/activate` (POSIX). On Windows venvs, the activate script is at `Scripts/activate`, not `bin/activate`. The wrapper fails the venv probe and aborts before running tests.

**Fix:** run pytest directly via the venv's Python with the hermetic env vars set manually.

```bash
cd /c/Data/Hermes_0.17.0

# First time only: install pytest into the local venv
uv pip install --python ./.venv/Scripts/python.exe pytest pytest-asyncio

# Run with hermetic env (matches what run_tests.sh sets via `env -i`)
PYTHONPATH=. \
TZ=UTC \
LANG=C.UTF-8 \
LC_ALL=C.UTF-8 \
PYTHONHASHSEED=0 \
PYTHONDONTWRITEBYTECODE=1 \
./.venv/Scripts/python.exe -m pytest tests/<path> -v --tb=short
```

What each var does (from `scripts/run_tests.sh` comments):
- `TZ=UTC` — dates don't drift by timezone.
- `LANG=C.UTF-8` / `LC_ALL=C.UTF-8` — locale-independent string handling.
- `PYTHONHASHSEED=0` — deterministic hash ordering for set/dict iteration.
- `PYTHONDONTWRITEBYTECODE=1` — keeps the repo clean of `__pycache__/` pollution from test runs.
- `PYTHONPATH=.` — lets tests find the package source.

If `scripts/run_tests.sh` ever gets a Windows-native update, switch back to the wrapper.

## Trap 2: `patch` tool mangles indentation

**Symptom:** `patch` returns `success: true` but the diff shows lines shifted by 2 or 4 spaces — typically right after the matched `old_string`. TypeScript/JSX and Python files with nested structures are most prone.

**Why:** the patch tool has heuristics around indentation that sometimes mis-detect the baseline of a multi-line replacement, especially when `old_string` and `new_string` differ in structural shape.

**Fix:** don't loop on patch — restore the file and use a Python script.

```bash
# 1. Restore from git
git checkout HEAD -- <file>

# 2. Write a small Python script to scripts/_patch_<thing>.py
cat > scripts/_patch_<thing>.py <<'PY'
p = '<file>'
with open(p, 'r', encoding='utf-8') as f:
    content = f.read()

old = """<exact existing block>"""
new = """<replacement block>"""
assert old in content, 'old block not found'
content = content.replace(old, new, 1)
with open(p, 'w', encoding='utf-8') as f:
    f.write(content)
print('OK', len(content))
PY

# 3. Run it
python scripts/_patch_<thing>.py
rm scripts/_patch_<thing>.py

# 4. Verify with git diff
git diff <file> | head -50
```

If the script's `assert` fires, the existing block has changed (probably an earlier edit). Re-read the file and update the `old` string.

## Trap 3: `write_file` is destructive

**Symptom:** you pass what you thought was a targeted edit to `write_file`, and the file shrinks from 2000+ lines to the 13 lines of content you wrote.

**Why:** `write_file` overwrites the ENTIRE file. The tool description says so but it's easy to mistake it for `patch` when you're moving fast.

**Fix:**
- **Never use `write_file` for targeted edits.** Use `patch` for single-file edits.
- For multi-file bulk changes, write a Python script to `scripts/` and run it.
- Recover from mistakes: `git checkout HEAD -- <file>`.

## Trap 4: `from X import Y` inside a function

**Symptom:** in a test, `mock_patch.object(consumer_module, "Y", fake)` raises:
```
AttributeError: <module 'consumer'> does not have the attribute 'Y'
```

**Why:** the production code does `from hermes_state import SessionDB` inside the function body. That creates a local binding in the function's scope, but the name `SessionDB` is never attached to the consumer module's globals. Patching `consumer.SessionDB` finds nothing.

**Fix:** patch the source module, not the consumer.

```python
# WRONG
with mock_patch.object(cron_module, "SessionDB", FakeDB):  # AttributeError

# RIGHT
import hermes_state
with mock_patch.object(hermes_state, "SessionDB", FakeDB):  # works
```

The local `from hermes_state import SessionDB` re-reads `hermes_state.SessionDB` at call time, so the patched object is what gets used.

## Bonus: terminal command paths with spaces

When using `cmd //c` from bash with a path containing spaces (e.g. `C:\Data\Hermes_0.17.0\`), bash splits on the space and tries to run `C:\Data\Hermes` as a command. Fix:

- Write a `.bat` wrapper that does the invocation, OR
- Use PowerShell's `&` operator which handles spaces natively.

This catches you when running things like:
```bash
cmd //c "C:\Data\Hermes_0.17.0\some_script.bat"  # BROKEN — splits at the space
```