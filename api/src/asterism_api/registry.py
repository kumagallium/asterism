"""Persistence for materialized schema bundles — the "datasets" registry.

Today the workbench (inspect→propose→refine→materialize) produces artifacts and
throws them away (they are only returned for client download). That makes the
Gallery a static fixture and breaks the authoring→catalog→ask lifecycle the
product wants. This module closes the *authoring→catalog* half cheaply: each
materialized bundle is saved under ``registry_root/<dataset_id>/`` so the
Gallery can list what has actually been built.

A registered dataset is a *design* (TBox + mapping artifacts), not yet ingested
data. Loading triples is the separate, human-gated step (Phase 5 #15): rather
than run the generated *ingester.py* (executing AI-authored code = RCE risk), the
safe path runs the persisted declarative *mapping.rml.ttl* through the Morph-KGC
substrate into an isolated draft graph (see
``docs/architecture/phase5-workbench-materialize-gate.md``). ``mark_ingested``
records that outcome on the dataset's meta.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

# Files written per dataset (artifact key -> filename on disk).
_ARTIFACT_FILES = {
    "diagram.md": "diagram.md",
    "model.yaml": "model.yaml",
    "mie.yaml": "mie.yaml",
    "ingester.py": "ingester.py",
    # The declarative RML mapping (Phase 5). Persisted so the human-gated
    # substrate ingest (POST /api/datasets/{id}/ingest) can run it later.
    "mapping.rml.ttl": "mapping.rml.ttl",
}
_META_FILE = "meta.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CLASS_RE = re.compile(r"^\s*class\s+(\w+)", re.MULTILINE)
_MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


def _slug(name: str) -> str:
    s = _SLUG_RE.sub("-", name.lower()).strip("-")
    return s or "dataset"


def extract_classes(mermaid: str) -> list[str]:
    """Pull class names out of a Mermaid classDiagram (cheap, regex-only)."""
    return _CLASS_RE.findall(mermaid or "")


def mermaid_of(diagram_md: str) -> str:
    """Return the ```mermaid fenced block (or the whole text if unfenced)."""
    m = _MERMAID_BLOCK_RE.search(diagram_md or "")
    return (m.group(1) if m else (diagram_md or "")).strip()


def save_dataset(
    root: Path,
    name: str,
    artifacts: dict[str, str],
    *,
    complete: bool,
    warnings: list[str],
    traps: list[dict],
    exit_code: int,
    created_at: str,
) -> dict:
    """Persist a materialized bundle under ``root/<id>/``; return its meta dict.

    ``artifacts`` maps the 4 logical names (diagram.md / model.yaml / mie.yaml /
    ingester.py) to their text contents. A ``meta.json`` summary (name, time,
    validation outcome, extracted class list) is written alongside so the
    listing endpoint stays cheap (no re-parsing of artifacts).
    """
    dataset_id = f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
    dest = root / dataset_id
    dest.mkdir(parents=True, exist_ok=True)

    for key, filename in _ARTIFACT_FILES.items():
        (dest / filename).write_text(artifacts.get(key, "") or "", encoding="utf-8")

    classes = extract_classes(mermaid_of(artifacts.get("diagram.md", "")))
    meta = {
        "id": dataset_id,
        "name": name,
        "created_at": created_at,
        "complete": complete,
        "warnings": warnings,
        "exit_code": exit_code,
        "traps": traps,
        "classes": classes,
        "class_count": len(classes),
        "has_ingester": bool((artifacts.get("ingester.py") or "").strip()),
        "has_mie": bool((artifacts.get("mie.yaml") or "").strip()),
        # Phase 5: whether a declarative RML mapping is present (ingestable), and
        # whether it has been ingested into a draft graph yet.
        "has_rml": bool((artifacts.get("mapping.rml.ttl") or "").strip()),
        "ingested": False,
    }
    (dest / _META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def mark_ingested(
    root: Path,
    dataset_id: str,
    *,
    graph_iri: str,
    triple_count: int,
    ingested_at: str,
) -> dict | None:
    """Record that ``dataset_id`` was ingested into ``graph_iri`` (a draft graph).

    Updates the dataset's ``meta.json`` in place and returns the new meta, or
    ``None`` if the id is unsafe / the dataset does not exist.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["ingested"] = True
    meta["graph_iri"] = graph_iri
    meta["triple_count"] = triple_count
    meta["ingested_at"] = ingested_at
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def list_datasets(root: Path) -> list[dict]:
    """Return every dataset's meta, newest first. Missing root -> empty list."""
    if not root.is_dir():
        return []
    metas: list[dict] = []
    for child in root.iterdir():
        meta_path = child / _META_FILE
        if not meta_path.is_file():
            continue
        try:
            metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    metas.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    return metas


def load_dataset(root: Path, dataset_id: str) -> dict | None:
    """Return one dataset's meta + artifact contents, or None if absent.

    ``dataset_id`` is validated as a bare slug-id (no path separators) so it
    cannot escape ``root``.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    dest = root / dataset_id
    meta_path = dest / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    artifacts = {
        key: (dest / filename).read_text(encoding="utf-8")
        for key, filename in _ARTIFACT_FILES.items()
        if (dest / filename).is_file()
    }
    return {"meta": meta, "artifacts": artifacts}
