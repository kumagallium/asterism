"""Discovery (`find_datasets`) — the `find_databases` analogue."""

from __future__ import annotations

import json
from pathlib import Path

from asterism.catalog import find_datasets, resolve_tool_names, tool_sources
from asterism.query_tools import QueryTool


def _dataset(reg: Path, ds_id: str, **meta) -> Path:
    d = reg / ds_id
    d.mkdir(parents=True, exist_ok=True)
    base = {"id": ds_id, "name": ds_id, "promoted": True, "status": "active"}
    base.update(meta)
    (d / "meta.json").write_text(json.dumps(base), encoding="utf-8")
    return d


def _tools(d: Path, body: str) -> None:
    (d / "query_tools.yaml").write_text(body, encoding="utf-8")


def test_lists_promoted_datasets_with_their_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    d = _dataset(reg, "zem-abc12345", name="ZEM", classes=["Sample", "Measurement"])
    _tools(
        d,
        "tools:\n"
        "  - name: sample_lookup\n"
        "    description: find a sample by its code\n"
        "    parameters:\n"
        "      - name: code\n"
        "    query: 'SELECT ?s WHERE { ?s ?p \"{code}\" }'\n",
    )
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))

    out = find_datasets()
    assert out["count"] == 1 and out["truncated"] is False
    ds = out["datasets"][0]
    assert ds["id"] == "zem-abc12345" and ds["name"] == "ZEM"
    assert ds["classes"] == ["Sample", "Measurement"]
    assert [t["name"] for t in ds["tools"]] == ["sample_lookup"]
    assert ds["tools"][0]["params"] == ["code"]


def test_drafts_and_retracted_are_hidden_unless_asked(monkeypatch, tmp_path) -> None:
    # A draft answers from no promoted graph, and a retracted dataset is no longer
    # citable — listing either invites a tool call that returns nothing.
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    _dataset(reg, "draft-11111111", name="Draft", promoted=False)
    _dataset(reg, "gone-22222222", name="Gone", status="retracted")
    _dataset(reg, "live-33333333", name="Live")
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))

    assert [d["name"] for d in find_datasets()["datasets"]] == ["Live"]
    names = {d["name"] for d in find_datasets(include_drafts=True)["datasets"]}
    assert names == {"Draft", "Gone", "Live"}


def test_keywords_and_all_must_match(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    _dataset(reg, "thermo-11111111", name="Thermoelectric", classes=["Seebeck"])
    _dataset(reg, "battery-22222222", name="Battery", classes=["Capacity"])
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))

    assert [d["name"] for d in find_datasets(["seebeck"])["datasets"]] == ["Thermoelectric"]
    # AND, not OR: a term that matches nothing empties the result.
    assert find_datasets(["seebeck", "capacity"])["count"] == 0
    # A bare string is accepted like a one-element list.
    assert find_datasets("battery")["count"] == 1


def test_reported_tool_name_is_the_served_name(monkeypatch, tmp_path) -> None:
    # The server prefixes a colliding tool with `{dataset}_`; discovery that
    # reported the DECLARED name would hand the agent a name that does not exist.
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    body = "tools:\n  - name: dup\n    query: 'SELECT ?s WHERE { ?s ?p ?o }'\n"
    _tools(_dataset(reg, "alpha-11111111", name="Alpha"), body)
    _tools(_dataset(reg, "beta-22222222", name="Beta"), body)
    # A tool colliding with a hardcoded server tool is prefixed too.
    _tools(
        _dataset(reg, "gamma-33333333", name="Gamma"),
        "tools:\n  - name: schema_summary\n    query: 'SELECT ?s WHERE { ?s ?p ?o }'\n",
    )
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))

    served = {
        d["name"]: [t["name"] for t in d["tools"]] for d in find_datasets()["datasets"]
    }
    assert served["Alpha"] == ["dup"]
    assert served["Beta"] == ["beta-22222222_dup"]
    assert served["Gamma"] == ["gamma-33333333_schema_summary"]


def test_resolve_tool_names_matches_registration_order() -> None:
    q = QueryTool(name="dup", title="", description="", params=(), query="")
    mapping = resolve_tool_names({"a": [q], "b": [q]})
    assert mapping == {"a": {"dup": "dup"}, "b": {"dup": "b_dup"}}


def test_no_registry_yields_empty_not_an_error(monkeypatch) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    monkeypatch.delenv("CSV2RDF_REGISTRY_ROOT", raising=False)
    assert find_datasets() == {"datasets": [], "count": 0, "truncated": False}
    assert tool_sources() == {}


def test_bundled_examples_only_under_the_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CSV2RDF_REGISTRY_ROOT", raising=False)
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    assert find_datasets()["count"] == 0
    monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", "1")
    names = {d["id"] for d in find_datasets(limit=100)["datasets"]}
    assert "starrydata" in names  # the repo's bundled example is now discoverable


def test_limit_is_reported_as_truncated(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    for i in range(3):
        _dataset(reg, f"ds{i}-1111111{i}", name=f"DS{i}")
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))
    out = find_datasets(limit=2)
    assert out["count"] == 2 and out["truncated"] is True


def test_mie_description_is_surfaced_and_searchable(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    d = _dataset(reg, "zem-abc12345", name="ZEM")
    (d / "mie.yaml").write_text(
        "schema_info:\n  title: ZEM\n  description: thermoelectric transport curves\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))
    out = find_datasets(["transport"])
    assert out["count"] == 1
    assert out["datasets"][0]["description"] == "thermoelectric transport curves"


def test_a_malformed_artifact_does_not_hide_the_dataset(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    d = _dataset(reg, "zem-abc12345", name="ZEM")
    (d / "mie.yaml").write_text("schema_info: [not, a, mapping\n", encoding="utf-8")
    (reg / "broken-44444444").mkdir(parents=True)
    (reg / "broken-44444444" / "meta.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))
    out = find_datasets()
    assert [x["name"] for x in out["datasets"]] == ["ZEM"]
    assert out["datasets"][0]["description"] == ""
    assert d.is_dir()
