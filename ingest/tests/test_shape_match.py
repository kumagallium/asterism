"""Tests for asterism.shape_match (object-cards-ui.md §4 / 契約メモ §4)。

「データを置く」画面が使う決定論: 型の形の読み出し (``type_signatures``)・
アップロードされた表との列突き合わせ (``match_shape``)・1 件ごとの 3 状態照合
(``match_subjects``)。フィクスチャは架空の 2 分野（図書館の貸出目録・見回りの
動物記録）— どちらも材料科学の語彙を含まない。

``match_subjects`` は pyoxigraph 実 store で検証する（同じ流儀は
test_class_schema.py の ``_pyoxi_client``）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from asterism.shape_match import (
    ColumnSignature,
    MissingKeyColumnsError,
    TypeSignature,
    _safe_iri,
    match_shape,
    match_subjects,
    prune_mapping_ir_yaml,
    type_signatures,
)
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str]):
    """{graph_iri: ttl} を実 pyoxigraph.Store に積む。``CANONICAL_GRAPH_BASE`` 配下の
    グラフは control グラフで ``promoted`` を立てる（test_class_schema.py と同じ流儀）。
    """
    store = pyoxigraph.Store()
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
        if giri.startswith(CANONICAL_GRAPH_BASE):
            store.add(
                pyoxigraph.Quad(
                    pyoxigraph.NamedNode(giri),
                    pyoxigraph.NamedNode(STATUS_PREDICATE),
                    pyoxigraph.Literal(STATUS_PROMOTED),
                    pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
                )
            )

    class _C:
        async def sparql_select(self, query: str) -> dict:
            result = store.query(query)
            names = [v.value for v in result.variables]
            bindings = []
            for solution in result:
                row = {}
                for name in names:
                    term = solution[name]
                    if term is None:
                        continue
                    if isinstance(term, pyoxigraph.NamedNode):
                        row[name] = {"type": "uri", "value": term.value}
                    elif isinstance(term, pyoxigraph.Literal):
                        row[name] = {"type": "literal", "value": term.value}
                bindings.append(row)
            return {"results": {"bindings": bindings}}

        async def sparql_update(self, update: str) -> None:  # pragma: no cover
            raise AssertionError("match_subjects should never write")

    return _C()


# ---------------------------------------------------------------------------
# match_shape — 列名/ラベルの正規化・key 条件・部分一致・同点タイブレーク
# ---------------------------------------------------------------------------

_LIBRARY_SIG = TypeSignature(
    type_id="https://ex/library#Item",
    dataset_id="library-aaaaaaaa",
    key_columns=("code",),
    columns=(
        ColumnSignature(column="code", label="登録番号", unit=None),
        ColumnSignature(column="title", label=None, unit=None),
        ColumnSignature(column="weight_kg", label="重さ", unit="kg"),
    ),
    template="https://ex/library/resource/item/{code}",
)


def test_normalize_matches_case_unit_suffix_and_punctuation() -> None:
    """列名の大文字小文字・単位表記 `[..]`/`(..)`・記号の違いを無視して一致する。"""
    columns = ["Code", "Weight_KG [kg]", "Title!"]
    result = match_shape(columns, [_LIBRARY_SIG])
    assert result.type_id == "https://ex/library#Item"
    assert set(result.matched_columns) == {"Code", "Weight_KG [kg]", "Title!"}
    assert result.unmatched_columns == ()
    assert result.confidence == 1.0


_LIBRARY_SIG_ASCII_LABEL = TypeSignature(
    type_id="https://ex/library#Item",
    dataset_id="library-aaaaaaaa",
    key_columns=("code",),
    columns=(
        ColumnSignature(column="code", label="item_no", unit=None),
        ColumnSignature(column="title", label=None, unit=None),
    ),
    template="https://ex/library/resource/item/{code}",
)


def test_normalize_matches_via_label_not_just_column_name() -> None:
    """列名そのものが違っても、label と正規化一致すれば列として数えられる —
    key 列 (code) 自身も含めて。列名の正規化は英数以外を落とす（§4.1）ので、
    ASCII の label で検証する（全角のみの label は次のテストの通り別の理由で
    一致しない）。"""
    columns = ["item_no", "title", "extra_unrelated"]
    result = match_shape(columns, [_LIBRARY_SIG_ASCII_LABEL])
    assert result.type_id == "https://ex/library#Item"
    assert set(result.matched_columns) == {"item_no", "title"}
    assert result.unmatched_columns == ("extra_unrelated",)
    assert result.confidence == round(2 / 3, 2)


def test_all_nonalnum_label_never_falsely_matches() -> None:
    """正規化 (英数以外を落とす) は全角のみの列名/label を空文字列に潰す。空文字列
    どうしを一致とみなすと無関係な列が偶然一致してしまうので、一致の根拠にしない
    （§4.1 の正規化規則そのものの帰結 — 全角のみの label は列名一致の対象にできない、
    という既知の限界。notes に記載）。"""
    columns = ["登録番号", "重さ", "備考"]  # ラベルはどれも全角のみ
    result = match_shape(columns, [_LIBRARY_SIG])
    # 3 列とも正規化すると空文字列になり、互いに「一致」してしまうことはない。
    assert result.type_id is None
    assert result.matched_columns == ()
    assert result.unmatched_columns == tuple(columns)


def test_key_columns_must_all_be_present() -> None:
    """key_columns (ここでは code 1 つ) が入力に無ければ、他がどれだけ揃っても不一致。"""
    columns = ["title", "weight_kg"]  # code が無い
    result = match_shape(columns, [_LIBRARY_SIG])
    assert result.type_id is None
    assert result.dataset_id is None
    assert result.matched_columns == ()
    assert result.unmatched_columns == tuple(columns)


def test_key_present_but_fewer_than_two_matched_columns_is_no_match() -> None:
    """key 列はあっても matched が 1 列（key 自身だけ）では条件を満たさない。"""
    columns = ["code", "unrelated_a", "unrelated_b"]
    result = match_shape(columns, [_LIBRARY_SIG])
    assert result.type_id is None


def test_partial_match_returns_type_id_with_unmatched_columns() -> None:
    """一致列だけで置ける — 未知の列があっても type_id は返る（部分一致）。"""
    columns = ["code", "title", "unrelated_extra_column"]
    result = match_shape(columns, [_LIBRARY_SIG])
    assert result.type_id == "https://ex/library#Item"
    assert set(result.matched_columns) == {"code", "title"}
    assert result.unmatched_columns == ("unrelated_extra_column",)
    assert result.confidence == round(2 / 3, 2)


def test_no_candidate_satisfies_returns_type_id_none() -> None:
    columns = ["totally", "unrelated", "columns"]
    result = match_shape(columns, [_LIBRARY_SIG])
    assert result.type_id is None
    assert result.dataset_id is None
    assert result.matched_columns == ()
    assert result.unmatched_columns == tuple(columns)
    assert result.confidence == 0.0


def test_tie_break_is_dataset_id_lexicographic_order() -> None:
    """confidence が同点のとき、dataset_id の辞書順で決定論的に選ぶ。"""
    sig_b = TypeSignature(
        type_id="https://ex/animal#Sighting",
        dataset_id="patrol-zzzzzzzz",
        key_columns=("tag",),
        columns=(
            ColumnSignature(column="tag", label=None, unit=None),
            ColumnSignature(column="note", label=None, unit=None),
        ),
        template="https://ex/animal/resource/sighting/{tag}",
    )
    sig_a = TypeSignature(
        type_id="https://ex/animal#Sighting",
        dataset_id="patrol-aaaaaaaa",
        key_columns=("tag",),
        columns=(
            ColumnSignature(column="tag", label=None, unit=None),
            ColumnSignature(column="note", label=None, unit=None),
        ),
        template="https://ex/animal/resource/other/{tag}",
    )
    columns = ["tag", "note"]
    # 候補の並び順を変えても結果は変わらない（辞書順の勝者は常に patrol-aaaaaaaa）。
    assert match_shape(columns, [sig_b, sig_a]).dataset_id == "patrol-aaaaaaaa"
    assert match_shape(columns, [sig_a, sig_b]).dataset_id == "patrol-aaaaaaaa"


def test_higher_confidence_wins_over_dataset_id_order() -> None:
    """同点でなければ confidence が高い方が勝つ（辞書順より優先）。

    両候補とも key 条件 (key 全一致 ∧ matched >= 2 列) を満たす正当な候補にした上で
    confidence だけを違えないと、辞書順の勝者が「実は他方が候補失格だっただけ」に
    なってしまう — ここでは両方とも matched=2 以上の有効な候補にしている。
    """
    partial = TypeSignature(  # dataset_id は辞書順で先頭だが confidence は低い
        type_id="https://ex/a#A",
        dataset_id="aaa-dataset",
        key_columns=("code",),
        columns=(
            ColumnSignature(column="code", label=None, unit=None),
            ColumnSignature(column="note", label=None, unit=None),
        ),
        template="https://ex/a/resource/{code}",
    )
    full = TypeSignature(  # dataset_id は辞書順で後だが全列一致で confidence が高い
        type_id="https://ex/z#Z",
        dataset_id="zzz-dataset",
        key_columns=("code",),
        columns=(
            ColumnSignature(column="code", label=None, unit=None),
            ColumnSignature(column="note", label=None, unit=None),
            ColumnSignature(column="extra1", label=None, unit=None),
            ColumnSignature(column="extra2", label=None, unit=None),
        ),
        template="https://ex/z/resource/{code}",
    )
    columns = ["code", "note", "extra1", "extra2"]
    partial_only = match_shape(columns, [partial])
    assert partial_only.type_id == "https://ex/a#A"  # partial 単体でも候補として有効
    assert partial_only.confidence == 0.5

    result = match_shape(columns, [partial, full])
    assert result.dataset_id == "zzz-dataset"
    assert result.confidence == 1.0


def test_empty_candidates_or_columns() -> None:
    assert match_shape(["a", "b"], []).type_id is None
    result = match_shape([], [_LIBRARY_SIG])
    assert result.type_id is None
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# type_signatures — promoted mapping.yaml の読み出し（決定論の並び）
# ---------------------------------------------------------------------------

_LIBRARY_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/library#"
  exr: "https://ex/library/resource/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: item
    source: items.csv
    subject:
      template: "exr:item/{code}"
      classes: [ex:Item]
    properties:
      - predicate: ex:code
        column: code
        label: "登録番号"
      - predicate: ex:title
        column: title
      - predicate: ex:weight
        column: weight_kg
        datatype: xsd:double
        unit: "kg"
"""

# key 列 (tag) が properties に出てこない — key_predicate が付かないケース。
_ANIMAL_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/animal#"
  exr: "https://ex/animal/resource/"
maps:
  - name: sighting
    source: sightings.csv
    subject:
      template: "exr:sighting/{tag}"
      classes: [ex:Sighting]
    properties:
      - predicate: ex:note
        column: note
      - predicate: ex:spot
        column: spot
"""


def _write_dataset(
    registry_root: Path,
    dataset_id: str,
    *,
    promoted: bool,
    mapping_yaml: str | None,
) -> None:
    dest = registry_root / dataset_id
    dest.mkdir(parents=True)
    meta = {"id": dataset_id, "promoted": promoted}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if mapping_yaml is not None:
        (dest / "mapping.yaml").write_text(mapping_yaml, encoding="utf-8")


def test_type_signatures_reads_promoted_datasets_only(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    _write_dataset(root, "library-aaaaaaaa", promoted=True, mapping_yaml=_LIBRARY_MAPPING_YAML)
    _write_dataset(root, "patrol-bbbbbbbb", promoted=True, mapping_yaml=_ANIMAL_MAPPING_YAML)
    _write_dataset(root, "draft-cccccccc", promoted=False, mapping_yaml=_LIBRARY_MAPPING_YAML)
    _write_dataset(root, "empty-dddddddd", promoted=True, mapping_yaml=None)

    sigs = type_signatures(root)
    ids = {s.dataset_id for s in sigs}
    assert ids == {"library-aaaaaaaa", "patrol-bbbbbbbb"}  # draft/empty は除外


def test_type_signatures_deterministic_dataset_order(tmp_path: Path) -> None:
    """データセット id の辞書順で読む — ディレクトリ作成順に依存しない。"""
    root = tmp_path / "registry"
    _write_dataset(root, "zzz-later", promoted=True, mapping_yaml=_ANIMAL_MAPPING_YAML)
    _write_dataset(root, "aaa-first", promoted=True, mapping_yaml=_LIBRARY_MAPPING_YAML)
    sigs = type_signatures(root)
    assert [s.dataset_id for s in sigs] == ["aaa-first", "zzz-later"]


def test_type_signatures_extracts_key_predicate_when_key_column_is_a_property(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registry"
    _write_dataset(root, "library-aaaaaaaa", promoted=True, mapping_yaml=_LIBRARY_MAPPING_YAML)
    (sig,) = type_signatures(root)
    assert sig.type_id == "https://ex/library#Item"
    assert sig.key_columns == ("code",)
    assert sig.key_predicate == "https://ex/library#code"
    assert {c.column for c in sig.columns} == {"code", "title", "weight_kg"}
    # template は CURIE プレフィクスを完全 IRI に展開したもの — プレースホルダは
    # そのまま。展開し忘れると match_subjects のフォールバック経路や commit の
    # 新規 IRI 鋳造が "exr:item/…" という CURIE 文字列を IRI として使ってしまう。
    assert sig.template == "https://ex/library/resource/item/{code}"


def test_type_signatures_key_predicate_is_none_when_key_column_not_a_property(
    tmp_path: Path,
) -> None:
    """key 列が properties[] に出てこないデータセットは key_predicate 無し
    （match_subjects はテンプレート埋めのフォールバックに回る — 契約メモの逸脱注記）。"""
    root = tmp_path / "registry"
    _write_dataset(root, "patrol-bbbbbbbb", promoted=True, mapping_yaml=_ANIMAL_MAPPING_YAML)
    (sig,) = type_signatures(root)
    assert sig.key_columns == ("tag",)
    assert sig.key_predicate is None


def test_type_signatures_missing_registry_root_returns_empty(tmp_path: Path) -> None:
    assert type_signatures(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# prune_mapping_ir_yaml — 部分一致は一致した列だけで置く（O5）
# ---------------------------------------------------------------------------


def test_prune_keeps_only_properties_whose_columns_are_in_the_file() -> None:
    """``weight_kg`` を持たないファイルへ置くと、その property 行だけ落ちる —
    ``code``/``title`` の 2 行と ID 列 (``code``) は残る。"""
    pruned = prune_mapping_ir_yaml(
        _LIBRARY_MAPPING_YAML, "https://ex/library#Item", ["code", "title"]
    )
    doc = yaml.safe_load(pruned)
    assert len(doc["maps"]) == 1
    (m,) = doc["maps"]
    assert m["name"] == "item"
    columns = {p["column"] for p in m["properties"]}
    assert columns == {"code", "title"}
    # 他のキー（version・prefixes・subject）はそのまま保たれる。
    assert doc["version"] == 1
    assert doc["prefixes"]["ex"] == "https://ex/library#"
    assert m["subject"]["template"] == "exr:item/{code}"


def test_prune_matches_target_map_by_curie_class() -> None:
    """``map_name_or_class`` は展開前の CURIE でも一致する。"""
    pruned = prune_mapping_ir_yaml(_LIBRARY_MAPPING_YAML, "ex:Item", ["code", "title"])
    (m,) = yaml.safe_load(pruned)["maps"]
    assert m["name"] == "item"


def test_prune_matches_target_map_by_name() -> None:
    pruned = prune_mapping_ir_yaml(_LIBRARY_MAPPING_YAML, "item", ["code", "title"])
    (m,) = yaml.safe_load(pruned)["maps"]
    assert m["name"] == "item"


_COMBINED_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/library#"
  exr: "https://ex/library/resource/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: item
    source: items.csv
    subject:
      template: "exr:item/{code}"
      classes: [ex:Item]
    properties:
      - predicate: ex:code
        column: code
      - predicate: ex:title
        column: title
  - name: sighting
    source: sightings.csv
    subject:
      template: "exr:sighting/{tag}"
      classes: [ex:Sighting]
    properties:
      - predicate: ex:note
        column: note
"""


def test_prune_drops_other_triples_maps() -> None:
    """複数 map を含む mapping.yaml から、signature の 1 つだけが残る。"""
    pruned = prune_mapping_ir_yaml(
        _COMBINED_MAPPING_YAML, "https://ex/library#Item", ["code", "title"]
    )
    doc = yaml.safe_load(pruned)
    assert [m["name"] for m in doc["maps"]] == ["item"]


def test_prune_missing_id_column_raises() -> None:
    """subject template の列 (``code``) がファイルに無ければ、判断が要る前に
    :class:`MissingKeyColumnsError` で止まる。"""
    with pytest.raises(MissingKeyColumnsError) as excinfo:
        prune_mapping_ir_yaml(_LIBRARY_MAPPING_YAML, "https://ex/library#Item", ["title"])
    assert excinfo.value.columns == ("code",)


def test_prune_unknown_map_raises() -> None:
    with pytest.raises(Exception):  # noqa: B017 — MappingIRReadError (ValueError subclass)
        prune_mapping_ir_yaml(_LIBRARY_MAPPING_YAML, "https://ex/library#NoSuchClass", ["code"])


def test_prune_column_normalization_matches_shape_match(tmp_path: Path) -> None:
    """列名の一致は match_shape と同じ正規化 — 単位表記・大文字小文字・記号を無視する。"""
    pruned = prune_mapping_ir_yaml(
        _LIBRARY_MAPPING_YAML,
        "https://ex/library#Item",
        ["Code", "Title", "Weight-Kg"],
    )
    (m,) = yaml.safe_load(pruned)["maps"]
    columns = {p["column"] for p in m["properties"]}
    assert columns == {"code", "title", "weight_kg"}


# ---------------------------------------------------------------------------
# match_subjects — 1 件の照合（3 状態: linked / ambiguous / own_only）
# ---------------------------------------------------------------------------

_LIBRARY_CODE_PRED = "https://ex/library#code"
_LIBRARY_ITEM_CLASS = "https://ex/library#Item"
_LIB_GRAPH = canonical_graph_iri("library-aaaaaaaa") + "/v1"

_WITH_KEY_PREDICATE = TypeSignature(
    type_id=_LIBRARY_ITEM_CLASS,
    dataset_id="library-aaaaaaaa",
    key_columns=("code",),
    columns=(ColumnSignature(column="code", label=None, unit=None),),
    template="https://ex/library/resource/item/{code}",
    key_predicate=_LIBRARY_CODE_PRED,
)


def _library_store_ttl() -> str:
    # item-1 と item-2 はどちらも code "ITM-01" を名乗る（ambiguous）。
    # item-3 は "ITM-02"（linked、大文字小文字・前後空白のゆらぎも吸収）。
    return "\n".join(
        [
            f'<https://ex/library/resource/item/item-1> a <{_LIBRARY_ITEM_CLASS}> ;'
            f' <{_LIBRARY_CODE_PRED}> "ITM-01" .',
            f'<https://ex/library/resource/item/item-2> a <{_LIBRARY_ITEM_CLASS}> ;'
            f' <{_LIBRARY_CODE_PRED}> "ITM-01" ;'
            f' <http://www.w3.org/2000/01/rdf-schema#label> "二重登録分" .',
            f'<https://ex/library/resource/item/item-3> a <{_LIBRARY_ITEM_CLASS}> ;'
            f' <{_LIBRARY_CODE_PRED}> "ITM-02" .',
        ]
    )


async def test_match_subjects_with_key_predicate_three_states() -> None:
    client = _pyoxi_client({_LIB_GRAPH: _library_store_ttl()})
    values = ["itm-01", " ITM-02 ", "ITM-99"]  # 大小・前後空白のゆらぎ／未登録
    results = await match_subjects(client, _WITH_KEY_PREDICATE, values)
    by_value = {r["value"]: r for r in results}

    ambiguous = by_value["itm-01"]
    assert ambiguous["match"] == "ambiguous"
    assert ambiguous["iri"] is None
    assert {c["iri"] for c in ambiguous["candidates"]} == {
        "https://ex/library/resource/item/item-1",
        "https://ex/library/resource/item/item-2",
    }
    # ラベルがある候補はラベル、無い方はフォールバック（ローカル名の分かち書き）。
    labels = {c["iri"]: c["label"] for c in ambiguous["candidates"]}
    assert labels["https://ex/library/resource/item/item-2"] == "二重登録分"
    assert labels["https://ex/library/resource/item/item-1"] == "item 1"

    linked = by_value[" ITM-02 "]
    assert linked["match"] == "linked"
    assert linked["iri"] == "https://ex/library/resource/item/item-3"
    assert linked["candidates"] == []

    unknown = by_value["ITM-99"]
    assert unknown["match"] == "own_only"
    assert unknown["iri"] is None
    assert unknown["candidates"] == []


async def test_match_subjects_dedups_and_caps_at_500() -> None:
    client = _pyoxi_client({_LIB_GRAPH: _library_store_ttl()})
    values = ["ITM-02"] * 3 + [f"ITM-X{i}" for i in range(600)]
    results = await match_subjects(client, _WITH_KEY_PREDICATE, values)
    assert len(results) == 500  # 重複除去してから先頭 500 件
    assert results[0]["value"] == "ITM-02"
    assert results[0]["match"] == "linked"


async def test_match_subjects_empty_values_returns_empty() -> None:
    client = _pyoxi_client({})
    assert await match_subjects(client, _WITH_KEY_PREDICATE, []) == []
    assert await match_subjects(client, _WITH_KEY_PREDICATE, ["   ", ""]) == []


_ANIMAL_TAG_CLASS = "https://ex/animal#Sighting"
_ANIMAL_TEMPLATE = "https://ex/animal/resource/sighting/{tag}"
_ANIMAL_GRAPH = canonical_graph_iri("patrol-bbbbbbbb") + "/v1"

_WITHOUT_KEY_PREDICATE = TypeSignature(
    type_id=_ANIMAL_TAG_CLASS,
    dataset_id="patrol-bbbbbbbb",
    key_columns=("tag",),
    columns=(ColumnSignature(column="tag", label=None, unit=None),),
    template=_ANIMAL_TEMPLATE,
    key_predicate=None,
)


async def test_match_subjects_fallback_template_fill_when_no_key_predicate() -> None:
    """key_predicate が無いときは template を埋めた IRI が棚にあるかどうかだけを見る
    （§4.2 のフォールバック — 1 対 1 なので ambiguous は検出できない）。"""
    ttl = (
        f'<https://ex/animal/resource/sighting/A1> a <{_ANIMAL_TAG_CLASS}> ;'
        f' <https://ex/animal#note> "known" .'
    )
    client = _pyoxi_client({_ANIMAL_GRAPH: ttl})
    results = await match_subjects(client, _WITHOUT_KEY_PREDICATE, ["A1", "A2"])
    by_value = {r["value"]: r for r in results}
    assert by_value["A1"]["match"] == "linked"
    assert by_value["A1"]["iri"] == "https://ex/animal/resource/sighting/A1"
    assert by_value["A2"]["match"] == "own_only"
    assert by_value["A2"]["iri"] is None


async def test_match_subjects_multi_placeholder_template_is_own_only_deviation() -> None:
    """複合キーの subject template (プレースホルダ 2 つ以上) は Phase 1 で照合できず、
    すべて own_only を返す — shape_match.py の docstring に明記された逸脱。"""
    signature = TypeSignature(
        type_id="https://ex/animal#Pair",
        dataset_id="patrol-cccccccc",
        key_columns=("a", "b"),
        columns=(ColumnSignature(column="a", label=None, unit=None),),
        template="https://ex/animal/resource/pair/{a}-{b}",
        key_predicate=None,
    )
    client = _pyoxi_client({})
    results = await match_subjects(client, signature, ["x", "y"])
    assert [r["match"] for r in results] == ["own_only", "own_only"]
    assert all(r["iri"] is None for r in results)


# ---------------------------------------------------------------------------
# 文字列連結で生の値を SPARQL に埋めない（契約メモ §0）— フォールバック経路は
# アップロードされた（信頼できない）値をそのまま IRI に組み込んで VALUES 節へ
# 埋め込むので、`<`/`>` を含む値が IRIREF を早期に閉じて SPARQL を注入できて
# しまわないことを確認する。
# ---------------------------------------------------------------------------


def test_safe_iri_rejects_sparql_breaking_characters() -> None:
    assert _safe_iri("https://ex/ok/value") == "https://ex/ok/value"
    # SPARQL の IRIREF 文法がそもそも許さない文字を含むものはすべて None
    # （呼び出し側は「安全に埋め込めない = 一致しないものとして扱う」）。
    for bad in [
        "evil> . ?s ?p ?o } #",
        'has"quote',
        "has{brace",
        "has}brace",
        "has|pipe",
        "has^caret",
        "has\\backslash",
        "has space",
        "has\ttab",
        "has<bracket",
    ]:
        assert _safe_iri(bad) is None, bad


async def test_match_subjects_fallback_neutralizes_angle_bracket_injection_attempt() -> None:
    """テンプレート埋め込み後の値に SPARQL を壊す `<`/`>` が混じっていても例外に
    ならず、安全側 (own_only) に倒れる — 決して例外にも誤ヒットにもならない。"""
    ttl = (
        f'<https://ex/animal/resource/sighting/A1> a <{_ANIMAL_TAG_CLASS}> ;'
        f' <https://ex/animal#note> "known" .'
    )
    client = _pyoxi_client({_ANIMAL_GRAPH: ttl})
    malicious = "A1> . ?x ?y ?z } #"
    results = await match_subjects(client, _WITHOUT_KEY_PREDICATE, [malicious])
    assert results == [{"value": malicious, "match": "own_only", "iri": None, "candidates": []}]
