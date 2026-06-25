"""Loads the editable domain tier table from data/domains.json.

The file is re-read on every call so edits Bill makes at runtime take
effect without a restart. We resolve the file via importlib.resources
first (works in installed packages) and fall back to walking up from
__file__ (works in editable installs and source checkouts).
"""

import json
import os
from importlib import resources
from pathlib import Path
from threading import RLock
from typing import Any

_lock = RLock()
_cache: dict[str, Any] | None = None
_cache_mtime: float | None = None


def _data_file_path() -> Path:
    """Return the on-disk path of data/domains.json.

    Tries in order:
      1. $SOURCE_CREDIBILITY_DATA_DIR/domains.json (env override)
      2. importlib.resources lookup (works for installed wheels)
      3. Walk up from __file__ looking for data/domains.json (source checkout)
    """
    env_dir = os.environ.get("SOURCE_CREDIBILITY_DATA_DIR")
    if env_dir:
        return Path(env_dir) / "domains.json"

    try:
        with resources.as_file(resources.files("credibility_mcp.data").joinpath("domains.json")) as p:
            # as_file is a context manager; use it just to resolve the path.
            return Path(str(p))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "data" / "domains.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate data/domains.json. Set SOURCE_CREDIBILITY_DATA_DIR "
        "or reinstall the package."
    )


def _load_from_disk() -> dict[str, Any]:
    path = _data_file_path()
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_table() -> dict[str, Any]:
    """Return the current tier table. Reloads if the file changed on disk."""
    global _cache, _cache_mtime
    with _lock:
        path = _data_file_path()
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            raise FileNotFoundError(f"data/domains.json missing at {path}") from exc
        if _cache is None or _cache_mtime != mtime:
            _cache = _load_from_disk()
            _cache_mtime = mtime
        return _cache


def reload() -> dict[str, Any]:
    """Force a reload (e.g. after add_custom_domain)."""
    global _cache, _cache_mtime
    with _lock:
        _cache = _load_from_disk()
        _cache_mtime = _data_file_path().stat().st_mtime
        return _cache


def write_table(data: dict[str, Any]) -> None:
    """Persist a modified table (used by add_custom_domain tool)."""
    global _cache, _cache_mtime
    with _lock:
        path = _data_file_path()
        tmp_path = str(path) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
        _cache = data
        _cache_mtime = path.stat().st_mtime


def get_weights() -> dict[str, float]:
    return get_table().get("weights", {})


def get_thresholds() -> dict[str, float]:
    return get_table().get("default_thresholds", {})


def get_data_path() -> str:
    """Return the resolved on-disk path of the active tier table (for
    diagnostics / the cred_health tool)."""
    return str(_data_file_path())
