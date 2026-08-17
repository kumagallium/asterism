"""Design-time source staging — the source has a server-side home from the
moment it is dropped (ADR source-staging.md).

Why
---
Until now the source file lived in the browser during design (S1-S4) and on
the server only from S5, when the dataset — and its ``source/`` directory —
came into being. Every design call (inspect / skeleton / re-check / continue)
re-uploaded the same file into a temp dir that was deleted when the call
returned. Two consequences: the design phase depended on one browser tab (a
reload lost the file: dead re-check, dead "add the row kind", vanished "ask the
AI again"), and a 47-row card was uploaded four or five times per design.

Neither followed from the platform — the desktop app's "server" IS the user's
own disk, and the web instance has one too. It followed from the code path.
So: ``POST /api/staging`` writes the uploads under
``<registry_root>/_staging/<staging_id>/`` once; the design calls take
``staging_id`` instead of files; S5's "attach source" copies from staging into
the dataset's ``source/`` (the same converter path as a fresh upload, so
``.xlsx``/``.docx`` still keep their originals alongside).

Layout
------
::

    _staging/<uuid4>/
        meta.json          {"created_at", "sources": [canonical names]}
        raw/<name>         the upload as received (converted at attach)
        <canonical>.csv    what the design reads (xlsx already expanded)

Lifecycle
---------
A staging record lives until the client deletes it (a fresh start), the
attach consumes it, or the TTL sweep (7 days, run on every create) removes it.
The id is a uuid4 — a capability, unguessable, and validated strictly so it
can never be a path. Everything else is best-effort: an unreadable record is a
404 the client answers by falling back to its own copy of the files.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

STAGING_DIRNAME = "_staging"
TTL = timedelta(days=7)

_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class StagingNotFound(LookupError):
    """No live staging record for this id (expired, consumed, or never was)."""


def root(registry_root: Path | str) -> Path:
    return Path(registry_root) / STAGING_DIRNAME


def new_id() -> str:
    return str(uuid.uuid4())


def valid_id(staging_id: str | None) -> bool:
    return bool(staging_id) and _ID.match(staging_id) is not None


def dir_for(registry_root: Path | str, staging_id: str, *, create: bool = False) -> Path:
    """The record's directory. Rejects anything that is not a uuid4 — the id is
    the ONLY path component a client controls, and it must never traverse."""
    if not valid_id(staging_id):
        raise StagingNotFound(f"invalid staging id {staging_id!r}")
    d = root(registry_root) / staging_id
    if create:
        (d / "raw").mkdir(parents=True, exist_ok=True)
    return d


def write_meta(sdir: Path, sources: list[str]) -> dict:
    meta = {"created_at": datetime.now(UTC).isoformat(), "sources": list(sources)}
    (sdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return meta


def load(registry_root: Path | str, staging_id: str) -> tuple[Path, list[Path]]:
    """``(dir, design_paths)`` for a live record, in the recorded source order.

    Raises :class:`StagingNotFound` when the id is malformed, the directory is
    gone, or the meta is unreadable — the caller turns that into a 404 and the
    client falls back to uploading its own copy.
    """
    sdir = dir_for(registry_root, staging_id)
    try:
        meta = json.loads((sdir / "meta.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise StagingNotFound(f"staging {staging_id} not found") from exc
    names = [str(n) for n in meta.get("sources") or []]
    paths = [sdir / n for n in names if (sdir / n).is_file()]
    if not paths:
        raise StagingNotFound(f"staging {staging_id} has no sources")
    return sdir, paths


def raw_paths(sdir: Path) -> list[Path]:
    """The uploads as received — what S5's attach converts, exactly like a fresh
    upload would be."""
    raw = sdir / "raw"
    return sorted(p for p in raw.iterdir() if p.is_file()) if raw.is_dir() else []


def delete(registry_root: Path | str, staging_id: str) -> bool:
    """Forget a record. True when something was removed."""
    try:
        sdir = dir_for(registry_root, staging_id)
    except StagingNotFound:
        return False
    if not sdir.is_dir():
        return False
    shutil.rmtree(sdir, ignore_errors=True)
    return True


def sweep(registry_root: Path | str, ttl: timedelta = TTL) -> int:
    """Remove records older than ``ttl`` (by mtime of the directory — a record
    a client stopped touching a week ago belongs to a tab long gone). Returns
    how many were removed. Never raises."""
    base = root(registry_root)
    if not base.is_dir():
        return 0
    cutoff = time.time() - ttl.total_seconds()
    removed = 0
    for entry in base.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def expires_at(sdir: Path, ttl: timedelta = TTL) -> str:
    try:
        return datetime.fromtimestamp(sdir.stat().st_mtime + ttl.total_seconds(), UTC).isoformat()
    except OSError:
        return datetime.now(UTC).isoformat()
