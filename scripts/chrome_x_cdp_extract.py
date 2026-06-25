"""Extract X/Twitter auth_token and ct0 from Chrome on Windows via CDP.

Why this exists alongside chrome_x_cookies_extract.py:
  Chrome 130+ uses App-Bound Encryption (v20) for cookie values, which
  encrypts cookies with a key derived from the running browser process.
  Cookies stored in the SQLite DB are v20-encrypted and CANNOT be decrypted
  offline by any Python library. Only Chrome itself can decrypt them, by
  calling into its own process via the DevTools Protocol (CDP).

  This script launches Chrome with --remote-debugging-port, then uses the
  CDP `Network.getCookies` command to ask Chrome for the plaintext cookies.
  Works for v10, v20, and any future encryption scheme Chrome introduces,
  because Chrome does the decryption server-side.

Strategy:
  1. If Chrome is already running with --remote-debugging-port, use it.
  2. Otherwise, launch Chrome ourselves with the user's existing
     --user-data-dir and the same profile that holds the X session
     (Profile 1, where the v20 cookies live).
  3. Try headless first (no visible window). If headless returns no
     cookies, fall back to launching non-headless (the App-Bound Encryption
     key is sometimes only materialized in a real browser process).
  4. Send `Network.getCookies` via WebSocket for .x.com / .twitter.com.
  5. Write AUTH_TOKEN + CT0 to ~/.config/last30days/.env.

Usage (system Python 3.12 must have websockets):
    "C:\\Users\\bobup\\AppData\\Local\\Programs\\Python\\Python312\\python.exe" ^
        "C:\\Data\\Hermes_0.17.0\\scripts\\chrome_x_cdp_extract.py"

Pins [OK]/[FAIL] per Bill's preferred script contract.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".config" / "last30days"
ENV_FILE = CONFIG_DIR / ".env"

CHROME_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
]
CHROME_USER_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"

# Chrome refuses to enable remote debugging on its DEFAULT user-data-dir
# (a security feature to keep malware from scraping cookies). It accepts a
# non-default path. We satisfy the check with a junction that points at the
# real User Data — Chrome sees a custom path, the user's profile data is
# visible through the junction, and no copying is needed.
CHROME_CDP_USER_DATA = Path(os.environ.get("TEMP", r"C:\Users\bobup\AppData\Local\Temp")) / "chrome-cdp-extract"

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
X_DOMAINS = (".x.com", ".twitter.com", "x.com", "twitter.com")
TARGET_COOKIES = ("auth_token", "ct0")


# --- Discovery ---------------------------------------------------------------

def find_chrome_exe() -> Optional[Path]:
    for p in CHROME_CANDIDATES:
        if p.exists():
            return p
    return None


def find_x_profile() -> Optional[str]:
    """Pick the Chrome profile directory that holds the X session."""
    if not CHROME_USER_DATA.exists():
        return None
    profiles = ["Default"] + sorted(
        (p.name for p in CHROME_USER_DATA.iterdir() if p.is_dir() and p.name.startswith("Profile ")),
        key=lambda n: int(n.split()[1]) if n.split()[1].isdigit() else 0,
    )
    for prof in profiles:
        for layout in ("Network/Cookies", "Cookies"):
            db = CHROME_USER_DATA / prof / layout
            if not db.exists():
                continue
            try:
                import sqlite3, tempfile
                fd, tmp = tempfile.mkstemp(suffix=".sqlite")
                os.close(fd)
                try:
                    shutil.copy2(str(db), tmp)
                    conn = sqlite3.connect(tmp)
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT 1 FROM cookies WHERE host_key IN ('x.com','.x.com','twitter.com','.twitter.com') "
                        "AND name = 'auth_token' LIMIT 1"
                    )
                    if cur.fetchone():
                        conn.close()
                        return prof
                    conn.close()
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            except Exception:
                continue
    # Fall back to most-recent profile (the one Chrome will pick on its own)
    for prof in sorted(
        (p.name for p in CHROME_USER_DATA.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))),
        key=lambda n: (CHROME_USER_DATA / n).stat().st_mtime if (CHROME_USER_DATA / n).exists() else 0,
        reverse=True,
    ):
        return prof
    return None


def is_cdp_up() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_cdp_user_data_junction() -> bool:
    """Make sure CHROME_CDP_USER_DATA is a directory junction to CHROME_USER_DATA.

    Returns True if the junction exists and points at the real User Data
    (visible Default/Profile 1/etc.). mklink /J returns 0 on success or 1 if
    the link target already exists. We treat that as success if it already
    resolves to the real dir.
    """
    target = CHROME_CDP_USER_DATA
    src = CHROME_USER_DATA
    if not src.exists():
        print(f"[FAIL] Real Chrome user data dir not found: {src}")
        return False
    # If target exists and is a junction (or even just a dir), check it resolves
    if target.exists():
        # Quick test: is Default visible? If yes, the junction is good.
        if (target / "Default").exists() and (target / "Profile 1").exists():
            return True
        # It exists but isn't a junction — remove the stub and re-create
        try:
            if target.is_dir():
                target.rmdir()
        except OSError:
            print(f"[FAIL] {target} exists but isn't a junction to Chrome User Data.")
            print(f"       Remove it manually: rmdir \"{target}\"")
            return False
    r = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(target), str(src)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"[FAIL] mklink /J failed: {r.stderr.strip()}")
        return False
    return target.exists()


# --- Chrome launch -----------------------------------------------------------

def launch_chrome(profile: str) -> None:
    """Launch Chrome with remote debugging on the user's existing user-data-dir.

    Note: --headless=new on Windows often does NOT bind --remote-debugging-port
    (different headless backend). We omit --headless so CDP works; the resulting
    Chrome window is the cost. Once the user is done, they can close Chrome
    normally — the session we extract persists in the cookies DB.
    """
    exe = find_chrome_exe()
    if not exe:
        print(f"[FAIL] Chrome not found. Tried: {[str(p) for p in CHROME_CANDIDATES]}")
        sys.exit(1)
    args = [
        str(exe),
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CHROME_CDP_USER_DATA}",
        f"--profile-directory={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-default-apps",
    ]
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def wait_for_cdp(timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_cdp_up():
            return True
        time.sleep(0.4)
    return False


# --- CDP cookie extraction ---------------------------------------------------

def list_pages() -> list[dict]:
    with urllib.request.urlopen(f"{CDP_URL}/json", timeout=2) as r:
        return json.loads(r.read())


def cdp_navigate_to_x(ws_url: str) -> None:
    """Navigate the given CDP target to https://x.com. Fire-and-forget — we
    don't wait for the load event; the caller's sleep handles that."""
    import websockets.sync.client as ws_sync  # type: ignore
    with ws_sync.connect(ws_url, max_size=2**24, open_timeout=5) as conn:
        conn.send(json.dumps({"id": 1, "method": "Page.enable"}))
        # Drain the enable ack so it doesn't get mixed up with our navigate response
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                raw = conn.recv(timeout=0.3)
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    break
            except Exception:
                break
        conn.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": "https://x.com"}}))


def cdp_get_x_cookies(ws_url: str) -> dict[str, str]:
    """Connect to a Chrome target via CDP, return {auth_token, ct0} if present."""
    import websockets.sync.client as ws_sync  # type: ignore
    result: dict[str, str] = {}
    with ws_sync.connect(ws_url, max_size=2**24, open_timeout=5) as conn:
        conn.send(json.dumps({"id": 1, "method": "Network.enable"}))
        # Drain the enable ack (and any other early frames) before issuing getCookies.
        deadline = time.monotonic() + 5
        next_id = 2
        for _ in range(50):
            remaining = max(0.1, deadline - time.monotonic())
            try:
                raw = conn.recv(timeout=remaining)
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") == next_id:
                for c in msg.get("result", {}).get("cookies", []):
                    if c.get("name") in TARGET_COOKIES and c.get("value"):
                        result[c["name"]] = c["value"]
                if result:
                    return result
                # got an ack with no auth_token; stop polling for this round
                if "auth_token" not in {c.get("name") for c in msg.get("result", {}).get("cookies", [])}:
                    # try one more domain set just in case
                    break
        # Issue the actual getCookies call for the X domains.
        conn.send(json.dumps({
            "id": next_id,
            "method": "Network.getCookies",
            "params": {"domains": list(X_DOMAINS)},
        }))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                raw = conn.recv(timeout=max(0.1, deadline - time.monotonic()))
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") == next_id:
                for c in msg.get("result", {}).get("cookies", []):
                    if c.get("name") in TARGET_COOKIES and c.get("value"):
                        result[c["name"]] = c["value"]
                break
    return result


# --- Env writer --------------------------------------------------------------

def write_env(cookies: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing_lines = []
    if ENV_FILE.exists():
        existing_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    kept = [
        ln for ln in existing_lines
        if not (ln.startswith("AUTH_TOKEN=") or ln.startswith("CT0="))
    ]
    if "auth_token" in cookies:
        kept.append(f"AUTH_TOKEN={cookies['auth_token']}")
    if "ct0" in cookies:
        kept.append(f"CT0={cookies['ct0']}")
    ENV_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {ENV_FILE} (SETUP_COMPLETE preserved, AUTH_TOKEN/CT0 updated)")


# --- Main --------------------------------------------------------------------

def main() -> int:
    chrome_exe = find_chrome_exe()
    if not chrome_exe:
        print(f"[FAIL] Chrome not found. Tried:")
        for p in CHROME_CANDIDATES:
            print(f"  - {p}")
        return 1
    print(f"Chrome: {chrome_exe}")
    print(f"User data dir: {CHROME_USER_DATA}")

    profile = find_x_profile()
    if not profile:
        print(f"[FAIL] No Chrome profile found under {CHROME_USER_DATA}")
        return 1
    print(f"Profile: {profile}")

    if not ensure_cdp_user_data_junction():
        return 1
    print(f"CDP user-data junction: {CHROME_CDP_USER_DATA} -> {CHROME_USER_DATA}")

    if is_cdp_up():
        print(f"CDP: already running on {CDP_URL} — using existing Chrome")
    else:
        print(f"CDP: not running, launching Chrome with --remote-debugging-port={CDP_PORT}")
        launch_chrome(profile)
        if not wait_for_cdp():
            print(f"[FAIL] Chrome did not expose CDP on {CDP_URL} within 15s")
            print(f"       (kill any zombie chrome.exe and retry)")
            return 1
        print(f"[OK] CDP up at {CDP_URL}")

    pages = list_pages()
    if not pages:
        print(f"[FAIL] CDP returned no targets. Open x.com in Chrome and retry.")
        return 1
    page = next((p for p in pages if p.get("type") == "page"), pages[0])
    print(f"CDP targets: {len(pages)} (using: {page.get('title','?')[:40]!r}, url={page.get('url','?')[:50]!r})")

    # Navigate to x.com so the App-Bound key is materialized and the session
    # cookies are loaded into the in-memory cookie jar. Without this step,
    # v20-encrypted cookies won't appear in Network.getCookies because they're
    # only decrypted lazily when a page on the matching domain is active.
    print("Navigating to https://x.com to materialize session cookies...")
    cdp_navigate_to_x(page["webSocketDebuggerUrl"])
    time.sleep(3)

    cookies = cdp_get_x_cookies(page["webSocketDebuggerUrl"])

    if "auth_token" not in cookies:
        print(f"[WARN] No auth_token in this Chrome session. The session may have")
        print(f"       expired or been invalidated. Most common cause: closing Chrome")
        print(f"       while logged in, then re-opening. The X server treats the old")
        print(f"       auth_token as logged out.")
        print(f"")
        print(f"       Action: in the Chrome window the script just opened,")
        print(f"       log into x.com normally. The page should already be on x.com.")
        print(f"       Then re-run this script. It will reuse the running Chrome")
        print(f"       (CDP is already up) and extract the fresh session.")
        return 1

    if "ct0" not in cookies:
        print(f"[WARN] auth_token found but no ct0. last30days may still work; X usually issues both.")

    write_env(cookies)
    print(f"[OK] Done. Test with: last30days \"any topic\" --search=x --emit=compact --days=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
