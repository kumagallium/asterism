"""``registry._project_description`` — the design-save-time writer (ADR
``dataset-description-in-the-store.md`` §4).

``save_dataset`` / ``update_dataset_artifacts`` compile a dataset's ``mie.yaml``
into ``metadata.ttl`` and re-project ``mie.yaml`` FROM that graph, all before any
byte reaches disk. These tests exercise that at the registry-function level (no
FastAPI app, no store) — the store-level writers (ingest/promote/delete) are
covered in ``test_ingest.py``.
"""

from __future__ import annotations

from pathlib import Path

import rdflib
from asterism.metadata import normalize_document, parse_mie_yaml
from rdflib.namespace import DCTERMS

from asterism_api import registry

_MIE = (
    "schema_info:\n"
    "  title: ZEM 熱電測定\n"
    "  description: a demo dataset\n"
    "  keywords:\n"
    "    - thermoelectric\n"
    "    - measurement\n"
)


def _save(tmp: Path, mie: str = _MIE, **extra: str) -> dict:
    artifacts = {
        "diagram.md": "classDiagram\n  class Sample",
        "model.yaml": "- Sample:",
        "mie.yaml": mie,
        **extra,
    }
    return registry.save_dataset(
        tmp / "registry",
        "demo",
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-07T00:00:00+00:00",
    )


def test_save_projects_mie_yaml_and_metadata_ttl(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    dataset_id = meta["id"]
    root = tmp_path / "registry"

    # The mie.yaml written to disk round-trips through build -> project
    # semantically equal to what was handed to save_dataset.
    on_disk_mie = (root / dataset_id / "mie.yaml").read_text(encoding="utf-8")
    assert normalize_document(parse_mie_yaml(on_disk_mie)) == normalize_document(
        parse_mie_yaml(_MIE)
    )

    # metadata.ttl parses as Turtle and carries dcterms:title.
    ttl_path = root / dataset_id / "metadata.ttl"
    graph = rdflib.Graph()
    graph.parse(data=ttl_path.read_text(encoding="utf-8"), format="turtle")
    titles = list(graph.objects(None, DCTERMS.title))
    assert [str(t) for t in titles] == ["ZEM 熱電測定"]

    # load_dataset returns metadata.ttl as one of the artifacts.
    loaded = registry.load_dataset(root, dataset_id)
    assert loaded is not None
    assert "dcterms:title" in loaded["artifacts"]["metadata.ttl"] or (
        "title" in loaded["artifacts"]["metadata.ttl"]
    )
    assert loaded["artifacts"]["metadata.ttl"].strip() != ""


def test_empty_mie_yaml_projects_empty_metadata_and_clears_has_mie(tmp_path: Path) -> None:
    meta = _save(tmp_path, mie="")
    dataset_id = meta["id"]
    root = tmp_path / "registry"

    assert meta["has_mie"] is False
    assert (root / dataset_id / "metadata.ttl").read_text(encoding="utf-8") == ""
    assert (root / dataset_id / "mie.yaml").read_text(encoding="utf-8") == ""


def test_broken_mie_yaml_is_kept_verbatim_with_empty_metadata(tmp_path: Path) -> None:
    broken = "schema_info: [this is not a mapping\n"
    meta = _save(tmp_path, mie=broken)
    dataset_id = meta["id"]
    root = tmp_path / "registry"

    # The original (unparseable) text is preserved byte-for-byte — never
    # silently discarded (spec §A.2).
    assert (root / dataset_id / "mie.yaml").read_text(encoding="utf-8") == broken
    assert (root / dataset_id / "metadata.ttl").read_text(encoding="utf-8") == ""
    # A YAML scalar (not a mapping) also counts as "did not parse" here.
    not_a_mapping = "just a string\n"
    meta2 = _save(tmp_path, mie=not_a_mapping)
    dataset_id2 = meta2["id"]
    assert (
        root / dataset_id2 / "mie.yaml"
    ).read_text(encoding="utf-8") == not_a_mapping
    assert (root / dataset_id2 / "metadata.ttl").read_text(encoding="utf-8") == ""


def test_mie_yaml_with_non_string_key_does_not_crash_save(tmp_path: Path) -> None:
    """A ``mie.yaml`` that IS valid YAML and parses to a top-level mapping (so
    ``parse_mie_yaml`` succeeds) can still carry a shape ``build_metadata_graph``
    does not expect — e.g. a bare/unquoted top-level key like ``1:``, which
    ``yaml.safe_load`` turns into an int key, not a str. That must degrade the
    same best-effort way a parse failure does (ADR §4: "書くのは best-effort"),
    not raise out of ``save_dataset`` and leave a half-created dataset dir.
    """
    non_string_key = "1: value\nschema_info:\n  title: t\n"
    meta = _save(tmp_path, mie=non_string_key)
    dataset_id = meta["id"]
    root = tmp_path / "registry"

    # The original text is preserved verbatim (the compile failed, so there is
    # nothing to project it FROM) and metadata.ttl is left empty — never a
    # crash, never a silently-invented description.
    assert (root / dataset_id / "mie.yaml").read_text(encoding="utf-8") == non_string_key
    assert (root / dataset_id / "metadata.ttl").read_text(encoding="utf-8") == ""
    # And the dataset dir is a normal, fully-written dataset (no orphaned
    # half-created dir from a crash mid-save_dataset).
    assert (root / dataset_id / "meta.json").exists()
    assert registry.load_dataset(root, dataset_id) is not None


def test_update_projection_is_idempotent_history_grows_once(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    dataset_id = meta["id"]
    root = tmp_path / "registry"

    new_mie = (
        "schema_info:\n"
        "  title: 改訂版\n"
        "  description: revised\n"
    )
    common = dict(
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
    )
    artifacts = {
        "diagram.md": "classDiagram\n  class Sample",
        "model.yaml": "- Sample:",
        "mie.yaml": new_mie,
    }

    registry.update_dataset_artifacts(root, dataset_id, dict(artifacts), **common)
    history_after_first = registry.list_dataset_history(root, dataset_id)
    assert len(history_after_first) == 1

    # Same INPUT artifacts a second time: the projection is deterministic, so
    # the projected artifacts are identical to what is already on disk —
    # _snapshot_before_overwrite must see "unchanged" and not add an entry.
    registry.update_dataset_artifacts(root, dataset_id, dict(artifacts), **common)
    history_after_second = registry.list_dataset_history(root, dataset_id)
    assert len(history_after_second) == 1

    # And the projected mie.yaml/metadata.ttl reflect the new content.
    on_disk_mie = (root / dataset_id / "mie.yaml").read_text(encoding="utf-8")
    assert normalize_document(parse_mie_yaml(on_disk_mie)) == normalize_document(
        parse_mie_yaml(new_mie)
    )
