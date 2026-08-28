"""Snapshot exchange — dataset hand-off between Asterism instances.

ADR ``local-first-distribution.md`` §5 (第 3 の軸 "exchange"): the unit is a
dataset VERSION, exported as an immutable ``.tar.gz`` snapshot::

    manifest.json          format/id/name/origin iri_base/counts/hashes
    graphs/canonical.ttl   the citable live graph (Turtle dump)
    graphs/ontology.ttl    TBox projection (reference only — import re-projects)
    registry/**            the whole registry dataset dir (meta, artifacts,
                           accumulated source/, history/, query_tools.yaml)

Export requires the dataset to be PROMOTED — a snapshot carries only 確定版.

Import lands the dataset as *ingested (staged, unpublished)*: registry dir +
``canonical/{id}/v{seq}`` staged graph + ``mark_ingested``. Publication stays
behind the existing human promote gate (``POST /api/datasets/{id}/promote``),
which is what runs the alignment report, the ontology projection, crosswalk
rebuild and togomcp publication — import re-implements none of them.

IRI policy (ADR §4/§5): the manifest records the origin ``iri_base``. When it
equals the receiver's base, IRIs pass through. When the origin base is the
RFC 2606 ``https://asterism.invalid`` default (self-describingly unpublished),
every ``<origin>/datasets/`` prefix is deterministically rebased to the
receiver's base — the receiving instance becomes the issuer. A *real* foreign
base is never rewritten: those identifiers are owned elsewhere.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism import substrate
from asterism_step0.instance_iri import DEFAULT_IRI_BASE
from fastapi import HTTPException

from . import registry

SNAPSHOT_FORMAT = "asterism-snapshot"
SNAPSHOT_FORMAT_VERSION = 1

# Registry ids must satisfy both the registry accessor guard and the substrate
# graph-IRI mint guard.
_ID_RE = re.compile(r"[a-z0-9-]{1,128}")
_SUBSTRATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Text artifacts eligible for the deterministic prefix rebase. Binary source
# payloads (xlsx 等) are copied verbatim.
_TEXT_SUFFIXES = {".ttl", ".yaml", ".yml", ".md", ".json", ".py", ".csv", ".txt"}

# meta.json fields that survive an import verbatim. Lifecycle/state fields
# (ingested/promoted/graph pointers/versions/appends…) are instance-coupled
# and are re-derived by the import's own mark_ingested + the later promote.
_META_KEEP = {
    "id",
    "name",
    "created_at",
    "complete",
    "warnings",
    "exit_code",
    "traps",
    "classes",
    "class_count",
    "has_mie",
    "has_rml",
    "has_mapping_ir",
    "has_proposal",
    "advisories",
    "has_source",
    "source_files",
    "source_kind",
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


async def _dump_graph(client: Any, graph_iri: str) -> str:
    return await client.sparql_construct(
        f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}"
    )


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    tar.addfile(info, io.BytesIO(payload))


# ---------------------------------------------------------------------------
# export


async def build_snapshot(cfg: Any, client: Any, dataset_id: str) -> tuple[bytes, str]:
    """Assemble the ``.tar.gz`` snapshot for a PROMOTED dataset.

    Returns ``(payload, filename)``. The whole registry dataset directory is
    archived (the artifacts dict alone would miss meta/source/history/tools).
    """
    record = registry.load_dataset(cfg.registry_root, dataset_id)
    if record is None:
        raise HTTPException(404, f"unknown dataset: {dataset_id}")
    meta = record["meta"]
    if not meta.get("promoted"):
        raise HTTPException(
            409,
            "スナップショットは公開 (promote) 済みの確定版だけを持ち出せます。"
            "先にこのデータセットを公開してください。",
        )

    dataset_key = substrate.canonical_graph_iri(dataset_id)
    live = await substrate.live_graph_of(client, dataset_key) or dataset_key
    canonical_ttl = (await _dump_graph(client, live)).encode("utf-8")
    if not canonical_ttl.strip():
        raise HTTPException(409, f"live graph is empty: {live}")

    ontology_ttl: bytes | None = None
    ontology_iri = substrate.ontology_graph_iri(dataset_id)
    if await substrate.graph_has_triples(client, ontology_iri):
        ontology_ttl = (await _dump_graph(client, ontology_iri)).encode("utf-8")

    manifest = {
        "format": SNAPSHOT_FORMAT,
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "dataset_id": dataset_id,
        "name": meta.get("name") or dataset_id,
        "origin_iri_base": cfg.iri_base,
        "exported_at": _utcnow(),
        "live_graph": live,
        "canonical_triples": await client.graph_triple_count(live),
        "canonical_sha256": _sha256(canonical_ttl),
        "ontology_included": ontology_ttl is not None,
        # The importer re-projects the TBox from model.yaml at promote;
        # graphs/ontology.ttl is carried for third-party consumers only.
    }

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add_bytes(
            tar,
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        _add_bytes(tar, "graphs/canonical.ttl", canonical_ttl)
        if ontology_ttl is not None:
            _add_bytes(tar, "graphs/ontology.ttl", ontology_ttl)
        dataset_dir = cfg.registry_root / dataset_id
        for path in sorted(dataset_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(dataset_dir).as_posix()
            _add_bytes(tar, f"registry/{rel}", path.read_bytes())
    return buf.getvalue(), f"asterism-snapshot-{dataset_id}.tar.gz"


# ---------------------------------------------------------------------------
# import


def _safe_members(tar: tarfile.TarFile, max_total: int) -> dict[str, bytes]:
    """Extract all members into memory with traversal + size guards."""
    out: dict[str, bytes] = {}
    total = 0
    for member in tar.getmembers():
        name = member.name
        if name.startswith(("/", "\\")) or ".." in Path(name).parts:
            raise HTTPException(400, f"unsafe member path in snapshot: {name}")
        if not member.isfile():
            continue
        total += member.size
        if total > max_total:
            raise HTTPException(413, "snapshot uncompressed size exceeds the upload cap")
        extracted = tar.extractfile(member)
        if extracted is None:
            continue
        out[name] = extracted.read()
    return out


def _rebase(payload: bytes, origin_base: str, local_base: str) -> bytes:
    """Deterministic issuer swap of minted dataset namespaces.

    Mirrors K13's host-agnostic mint shape: only ``<base>/datasets/…`` moves;
    engine vocabulary and third-party IRIs are untouched by construction.
    """
    return payload.replace(
        f"{origin_base}/datasets/".encode(), f"{local_base}/datasets/".encode()
    )


def _sanitized_meta(raw: bytes, manifest: dict[str, Any], rebased: bool) -> dict[str, Any]:
    try:
        original = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"snapshot registry/meta.json is not valid JSON: {exc}") from exc
    meta = {key: value for key, value in original.items() if key in _META_KEEP}
    meta["id"] = manifest["dataset_id"]
    meta.setdefault("name", manifest["name"])
    meta.setdefault("created_at", manifest.get("exported_at") or _utcnow())
    meta["ingested"] = False
    meta["promoted"] = False
    meta["imported"] = {
        "origin_iri_base": manifest.get("origin_iri_base"),
        "exported_at": manifest.get("exported_at"),
        "imported_at": _utcnow(),
        "rebased": rebased,
        "canonical_sha256": manifest.get("canonical_sha256"),
    }
    return meta


async def import_snapshot(
    cfg: Any, client: Any, payload: bytes, *, max_extracted_bytes: int
) -> dict[str, Any]:
    """Land a snapshot as an *ingested, unpublished* dataset.

    Sequence (identical to a normal ingest): registry dir → reserve_data_seq →
    stream Turtle into ``canonical/{id}/v{seq}`` → ``set_staged_graph`` →
    ``mark_ingested``. Publication is the existing promote gate.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            members = _safe_members(tar, max_extracted_bytes)
    except tarfile.TarError as exc:
        raise HTTPException(400, f"not a snapshot archive: {exc}") from exc

    manifest_raw = members.get("manifest.json")
    if manifest_raw is None:
        raise HTTPException(400, "snapshot is missing manifest.json")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"manifest.json is not valid JSON: {exc}") from exc
    if manifest.get("format") != SNAPSHOT_FORMAT:
        raise HTTPException(400, "not an asterism snapshot (manifest.format mismatch)")
    if manifest.get("format_version") != SNAPSHOT_FORMAT_VERSION:
        raise HTTPException(
            400, f"unsupported snapshot format_version: {manifest.get('format_version')!r}"
        )

    dataset_id = str(manifest.get("dataset_id") or "")
    if not (_ID_RE.fullmatch(dataset_id) and _SUBSTRATE_ID_RE.fullmatch(dataset_id)):
        raise HTTPException(400, f"invalid dataset id in manifest: {dataset_id!r}")
    canonical_ttl = members.get("graphs/canonical.ttl")
    if not canonical_ttl:
        raise HTTPException(400, "snapshot is missing graphs/canonical.ttl")
    if _sha256(canonical_ttl) != manifest.get("canonical_sha256"):
        raise HTTPException(400, "graphs/canonical.ttl does not match manifest sha256")
    if "registry/meta.json" not in members:
        raise HTTPException(400, "snapshot is missing registry/meta.json")

    dataset_dir = cfg.registry_root / dataset_id
    if dataset_dir.exists():
        raise HTTPException(
            409,
            f"dataset already exists: {dataset_id} — "
            "既存データセットへの再取り込みは v1 では未対応です (先に削除してください)",
        )

    # --- IRI policy -------------------------------------------------------
    origin_base = str(manifest.get("origin_iri_base") or DEFAULT_IRI_BASE)
    rebase = origin_base != cfg.iri_base and origin_base == DEFAULT_IRI_BASE
    if rebase:
        canonical_ttl = _rebase(canonical_ttl, origin_base, cfg.iri_base)

    # --- registry dir -----------------------------------------------------
    dataset_dir.mkdir(parents=True, exist_ok=False)
    try:
        for name, blob in members.items():
            if not name.startswith("registry/"):
                continue
            rel = name[len("registry/") :]
            if rel == "meta.json":
                continue
            if rebase and Path(rel).suffix.lower() in _TEXT_SUFFIXES:
                blob = _rebase(blob, origin_base, cfg.iri_base)
            dest = dataset_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        meta = _sanitized_meta(members["registry/meta.json"], manifest, rebase)
        (dataset_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # --- store --------------------------------------------------------
        dataset_key = substrate.canonical_graph_iri(dataset_id)
        data_seq = registry.reserve_data_seq(cfg.registry_root, dataset_id)
        staged_iri = substrate.versioned_graph_iri(dataset_id, data_seq)
        await client.post_turtle_bytes(canonical_ttl, graph_iri=staged_iri)
        triple_count = await client.graph_triple_count(staged_iri)
        await substrate.set_staged_graph(client, dataset_key, staged_iri)
        registry.mark_ingested(
            cfg.registry_root,
            dataset_id,
            graph_iri=staged_iri,
            triple_count=triple_count,
            ingested_at=_utcnow(),
            data_seq=data_seq,
        )
    except HTTPException:
        registry.delete_dataset(cfg.registry_root, dataset_id)
        raise
    except Exception as exc:
        registry.delete_dataset(cfg.registry_root, dataset_id)
        raise HTTPException(500, f"snapshot import failed: {exc}") from exc

    return {
        "dataset_id": dataset_id,
        "name": meta.get("name"),
        "staged_graph": staged_iri,
        "triples": triple_count,
        "rebased": rebase,
        "origin_iri_base": origin_base,
        "status": "ingested",
        "next": f"POST /api/datasets/{dataset_id}/promote で公開 (引用可能化) します",
    }
