"""On-disk Ask chat history + settings for single-user (desktop / asterism-local)
runs (ADR app-data-on-disk.md).

Why
---
The Tauri window runs the same SPA as the hosted deployment, and the SPA
keeps Ask's chat threads and app settings in ``localStorage`` — a per-browser
store with a hard size cap (5 MiB) and no relation to the machine's disk: a
reinstall of the desktop app wipes it, and the UI has to describe the history
as "saved in this browser" even though the app IS the browser here. When the
api is running in single-user mode (``asterism-local``, i.e. the desktop
shell's backend) there is exactly one user and the process already owns a
per-user data directory (:func:`asterism_api.local.default_data_home`) — so
the natural fix is to give threads/settings a server-side home there, the
same move :mod:`asterism_api.staging` made for design-time sources.

This module holds no opinion about *when* it is reachable — that gate lives
in ``main.py`` (only mounted/enabled under ``ASTERISM_SINGLE_USER=1``) so the
shared/hosted api's behaviour is untouched.

Layout
------
::

    appdata/
        ask/<uuid4>.json     one chat thread per file
        settings.json        app settings (secrets stripped before write)

Threads are addressed by a uuid4 the client mints — validated as strictly as
:mod:`asterism_api.staging`'s staging id, for the same reason: it is the ONLY
path component a client controls, and it must never be able to traverse.

Limits
------
Generous but finite, so a runaway client (or corrupted state) can't turn a
laptop's disk into an incident: one thread ``MAX_THREAD_BYTES`` (1 MiB), at
most ``MAX_THREADS`` (1000) thread files, settings ``MAX_SETTINGS_BYTES``
(256 KiB). Callers turn the size exceptions into 413.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("asterism.appdata")

ASK_DIRNAME = "ask"
SETTINGS_FILENAME = "settings.json"

MAX_THREAD_BYTES = 1 * 1024 * 1024
MAX_THREADS = 1000
MAX_SETTINGS_BYTES = 256 * 1024

# A settings key (at any nesting depth) whose name contains any of these
# (case-insensitive) is a credential and is never written to disk (ADR: API
# keys stay out of the on-disk settings file even in single-user mode).
# Deliberately NOT "key" (too broad — would eat sortKey/hotkey/etc); a bare
# ``key`` is instead matched exactly below, for the naive ``{"key": "sk-..."}``
# shape.
_SECRET_KEY_MARKERS = (
    "apikey",
    "api_key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
)
_SECRET_KEY_EXACT = {"key"}

# Nested settings can come from arbitrary client JSON; cap recursion so a
# pathological/adversarial payload can't blow the stack.
_MAX_STRIP_DEPTH = 20

_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class AppDataError(Exception):
    """Base for every error this module raises."""


class InvalidThreadId(AppDataError, ValueError):
    """The thread id is not a uuid4 — refused before it can touch a path."""


class ThreadTooLarge(AppDataError):
    """A single thread payload exceeds ``MAX_THREAD_BYTES``."""


class TooManyThreads(AppDataError):
    """The store already holds ``MAX_THREADS`` thread files."""


class SettingsTooLarge(AppDataError):
    """The settings payload exceeds ``MAX_SETTINGS_BYTES``."""


def appdata_root(home: Path) -> Path:
    return Path(home) / "appdata"


def valid_thread_id(thread_id: str | None) -> bool:
    return bool(thread_id) and _ID.match(thread_id) is not None


def _ask_dir(root: Path) -> Path:
    return root / ASK_DIRNAME


def _thread_path(root: Path, thread_id: str) -> Path:
    """The thread's file. Rejects anything that is not a uuid4 — same
    rationale as ``staging.dir_for``: the id is a client-controlled path
    component and must never traverse."""
    if not valid_thread_id(thread_id):
        raise InvalidThreadId(f"invalid thread id {thread_id!r}")
    return _ask_dir(root) / f"{thread_id}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a same-directory tmp file + ``os.replace``
    (atomic on POSIX and Windows), 0600 — the same recipe :mod:`staging` uses
    for meta.json, applied here to every appdata write since these files ARE
    the durable record (not a cache the source can be re-derived from)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_threads(root: Path) -> list[dict[str, Any]]:
    """Every readable thread under ``ask/``. A single corrupted/unreadable file
    is skipped (logged, not raised) so it never takes the rest of the history
    down with it."""
    ask_dir = _ask_dir(root)
    if not ask_dir.is_dir():
        return []
    threads: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(ask_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            skipped += 1
            continue
        if isinstance(payload, dict):
            threads.append(payload)
        else:
            skipped += 1
    if skipped:
        logger.debug("read_threads: skipped %d unreadable/malformed file(s)", skipped)
    return threads


def write_thread(root: Path, thread_id: str, payload: dict[str, Any]) -> None:
    """Persist one thread, atomically. Raises :class:`InvalidThreadId`,
    :class:`ThreadTooLarge`, or :class:`TooManyThreads`."""
    path = _thread_path(root, thread_id)
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_THREAD_BYTES:
        raise ThreadTooLarge(
            f"thread payload is {len(data)} bytes, over the {MAX_THREAD_BYTES} limit"
        )
    if not path.is_file():
        ask_dir = _ask_dir(root)
        existing = (
            sum(1 for p in ask_dir.glob("*.json") if valid_thread_id(p.stem))
            if ask_dir.is_dir()
            else 0
        )
        if existing >= MAX_THREADS:
            raise TooManyThreads(f"already at the {MAX_THREADS}-thread limit")
    _atomic_write(path, data)


def delete_thread(root: Path, thread_id: str) -> bool:
    """Forget a thread. True when a file was actually removed; False when
    there was nothing there. Raises :class:`InvalidThreadId` for a malformed
    id (never silently a no-op — the caller should 400/404, not pretend)."""
    path = _thread_path(root, thread_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SECRET_KEY_EXACT:
        return True
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _strip_secrets_value(value: Any, depth: int) -> tuple[Any, int]:
    """Recurse into dicts/lists, dropping credential-looking dict keys at any
    depth. Returns ``(scrubbed_value, dropped_count)``. Stops descending past
    ``_MAX_STRIP_DEPTH``: past it a container is DROPPED rather than kept,
    because its keys were never inspected — keeping it would be a silent hole
    exactly where the scrubbing stopped. A scalar there is already vetted by
    the key its parent held it under, so it is kept."""
    if depth > _MAX_STRIP_DEPTH:
        if isinstance(value, (dict, list)):
            return None, 1
        return value, 0
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        dropped = 0
        for key, sub in value.items():
            if _is_secret_key(key):
                dropped += 1
                continue
            scrubbed, sub_dropped = _strip_secrets_value(sub, depth + 1)
            kept[key] = scrubbed
            dropped += sub_dropped
        return kept, dropped
    if isinstance(value, list):
        dropped = 0
        scrubbed_list = []
        for item in value:
            scrubbed_item, item_dropped = _strip_secrets_value(item, depth + 1)
            scrubbed_list.append(scrubbed_item)
            dropped += item_dropped
        return scrubbed_list, dropped
    return value, 0


def _strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop every dict item (at any nesting depth, inside dicts and lists)
    whose key name looks like a credential. API keys stay in the
    browser/env, never on disk — even in single-user mode."""
    scrubbed, dropped = _strip_secrets_value(payload, 0)
    if dropped:
        # Count only — never log the key names or values.
        logger.debug("write_settings: dropped %d credential-looking key(s)", dropped)
    return scrubbed


def read_settings(root: Path) -> dict[str, Any]:
    """The persisted settings, or ``{}`` when absent/corrupted (never raises —
    settings are best-effort UI state, not a record worth failing a page over)."""
    path = root / SETTINGS_FILENAME
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_settings(root: Path, payload: dict[str, Any]) -> None:
    """Persist settings, atomically, after stripping credential-looking keys.
    Raises :class:`SettingsTooLarge`."""
    scrubbed = _strip_secrets(payload)
    data = json.dumps(scrubbed, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_SETTINGS_BYTES:
        raise SettingsTooLarge(
            f"settings payload is {len(data)} bytes, over the {MAX_SETTINGS_BYTES} limit"
        )
    _atomic_write(root / SETTINGS_FILENAME, data)
