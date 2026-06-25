"""Extract X/Twitter auth_token and ct0 from Chrome's Cookies DB on Windows.

Why this exists: the upstream last30days skill's `extract_chrome_cookies` is
macOS-only (`if platform.system() != "Darwin": return None` at
cookie_extract.py:282). On Windows, Chrome encrypts cookies with v10 (AES-128-CBC)
where the key is DPAPI-protected via the user account. We can decrypt that
with `win32crypt` (from pywin32, available on the system Python 3.12) without
elevating or prompting the user.

Outputs AUTH_TOKEN and CT0 to ~/.config/last30days/.env (last30days skill reads
those env vars directly, no browser session required for subsequent runs).

Usage (system Python 3.12 must have win32crypt + pycryptodome):
    "C:\\Users\\bobup\\AppData\\Local\\Programs\\Python\\Python312\\python.exe" ^
        "C:\\Data\\Hermes_0.17.0\\scripts\\chrome_x_cookies_extract.py"

Pins [OK]/[FAIL] per Bill's preferred script contract.
"""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


# --- Constants ---------------------------------------------------------------

CHROME_BASE = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
LOCAL_STATE = CHROME_BASE / "Local State"

# Modern Chromium (>=96) keeps cookies in Network/Cookies; older builds use
# the flat Cookies file. We scan all profiles and pick the one with an X
# session in find_cookies_db() — see below.
def _candidate_db_paths() -> list[Path]:
    """Yield (Network/Cookies, Cookies) for every profile under Chrome's User Data."""
    paths: list[Path] = []
    profiles = ["Default"] + sorted(
        (p.name for p in CHROME_BASE.iterdir() if p.is_dir() and p.name.startswith("Profile ")),
        key=lambda n: int(n.split()[1]) if n.split()[1].isdigit() else 0,
    )
    for prof in profiles:
        for layout in ("Network/Cookies", "Cookies"):
            p = CHROME_BASE / prof / layout
            if p.exists():
                paths.append(p)
    return paths

CONFIG_DIR = Path.home() / ".config" / "last30days"
ENV_FILE = CONFIG_DIR / ".env"

X_DOMAINS = (".x.com", ".twitter.com")
TARGET_COOKIES = ("auth_token", "ct0")

# Chrome v10 decryption: AES-128-CBC with the 16-byte key derived from
# PBKDF2(Local State passphrase, "saltysalt", 1003, 16) on macOS. On Windows
# the equivalent passphrase is the DPAPI-decrypted os_crypt.encrypted_key
# bytes directly (no PBKDF2 step). IV is 16 spaces.
V10_PREFIX = b"v10"
AES_IV = b" " * 16


# --- Discovery ---------------------------------------------------------------

def _has_x_session(cookies_db: Path) -> bool:
    """Check whether a cookies DB has an X/Twitter auth_token. Cheap probe only."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            shutil.copy2(str(cookies_db), tmp)
        except (PermissionError, OSError):
            return False
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM cookies WHERE host_key IN ('x.com','.x.com','twitter.com','.twitter.com') "
            "AND name = 'auth_token' LIMIT 1"
        )
        hit = cur.fetchone() is not None
        conn.close()
        return hit
    except Exception:
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def find_cookies_db() -> Optional[Path]:
    """Pick the best Chrome cookies DB across all profiles.

    Priority: a profile that has an X session (auth_token on x.com/twitter.com)
    beats a profile that doesn't. Within each tier, prefer the most-recently
    modified DB. Falls back to the first existing DB if no profile has an X
    session, so the caller can still report a useful diagnostic.
    """
    candidates = _candidate_db_paths()
    with_session = [p for p in candidates if _has_x_session(p)]
    pool = with_session or candidates
    if not pool:
        return None
    pool.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return pool[0]


def load_local_state_key() -> Optional[bytes]:
    """Return the AES key from Chrome's Local State, DPAPI-decrypted."""
    if not LOCAL_STATE.exists():
        print(f"[FAIL] Local State not found at {LOCAL_STATE}")
        return None
    try:
        import json
        import win32crypt  # pywin32

        state = json.loads(LOCAL_STATE.read_text(encoding="utf-8"))
        encrypted_b64 = state.get("os_crypt", {}).get("encrypted_key")
        if not encrypted_b64:
            print("[FAIL] Local State has no os_crypt.encrypted_key")
            return None
        encrypted = base64.b64decode(encrypted_b64)
        # Strip only the 5-byte "DPAPI" magic. The bytes after it
        # (version 4-byte header + provider GUID + ...) ARE the DPAPI blob
        # that CryptUnprotectData expects intact. Verified empirically: the
        # 4-byte value right after "DPAPI" is part of the DPAPI internal
        # structure (PROV_RSA_AES provider GUID prefix), not a separate
        # Chromium version header — so DON'T strip it. (Chromium docs are
        # misleading on this point.)
        if not encrypted.startswith(b"DPAPI"):
            print(f"[FAIL] Local State key is not DPAPI-encrypted (prefix: {encrypted[:8]!r})")
            return None
        if len(encrypted) <= 5:
            print(f"[FAIL] Local State key too short after DPAPI prefix: {len(encrypted)} bytes")
            return None
        decrypted = win32crypt.CryptUnprotectData(encrypted[5:], None, None, None, 0)[1]
        return decrypted
    except ImportError as e:
        print(f"[FAIL] Missing Windows crypto dep: {e}. Need pywin32 + system Python 3.12.")
        return None
    except Exception as e:
        print(f"[FAIL] Failed to load Local State key: {e}")
        return None


# --- Decryption --------------------------------------------------------------

def decrypt_v10(encrypted_value: bytes, key: bytes) -> Optional[str]:
    """Decrypt a v10-encrypted Chrome cookie value (AES-128-CBC, PKCS#7)."""
    try:
        from Crypto.Cipher import AES
        ciphertext = encrypted_value[len(V10_PREFIX):]
        if len(ciphertext) % 16 != 0:
            return None
        cipher = AES.new(key, AES.MODE_CBC, AES_IV)
        plaintext = cipher.decrypt(ciphertext)
        # Strip PKCS#7 padding
        pad = plaintext[-1]
        if 0 < pad <= 16 and plaintext[-pad:] == bytes([pad]) * pad:
            plaintext = plaintext[:-pad]
        return plaintext.decode("utf-8", errors="replace")
    except Exception:
        return None


def decrypt_dpapi(encrypted_value: bytes) -> Optional[str]:
    """Decrypt a DPAPI-encrypted (non-v10) cookie value, e.g. older Chromium."""
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode("utf-8", errors="replace")
    except Exception:
        return None


def cookie_value(encrypted_value: bytes, key: Optional[bytes]) -> Optional[str]:
    if encrypted_value.startswith(V10_PREFIX):
        if key is None:
            return None
        return decrypt_v10(encrypted_value, key)
    if encrypted_value:
        return decrypt_dpapi(encrypted_value)
    return None


# --- Extraction --------------------------------------------------------------

def extract_x_cookies(cookies_db: Path, key: Optional[bytes]) -> dict[str, str]:
    """Read auth_token and ct0 from a copy of Chrome's cookies DB."""
    result: dict[str, str] = {}
    tmp_path: Optional[str] = None
    for attempt in range(3):
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            shutil.copy2(str(cookies_db), tmp_path)
            break
        except PermissionError:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            time.sleep(0.5)
    else:
        print("[FAIL] Chrome cookies DB is locked. Close Chrome and retry.")
        return result
    assert tmp_path is not None

    try:
        conn = sqlite3.connect(tmp_path)
        cur = conn.cursor()
        domain_clause = " OR ".join(["host_key LIKE ?"] * len(X_DOMAINS))
        params = [f"%{d}" for d in X_DOMAINS] + list(TARGET_COOKIES)
        cur.execute(
            f"SELECT name, host_key, encrypted_value, value FROM cookies "
            f"WHERE ({domain_clause}) AND name IN ({','.join('?' * len(TARGET_COOKIES))})",
            params,
        )
        for name, host, enc_val, plain_val in cur.fetchall():
            if plain_val:
                value = plain_val
            else:
                value = cookie_value(enc_val, key)
            if value:
                result[name] = value
                print(f"  + {name:12s} from {host}  (len={len(value)})")
            else:
                print(f"  - {name:12s} from {host}  (decrypt failed)")
        conn.close()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return result


# --- .env writer -------------------------------------------------------------

def write_env(cookies: dict[str, str]) -> None:
    """Append AUTH_TOKEN and CT0 to ~/.config/last30days/.env (preserving existing)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if ENV_FILE.exists():
        existing_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    # Drop any pre-existing AUTH_TOKEN/CT0 (we are about to overwrite them)
    kept = [
        ln for ln in existing_lines
        if not (ln.startswith("AUTH_TOKEN=") or ln.startswith("CT0="))
    ]
    new_lines = list(kept)
    if "auth_token" in cookies:
        new_lines.append(f"AUTH_TOKEN={cookies['auth_token']}")
    if "ct0" in cookies:
        new_lines.append(f"CT0={cookies['ct0']}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {ENV_FILE} (SETUP_COMPLETE preserved, AUTH_TOKEN/CT0 {'updated' if cookies else 'unchanged'})")


# --- Main --------------------------------------------------------------------

def main() -> int:
    print(f"Chrome cookies DB candidates (scanning all profiles):")
    candidates = _candidate_db_paths()
    chosen = find_cookies_db() if candidates else None
    for p in candidates:
        has = _has_x_session(p)
        marker = "✓" if p == chosen else ("*" if has else " ")
        label = "[X-session]" if has else ""
        print(f"  {marker} {label:11s} {p}")

    cookies_db = find_cookies_db()
    if not cookies_db:
        print("[FAIL] No Chrome cookies DB found under LOCALAPPDATA. Is Chrome installed?")
        return 1
    print(f"[OK] Chrome cookies DB: {cookies_db}")

    key = load_local_state_key()
    if key is None:
        print("[WARN] Could not load Local State key. Will try plain-value cookies only.")

    print("Extracting X/Twitter cookies:")
    cookies = extract_x_cookies(cookies_db, key)
    if "auth_token" not in cookies:
        # Diagnose: is Chrome running (so on-disk DB is stale) or is the user
        # actually not logged in? SQLite copies a snapshot of the cookies DB
        # while Chrome has the file locked; Chrome buffers writes in memory
        # and only flushes on clean exit. If the DB mtime is hours/days old
        # but Chrome is running, that's the most likely cause.
        try:
            import subprocess
            chrome_running = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            chrome_up = "chrome.exe" in chrome_running.stdout.lower()
        except Exception:
            chrome_up = False
        db_age_h = (time.time() - cookies_db.stat().st_mtime) / 3600
        if chrome_up and db_age_h > 1:
            print(f"[FAIL] No auth_token found. Chrome is running and the cookies DB")
            print(f"       was last written {db_age_h:.1f}h ago — Chrome buffers cookies")
            print(f"       in memory and only flushes on clean exit.")
            print(f"       Fix: close all Chrome windows (File > Exit), then re-run.")
        else:
            print(f"[FAIL] No auth_token found in Chrome.  Are you logged into x.com?")
        return 1
    if "ct0" not in cookies:
        print("[WARN] auth_token found but no ct0. last30days may still work; X often issues both.")

    write_env(cookies)
    print("[OK] Done. Test with: last30days \"any topic\" --search=x --emit=compact --days=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
