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


_SOURCE = {
    "dataset_id": "paper-22222222",
    "table_id": "tbl2",
    "label": "Table 2",
    "caption": "c",
    "name": "論文X",
}


def _paper_dataset(root: Path) -> str:
    """論文のデータセット（registry にある側）。"""
    meta = registry.save_dataset(
        root,
        "paper",
        {"diagram.md": "classDiagram\n  class P", "model.yaml": "- P:", "mie.yaml": _MIE},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-07T00:00:00+00:00",
    )
    return meta["id"]


def test_load_dataset_does_not_overlay_source_paper(tmp_path: Path) -> None:
    # 13e: 出所を重ねるのはストアへ投影するところだけ。load_dataset の artifacts は
    # ディスクの metadata.ttl とバイトで一致する（履歴の差分に偽の差分を出さない）。
    root = tmp_path / "registry"
    meta = _save(tmp_path)
    registry.update_meta_atomic(root, meta["id"], {"source_paper": _SOURCE})
    data = registry.load_dataset(root, meta["id"])
    assert data is not None
    on_disk = (root / meta["id"] / "metadata.ttl").read_text(encoding="utf-8")
    assert data["artifacts"]["metadata.ttl"] == on_disk
    assert "wasDerivedFrom" not in on_disk and "source" not in on_disk.replace("dcterms:", "")


def test_description_with_source_paper_links_only_a_living_paper(tmp_path: Path) -> None:
    from asterism.metadata import PROV, graph_from_turtle
    from asterism.substrate import dataset_iri

    root = tmp_path / "registry"
    paper_id = _paper_dataset(root)
    meta = _save(tmp_path)
    source = {**_SOURCE, "dataset_id": paper_id}
    registry.update_meta_atomic(root, meta["id"], {"source_paper": source})
    d = rdflib.URIRef(dataset_iri(meta["id"]))
    artifacts = registry.load_dataset(root, meta["id"])["artifacts"]

    def graph_of(a: dict) -> rdflib.Graph:
        return graph_from_turtle(a["metadata.ttl"])

    g = graph_of(registry.description_with_source_paper(root, meta["id"], artifacts))
    assert (d, PROV.wasDerivedFrom, rdflib.URIRef(dataset_iri(paper_id))) in g
    assert any("論文「論文X」" in str(o) for o in g.objects(d, DCTERMS.source))
    assert (d, DCTERMS.description, None) in g  # 元の説明は残る
    # 入力は書き換えない
    assert "wasDerivedFrom" not in artifacts["metadata.ttl"]

    # 論文が撤回されている／消えている: 文字列は残り、リンクは付かない
    registry.mark_retracted(root, paper_id, retracted_at="2026-09-08T00:00:00+00:00")
    retracted = registry.description_with_source_paper(root, meta["id"], artifacts)
    registry.delete_dataset(root, paper_id)
    deleted = registry.description_with_source_paper(root, meta["id"], artifacts)
    for a in (retracted, deleted):
        g = graph_of(a)
        assert (d, PROV.wasDerivedFrom, None) not in g
        assert len(list(g.objects(d, DCTERMS.source))) == 2


def test_description_without_source_paper_is_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    meta = _save(tmp_path)
    artifacts = registry.load_dataset(root, meta["id"])["artifacts"]
    assert registry.description_with_source_paper(root, meta["id"], artifacts) == artifacts
