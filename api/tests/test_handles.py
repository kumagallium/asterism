"""Tests for ``asterism_api.handles`` と ``register_handles``（契約メモ
contract_pr_f15.md §1.1/§1.2。担当 api-handles）。

架空の一般題材（図書館の貸出と書評）でテストする — 分野固有の語彙は書かない。

``register_handles`` は ``test_appdata_cards.py`` と同じ形: ``build_app`` の
上に、統合されていないこの並行段のモジュール自身の ``register_handles(app,
settings)`` を追加で呼ぶ（main.py の 1 行配線はここでは検証しない — それは
api-autolink の担当）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.handles import handle_slots, load_handles
from asterism_api.handles_routes import register_handles
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://example.org/library#"
  exr: "https://example.org/library/resource/"
maps:
  - name: book
    source: books.csv
    subject:
      template: "exr:book/{book_id}"
      classes: [ex:Book]
    properties:
      - predicate: ex:hasIdentifier
        column: book_id
      - predicate: ex:hasTitle
        column: title
  - name: loan
    source: loans.csv
    subject:
      template: "exr:loan/{loan_id}"
      classes: [ex:Loan]
    properties:
      - predicate: ex:forBook
        column: book_id
      - predicate: ex:onDate
        column: date
"""


# ---------------------------------------------------------------------------
# load_handles
# ---------------------------------------------------------------------------


def test_load_handles_absent_is_empty() -> None:
    assert load_handles({}) == []


def test_load_handles_broken_json_is_empty() -> None:
    assert load_handles({"handles.json": "{not json"}) == []


def test_load_handles_wrong_shape_is_empty() -> None:
    assert load_handles({"handles.json": '{"handles": "not-a-list"}'}) == []
    assert load_handles({"handles.json": '{"handles": [1, 2]}'}) == []


def test_load_handles_round_trip() -> None:
    text = '{"version": 1, "handles": [{"source": "books.csv", "column": "book_id"}]}'
    assert load_handles({"handles.json": text}) == [{"source": "books.csv", "column": "book_id"}]


# ---------------------------------------------------------------------------
# handle_slots
# ---------------------------------------------------------------------------


def test_handle_slots_finds_predicates_across_maps() -> None:
    handles = [
        {"source": "books.csv", "column": "book_id"},
        {"source": "loans.csv", "column": "book_id"},
    ]
    slots = handle_slots(_MAPPING_YAML, handles)
    assert slots == [
        {
            "class_iri": "https://example.org/library#Book",
            "predicate": "https://example.org/library#hasIdentifier",
            "source": "books.csv",
            "column": "book_id",
        },
        {
            "class_iri": "https://example.org/library#Loan",
            "predicate": "https://example.org/library#forBook",
            "source": "loans.csv",
            "column": "book_id",
        },
    ]


def test_handle_slots_drops_handle_with_no_predicate() -> None:
    # ``review_id`` はどのマップの述語にも現れない列 — subject template にも
    # properties にも無い、つまり IR がその後変わった/列名が違うケース。
    handles = [{"source": "books.csv", "column": "review_id"}]
    assert handle_slots(_MAPPING_YAML, handles) == []


def test_handle_slots_subject_template_only_is_not_enough() -> None:
    # book_id は loan マップの subject template にも入っているが、そこ自体は
    # 述語ではない ── properties 側で使っている述語だけを数える（forBook は
    # 既に拾われているので、それ以外に増えないことを確認）。
    handles = [{"source": "loans.csv", "column": "loan_id"}]
    assert handle_slots(_MAPPING_YAML, handles) == []


def test_handle_slots_broken_ir_is_empty() -> None:
    assert handle_slots("not: [valid, yaml", [{"source": "a", "column": "b"}]) == []
    assert handle_slots("- 1\n- 2\n", [{"source": "a", "column": "b"}]) == []


def test_handle_slots_no_handles_is_empty() -> None:
    assert handle_slots(_MAPPING_YAML, []) == []


# ---------------------------------------------------------------------------
# update_dataset_artifacts must not wipe handles.json when a caller's
# artifacts dict simply omits the key (display-meta edit / column-decisions /
# column-meanings / attach_source stale-include removal all rebuild only the
# design-document-derived artifacts and never mention handles.json — see
# contract_pr_f15.md blocker on safety-determinism).
# ---------------------------------------------------------------------------


def test_update_dataset_artifacts_preserves_handles_when_key_omitted(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "library loans",
        {
            "diagram.md": "",
            "model.yaml": "",
            "mie.yaml": "",
            "mapping.rml.ttl": "",
            "mapping.yaml": _MAPPING_YAML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-25T00:00:00Z",
    )
    dataset_id = meta["id"]
    handles_text = '{"version": 1, "handles": [{"source": "books.csv", "column": "book_id"}]}'
    (root / dataset_id / "handles.json").write_text(handles_text, encoding="utf-8")

    # A partial artifacts dict, as built by e.g. the display-meta edit path
    # (`_artifacts_from_document`): no "handles.json" key at all.
    partial_artifacts = {
        "diagram.md": "",
        "model.yaml": "",
        "mie.yaml": "",
        "mapping.rml.ttl": "",
        "mapping.yaml": _MAPPING_YAML,
    }
    updated = registry.update_dataset_artifacts(
        root,
        dataset_id,
        partial_artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
    )
    assert updated is not None
    assert (root / dataset_id / "handles.json").read_text(encoding="utf-8") == handles_text
    loaded = registry.load_dataset(root, dataset_id)
    assert loaded is not None
    assert load_handles(loaded["artifacts"]) == [{"source": "books.csv", "column": "book_id"}]


def test_update_dataset_artifacts_still_writes_handles_when_key_present(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "library loans",
        {
            "diagram.md": "",
            "model.yaml": "",
            "mie.yaml": "",
            "mapping.rml.ttl": "",
            "mapping.yaml": _MAPPING_YAML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-25T00:00:00Z",
    )
    dataset_id = meta["id"]
    new_handles_text = '{"version": 1, "handles": [{"source": "loans.csv", "column": "book_id"}]}'
    updated = registry.update_dataset_artifacts(
        root,
        dataset_id,
        {
            "diagram.md": "",
            "model.yaml": "",
            "mie.yaml": "",
            "mapping.rml.ttl": "",
            "mapping.yaml": _MAPPING_YAML,
            "handles.json": new_handles_text,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
    )
    assert updated is not None
    assert (root / dataset_id / "handles.json").read_text(encoding="utf-8") == new_handles_text


# ---------------------------------------------------------------------------
# register_handles routes
# ---------------------------------------------------------------------------


def _client(tmp_path: Path, healthy_client) -> TestClient:
    settings = _settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    register_handles(app, settings)
    return TestClient(app, headers=_AUTH)


def _make_dataset(tmp_path: Path) -> str:
    meta = registry.save_dataset(
        tmp_path / "registry",
        "library loans",
        {
            "diagram.md": "",
            "model.yaml": "",
            "mie.yaml": "",
            "mapping.rml.ttl": "",
            "mapping.yaml": _MAPPING_YAML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-25T00:00:00Z",
    )
    return meta["id"]


def test_get_handles_unknown_dataset_404(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.get("/api/datasets/does-not-exist/handles")
        assert r.status_code == 404


def test_get_handles_defaults_to_empty(tmp_path: Path, healthy_client) -> None:
    dataset_id = _make_dataset(tmp_path)
    with _client(tmp_path, healthy_client) as client:
        r = client.get(f"/api/datasets/{dataset_id}/handles")
        assert r.status_code == 200
        assert r.json() == {"handles": []}


def test_put_then_get_handles_round_trip(tmp_path: Path, healthy_client) -> None:
    dataset_id = _make_dataset(tmp_path)
    body = {"handles": [{"source": "books.csv", "column": "book_id"}]}
    with _client(tmp_path, healthy_client) as client:
        r = client.put(f"/api/datasets/{dataset_id}/handles", json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {"handles": body["handles"]}

        r = client.get(f"/api/datasets/{dataset_id}/handles")
        assert r.status_code == 200
        assert r.json() == {"handles": body["handles"]}


def test_put_handles_requires_write_auth(tmp_path: Path, healthy_client) -> None:
    dataset_id = _make_dataset(tmp_path)
    settings = _settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    register_handles(app, settings)
    with TestClient(app) as client:  # no _AUTH headers
        r = client.put(
            f"/api/datasets/{dataset_id}/handles",
            json={"handles": [{"source": "books.csv", "column": "book_id"}]},
        )
        assert r.status_code == 401


def test_put_handles_unknown_dataset_404(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.put(
            "/api/datasets/does-not-exist/handles",
            json={"handles": []},
        )
        assert r.status_code == 404
