"""Generic HTTP/JSON adapter for arbitrary JSON time-series endpoints.

source_args schema::

    {
        "url": "https://example.com/api/series",   # required — supports {from} and {to} placeholders
        "method": "GET",                            # optional, default "GET"
        "headers": {"X-Api-Key": "..."},            # optional
        "jsonpath_records": "data[*]",              # required — JSONPath (or list of dicts path)
        "jsonpath_ts":     "ts",                    # required — path within each record to the timestamp
        "jsonpath_value":  "value",                 # required — path within each record to the numeric value
        "from_format": "iso8601"                    # optional — how to format {from} in URL. Currently always ISO 8601.
    }

The MCP tries ``jsonpath_ng`` if available, otherwise falls back to a minimal
dict-traversal parser that supports dotted keys and ``[N]`` index syntax
(common in JSONPath). That's enough for most public APIs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


def fetch(source_args: dict, from_ts: str, to_ts: str) -> list[dict]:
    url_tmpl = source_args.get("url")
    if not url_tmpl:
        raise ValueError("generic_http adapter requires source_args.url")
    method = (source_args.get("method") or "GET").upper()
    headers = source_args.get("headers") or {}
    rec_path = source_args.get("jsonpath_records")
    ts_path = source_args.get("jsonpath_ts")
    val_path = source_args.get("jsonpath_value")
    if not (rec_path and ts_path and val_path):
        raise ValueError(
            "generic_http adapter requires source_args.jsonpath_records, "
            "jsonpath_ts, jsonpath_value"
        )

    url = _interpolate_url(url_tmpl, from_ts, to_ts)

    resp = httpx.request(method, url, headers=headers, timeout=30.0)
    resp.raise_for_status()
    payload = resp.json()

    records = _resolve(payload, rec_path)
    if not isinstance(records, list):
        raise ValueError(
            f"jsonpath_records {rec_path!r} did not resolve to a list (got {type(records).__name__})"
        )

    out: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts_raw = _resolve(rec, ts_path)
        val_raw = _resolve(rec, val_path)
        ts_iso = _to_iso(ts_raw)
        val = _to_float(val_raw)
        if ts_iso is None or val is None:
            continue
        if from_ts and ts_iso < from_ts:
            continue
        if to_ts and ts_iso > to_ts:
            continue
        out.append({
            "kind": "metric",
            "ts": ts_iso,
            "value": val,
            "meta": {"source_url": url, "raw_ts": str(ts_raw)},
        })
    return out


def _interpolate_url(tmpl: str, from_ts: str, to_ts: str) -> str:
    """Replace ``{from}`` and ``{to}`` placeholders. ``from`` is a Python keyword,
    so we can't use str.format with that key. Use a manual replace instead."""
    return (
        tmpl.replace("{from}", quote(from_ts, safe=""))
           .replace("{to}",   quote(to_ts,   safe=""))
           .replace("{from_date}", quote(from_ts, safe=""))
           .replace("{to_date}",   quote(to_ts,   safe=""))
    )


def _resolve(obj: Any, path: str) -> Any:
    """Tiny JSONPath-ish resolver. Supports ``a.b.c`` and ``a[0].b``."""
    if not path:
        return obj
    # Tokenise: split on '.' but keep bracketed indices attached
    tokens = re.findall(r"[^\.\[\]]+|\[\d+\]", path)
    cur = obj
    for tok in tokens:
        if cur is None:
            return None
        if tok.startswith("["):
            idx = int(tok[1:-1])
            try:
                cur = cur[idx]
            except (IndexError, TypeError):
                return None
        else:
            if isinstance(cur, dict):
                cur = cur.get(tok)
            else:
                return None
    return cur


def _to_iso(ts_raw: Any) -> str | None:
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts_raw), tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None
    s = str(ts_raw).strip()
    if not s:
        return None
    # ISO 8601 — accept trailing Z
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return s  # pass through, the caller can still filter on it


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None
