"""Operator-configured (server-side) LLM keys — the opt-in "configure once" path.

Two ways to set them, both server-side (the key value is never sent back to the
browser):

- **UI**: ``POST /api/llm/server-keys`` (write-gated) persists them to a file in
  the api's registry volume via this module — the friendly path, no shell access.
- **env**: ``ASTERISM_LLM_KEY_<PROVIDER>`` (ops / bootstrap). The UI/file value wins.

By default NONE are set, so a browser-brought key stays required (D7). When set,
the api / demo-agent use it as a fallback only when a request carries no key of
its own (a browser key still wins). For an ``openai-compatible`` provider the UI
stores the key together with a PINNED ``api_base`` and the server sends the shared
key only to that endpoint — never to a per-request (user-controlled) base URL,
which would let any logged-in user exfiltrate the shared key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# The providers the settings UI can register (mirrors ui store PROVIDERS).
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "openai-compatible")


def _env_name(provider: str) -> str:
    """``openai-compatible`` -> ``ASTERISM_LLM_KEY_OPENAI_COMPATIBLE``."""
    slug = (provider or "anthropic").strip().lower().replace("-", "_")
    return f"ASTERISM_LLM_KEY_{slug.upper()}"


def _store_path(registry_root: Path | str) -> Path:
    return Path(registry_root) / "_llm" / "server_keys.json"


def _load_store(registry_root: Path | str | None) -> dict[str, dict]:
    """Read the persisted shared-key store, or ``{}`` if absent/unreadable."""
    if not registry_root:
        return {}
    try:
        raw = _store_path(registry_root).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve(
    provider: str | None, registry_root: Path | str | None = None
) -> tuple[str | None, str | None]:
    """Effective ``(api_key, api_base)`` for a provider: UI/file store first, then env.

    ``api_base`` is the pinned endpoint for a UI-stored openai-compatible key;
    ``None`` for anthropic/openai and for env-provided keys."""
    p = (provider or "anthropic").strip().lower() or "anthropic"
    entry = _load_store(registry_root).get(p)
    if isinstance(entry, dict) and str(entry.get("api_key") or "").strip():
        base = str(entry.get("api_base") or "").strip() or None
        return entry["api_key"].strip(), base
    env = os.environ.get(_env_name(p), "").strip()
    return (env or None), None


def server_key_for(
    provider: str | None, registry_root: Path | str | None = None
) -> str | None:
    """Just the effective key (see :func:`resolve`)."""
    return resolve(provider, registry_root)[0]


def configured_providers(registry_root: Path | str | None = None) -> dict[str, bool]:
    """Which providers have a key set — booleans only, never the key.

    Powers ``GET /api/llm/server-keys`` so the UI can let a user proceed without
    typing a key when the server already has one for the active model's provider."""
    return {p: resolve(p, registry_root)[0] is not None for p in PROVIDERS}


def set_server_key(
    registry_root: Path | str,
    provider: str,
    api_key: str | None,
    api_base: str | None = None,
) -> None:
    """Persist (or, with a blank key, remove) the shared key for a provider.

    Written to ``<registry_root>/_llm/server_keys.json`` with 0600 perms. For
    ``openai-compatible`` the ``api_base`` is stored alongside so the server pins
    the endpoint the shared key is sent to."""
    p = (provider or "").strip().lower()
    if p not in PROVIDERS:
        raise ValueError(f"unknown LLM provider: {provider!r}")
    store = _load_store(registry_root)
    key = (api_key or "").strip()
    if not key:
        store.pop(p, None)
    else:
        entry: dict[str, str] = {"api_key": key}
        base = (api_base or "").strip()
        if p == "openai-compatible" and base:
            entry["api_base"] = base
        store[p] = entry
    path = _store_path(registry_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)  # the file holds secrets
