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
import shutil
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
# Design-time source CSVs are persisted here so the dataset carries the exact
# data it was built from (reproducibility — the citable-facts product direction).
# This lets the catalog ingest a *design*-stage dataset with no CSV re-attach.
_SOURCE_DIR = "source"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"[a-z0-9-]{1,128}")
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


def source_dir(root: Path, dataset_id: str) -> Path | None:
    """Return ``root/<id>/source`` (where design-time CSVs live), or None.

    None when the id is unsafe or the dataset does not exist. The directory may
    not exist yet (no source attached); callers that write create it.
    """
    if not _ID_RE.fullmatch(dataset_id):
        return None
    if not (root / dataset_id / _META_FILE).is_file():
        return None
    return root / dataset_id / _SOURCE_DIR


def list_source_files(root: Path, dataset_id: str) -> list[Path]:
    """Persisted design-time source CSVs for ``dataset_id`` (sorted; [] if none)."""
    sdir = source_dir(root, dataset_id)
    if sdir is None or not sdir.is_dir():
        return []
    return sorted(p for p in sdir.iterdir() if p.is_file() and p.suffix == ".csv")


def mark_source_saved(root: Path, dataset_id: str, source_files: list[str]) -> dict | None:
    """Record on the meta which design-time source CSVs are now persisted.

    ``has_source`` lets the catalog offer a no-re-attach ingest; ``source_files``
    is the recorded filename list. Returns the new meta, or None if id is unsafe
    / absent.
    """
    return _update_meta(
        root, dataset_id, {"has_source": True, "source_files": sorted(source_files)}
    )


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


def mark_promoted(
    root: Path,
    dataset_id: str,
    *,
    triples_promoted: int,
    alignment: dict,
    promoted_at: str,
    canonical_graph: str | None = None,
) -> dict | None:
    """Record that ``dataset_id``'s draft graph was promoted into the canonical graph.

    The draft named graph no longer exists after promotion (its triples moved to
    the canonical graph), so we clear ``ingested``/``graph_iri`` and set
    ``promoted``. ``canonical_graph`` (the per-dataset canonical named graph IRI,
    #20 P3) is recorded so later retract / delete can target it. Returns the new
    meta, or ``None`` if id is unsafe / absent.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["promoted"] = True
    meta["ingested"] = False  # draft graph consumed by the MOVE
    meta["graph_iri"] = None
    meta["canonical_graph"] = canonical_graph  # #20 P3: per-dataset canonical graph
    meta["triples_promoted"] = triples_promoted
    meta["alignment"] = alignment
    meta["promoted_at"] = promoted_at
    # #20 P3: dataset versioning. IRIs stay immutable (ADR §3 確定②); each
    # (re-)promotion bumps a monotonic version and appends to an append-only log
    # so the catalog can show promotion history and a re-promote is traceable.
    # Point-in-time triple snapshots are deliberately NOT kept (要決定② = no):
    # the log + the reproducible registry bundle are the version record.
    meta["version"] = int(meta.get("version", 0)) + 1
    meta.setdefault("versions", []).append(
        {
            "version": meta["version"],
            "promoted_at": promoted_at,
            "triples_promoted": triples_promoted,
            "alignment": alignment,
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _update_meta(root: Path, dataset_id: str, changes: dict) -> dict | None:
    """Load a dataset's meta, apply ``changes``, persist, and return it (or None)."""
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(changes)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def mark_retracted(root: Path, dataset_id: str, *, retracted_at: str) -> dict | None:
    """Record that ``dataset_id``'s canonical graph was retracted (#20 P3 step3).

    Tombstone semantics: the data stays (IRIs keep resolving) but it leaves the
    citable corpus until reinstated. Returns the new meta, or None if absent.
    """
    return _update_meta(
        root, dataset_id, {"status": "retracted", "retracted_at": retracted_at}
    )


def mark_reinstated(root: Path, dataset_id: str, *, reinstated_at: str) -> dict | None:
    """Clear a retract tombstone: the dataset is canonical (active) again."""
    return _update_meta(
        root, dataset_id, {"status": "active", "reinstated_at": reinstated_at}
    )


def delete_dataset(root: Path, dataset_id: str) -> bool:
    """Remove a dataset's registry directory entirely (#20 P3 step4).

    Returns True if it existed and was removed, False if the id is unsafe or
    absent. The caller is responsible for dropping the dataset's graphs first.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return False
    dest = root / dataset_id
    if not (dest / _META_FILE).is_file():
        return False
    shutil.rmtree(dest)
    return True


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
