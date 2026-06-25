"""google-workspace-mcp FastMCP server.

Eleven tools, namespaced ``gws_*`` to keep them distinct from generic verbs
in other MCPs::

    gws_health                — token status + scopes
    gws_gmail_search          — Gmail search (any Gmail query)
    gws_gmail_get             — fetch one message
    gws_gmail_send            — send a new email
    gws_gmail_reply           — reply in-thread
    gws_gmail_list_labels     — list all labels
    gws_calendar_list_events  — list events in a range
    gws_calendar_get_event    — fetch one event
    gws_calendar_create_event — create event
    gws_calendar_delete_event — delete event

Reuses the existing google-workspace skill's OAuth setup. Token lives at
``C:\\Data\\Hermes\\google_token.json`` (env-overridable). On every call we
load the token, refresh if expired, and persist refreshed tokens back so
the agent never has to re-authenticate within Google's 6-month refresh
window.

NOTE: Do NOT add ``from __future__ import annotations`` to this file —
it makes annotations into strings and breaks FastMCP's tool-decorator
typing. Annotations below use bare types (``X = None``, not ``Optional[X]``).
"""

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from google_workspace_mcp.__about__ import __version__


# --- Token path resolution -------------------------------------------------

def _token_path() -> Path:
    """Resolve the OAuth token path. Prefer ``GOOGLE_OAUTH_TOKEN_PATH`` from env
    (managed by the dashboard KEYS page in ``.env``); fall back to the
    google-workspace skill's default."""
    override = os.environ.get("GOOGLE_OAUTH_TOKEN_PATH")
    if override:
        return Path(override).expanduser().resolve()
    # Default to where setup.py writes it. Mirrors HERMES_HOME on Windows.
    hermes_home = Path(os.environ.get("HERMES_HOME") or r"C:\Data\Hermes")
    return hermes_home / "google_token.json"


def _scopes() -> list[str]:
    """Read scopes from the stored token (best-effort). Falls back to a Gmail +
    Calendar default if the token is missing."""
    tp = _token_path()
    if not tp.exists():
        return [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar",
        ]
    try:
        data = json.loads(tp.read_text(encoding="utf-8"))
        scopes = data.get("scopes")
        if isinstance(scopes, list) and scopes:
            return scopes
        scope_str = data.get("scope")
        if isinstance(scope_str, str) and scope_str:
            return scope_str.split()
    except (OSError, ValueError):
        pass
    return [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar",
    ]


# --- Credentials + service builder ----------------------------------------

def _ensure_token() -> None:
    """Fail fast with a helpful error if the token is missing — the agent
    shouldn't have to guess why Gmail/Calendar returned a 401."""
    tp = _token_path()
    if not tp.exists():
        print(
            f"[FAIL] no OAuth token at {tp}.\n"
            "       Re-authenticate with:\n"
            "         python C:\\Data\\Hermes\\skills\\productivity\\google-workspace\\scripts\\setup.py --auth-url\n"
            "       then visit the URL, paste the redirect URL back, then:\n"
            "         python ...setup.py --auth-code <redirect-url>",
            file=sys.stderr,
        )
        sys.exit(1)


def _get_credentials():
    """Load credentials, refresh if expired, persist the refreshed token.
    Mirrors google-workspace/scripts/google_api.py:get_credentials but inlines
    it here to keep the MCP server self-contained (no import dependency on the
    skill's CLI script)."""
    _ensure_token()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    tp = _token_path()
    creds = Credentials.from_authorized_user_file(str(tp), _scopes())
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist refreshed token back to disk so the next MCP call doesn't
        # have to refresh again.
        payload = json.loads(creds.to_json())
        # Normalize: stored tokens use type=authorized_user.
        if "type" not in payload:
            payload["type"] = "authorized_user"
        tp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if not creds.valid:
        print(f"[FAIL] token at {tp} is invalid. Re-authenticate.", file=sys.stderr)
        sys.exit(1)
    return creds


# Scope check: refuse to attempt API calls for services the token doesn't have.
# Each Google API service requires a specific scope:
#   gmail.readonly / gmail.send / gmail.modify
#   calendar / calendar.events / calendar.settings
#   drive / drive.readonly / drive.file / drive.activity
#   contacts / contacts.readonly / contacts.other.readonly
#   spreadsheets / spreadsheets.readonly / documents / documents.readonly
_SCOPE_TO_SERVICE = {
    # gmail
    "https://www.googleapis.com/auth/gmail.readonly": "gmail",
    "https://www.googleapis.com/auth/gmail.send": "gmail",
    "https://www.googleapis.com/auth/gmail.modify": "gmail",
    "https://www.googleapis.com/auth/gmail.compose": "gmail",
    "https://www.googleapis.com/auth/gmail.full": "gmail",
    # calendar
    "https://www.googleapis.com/auth/calendar": "calendar",
    "https://www.googleapis.com/auth/calendar.readonly": "calendar",
    "https://www.googleapis.com/auth/calendar.events": "calendar",
    "https://www.googleapis.com/auth/calendar.events.readonly": "calendar",
    "https://www.googleapis.com/auth/calendar.settings.readonly": "calendar",
    # drive
    "https://www.googleapis.com/auth/drive": "drive",
    "https://www.googleapis.com/auth/drive.readonly": "drive",
    "https://www.googleapis.com/auth/drive.file": "drive",
    "https://www.googleapis.com/auth/drive.activity.readonly": "drive",
    "https://www.googleapis.com/auth/drive.metadata.readonly": "drive",
    # contacts
    "https://www.googleapis.com/auth/contacts": "contacts",
    "https://www.googleapis.com/auth/contacts.readonly": "contacts",
    "https://www.googleapis.com/auth/contacts.other.readonly": "contacts",
    # sheets
    "https://www.googleapis.com/auth/spreadsheets": "sheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly": "sheets",
    # docs
    "https://www.googleapis.com/auth/documents": "docs",
    "https://www.googleapis.com/auth/documents.readonly": "docs",
}


def _missing_scope_error(service: str) -> dict:
    """Return a structured error explaining that the token lacks the scope
    required for this service. Used in tools instead of calling the API."""
    return {
        "success": False,
        "error": f"token has no scope for {service}",
        "service": service,
        "token_scopes": _scopes(),
        "fix": (
            "Re-authorize with the missing scope. Either re-run "
            "setup.py --auth-url (and re-grant all scopes), or add the scope "
            "to GOOGLE_OAUTH_SCOPES in .env before re-auth. If your current "
            "token has wider scopes than GOOGLE_OAUTH_SCOPES, the scope "
            "allowlist is ignored."
        ),
    }


def _has_scope_for(service: str) -> bool:
    """True if the stored token's scopes include any scope that maps to `service`."""
    short = service.lower()
    for s in _scopes():
        if _SCOPE_TO_SERVICE.get(s, "").lower() == short:
            return True
    return False


# Granular capability check for Gmail: trash/archive/star/mark-read need the
# gmail.modify scope. gmail.send + gmail.readonly are NOT enough. Returns a
# structured error the caller can surface to the UI for a one-click re-auth.
_GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def _has_gmail_modify() -> bool:
    return _GMAIL_MODIFY_SCOPE in _scopes()


def _missing_gmail_modify_error() -> dict:
    return {
        "success": False,
        "error": "token lacks gmail.modify scope",
        "service": "gmail",
        "required_scope": _GMAIL_MODIFY_SCOPE,
        "token_scopes": _scopes(),
        "fix": (
            "Add https://www.googleapis.com/auth/gmail.modify to GOOGLE_OAUTH_SCOPES "
            "in .env, then re-run setup.py --auth-url and re-authorize. Until then, "
            "the dashboard's Trash/Archive/Star/Mark-read buttons will fail with this error."
        ),
    }


def _build_service(api: str, version: str):
    from googleapiclient.discovery import build
    return build(api, version, credentials=_get_credentials())


# --- Response helpers ------------------------------------------------------

def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _headers_dict(msg: dict) -> dict[str, str]:
    out = {}
    for h in msg.get("payload", {}).get("headers", []):
        name = h.get("name", "").lower()
        if name:
            out[name] = h.get("value", "")
    return out


def _extract_body(msg: dict) -> str:
    """Pull plain-text body from a Gmail message payload. Walks the
    multipart tree and picks the first text/plain part."""
    def _walk(part):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
        for child in part.get("parts", []) or []:
            text = _walk(child)
            if text:
                return text
        return ""

    payload = msg.get("payload", {})
    direct = _walk(payload)
    if direct:
        return direct
    # Fallback: snippet
    return msg.get("snippet", "")


def _isoformat(ts: str | datetime) -> str:
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


# --- mcp setup -------------------------------------------------------------

mcp = FastMCP("google-workspace")


# --- tools -----------------------------------------------------------------

@mcp.tool()
def gws_health() -> str:
    """Token status, scopes, file location, expiry, last-refresh age."""
    tp = _token_path()
    info = {
        "version": __version__,
        "token_path": str(tp),
        "token_exists": tp.exists(),
    }
    if tp.exists():
        try:
            data = json.loads(tp.read_text(encoding="utf-8"))
            info["client_id_prefix"] = (data.get("client_id") or "")[:18] + "..."
            info["scopes"] = data.get("scopes") or (data.get("scope") or "").split()
            info["has_refresh_token"] = bool(data.get("refresh_token"))
            info["has_access_token"] = bool(data.get("token"))
            expiry = data.get("expiry")
            if expiry:
                try:
                    if isinstance(expiry, str):
                        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    else:
                        expiry_dt = datetime.fromtimestamp(float(expiry), tz=timezone.utc)
                    now = datetime.now(timezone.utc)
                    info["expires_at"] = expiry_dt.isoformat()
                    info["expires_in_minutes"] = round((expiry_dt - now).total_seconds() / 60, 1)
                    info["expired"] = expiry_dt < now
                except (ValueError, TypeError, OSError) as e:
                    info["expiry_parse_error"] = str(e)
            info["size_bytes"] = tp.stat().st_size
            info["success"] = True
        except (OSError, ValueError) as e:
            info["success"] = False
            info["error"] = str(e)
    else:
        info["success"] = False
        info["error"] = "token file missing"
    return _to_json(info)


@mcp.tool()
def gws_gmail_search(query: str, max_results: int = 10) -> str:
    """Search Gmail with a Gmail query string.

    Args:
        query: Gmail search syntax. Examples: ``is:unread``, ``from:alice@x.com``,
               ``subject:\"Q3 deck\" newer_than:7d``.
        max_results: Cap on messages returned (default 10, max 100).
    """
    max_results = max(1, min(int(max_results), 100))
    service = _build_service("gmail", "v1")
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results,
    ).execute()
    messages = results.get("messages", [])
    if not messages:
        return _to_json({"success": True, "count": 0, "messages": []})

    output = []
    for meta in messages:
        msg = service.users().messages().get(
            userId="me", id=meta["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = _headers_dict(msg)
        output.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        })
    return _to_json({"success": True, "count": len(output), "messages": output})


@mcp.tool()
def gws_gmail_get(message_id: str, include_body: bool = True) -> str:
    """Fetch a single Gmail message by id.

    Args:
        message_id: The Gmail message id (from gws_gmail_search).
        include_body: If true (default), extract the plain-text body.
    """
    service = _build_service("gmail", "v1")
    fmt = "full" if include_body else "metadata"
    msg = service.users().messages().get(userId="me", id=message_id, format=fmt).execute()
    headers = _headers_dict(msg)
    payload = {
        "success": True,
        "id": msg["id"],
        "threadId": msg["threadId"],
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "labels": msg.get("labelIds", []),
        "snippet": msg.get("snippet", ""),
    }
    if include_body:
        payload["body"] = _extract_body(msg)
    return _to_json(payload)


@mcp.tool()
def gws_gmail_send(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Send a new email.

    Args:
        to: Recipient address(es), comma-separated.
        subject: Email subject.
        body: Plain-text email body.
        cc: Optional CC addresses, comma-separated.
        bcc: Optional BCC addresses, comma-separated.
    """
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    if bcc:
        msg["bcc"] = bcc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service = _build_service("gmail", "v1")
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return _to_json({
        "success": True,
        "id": sent.get("id"),
        "threadId": sent.get("threadId"),
        "labels": sent.get("labelIds", []),
        "to": to,
        "subject": subject,
    })


@mcp.tool()
def gws_gmail_reply(thread_id: str, body: str) -> str:
    """Reply in-thread (sends to all recipients of the latest message in the thread).

    Args:
        thread_id: The Gmail thread id (from gws_gmail_search).
        body: Plain-text reply body.
    """
    service = _build_service("gmail", "v1")
    thread = service.users().threads().get(userId="me", id=thread_id, format="metadata").execute()
    last_msg_id = thread["messages"][-1]["id"]
    last = service.users().messages().get(
        userId="me", id=last_msg_id, format="metadata",
        metadataHeaders=["From", "To", "Subject", "Reply-To"],
    ).execute()
    headers = _headers_dict(last)
    reply_to = headers.get("reply-to", "") or headers.get("from", "")
    subject = headers.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    msg = MIMEText(body)
    msg["to"] = reply_to
    msg["subject"] = subject
    msg["In-Reply-To"] = last.get("id", "")
    msg["References"] = last.get("id", "")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": thread_id},
    ).execute()
    return _to_json({
        "success": True,
        "id": sent.get("id"),
        "threadId": sent.get("threadId"),
        "to": reply_to,
        "subject": subject,
    })


@mcp.tool()
def gws_gmail_list_labels() -> str:
    """List all Gmail labels (system + user-created)."""
    service = _build_service("gmail", "v1")
    res = service.users().labels().list(userId="me").execute()
    return _to_json({"success": True, "labels": res.get("labels", [])})


@mcp.tool()
def gws_gmail_trash(message_id: str) -> str:
    """Move a Gmail message to trash (recoverable for 30 days).

    Requires gmail.modify scope. If the current token only has
    gmail.readonly/gmail.send, returns a structured error so the UI can prompt
    to re-authorize.

    Args:
        message_id: The Gmail message id (from gws_gmail_search).
    """
    if not _has_gmail_modify():
        return _to_json(_missing_gmail_modify_error())
    service = _build_service("gmail", "v1")
    service.users().messages().trash(userId="me", id=message_id).execute()
    return _to_json({"success": True, "trashed": message_id})


@mcp.tool()
def gws_gmail_archive(message_id: str) -> str:
    """Archive a Gmail message by removing the INBOX label.

    Requires gmail.modify scope.

    Args:
        message_id: The Gmail message id (from gws_gmail_search).
    """
    if not _has_gmail_modify():
        return _to_json(_missing_gmail_modify_error())
    service = _build_service("gmail", "v1")
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["INBOX"]},
    ).execute()
    return _to_json({"success": True, "archived": message_id})


@mcp.tool()
def gws_gmail_star(message_id: str, star: bool = True) -> str:
    """Add or remove the STARRED label on a Gmail message.

    Requires gmail.modify scope.

    Args:
        message_id: The Gmail message id (from gws_gmail_search).
        star: True to star (default), False to unstar.
    """
    if not _has_gmail_modify():
        return _to_json(_missing_gmail_modify_error())
    service = _build_service("gmail", "v1")
    body = {"addLabelIds": ["STARRED"]} if star else {"removeLabelIds": ["STARRED"]}
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()
    return _to_json({"success": True, "message_id": message_id, "starred": star})


@mcp.tool()
def gws_gmail_mark_read(message_id: str, read: bool = True) -> str:
    """Mark a Gmail message read or unread by toggling the UNREAD label.

    Requires gmail.modify scope.

    Args:
        message_id: The Gmail message id (from gws_gmail_search).
        read: True to mark read (default), False to mark unread.
    """
    if not _has_gmail_modify():
        return _to_json(_missing_gmail_modify_error())
    service = _build_service("gmail", "v1")
    body = {"removeLabelIds": ["UNREAD"]} if read else {"addLabelIds": ["UNREAD"]}
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()
    return _to_json({"success": True, "message_id": message_id, "read": read})


@mcp.tool()
def gws_calendar_list_events(
    calendar_id: str = "primary",
    time_min: str = "",
    time_max: str = "",
    max_results: int = 25,
    query: str = "",
) -> str:
    """List calendar events in a time range.

    Args:
        calendar_id: Calendar id (default ``primary``).
        time_min: ISO 8601 lower bound. Default: now (UTC).
        time_max: ISO 8601 upper bound. Default: 7 days from now.
        max_results: Cap on events (default 25, max 250).
        query: Optional free-text filter.
    """
    if not time_min:
        time_min = datetime.now(timezone.utc).isoformat()
    if not time_max:
        time_max = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    max_results = max(1, min(int(max_results), 250))
    service = _build_service("calendar", "v3")
    kwargs = {
        "calendarId": calendar_id,
        "timeMin": _isoformat(time_min),
        "timeMax": _isoformat(time_max),
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if query:
        kwargs["q"] = query
    res = service.events().list(**kwargs).execute()
    return _to_json({"success": True, "count": len(res.get("items", [])), "events": res.get("items", [])})


@mcp.tool()
def gws_calendar_get_event(event_id: str, calendar_id: str = "primary") -> str:
    """Fetch a single calendar event by id."""
    service = _build_service("calendar", "v3")
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    return _to_json({"success": True, "event": event})


@mcp.tool()
def gws_calendar_create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str = "",
    location: str = "",
    attendees: str = "",
    timezone: str = "UTC",
) -> str:
    """Create a new calendar event.

    Args:
        summary: Event title.
        start: ISO 8601 start (e.g. ``2026-06-22T14:00:00Z``).
        end: ISO 8601 end.
        calendar_id: Calendar id (default ``primary``).
        description: Optional description.
        location: Optional location.
        attendees: Optional comma-separated attendee emails.
        timezone: IANA timezone for the event (default ``UTC``).
    """
    body = {
        "summary": summary,
        "start": {"dateTime": _isoformat(start), "timeZone": timezone},
        "end": {"dateTime": _isoformat(end), "timeZone": timezone},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]
    service = _build_service("calendar", "v3")
    event = service.events().insert(calendarId=calendar_id, body=body).execute()
    return _to_json({"success": True, "event": event})


@mcp.tool()
def gws_calendar_delete_event(event_id: str, calendar_id: str = "primary") -> str:
    """Delete a calendar event."""
    if not _has_scope_for("calendar"):
        return _to_json(_missing_scope_error("calendar"))
    service = _build_service("calendar", "v3")
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return _to_json({"success": True, "deleted": event_id, "calendar_id": calendar_id})


# ---- Drive ----------------------------------------------------------------

@mcp.tool()
def gws_drive_search(query: str, max_results: int = 20, raw_query: bool = False) -> str:
    """Search Drive files.

    Args:
        query: Either a plain search term (``raw_query=False``, default) which is
               wrapped in ``fullText contains '<term>'``, or a raw Drive query string
               when ``raw_query=True`` (use Drive's operators: ``name contains 'X'``,
               ``mimeType='application/pdf'``, ``trashed=false``, ``'root' in parents``,
               etc.).
        max_results: Cap on results (default 20, max 100).
        raw_query: If true, treat ``query`` as a literal Drive API query.
    """
    if not _has_scope_for("drive"):
        return _to_json(_missing_scope_error("drive"))
    max_results = max(1, min(int(max_results), 100))
    q = query if raw_query else f"fullText contains '{query.replace(chr(39), chr(39)*4)}' and trashed=false"
    service = _build_service("drive", "v3")
    results = service.files().list(
        q=q, pageSize=max_results,
        fields="files(id, name, mimeType, modifiedTime, size, webViewLink)",
    ).execute()
    files = results.get("files", [])
    return _to_json({"success": True, "count": len(files), "files": files})


@mcp.tool()
def gws_drive_get(file_id: str) -> str:
    """Get metadata for a single Drive file by id."""
    if not _has_scope_for("drive"):
        return _to_json(_missing_scope_error("drive"))
    service = _build_service("drive", "v3")
    meta = service.files().get(
        fileId=file_id,
        fields="id, name, mimeType, modifiedTime, size, webViewLink, parents, owners(emailAddress,displayName)",
    ).execute()
    return _to_json({"success": True, "file": meta})


@mcp.tool()
def gws_drive_download(file_id: str, output_path: str, export_mime: str = "") -> str:
    """Download a Drive file to a local path. Google-native files (Docs/Sheets/Slides)
    must be exported — set export_mime or accept the sensible default.

    Args:
        file_id: Drive file id.
        output_path: Local destination path.
        export_mime: Optional override MIME for Google-native export (default maps
                     Docs→pdf, Sheets→csv, Slides→pdf, Drawings→png).
    """
    if not _has_scope_for("drive"):
        return _to_json(_missing_scope_error("drive"))
    from googleapiclient.http import MediaIoBaseDownload

    service = _build_service("drive", "v3")
    meta = service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
    mime = meta.get("mimeType", "")
    name = meta.get("name", file_id)

    native_export_map = {
        "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
        "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
        "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
        "application/vnd.google-apps.drawing": ("image/png", ".png"),
    }

    out_path = Path(output_path).expanduser()
    if out_path.is_dir():
        out_path = out_path / name

    if mime in native_export_map:
        export = export_mime or native_export_map[mime][0]
        default_ext = native_export_map[mime][1]
        if not out_path.suffix:
            out_path = out_path.with_suffix(default_ext)
        request = service.files().export_media(fileId=file_id, mimeType=export)
    else:
        request = service.files().get_media(fileId=file_id)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    return _to_json({
        "success": True,
        "saved_to": str(out_path),
        "size_bytes": out_path.stat().st_size if out_path.exists() else None,
        "mime": mime,
        "export_mime": export_mime or (native_export_map.get(mime, (None,))[0] if mime in native_export_map else None),
    })


@mcp.tool()
def gws_drive_create_folder(name: str, parent: str = "") -> str:
    """Create a new Drive folder.

    Args:
        name: Folder name.
        parent: Optional parent folder id (default: root).
    """
    if not _has_scope_for("drive"):
        return _to_json(_missing_scope_error("drive"))
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    service = _build_service("drive", "v3")
    result = service.files().create(body=body, fields="id, name, webViewLink").execute()
    return _to_json({
        "success": True,
        "folder": {"id": result["id"], "name": result.get("name", ""), "webViewLink": result.get("webViewLink", "")},
    })


@mcp.tool()
def gws_drive_trash(file_id: str) -> str:
    """Move a Drive file to trash (recoverable from drive.google.com/trash).

    The Google Drive v3 API: ``files().delete()`` moves a file to trash rather
    than permanently deleting it. Files can be restored from the Drive trash
    for ~30 days before Google permanently purges them.

    Requires the full ``drive`` scope (not just ``drive.readonly``). The
    current token has full drive, so this works out of the box.

    Args:
        file_id: Drive file id (from gws_drive_search).
    """
    if not _has_scope_for("drive"):
        return _to_json(_missing_scope_error("drive"))
    service = _build_service("drive", "v3")
    service.files().delete(fileId=file_id).execute()
    return _to_json({"success": True, "trashed": file_id})


@mcp.tool()
def gws_drive_share_link(file_id: str) -> str:
    """Return (or create) a ``anyone with the link`` shareable URL for a Drive file.

    If the file is not yet ``anyone``-shared, this calls ``permissions.create``
    to grant ``reader`` to ``anyone``. Otherwise it just returns the existing
    ``webViewLink``.

    Args:
        file_id: Drive file id.
    """
    if not _has_scope_for("drive"):
        return _to_json(_missing_scope_error("drive"))
    service = _build_service("drive", "v3")
    meta = service.files().get(
        fileId=file_id, fields="id, name, webViewLink, sharingUser",
    ).execute()
    # Try to create the anyone permission; if it already exists the API raises
    # 400 which we swallow and just return the existing webViewLink.
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except Exception:
        pass  # already shared with anyone — return existing link
    refreshed = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return _to_json({
        "success": True,
        "file_id": file_id,
        "name": meta.get("name", ""),
        "share_link": refreshed.get("webViewLink", meta.get("webViewLink", "")),
    })


# ---- Sheets ----------------------------------------------------------------

@mcp.tool()
def gws_sheets_get(spreadsheet_id: str, range_a1: str) -> str:
    """Read a range of cells from a Google Sheet.

    Args:
        spreadsheet_id: The Sheet's id (from its URL).
        range_a1: A1 notation range, e.g. ``Sheet1!A1:D10`` or just ``A1:D10``.
    """
    if not _has_scope_for("sheets"):
        return _to_json(_missing_scope_error("sheets"))
    service = _build_service("sheets", "v4")
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_a1,
    ).execute()
    return _to_json({
        "success": True,
        "range": range_a1,
        "values": result.get("values", []),
    })


@mcp.tool()
def gws_sheets_update(spreadsheet_id: str, range_a1: str, values_json: str) -> str:
    """Update a range of cells in a Google Sheet.

    Args:
        spreadsheet_id: The Sheet's id.
        range_a1: A1 notation range to write to (must match values dimensions).
        values_json: JSON array of arrays of cell values, e.g. ``[["A","B"],["1","2"]]``.
                     Pass as a JSON string.
    """
    if not _has_scope_for("sheets"):
        return _to_json(_missing_scope_error("sheets"))
    values = json.loads(values_json)
    body = {"values": values}
    service = _build_service("sheets", "v4")
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_a1,
        valueInputOption="USER_ENTERED", body=body,
    ).execute()
    return _to_json({
        "success": True,
        "updated_cells": result.get("updatedCells", 0),
        "updated_range": result.get("updatedRange", ""),
        "updated_rows": result.get("updatedRows", 0),
    })


@mcp.tool()
def gws_sheets_append(spreadsheet_id: str, range_a1: str, values_json: str) -> str:
    """Append rows to a Google Sheet (after the last row with data in the range).

    Args:
        spreadsheet_id: The Sheet's id.
        range_a1: A1 notation — the data is appended after the last row in this range.
        values_json: JSON array of arrays of cell values.
    """
    if not _has_scope_for("sheets"):
        return _to_json(_missing_scope_error("sheets"))
    values = json.loads(values_json)
    body = {"values": values}
    service = _build_service("sheets", "v4")
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=range_a1,
        valueInputOption="USER_ENTERED", body=body,
    ).execute()
    return _to_json({
        "success": True,
        "updated_cells": result.get("updatedCells", 0),
        "updated_range": result.get("updates", {}).get("updatedRange", ""),
    })


# ---- Docs ------------------------------------------------------------------

@mcp.tool()
def gws_docs_get(document_id: str) -> str:
    """Get a Google Doc's title and body text (paragraphs).

    Args:
        document_id: The Doc's id (from its URL).
    """
    if not _has_scope_for("docs"):
        return _to_json(_missing_scope_error("docs"))
    service = _build_service("docs", "v1")
    doc = service.documents().get(documentId=document_id).execute()

    def _walk_text(content):
        out = []
        for elem in content or []:
            if elem.get("paragraph"):
                run_text = "".join(r.get("text", "") for r in elem["paragraph"].get("elements", []))
                if run_text:
                    out.append(run_text)
            elif elem.get("table"):
                for row in elem["table"].get("tableRows", []):
                    cells = []
                    for cell in row.get("tableCells", []):
                        cells.append("\n".join(_walk_text(cell.get("content", []))).strip())
                    out.append(" | ".join(cells))
        return out

    body = _walk_text(doc.get("body", {}).get("content", []))
    return _to_json({
        "success": True,
        "title": doc.get("title", ""),
        "document_id": doc.get("documentId", ""),
        "paragraphs": body,
        "url": f"https://docs.google.com/document/d/{doc.get('documentId', '')}/edit",
    })


@mcp.tool()
def gws_docs_create(title: str, body_text: str = "") -> str:
    """Create a new Google Doc, optionally seeded with initial text.

    Args:
        title: Doc title.
        body_text: Optional initial text inserted at the top.
    """
    if not _has_scope_for("docs"):
        return _to_json(_missing_scope_error("docs"))
    service = _build_service("docs", "v1")
    doc = service.documents().create(body={"title": title}).execute()
    doc_id = doc.get("documentId", "")
    inserted = 0
    if body_text and doc_id:
        body = {"requests": [{"insertText": {"text": body_text, "location": {"index": 1}}}]}
        service.documents().batchUpdate(documentId=doc_id, body=body).execute()
        inserted = len(body_text)
    return _to_json({
        "success": True,
        "document_id": doc_id,
        "title": doc.get("title", ""),
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        "inserted_chars": inserted,
    })


# ---- Contacts --------------------------------------------------------------

@mcp.tool()
def gws_contacts_list(max_results: int = 100) -> str:
    """List the user's Google contacts (name + emails + phones).

    Args:
        max_results: Cap on contacts (default 100, max 1000).
    """
    if not _has_scope_for("contacts"):
        return _to_json(_missing_scope_error("contacts"))
    max_results = max(1, min(int(max_results), 1000))
    service = _build_service("people", "v1")
    results = service.people().connections().list(
        resourceName="people/me",
        pageSize=max_results,
        personFields="names,emailAddresses,phoneNumbers,organizations",
    ).execute()
    contacts = []
    for person in results.get("connections", []):
        names = person.get("names", [{}])
        emails = person.get("emailAddresses", [])
        phones = person.get("phoneNumbers", [])
        orgs = person.get("organizations", [])
        contacts.append({
            "name": names[0].get("displayName", "") if names else "",
            "emails": [e.get("value", "") for e in emails],
            "phones": [p.get("value", "") for p in phones],
            "organization": (orgs[0].get("name", "") if orgs else ""),
        })
    return _to_json({"success": True, "count": len(contacts), "contacts": contacts})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
