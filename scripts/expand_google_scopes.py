"""Update GOOGLE_OAUTH_SCOPES in .env to the full Workspace set.

Drive/Sheets/Docs/Contacts scopes are added. Re-authorize when those tools
are needed; until then, those tools return structured "missing scope"
errors without making real API calls.
"""
import re
import sys
from pathlib import Path

env_path = Path(r"C:\Data\Hermes_0.17.0\.env")
env = env_path.read_text(encoding="utf-8")

new_scopes = (
    "https://www.googleapis.com/auth/gmail.readonly,"
    "https://www.googleapis.com/auth/gmail.send,"
    "https://www.googleapis.com/auth/calendar,"
    "https://www.googleapis.com/auth/drive,"
    "https://www.googleapis.com/auth/drive.readonly,"
    "https://www.googleapis.com/auth/contacts.readonly,"
    "https://www.googleapis.com/auth/spreadsheets,"
    "https://www.googleapis.com/auth/documents"
)

pattern = re.compile(r"^GOOGLE_OAUTH_SCOPES=.*$", re.MULTILINE)
new_env, n = pattern.subn(f"GOOGLE_OAUTH_SCOPES={new_scopes}", env)
if n == 0:
    print("[FAIL] no GOOGLE_OAUTH_SCOPES line found in .env", file=sys.stderr)
    sys.exit(1)

env_path.write_text(new_env, encoding="utf-8")
print(f"[OK] replaced {n} line(s)")

# Verify setup.py picks it up.
sys.path.insert(0, r"C:\Data\Hermes_0.17.0\skills\productivity\google-workspace\scripts")
from setup import _scopes
scopes = _scopes()
print(f"\nscope count: {len(scopes)}")
for s in scopes:
    print(f"  {s}")