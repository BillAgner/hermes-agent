p = 'hermes_cli/web_server.py'
with open(p, 'r', encoding='utf-8') as f:
    content = f.read()

# Revert my over-engineered addition by replacing the just-inserted block
# with a simpler one. Use the literal block we just added.
old_block_start = '@app.get("/api/cron/runs/{session_id}/output")'
old_block_end_marker = '''        import cron.jobs as _cron_jobs_restore
        importlib_reload = __import__("importlib").reload
        importlib_reload(_cron_jobs_restore)

    if not output_file.exists() or not output_file.is_file():'''

assert old_block_start in content, 'start marker not found'
assert old_block_end_marker in content, 'end marker not found'

# Slice from the start of the endpoint through the marker that precedes the
# next @app.post block. Walk backward from the end marker.
start_idx = content.index(old_block_start)
end_idx = content.index(old_block_end_marker, start_idx)

new_block = '''@app.get("/api/cron/runs/{session_id}/output")
async def get_cron_run_output(session_id: str, profile: Optional[str] = None):
    """Return the markdown output file for a single cron run session.

    Session ids look like ``cron_<job_id>_<YYYYMMDD_HHMMSS>`` (see
    ``cron/scheduler.run_job``). The corresponding output file lives at
    ``<HERMES_HOME>/cron/output/<job_id>/<YYYY-MM-DD_HH-MM-SS>.md`` — the
    timestamp is reformatted with dashes and the second underscore becomes
    a hyphen so it matches the filename pattern written by
    ``cron.jobs.save_job_output`` (``%Y-%m-%d_%H-%M-%S``).

    Returns ``{session_id, job_id, profile, content, mtime, size}`` on
    success, 404 if the file is missing (e.g. the run is still active or
    the output hasn't been flushed yet), or 400 on a malformed session id.
    The job_id portion is validated by ``cron.jobs._job_output_dir`` so a
    hostile session_id cannot escape the cron output sandbox.
    """
    import re as _re
    from cron.jobs import OUTPUT_DIR as _OUTPUT_DIR
    from cron.jobs import _job_output_dir as _job_output_dir

    # Validate session_id shape: cron_<12hex>_<8digits>_<6digits>.
    # Cron job ids are 12-char hex strings (see cron.jobs.create_job); the
    # timestamp is two runs of digits separated by an underscore.
    m = _re.fullmatch(r"cron_([0-9a-f]{12})_(\\d{8}_\\d{6})", session_id)
    if not m:
        raise HTTPException(status_code=400, detail="Malformed cron session id")

    job_id = m.group(1)
    ts_compact = m.group(2)  # YYYYMMDD_HHMMSS
    # Reformat compact timestamp to the file's convention:
    # YYYYMMDD_HHMMSS -> YYYY-MM-DD_HH-MM-SS (insert dashes, second _ -> -).
    file_ts = (
        f"{ts_compact[0:4]}-{ts_compact[4:6]}-{ts_compact[6:8]}"
        f"_{ts_compact[9:11]}-{ts_compact[11:13]}-{ts_compact[13:15]}"
    )

    # _job_output_dir validates job_id is a safe single path component;
    # raises ValueError on path traversal / absolute / nested separators.
    try:
        job_output_dir = _job_output_dir(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Resolve which profile owns this session. The output file lives under
    # that profile's HERMES_HOME; if no profile claims the job, default to
    # whatever the dashboard's current HERMES_HOME points at (single-profile
    # installs).
    selected = profile or _find_cron_job_profile(job_id) or "default"

    # Per-profile: temporarily swap HERMES_HOME so OUTPUT_DIR resolves to
    # the right base directory, then read.
    import os as _os
    from hermes_constants import get_hermes_home as _get_hermes_home
    saved_home = _os.environ.get("HERMES_HOME")
    profiles_root = _Path(_get_hermes_home()).parent / "profiles"
    candidate = profiles_root / selected
    if selected != "default" and candidate.exists():
        _os.environ["HERMES_HOME"] = str(candidate)
    try:
        # Reload cron.jobs under the swapped env so OUTPUT_DIR rebinds.
        import importlib as _importlib
        import cron.jobs as _cron_jobs_mod
        _importlib.reload(_cron_jobs_mod)
        output_file = _cron_jobs_mod.OUTPUT_DIR / job_id / f"{file_ts}.md"
    finally:
        if saved_home is not None:
            _os.environ["HERMES_HOME"] = saved_home
        else:
            _os.environ.pop("HERMES_HOME", None)
        # Always restore the module to its pre-call state so subsequent
        # requests see the original OUTPUT_DIR.
        import importlib as _importlib2
        import cron.jobs as _cron_jobs_restore
        _importlib2.reload(_cron_jobs_restore)

    if not output_file.exists() or not output_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No output file for {session_id} (looking for {output_file.name})",
        )

    try:
        text = output_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read output: {exc}")

    stat = output_file.stat()
    return {
        "session_id": session_id,
        "job_id": job_id,
        "profile": selected,
        "path": str(output_file),
        "content": text,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }


'''

# Replace the over-engineered block with the simplified one.
content = content[:start_idx] + new_block + content[end_idx:]

with open(p, 'w', encoding='utf-8') as f:
    f.write(content)
print('OK', len(content))