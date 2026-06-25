"""SQLite storage layer for time-series MCP.

Schema (one DB file, default ``C:\\Data\\Hermes\\finance\\data\\timeseries.db``):

- ``series_meta``      — registered series (one row per series)
- ``metric_value``     — generic (ts, value) points, idempotent on (series_id, ts)
- ``ohlcv_bars``       — OHLCV bars, idempotent on (series_id, ts)
- ``series_links``     — series ↔ research project linkages

All writes use INSERT OR REPLACE so re-syncing the same window is safe and
idempotent. Reads are range scans on (series_id, ts).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = Path(
    os.environ.get("TIME_SERIES_DB")
    or r"C:\Data\Hermes\finance\data\timeseries.db"
)


class SeriesNotFoundError(LookupError):
    """Raised when a series_id or name doesn't exist."""


class SeriesAlreadyExistsError(ValueError):
    """Raised when creating a series with a duplicate name."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS series_meta (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    metric          TEXT NOT NULL,
    unit            TEXT NOT NULL,
    cadence         TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_args     TEXT NOT NULL DEFAULT '{}',
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    last_synced_at  TEXT,
    last_value_ts   TEXT,
    last_value      REAL
);

CREATE TABLE IF NOT EXISTS metric_value (
    series_id   INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    value       REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, ts),
    FOREIGN KEY (series_id) REFERENCES series_meta(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ohlcv_bars (
    series_id   INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL,
    PRIMARY KEY (series_id, ts),
    FOREIGN KEY (series_id) REFERENCES series_meta(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS series_links (
    series_id     INTEGER NOT NULL,
    project_slug  TEXT NOT NULL,
    ref_type      TEXT NOT NULL,
    ref_id        TEXT NOT NULL,
    linked_at     TEXT NOT NULL,
    PRIMARY KEY (series_id, project_slug, ref_type, ref_id),
    FOREIGN KEY (series_id) REFERENCES series_meta(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mv_series_ts ON metric_value(series_id, ts);
CREATE INDEX IF NOT EXISTS idx_ohlcv_series_ts ON ohlcv_bars(series_id, ts);
CREATE INDEX IF NOT EXISTS idx_links_project ON series_links(project_slug);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create schema if absent. Idempotent."""
    con = _connect(db_path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


def create_series(
    db_path: Path,
    name: str,
    metric: str,
    unit: str,
    cadence: str,
    source_type: str,
    source_args: Optional[dict] = None,
    description: str = "",
    now_iso: str = "",
) -> dict:
    """Register a new series. Raises SeriesAlreadyExistsError on duplicate name."""
    if not name or not metric or not unit or not cadence or not source_type:
        raise ValueError("name, metric, unit, cadence, source_type are all required")
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    init_db(db_path)
    con = _connect(db_path)
    try:
        try:
            cur = con.execute(
                """
                INSERT INTO series_meta
                    (name, metric, unit, cadence, source_type, source_args, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name, metric, unit, cadence, source_type,
                    json.dumps(source_args or {}, ensure_ascii=False),
                    description, now_iso,
                ),
            )
            con.commit()
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                raise SeriesAlreadyExistsError(f"series {name!r} already exists") from e
            raise
        sid = cur.lastrowid
        return get_series(db_path, sid)
    finally:
        con.close()


def get_series(db_path: Path, series_id_or_name) -> dict:
    """Fetch series metadata by id (int) or name (str)."""
    con = _connect(db_path)
    try:
        if isinstance(series_id_or_name, int):
            cur = con.execute("SELECT * FROM series_meta WHERE id = ?", (series_id_or_name,))
        else:
            cur = con.execute("SELECT * FROM series_meta WHERE name = ?", (str(series_id_or_name),))
        row = cur.fetchone()
        if row is None:
            raise SeriesNotFoundError(f"no series with id/name {series_id_or_name!r}")
        return _row_to_series(row)
    finally:
        con.close()


def list_series(db_path: Path) -> list[dict]:
    con = _connect(db_path)
    try:
        cur = con.execute("SELECT * FROM series_meta ORDER BY id ASC")
        return [_row_to_series(r) for r in cur.fetchall()]
    finally:
        con.close()


def append_metric(
    db_path: Path, series_id: int, ts: str, value: float, meta: Optional[dict] = None
) -> None:
    """Upsert a single metric point. Idempotent on (series_id, ts)."""
    init_db(db_path)
    con = _connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO metric_value (series_id, ts, value, meta)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(series_id, ts) DO UPDATE SET
                value = excluded.value,
                meta  = excluded.meta
            """,
            (series_id, ts, value, json.dumps(meta or {}, ensure_ascii=False)),
        )
        con.execute(
            """
            UPDATE series_meta
            SET last_value_ts = CASE WHEN last_value_ts IS NULL OR last_value_ts < ? THEN ? ELSE last_value_ts END,
                last_value    = CASE WHEN last_value_ts IS NULL OR last_value_ts < ? THEN ? ELSE last_value END
            WHERE id = ?
            """,
            (ts, ts, ts, value, series_id),
        )
        con.commit()
    finally:
        con.close()


def append_ohlcv(
    db_path: Path, series_id: int, ts: str,
    open: float, high: float, low: float, close: float,
    volume: float | None = None,
) -> None:
    """Upsert a single OHLCV bar. Idempotent on (series_id, ts)."""
    init_db(db_path)
    con = _connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO ohlcv_bars (series_id, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, ts) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low  = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            (series_id, ts, open, high, low, close, volume),
        )
        # Use 'close' as the last_value for OHLCV series
        con.execute(
            """
            UPDATE series_meta
            SET last_value_ts = CASE WHEN last_value_ts IS NULL OR last_value_ts < ? THEN ? ELSE last_value_ts END,
                last_value    = CASE WHEN last_value_ts IS NULL OR last_value_ts < ? THEN ? ELSE last_value END
            WHERE id = ?
            """,
            (ts, ts, ts, close, series_id),
        )
        con.commit()
    finally:
        con.close()


def query_metric(
    db_path: Path, series_id: int,
    from_ts: str | None = None, to_ts: str | None = None,
    limit: int = 10000,
) -> list[dict]:
    """Return metric points for series_id in [from_ts, to_ts], ascending by ts."""
    con = _connect(db_path)
    try:
        clauses = ["series_id = ?"]
        params: list = [series_id]
        if from_ts:
            clauses.append("ts >= ?")
            params.append(from_ts)
        if to_ts:
            clauses.append("ts <= ?")
            params.append(to_ts)
        params.append(int(limit))
        cur = con.execute(
            f"SELECT ts, value, meta FROM metric_value WHERE {' AND '.join(clauses)} ORDER BY ts ASC LIMIT ?",
            params,
        )
        out = []
        for r in cur.fetchall():
            try:
                meta = json.loads(r["meta"]) if r["meta"] else {}
            except (TypeError, ValueError):
                meta = {}
            out.append({"ts": r["ts"], "value": r["value"], "meta": meta})
        return out
    finally:
        con.close()


def query_ohlcv(
    db_path: Path, series_id: int,
    from_ts: str | None = None, to_ts: str | None = None,
    limit: int = 10000,
) -> list[dict]:
    """Return OHLCV bars for series_id in [from_ts, to_ts], ascending by ts."""
    con = _connect(db_path)
    try:
        clauses = ["series_id = ?"]
        params: list = [series_id]
        if from_ts:
            clauses.append("ts >= ?")
            params.append(from_ts)
        if to_ts:
            clauses.append("ts <= ?")
            params.append(to_ts)
        params.append(int(limit))
        cur = con.execute(
            f"""SELECT ts, open, high, low, close, volume FROM ohlcv_bars
                WHERE {' AND '.join(clauses)} ORDER BY ts ASC LIMIT ?""",
            params,
        )
        return [
            {
                "ts": r["ts"], "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
            }
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def mark_synced(db_path: Path, series_id: int, now_iso: str) -> None:
    con = _connect(db_path)
    try:
        con.execute(
            "UPDATE series_meta SET last_synced_at = ? WHERE id = ?",
            (now_iso, series_id),
        )
        con.commit()
    finally:
        con.close()


def link_series(
    db_path: Path, series_id: int, project_slug: str,
    ref_type: str, ref_id: str, now_iso: str = "",
) -> None:
    """Link a series to a research project (idempotent)."""
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    init_db(db_path)
    con = _connect(db_path)
    try:
        con.execute(
            """
            INSERT OR IGNORE INTO series_links
                (series_id, project_slug, ref_type, ref_id, linked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (series_id, project_slug, ref_type, ref_id, now_iso),
        )
        con.commit()
    finally:
        con.close()


def list_links_for_project(db_path: Path, project_slug: str) -> list[dict]:
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            SELECT s.id AS series_id, s.name, s.metric, s.unit,
                   l.ref_type, l.ref_id, l.linked_at
            FROM series_links l
            JOIN series_meta s ON s.id = l.series_id
            WHERE l.project_slug = ?
            ORDER BY s.name, l.ref_type, l.ref_id
            """,
            (project_slug,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def series_status(db_path: Path, series_id: int) -> dict:
    """Compute freshness, gap detection, row counts."""
    con = _connect(db_path)
    try:
        meta = get_series(db_path, series_id)
        if meta["source_type"] == "yahoo":
            tbl = "ohlcv_bars"
        else:
            tbl = "metric_value"
        cnt = con.execute(f"SELECT COUNT(*) AS c FROM {tbl} WHERE series_id = ?", (series_id,)).fetchone()["c"]
        first = con.execute(f"SELECT ts FROM {tbl} WHERE series_id = ? ORDER BY ts ASC LIMIT 1", (series_id,)).fetchone()
        last = con.execute(f"SELECT ts FROM {tbl} WHERE series_id = ? ORDER BY ts DESC LIMIT 1", (series_id,)).fetchone()
        return {
            "series": meta,
            "row_count": cnt,
            "first_ts": first["ts"] if first else None,
            "last_ts": last["ts"] if last else None,
            "last_value": meta["last_value"],
            "last_value_ts": meta["last_value_ts"],
            "last_synced_at": meta["last_synced_at"],
            "freshness_minutes": _freshness_minutes(meta.get("last_value_ts")),
        }
    finally:
        con.close()


def _freshness_minutes(last_ts: str | None) -> float | None:
    if not last_ts:
        return None
    from datetime import datetime, timezone
    try:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0


def _row_to_series(row: sqlite3.Row) -> dict:
    try:
        source_args = json.loads(row["source_args"]) if row["source_args"] else {}
    except (TypeError, ValueError):
        source_args = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "metric": row["metric"],
        "unit": row["unit"],
        "cadence": row["cadence"],
        "source_type": row["source_type"],
        "source_args": source_args,
        "description": row["description"],
        "created_at": row["created_at"],
        "last_synced_at": row["last_synced_at"],
        "last_value_ts": row["last_value_ts"],
        "last_value": row["last_value"],
    }


def db_summary(db_path: Path) -> dict:
    init_db(db_path)
    con = _connect(db_path)
    try:
        s_cnt = con.execute("SELECT COUNT(*) AS c FROM series_meta").fetchone()["c"]
        mv_cnt = con.execute("SELECT COUNT(*) AS c FROM metric_value").fetchone()["c"]
        oh_cnt = con.execute("SELECT COUNT(*) AS c FROM ohlcv_bars").fetchone()["c"]
        lk_cnt = con.execute("SELECT COUNT(*) AS c FROM series_links").fetchone()["c"]
        size = db_path.stat().st_size if db_path.exists() else 0
        return {
            "db_path": str(db_path),
            "size_bytes": size,
            "series_count": s_cnt,
            "metric_value_rows": mv_cnt,
            "ohlcv_bar_rows": oh_cnt,
            "link_count": lk_cnt,
        }
    finally:
        con.close()
