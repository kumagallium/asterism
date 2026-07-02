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

import yaml

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
# Design-time source files are persisted here so the dataset carries the exact
# data it was built from (reproducibility — the citable-facts product direction).
# This lets the catalog ingest a *design*-stage dataset with no re-attach.
_SOURCE_DIR = "source"
# Accepted source file extensions: CSV and JSON (#19) and XML/JATS + Word/PDF
# (document-ontology layer). Morph-KGC reads csv/json/xml via the RML's
# referenceFormulation (ql:CSV / ql:JSONPath / ql:XPath); a .pdf is a document source
# converted to JATS by the Docling sidecar at ingest (ADR pdf-docling-conversion.md).
_SOURCE_SUFFIXES = (".csv", ".json", ".geojson", ".xml", ".pdf")


def source_kind_of(filenames: list[str]) -> str:
    """Classify a dataset's source as ``"xml"`` / ``"json"`` / ``"csv"`` by extension.

    A dataset's source is a single kind in practice; a DOCUMENT source (JATS ``.xml`` or
    born-digital ``.pdf``) maps to ``"xml"`` (the deterministic structurer path) and wins,
    then JSON, else CSV (the default for an empty set). ``.pdf`` is converted to JATS at
    ingest, so it shares the document path under the ``"xml"`` kind.
    """
    if any(Path(n).suffix.lower() in (".xml", ".pdf") for n in filenames):
        return "xml"
    if any(Path(n).suffix.lower() in (".json", ".geojson") for n in filenames):
        return "json"
    return "csv"

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


# The propose/refine Markdown the bundle was materialized from. Persisted so a
# dataset's design can be RE-OPENED in the workbench (refine/edit → re-materialize)
# without losing the dataset — the "見直す" (redesign) flow. It is the source the 4
# artifacts are extracted from, so it round-trips a full re-design.
_PROPOSAL_FILE = "proposal.md"


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
    proposal_md: str = "",
) -> dict:
    """Persist a materialized bundle under ``root/<id>/``; return its meta dict.

    ``artifacts`` maps the 4 logical names (diagram.md / model.yaml / mie.yaml /
    ingester.py) to their text contents. A ``meta.json`` summary (name, time,
    validation outcome, extracted class list) is written alongside so the
    listing endpoint stays cheap (no re-parsing of artifacts). ``proposal_md``
    (the design source) is persisted so the dataset can later be re-opened in the
    workbench for a redesign (refine/edit → re-materialize in place).
    """
    dataset_id = f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
    dest = root / dataset_id
    dest.mkdir(parents=True, exist_ok=True)

    for key, filename in _ARTIFACT_FILES.items():
        (dest / filename).write_text(artifacts.get(key, "") or "", encoding="utf-8")
    (dest / _PROPOSAL_FILE).write_text(proposal_md or "", encoding="utf-8")

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
        # The design (propose/refine Markdown) is stored — so the catalog can offer
        # a "見直す" (redesign) action that reopens it in the workbench.
        "has_proposal": bool((proposal_md or "").strip()),
        "ingested": False,
    }
    (dest / _META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def load_proposal(root: Path, dataset_id: str) -> str | None:
    """Return the persisted design (propose/refine Markdown) for ``dataset_id``.

    None when the id is unsafe, the dataset is absent, or no proposal was stored
    (datasets materialized before proposal persistence have none). The empty
    string is a valid (but unusable) stored value and is returned as-is.
    """
    if not _ID_RE.fullmatch(dataset_id):
        return None
    dest = root / dataset_id
    if not (dest / _META_FILE).is_file():
        return None
    path = dest / _PROPOSAL_FILE
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def update_dataset_artifacts(
    root: Path,
    dataset_id: str,
    artifacts: dict[str, str],
    *,
    complete: bool,
    warnings: list[str],
    traps: list[dict],
    exit_code: int,
    proposal_md: str = "",
) -> dict | None:
    """Re-materialize a dataset IN PLACE — overwrite its artifacts + design, keep its id.

    The redesign counterpart of :func:`save_dataset`: the user reopened an existing
    dataset's design in the workbench, refined/edited it, and re-materialized. We must
    update the SAME registry record (so IRIs / graphs / lifecycle / source are
    preserved) rather than mint a duplicate. Overwrites the 4 artifact files + the
    stored ``proposal_md`` and refreshes the design-derived meta (classes, has_rml, …)
    while leaving identity + lifecycle/source fields (``id`` / ``promoted`` /
    ``ingested`` / ``has_source`` / ``version`` / …) untouched. Re-design changes the
    MAPPING only; the user re-applies data via the existing re-ingest controls.

    Returns the new meta, or ``None`` if the id is unsafe / absent.
    """
    if not _ID_RE.fullmatch(dataset_id):
        return None
    dest = root / dataset_id
    meta_path = dest / _META_FILE
    if not meta_path.is_file():
        return None

    for key, filename in _ARTIFACT_FILES.items():
        (dest / filename).write_text(artifacts.get(key, "") or "", encoding="utf-8")
    (dest / _PROPOSAL_FILE).write_text(proposal_md or "", encoding="utf-8")

    classes = extract_classes(mermaid_of(artifacts.get("diagram.md", "")))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "complete": complete,
            "warnings": warnings,
            "exit_code": exit_code,
            "traps": traps,
            "classes": classes,
            "class_count": len(classes),
            "has_ingester": bool((artifacts.get("ingester.py") or "").strip()),
            "has_mie": bool((artifacts.get("mie.yaml") or "").strip()),
            "has_rml": bool((artifacts.get("mapping.rml.ttl") or "").strip()),
            "has_proposal": bool((proposal_md or "").strip()),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
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
    """Persisted design-time source files for ``dataset_id`` (sorted; [] if none).

    Includes CSV and JSON sources (#19) — Morph-KGC reads either via the RML's
    referenceFormulation.
    """
    sdir = source_dir(root, dataset_id)
    if sdir is None or not sdir.is_dir():
        return []
    return sorted(
        p for p in sdir.iterdir() if p.is_file() and p.suffix.lower() in _SOURCE_SUFFIXES
    )


def mark_source_saved(
    root: Path, dataset_id: str, source_files: list[str], conversion: dict | None = None
) -> dict | None:
    """Record on the meta which design-time source files are now persisted.

    ``has_source`` lets the catalog offer a no-re-attach ingest; ``source_files``
    is the recorded filename list; ``source_kind`` (csv | json | xml) lets the UI
    label the source and pick the right picker. ``conversion`` (optional) records
    that the persisted source was produced from another format by an external
    converter (e.g. ``{"converter": "pandoc/3.1", "sourceFormat": "docx"}``), so the
    document ingest can disclose it as a ``lit:DocumentConversionActivity``. Returns
    the new meta, or None if id is unsafe / absent.
    """
    changes: dict = {
        "has_source": True,
        "source_files": sorted(source_files),
        "source_kind": source_kind_of(source_files),
    }
    changes["conversion"] = conversion  # None clears any prior conversion record
    return _update_meta(root, dataset_id, changes)


def next_data_seq(root: Path, dataset_id: str) -> int:
    """The next monotonic per-dataset ingest sequence (part5 version numbering).

    Monotonic (never reused even after old versions are dropped), so version graphs
    never collide. 1 for the first ingest. Returns 1 if the id is unsafe / absent
    (the caller validates separately).
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return 1
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return int(meta.get("data_seq", 0)) + 1


def mark_ingested(
    root: Path,
    dataset_id: str,
    *,
    graph_iri: str,
    triple_count: int,
    ingested_at: str,
    data_seq: int,
) -> dict | None:
    """Record that ``dataset_id`` was ingested into ``graph_iri`` (a *version* data
    graph, staged but not yet citable).

    part5: ingest streams into a fresh per-ingest version graph
    (``canonical/{id}/v{n}``) that stays out of the Ask scope until promote points
    the dataset's ``liveGraph`` at it — so a re-ingest does NOT touch the currently
    live graph (no DROP, no citability gap). A fresh ingest clears ``promoted`` (a
    re-ingest supersedes any prior promotion and needs a new human promote gate) and
    records ``data_seq`` (the monotonic version). Updates ``meta.json`` in place;
    returns the new meta, or ``None`` if the id is unsafe / absent.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["ingested"] = True
    meta["promoted"] = False  # staged in a version graph; awaits a promote gate
    meta["graph_iri"] = graph_iri  # the staged version graph
    meta["triple_count"] = triple_count
    meta["ingested_at"] = ingested_at
    meta["data_seq"] = int(data_seq)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def next_append_seq(root: Path, dataset_id: str) -> int:
    """The next monotonic per-dataset *append* sequence (incremental-ingest ADR).

    Distinct from :func:`next_data_seq` (version graphs): an append grows the live
    graph in place rather than minting a new version. 1 for the first append. Returns
    1 if the id is unsafe / absent (the caller validates separately).
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return 1
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return int(meta.get("append_seq", 0)) + 1


def mark_appended(
    root: Path,
    dataset_id: str,
    *,
    batch_files: list[str],
    source_files: list[str],
    triples_in_batch: int,
    appended_at: str,
    append_seq: int,
    batch_id: str,
) -> dict | None:
    """Record an incremental append batch on the dataset's meta (incremental-ingest ADR).

    Append grows the dataset's already-live canonical graph *in place* — it does NOT
    create a new version graph — so, unlike :func:`mark_ingested`, this leaves
    ``promoted`` / ``graph_iri`` / ``data_seq`` untouched. It marks the dataset a live
    ``feed``, appends this batch to an append-only ``appends`` log (mirroring
    ``versions``), refreshes the union ``source_files`` (+ ``source_kind``), and bumps
    the running ``triples_appended`` counter and ``append_seq``.

    ``batch_id`` is the batch's content fingerprint
    (:func:`asterism.substrate.batch_fingerprint`) recorded as the append's
    idempotency key: :func:`find_append_by_batch_id` lets the caller short-circuit a
    re-delivered batch (a retry after a client-side timeout that the server had
    already applied) so its rows are not double-accumulated and its counters not
    double-bumped. This write is the append's commit point — it runs last, after the
    POST-merge and the source accumulation, so a recorded ``batch_id`` means the whole
    append completed.

    ``triple_count`` is advanced by ``triples_in_batch`` as a best-effort UPPER bound:
    re-emitted rows dedupe in the store, so the true count may be lower. The
    authoritative count is a SPARQL COUNT when the catalog needs it — kept off the
    per-append path so append stays O(new). Returns the new meta, or ``None`` if the id
    is unsafe / absent.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["feed"] = True
    meta["append_seq"] = int(append_seq)
    meta["triples_appended"] = int(meta.get("triples_appended", 0) or 0) + int(
        triples_in_batch
    )
    meta["triple_count"] = int(meta.get("triple_count", 0) or 0) + int(triples_in_batch)
    meta["source_files"] = sorted(source_files)
    meta["source_kind"] = source_kind_of(source_files)
    meta["last_appended_at"] = appended_at
    meta.setdefault("appends", []).append(
        {
            "seq": int(append_seq),
            "batch_files": sorted(batch_files),
            "triples_in_batch": int(triples_in_batch),
            "appended_at": appended_at,
            "batch_id": batch_id,
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def find_append_by_batch_id(root: Path, dataset_id: str, batch_id: str) -> dict | None:
    """The recorded ``appends`` log entry for ``batch_id``, or ``None`` if not applied.

    The append idempotency lookup (incremental-ingest ADR, A3): a batch is identified
    by its content fingerprint (:func:`asterism.substrate.batch_fingerprint`), so a
    re-delivered batch — e.g. a retry after the server applied it but the client timed
    out reading the response — is recognised here and short-circuited by the caller
    (no double source accumulation, no double counter bump, no new ``append_seq``)
    while still returning the original outcome. ``None`` for an unsafe / absent id, an
    un-appended dataset, or a genuinely new batch.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for entry in meta.get("appends", []):
        if isinstance(entry, dict) and entry.get("batch_id") == batch_id:
            return entry
    return None


def mark_promoted(
    root: Path,
    dataset_id: str,
    *,
    triples_promoted: int,
    alignment: dict,
    promoted_at: str,
    canonical_graph: str | None = None,
    live_graph: str | None = None,
) -> dict | None:
    """Record that ``dataset_id``'s staged canonical graph was promoted (made citable).

    Memory-bounded promote: the triples were already streamed into the canonical
    graph at ingest, and promote just flipped a control-graph flag — nothing moved.
    There is no longer a pending draft, so we clear ``ingested``/``graph_iri`` and
    set ``promoted``. ``canonical_graph`` (the per-dataset canonical named graph
    IRI, #20 P3) is recorded so later retract / delete can target it. Returns the
    new meta, or ``None`` if id is unsafe / absent.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    meta_path = root / dataset_id / _META_FILE
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["promoted"] = True
    meta["ingested"] = False  # no pending staged graph; the version graph is now live
    meta["status"] = "active"  # a (re-)promote makes it citable again (clears retracted)
    meta["graph_iri"] = None
    meta["canonical_graph"] = canonical_graph  # #20 P3: per-dataset canonical key graph
    # part5: the version graph now holding the citable data (the live pointer). The
    # startup backfill restores the control-graph liveGraph from this after upgrade.
    meta["live_graph"] = live_graph
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


def rename_dataset(root: Path, dataset_id: str, name: str) -> dict | None:
    """Change a dataset's DISPLAY name only — the ``id`` (the IRI seed) never changes.

    Names are a human label; identity is the id and the graph IRIs derived from it, so
    a rename touches only ``meta.name`` and leaves all data / IRIs untouched. Returns
    the new meta, or None if the id is unsafe / absent.
    """
    return _update_meta(root, dataset_id, {"name": name})


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


# ---------------------------------------------------------------------------
# Per-dataset query tools (the "grow verified tools" store, P1).
# ---------------------------------------------------------------------------
# A dataset's typed Ask tools live at ``registry/<id>/query_tools.yaml`` — the
# SAME shape as the repo's ``datasets/<name>/query_tools.yaml`` content, so the
# engine (asterism.query_tools) and the Ask layer load registry tools with the
# exact same loader. This is what lets a workbench-onboarded dataset (not just a
# repo example) carry human-vetted, deterministic, citable tools. Tools are
# persisted content; nothing is generated at runtime. The CALLER validates a tool
# with asterism.query_tools.parse_query_tools before saving (the human-vet gate).

_QUERY_TOOLS_FILE = "query_tools.yaml"


def query_tools_path(root: Path, dataset_id: str) -> Path | None:
    """``registry/<id>/query_tools.yaml`` for a valid id, else None (id is a bare
    slug so it cannot escape ``root``)."""
    if not re.fullmatch(r"[a-z0-9-]{1,128}", dataset_id):
        return None
    return root / dataset_id / _QUERY_TOOLS_FILE


def list_query_tools(root: Path, dataset_id: str) -> list[dict]:
    """The dataset's declared query tools (raw dicts), or ``[]`` if none/invalid."""
    path = query_tools_path(root, dataset_id)
    if path is None or not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tools = data.get("tools") if isinstance(data, dict) else None
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


def _write_tools(path: Path, tools: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"tools": tools}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def save_query_tool(root: Path, dataset_id: str, tool: dict) -> list[dict]:
    """Upsert ``tool`` (by ``name``) into the dataset's query_tools.yaml; return
    the full tool list. Raises ``FileNotFoundError`` if the dataset dir is absent.

    The caller MUST have validated ``tool`` via
    ``asterism.query_tools.parse_query_tools`` first (read-only + safe binding) —
    that validation IS the human-vet gate; this just persists vetted content.
    """
    path = query_tools_path(root, dataset_id)
    if path is None or not path.parent.is_dir():
        raise FileNotFoundError(dataset_id)
    name = str(tool.get("name", ""))
    tools = [t for t in list_query_tools(root, dataset_id) if str(t.get("name")) != name]
    tools.append(tool)
    _write_tools(path, tools)
    return tools


def delete_query_tool(root: Path, dataset_id: str, name: str) -> bool:
    """Remove the named tool; return True if one was removed."""
    path = query_tools_path(root, dataset_id)
    if path is None or not path.is_file():
        return False
    tools = list_query_tools(root, dataset_id)
    remaining = [t for t in tools if str(t.get("name")) != name]
    if len(remaining) == len(tools):
        return False
    _write_tools(path, remaining)
    return True
