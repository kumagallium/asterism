"""W3: 共有のことばの CQ ツールを載せる土台（宣言ツール側）。

- QueryTool.for_terms の保持（parse / lenient / load / annotate）
- upsert_registry_query_tools_by_name（冪等・接頭辞掃除・lint・atomic・読めない yaml で中止）
- write_registry_query_tools の既存の保持規則が壊れないこと＋読めない yaml で中止
- wired.json による for_terms ツールの除外（load_all_query_tools / tool_sources）
- 共有のことば（is_shared_vocab）をデータセットとして返さない 4 経路
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from asterism.catalog import _registry_entries, find_datasets, tool_sources
from asterism.class_schema import _promoted_metas
from asterism.classes_index import _promoted_datasets
from asterism.metadata_cli import _iter_dataset_dirs
from asterism.query_tools import (
    annotate_output_kind,
    load_all_query_tools,
    load_query_tools,
    parse_query_tools,
    parse_query_tools_lenient,
    synthesize_query_tools_from_trial_queries,
    upsert_registry_query_tools_by_name,
    write_registry_query_tools,
)

T1 = "https://kumagallium.github.io/asterism/vocab/shared#sample"
T2 = "https://kumagallium.github.io/asterism/vocab/shared#hardness"
QUERY = "SELECT ?s WHERE { ?s a <https://ex/Sample> } LIMIT 5"


def _raw(name: str, *, for_terms=None, query: str = QUERY) -> dict:
    t: dict = {"name": name, "title": name, "description": "d", "parameters": [], "query": query}
    if for_terms is not None:
        t["for_terms"] = for_terms
    return t


def _registry(tmp_path: Path, *ids: str) -> Path:
    reg = tmp_path / "registry"
    for i in ids:
        (reg / i).mkdir(parents=True)
    return reg


def _names(reg: Path, dataset_id: str) -> list[str]:
    data = yaml.safe_load((reg / dataset_id / "query_tools.yaml").read_text(encoding="utf-8"))
    return [t["name"] for t in data["tools"]]


def _wired(reg: Path, terms: dict[str, int]) -> None:
    d = reg / "vocab-shared"
    d.mkdir(parents=True, exist_ok=True)
    (d / "wired.json").write_text(json.dumps({"terms": terms, "at": "2026-01-01T00:00:00Z"}))


# --- for_terms の保持 ---------------------------------------------------------


def test_for_terms_is_kept_by_parse_lenient_load_and_annotate(tmp_path) -> None:
    raw = _raw("cq_sample_count", for_terms=[T1, T2])
    assert parse_query_tools({"tools": [raw]})[0].for_terms == (T1, T2)
    tools, issues = parse_query_tools_lenient({"tools": [raw, _raw("plain")]})
    assert issues == [] and [t.for_terms for t in tools] == [(T1, T2), ()]

    reg = _registry(tmp_path, "vocab-shared")
    (reg / "vocab-shared" / "query_tools.yaml").write_text(yaml.safe_dump({"tools": [raw]}))
    assert load_query_tools("vocab-shared", reg)[0].for_terms == (T1, T2)  # MCP 経路
    assert annotate_output_kind(raw)["for_terms"] == [T1, T2]  # API の生の宣言


def test_bad_for_terms_is_a_declaration_error_and_skipped_leniently() -> None:
    bad = _raw("bad", for_terms={"x": 1})
    tools, issues = parse_query_tools_lenient({"tools": [bad, _raw("ok")]})
    assert [t.name for t in tools] == ["ok"] and "for_terms" in issues[0]
    assert parse_query_tools({"tools": [_raw("s", for_terms=T1)]})[0].for_terms == (T1,)


# --- upsert_registry_query_tools_by_name -----------------------------------------


def test_upsert_is_idempotent_and_replaces_by_name(tmp_path) -> None:
    reg = _registry(tmp_path, "vocab-shared")
    path = reg / "vocab-shared" / "query_tools.yaml"
    batch = [_raw("cq_a_count", for_terms=[T1]), _raw("cq_a_values", for_terms=[T1])]

    assert upsert_registry_query_tools_by_name(reg, "vocab-shared", batch) == []
    first = path.read_text(encoding="utf-8")
    assert upsert_registry_query_tools_by_name(reg, "vocab-shared", batch) == []
    assert path.read_text(encoding="utf-8") == first
    assert _names(reg, "vocab-shared") == ["cq_a_count", "cq_a_values"]

    # 同名は位置を保って置換。他は触らない。
    changed = _raw("cq_a_count", for_terms=[T1], query=QUERY.replace("LIMIT 5", "LIMIT 9"))
    upsert_registry_query_tools_by_name(reg, "vocab-shared", [changed])
    assert _names(reg, "vocab-shared") == ["cq_a_count", "cq_a_values"]
    assert "LIMIT 9" in path.read_text(encoding="utf-8")


def test_upsert_remove_missing_prefix_only_drops_that_prefix_and_keeps_others(tmp_path) -> None:
    reg = _registry(tmp_path, "vocab-shared")
    upsert_registry_query_tools_by_name(
        reg,
        "vocab-shared",
        [_raw("cq_a_count"), _raw("cq_a_values"), _raw("cq_b_count"), _raw("hand_made")],
    )
    out = upsert_registry_query_tools_by_name(
        reg, "vocab-shared", [_raw("cq_a_count")], remove_missing_prefix="cq_a_"
    )
    assert out == []
    # cq_a_values は今回に無いので消える。cq_b_* と接頭辞外は残る。
    assert _names(reg, "vocab-shared") == ["cq_a_count", "cq_b_count", "hand_made"]

    # 空の tools でも接頭辞掃除はできる（語の全 CQ を消す）。
    assert (
        upsert_registry_query_tools_by_name(reg, "vocab-shared", [], remove_missing_prefix="cq_a_")
        == []
    )
    assert _names(reg, "vocab-shared") == ["cq_b_count", "hand_made"]


def test_upsert_returns_rejected_names_and_does_not_write_them(tmp_path) -> None:
    reg = _registry(tmp_path, "vocab-shared")
    broken = _raw("broken", query="SELECT ?s WHERE { ?s ")  # 構文エラーで lint を通らない
    out = upsert_registry_query_tools_by_name(reg, "vocab-shared", [_raw("good"), broken])
    assert out == ["broken"]
    assert _names(reg, "vocab-shared") == ["good"]


def test_upsert_stops_on_unreadable_yaml_and_leaves_it_untouched(tmp_path) -> None:
    reg = _registry(tmp_path, "vocab-shared")
    path = reg / "vocab-shared" / "query_tools.yaml"
    path.write_text("tools: [unclosed\n  - : :", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert upsert_registry_query_tools_by_name(reg, "vocab-shared", [_raw("x")]) is None
    assert path.read_text(encoding="utf-8") == before
    # 読めないときは接頭辞掃除だけでも中止。
    assert (
        upsert_registry_query_tools_by_name(reg, "vocab-shared", [], remove_missing_prefix="cq_")
        is None
    )
    assert not list((reg / "vocab-shared").glob(".*tmp"))  # tmp を残さない


def test_upsert_never_mints_a_registry_entry_and_ignores_empty_input(tmp_path) -> None:
    reg = _registry(tmp_path, "ds")
    assert upsert_registry_query_tools_by_name(reg, "nope", [_raw("x")]) is None
    assert not (reg / "nope").exists()
    assert upsert_registry_query_tools_by_name(reg, "ds", []) is None
    assert not (reg / "ds" / "query_tools.yaml").exists()


def test_upsert_leaves_no_tmp_file_and_keeps_reserved_names_as_plain_names(tmp_path) -> None:
    reg = _registry(tmp_path, "ds")
    # 予約名の「置換して消す」規則は write_registry_query_tools のもの。upsert は名前で置くだけ。
    upsert_registry_query_tools_by_name(reg, "ds", [_raw("counts_by_kind"), _raw("mine")])
    upsert_registry_query_tools_by_name(reg, "ds", [_raw("value_range")])
    assert _names(reg, "ds") == ["counts_by_kind", "mine", "value_range"]
    assert [p.name for p in (reg / "ds").iterdir()] == ["query_tools.yaml"]


# --- write_registry_query_tools（既存挙動＋中止）-------------------------------------

_TRIAL = {
    "available": True,
    "classes": [{"iri": "https://ex/Sample", "label": "Sample", "count": 3}],
    "range": {"predicate_iri": "https://ex/hardness", "label": "hardness"},
    "top": {"predicate_iri": "https://ex/hardness", "label": "hardness"},
}


def test_write_registry_keeps_reserved_rule_and_hand_tool_after_atomic_write(tmp_path) -> None:
    reg = _registry(tmp_path, "ds")
    hand = _raw("custom_lookup", for_terms=[T1])
    (reg / "ds" / "query_tools.yaml").write_text(yaml.safe_dump({"tools": [hand]}))
    write_registry_query_tools(reg, "ds", synthesize_query_tools_from_trial_queries(_TRIAL))
    names = _names(reg, "ds")
    assert names[0] == "custom_lookup" and {"counts_by_kind", "value_range"} <= set(names)
    kept = load_query_tools("ds", reg)[0]
    assert kept.name == "custom_lookup" and kept.for_terms == (T1,)  # 手書きの欄を落とさない

    fewer = synthesize_query_tools_from_trial_queries({**_TRIAL, "classes": []})
    write_registry_query_tools(reg, "ds", fewer)
    assert "counts_by_kind" not in _names(reg, "ds") and "custom_lookup" in _names(reg, "ds")
    assert [p.name for p in (reg / "ds").iterdir()] == ["query_tools.yaml"]


def test_write_registry_stops_on_unreadable_existing_yaml(tmp_path) -> None:
    reg = _registry(tmp_path, "ds")
    path = reg / "ds" / "query_tools.yaml"
    path.write_text("tools: [unclosed\n  - : :", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert (
        write_registry_query_tools(reg, "ds", synthesize_query_tools_from_trial_queries(_TRIAL))
        is None
    )
    assert path.read_text(encoding="utf-8") == before


# --- wired.json による除外 --------------------------------------------------------


def _vocab_with_cqs(reg: Path) -> None:
    (reg / "vocab-shared").mkdir(parents=True, exist_ok=True)
    upsert_registry_query_tools_by_name(
        reg,
        "vocab-shared",
        [
            _raw("cq_sample_count", for_terms=[T1]),
            _raw("cq_hardness_values", for_terms=[T2]),
            _raw("cq_both", for_terms=[T1, T2]),
            _raw("plain_tool"),
        ],
    )


def _served(reg: Path) -> set[str]:
    return {t.name for t in load_all_query_tools(reg).get("vocab-shared", [])}


def test_for_terms_tools_are_hidden_without_wired_json_and_when_all_terms_are_zero(
    tmp_path,
) -> None:
    reg = _registry(tmp_path)
    _vocab_with_cqs(reg)
    assert _served(reg) == {"plain_tool"}  # wired.json 無し＝安全側で全部除く

    _wired(reg, {T1: 0, T2: 0})
    assert _served(reg) == {"plain_tool"}


def test_for_terms_tool_is_served_when_any_of_its_terms_is_wired(tmp_path) -> None:
    reg = _registry(tmp_path)
    _vocab_with_cqs(reg)
    _wired(reg, {T1: 2, T2: 0})
    # 「すべて 0」のときだけ除く。語が 1 つでも答えるなら載る。
    assert _served(reg) == {"plain_tool", "cq_sample_count", "cq_both"}
    _wired(reg, {T1: 1, T2: 3})
    assert _served(reg) == {"plain_tool", "cq_sample_count", "cq_hardness_values", "cq_both"}


def test_tool_sources_applies_the_same_rule_and_a_flat_wired_file_is_read(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = _registry(tmp_path)
    _vocab_with_cqs(reg)
    assert {t.name for t in tool_sources(reg)["vocab-shared"]} == {"plain_tool"}
    (reg / "vocab-shared" / "wired.json").write_text(json.dumps({T2: 1, "at": "x"}))
    assert {t.name for t in tool_sources(reg)["vocab-shared"]} == {
        "plain_tool",
        "cq_hardness_values",
        "cq_both",
    }


# --- 共有のことばをデータセットとして返さない --------------------------------------


def _meta(reg: Path, dataset_id: str, **extra) -> None:
    d = reg / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": dataset_id, "name": dataset_id, "promoted": True, **extra}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_shared_vocab_entry_is_not_returned_as_a_dataset_by_the_four_scans(tmp_path) -> None:
    reg = tmp_path / "registry"
    _meta(reg, "real-abc12345", classes=["Sample"])
    _meta(reg, "vocab-shared", is_shared_vocab=True)

    assert [e["id"] for e in _registry_entries(reg)] == ["real-abc12345"]
    assert [i for i, _ in _promoted_datasets(reg)] == ["real-abc12345"]
    assert [m["id"] for m in _promoted_metas(reg)] == ["real-abc12345"]
    assert [d.name for d in _iter_dataset_dirs(reg, None)] == ["real-abc12345"]
    assert [d.name for d in _iter_dataset_dirs(reg, {"vocab-shared"})] == []


def test_find_datasets_hides_shared_vocab_and_hub_handling_is_unchanged(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    _meta(reg, "real-abc12345")
    _meta(reg, "vocab-shared", is_shared_vocab=True)
    _meta(reg, "crosswalk", is_crosswalk=True, crosswalk_perspective_id="p")
    out = find_datasets(root=reg)
    # ハブ（is_crosswalk）はこれまでどおり扱う（W3 は共有のことばだけを除く）。
    assert sorted(d["id"] for d in out["datasets"]) == ["crosswalk", "real-abc12345"]
    assert [i for i, _ in _promoted_datasets(reg)] == ["crosswalk", "real-abc12345"]
