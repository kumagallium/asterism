"""1 件の種類（class）のスキーマを読む（object-cards-ui.md §2 / 契約メモ §2）。

「ページ」「カード詳細」が同じクラスの主語をまとめて扱うために、そのクラスが
持つ性質（property）を kind（quantity/category/text/link/identifier）付きで返す。
出所は決定論の優先順位で選ぶ（生成コードは実行しない・LLM は呼ばない）:

1. registry の ``mapping.yaml``（Mapping IR, K8）— このクラスを ``subject.classes``
   に宣言している TriplesMap の ``properties[]``。同じクラスを宣言する promoted
   データセットが複数あれば ``meta.promoted_at`` が新しい方を採用する。
2. ``display-meta.json`` の ``edits[]`` — 同じ (predicate, column) の
   label/unit を上書きする（S6 の人間による訂正）。
3. ``model.yaml`` — ``properties.<curie>.range`` がクラスを指す性質は
   kind=link とみなす（Mapping IR の ``object_template``/``object_type`` だけでは
   拾えない古い形の設計向け）。
4. どの registry データセットもこのクラスを宣言していないとき（同梱データセット
   など設計成果物を経由していないもの）は、promote 時に投影された「ontology」
   named graph（``asterism.substrate.ONTOLOGY_GRAPH_BASE``）を直接 SPARQL で読み、
   predicates とラベルだけを返す（列・データ型・単位は不明なので null）。

分野固有の語彙は一切書かない — クラス/性質の名前はすべて読んだ設計・ストアから
そのまま出てくる文字列で、コード自身が知っている語ではない。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rdflib
import yaml

from asterism.mapping_ir_read import (
    BUILTIN_PREFIXES,
    MappingIRView,
    PropertyView,
    TriplesMapView,
    read_mapping_ir,
)
from asterism.ontology_projection import RDF, RDFS, STANDARD_PREFIXES, project_model_yaml
from asterism.query_tools import annotate_output_kind
from asterism.subjects import safe_http_iri
from asterism.substrate import (
    ONTOLOGY_GRAPH_BASE,
    SupportsSparql,
    canonical_from_clauses,
    canonical_graphs,
    dataset_id_of_canonical_graph,
)

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"[a-z0-9-]{1,128}")
_META_FILE = "meta.json"
_MAPPING_FILE = "mapping.yaml"
_MODEL_FILE = "model.yaml"
_DISPLAY_META_FILE = "display-meta.json"
_QUERY_TOOLS_FILE = "query_tools.yaml"

# datatype の局所名（``xsd:`` プレフィクスや完全 IRI を剥いだ末尾）が量とみなせる
# もの。分野語ではなく XML Schema の型名（誰のデータにも共通）。
_NUMERIC_LOCAL_NAMES: frozenset[str] = frozenset(
    {"double", "float", "decimal", "integer", "int", "long"}
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_RDFS_RANGE = rdflib.URIRef(RDFS + "range")

#: unit の「値が無いことを表す文字」— 実機で Mapping IR の列が無い定数行
#: （``object_template: schema:name`` のような列なしの行）が ``unit: "N/A"`` を
#: 引きずっていた（§5(b)）。どの出所（Mapping IR／display-meta）から来た値でも、
#: ここを通れば null になる（表記ゆれの大文字小文字は無視）。
_UNIT_PLACEHOLDERS: frozenset[str] = frozenset({"n/a", "", "-", "—", "none", "null"})


def _normalize_unit(unit: str | None) -> str | None:
    """placeholder（"N/A"/"n/a"/""/"-"/"—"/"none"/"null"）を ``None`` にする。"""
    if unit is None:
        return None
    return None if unit.strip().lower() in _UNIT_PLACEHOLDERS else unit


#: ``rdf:type``/``rdfs:label``/... の IRIREF — compile-time 定数（RDF/RDFS の
#: 値）を埋め込むだけで、mapping.yaml やストアから来た値は一切混ざらない。
#: ``f"<{...}>"`` という書き方を避けているのは意図的（grep で「``_ref`` 以外に
#: ``f"<{`` が残っていない」ことを機械的に確認できるようにするため）。
_RDF_TYPE_REF = "<" + RDF + "type>"
_RDFS_LABEL_REF = "<" + RDFS + "label>"
_RDFS_CLASS_REF = "<" + RDFS + "Class>"
_RDF_PROPERTY_REF = "<" + RDF + "Property>"
_RDFS_DOMAIN_REF = "<" + RDFS + "domain>"


def _ref(iri: str | None) -> str | None:
    """``<iri>`` として SPARQL に literal に埋め込める形、埋め込めないなら
    ``None``。

    このモジュールが SPARQL へ埋め込む IRI（mapping.yaml の predicate・
    store が返した ``?p``/``?g`` を含む）は **すべて** ここを通す
    （:func:`asterism.subjects.safe_http_iri` — IRIREF が禁じる文字集合の
    唯一の定義）。実機で見つかった穴: mapping.yaml の predicate を検証
    なしに ``f"<{predicate_iri}>"`` へ埋めており、
    ``http://evil.example/p> } SELECT * WHERE { ?s ?p ?o`` のような値だと
    IRIREF が早期に閉じ任意の節を注入できた（GET /api/classes/schema は
    無認証）。呼び出し側は ``None`` を受け取ったらその行/性質を**捨てる**
    （例外にしない — 壊れた IR の 1 行でスキーマ全体を落とさない）。
    """
    safe = safe_http_iri(iri) if isinstance(iri, str) else None
    return f"<{safe}>" if safe is not None else None


def _safe_iri(value: str) -> str | None:
    """``value`` if it is a well-formed http(s) IRI safe to embed literally in
    SPARQL, else ``None`` — this module only ever embeds an IRI (``class_iri``,
    the caller-supplied query param) that has passed this, never a raw
    string. Delegates to :func:`asterism.subjects.safe_http_iri` — the ONE
    place the SPARQL-IRIREF-forbidden character set is defined (§0: no
    duplicated validation logic across ``subjects.py``/``subject_tools.py``/
    ``class_schema.py``)."""
    return safe_http_iri(value)


def _local_name(token: str) -> str:
    """``ex:hasCount`` / ``…#hasCount`` → ``hasCount``."""
    for sep in (":", "/", "#"):
        if sep in token:
            token = token.rsplit(sep, 1)[-1]
    return token


def _humanize(local: str) -> str:
    """Last-resort readable form of a local name (K4: never the raw identifier
    alone). ``hasItemCount`` → ``Item Count``."""
    if not local:
        return ""
    stripped = re.sub(r"^(has|is)(?=[A-Z])", "", local)
    spaced = _CAMEL_BOUNDARY.sub(" ", stripped.replace("_", " ").replace("-", " "))
    return " ".join(spaced.split())


def _fallback_label(iri: str) -> str:
    local = _local_name(iri)
    return _humanize(local) or local


def _is_iri_object(prop: PropertyView) -> bool:
    """Mirror of the design-side compiler's own ``_is_iri_object`` (in its
    ``ir2mermaid`` module; kept in sync by hand — this module does not import
    the design-side package, see ``mapping_ir_read.py``'s docstring):
    ``object_template`` defaults to IRI (unless ``object_type: literal``);
    ``column``/``columns``/``constant`` default to literal (unless
    ``object_type: iri``)."""
    if prop.object_template is not None:
        return prop.object_type != "literal"
    return prop.object_type == "iri"


def _is_numeric_datatype(datatype: str | None) -> bool:
    if not datatype:
        return False
    return _local_name(datatype).strip().lower() in _NUMERIC_LOCAL_NAMES


def _expand(term: str, prefixes: dict[str, str]) -> str:
    """CURIE → full IRI via ``prefixes``; already-expanded terms pass through
    (same rule ``api.main._ir_display_entries`` uses for the Mapping IR)."""
    prefix, sep, rest = term.partition(":")
    if sep and prefix in prefixes:
        return prefixes[prefix] + rest
    return term


async def _run_select(client: SupportsSparql, query: str) -> list[dict[str, dict[str, Any]]]:
    raw = await client.sparql_select(query)
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


def _cell(row: dict[str, dict[str, Any]], var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


async def _distinct_count(
    client: SupportsSparql, class_iri: str, predicate_iri: str, from_block: str
) -> int:
    class_ref = _ref(class_iri)
    pred_ref = _ref(predicate_iri)
    if class_ref is None or pred_ref is None:
        return 0
    q = (
        "SELECT (COUNT(DISTINCT ?v) AS ?n)\n"
        f"{from_block}"
        "WHERE { "
        f"?s a {class_ref} ; {pred_ref} ?v . "
        "}"
    )
    rows = await _run_select(client, q)
    if not rows:
        return 0
    raw = _cell(rows[0], "n")
    try:
        return int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _int_cell(row: dict[str, dict[str, Any]], var: str) -> int:
    raw = _cell(row, var)
    try:
        return int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def _class_property_stats(
    client: SupportsSparql, class_iri: str, from_block: str
) -> dict[str, dict[str, int]]:
    """§5(a) の 1 回の集計 SPARQL: このクラスの実データが各述語にいくつの値を
    持つか（``n``）・そのうち IRI がいくつか（``iris``）・数値がいくつか
    （``nums``）・distinct な値がいくつか（``distinct``）。``rdf:type`` は除く。

    このクラスがまだストアに 1 件も無ければ空 dict — 呼び出し側はこの場合
    Mapping IR / ``model.yaml`` だけで kind を決める（§5:「ストアに 0 件の
    クラス（未公開）は今までどおり IR/model.yaml から」）。
    """
    class_ref = _ref(class_iri)
    if class_ref is None:
        return {}
    q = (
        "SELECT ?p (COUNT(?o) AS ?n) (SUM(IF(isIRI(?o),1,0)) AS ?iris) "
        "(SUM(IF(isNumeric(?o),1,0)) AS ?nums) (COUNT(DISTINCT ?o) AS ?distinct)\n"
        f"{from_block}"
        f"WHERE {{ ?s a {class_ref} ; ?p ?o . FILTER(?p != {_RDF_TYPE_REF}) }} "
        "GROUP BY ?p"
    )
    out: dict[str, dict[str, int]] = {}
    for row in await _run_select(client, q):
        p = _cell(row, "p")
        if not p:
            continue
        out[p] = {
            "n": _int_cell(row, "n"),
            "iris": _int_cell(row, "iris"),
            "nums": _int_cell(row, "nums"),
            "distinct": _int_cell(row, "distinct"),
        }
    return out


def _kind_from_stats(stats: dict[str, int]) -> tuple[str, int | None]:
    """§5(a) のストア基準の判定: ``iris`` が過半なら link、``nums`` が過半なら
    quantity、それ以外は ``distinct`` が 50 以下なら category、超えれば text。"""
    n = stats["n"]
    if n > 0 and stats["iris"] * 2 > n:
        return "link", None
    if n > 0 and stats["nums"] * 2 > n:
        return "quantity", None
    distinct = stats["distinct"]
    return ("category" if distinct <= 50 else "text"), distinct


async def _kind_from_ir(
    client: SupportsSparql,
    class_iri: str,
    pred_iri: str,
    prop: PropertyView,
    datatype: str | None,
    unit: str | None,
    range_is_class: set[str],
    from_block: str,
) -> tuple[str, int | None]:
    """設計成果物（Mapping IR / ``model.yaml``）だけで kind を決める、ストア登場
    以前の唯一の経路（このクラスがまだストアに 0 件のとき、または store には
    現れているのにこの述語だけ 1 件も値が無いとき、の両方から呼ばれる）。"""
    if _is_iri_object(prop) or pred_iri in range_is_class:
        return "link", None
    if _is_numeric_datatype(datatype) or unit:
        return "quantity", None
    distinct_count = await _distinct_count(client, class_iri, pred_iri, from_block)
    return ("category" if distinct_count <= 50 else "text"), distinct_count


async def _ontology_labels(client: SupportsSparql, predicates: list[str]) -> dict[str, str]:
    """述語 IRI → ontology named graph の ``rdfs:label``（無ければキーが無い）。

    ``predicates`` は store の集計クエリが返した ``?p``（§5(c) の「ストアにだけ
    現れる述語」）— 素の SPARQL を返す store は無いはずだが、埋め込む前に
    :func:`_ref` を通す（安全でない値はその述語だけ VALUES から落ちる。
    ラベルが引けないだけで、他の述語や全体は影響を受けない）。
    """
    refs = [r for p in predicates if (r := _ref(p)) is not None]
    if not refs:
        return {}
    values = " ".join(refs)
    q = (
        f"SELECT ?p ?label WHERE {{ GRAPH ?g {{ VALUES ?p {{ {values} }} "
        f"OPTIONAL {{ ?p {_RDFS_LABEL_REF} ?label }} }} "
        f'FILTER(STRSTARTS(STR(?g), "{ONTOLOGY_GRAPH_BASE}")) }} ORDER BY ?p ?label'
    )
    out: dict[str, str] = {}
    for row in await _run_select(client, q):
        p = _cell(row, "p")
        label = _cell(row, "label")
        if p and label and p not in out:
            out[p] = label
    return out


# ---------------------------------------------------------------------------
# ① registry の mapping.yaml から見つける（このクラスを宣言している promoted
#    データセット、複数あれば promoted_at が新しい方）。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Match:
    dataset_id: str
    meta: dict[str, Any]
    prefixes: dict[str, str]
    maps: tuple[TriplesMapView, ...]


def _promoted_metas(registry_root: Path) -> list[dict[str, Any]]:
    """Every promoted dataset's ``meta.json``, newest ``promoted_at`` first."""
    if not registry_root.is_dir():
        return []
    metas: list[dict[str, Any]] = []
    for child in sorted(registry_root.iterdir()):
        if not child.is_dir() or not _ID_RE.fullmatch(child.name):
            continue
        meta_path = child / _META_FILE
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(meta, dict) and meta.get("promoted"):
            metas.append(meta)
    metas.sort(key=lambda m: str(m.get("promoted_at") or ""), reverse=True)
    return metas


def _find_owning_dataset(registry_root: Path, class_iri: str) -> _Match | None:
    for meta in _promoted_metas(registry_root):
        dataset_id = str(meta.get("id") or "")
        if not _ID_RE.fullmatch(dataset_id):
            continue
        mapping_path = registry_root / dataset_id / _MAPPING_FILE
        if not mapping_path.is_file():
            continue
        try:
            ir: MappingIRView = read_mapping_ir(mapping_path.read_text(encoding="utf-8"))
        except Exception:  # best-effort registry scan (MappingIRReadError, or a
            # bad dataset's mapping.yaml must not hide every other dataset's class).
            logger.warning("class_schema: %s's mapping.yaml could not be parsed", dataset_id)
            continue
        prefixes = dict(BUILTIN_PREFIXES) | dict(ir.prefixes)
        matched = tuple(
            tm
            for tm in ir.maps
            if class_iri in {_expand(c, prefixes) for c in tm.subject_classes}
        )
        if matched:
            return _Match(dataset_id=dataset_id, meta=meta, prefixes=prefixes, maps=matched)
    return None


def _load_display_meta(dest: Path) -> list[dict[str, Any]]:
    path = dest / _DISPLAY_META_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    edits = data.get("edits") if isinstance(data, dict) else None
    return [e for e in edits or [] if isinstance(e, dict) and e.get("predicate")]


def _index_display_meta(
    edits: list[dict[str, Any]], prefixes: dict[str, str]
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    """``{(predicate_iri, column): edit}`` and ``{predicate_iri: edit}``.

    Keyed by (predicate, column) first — same reasoning as
    ``api.main._ir_display_by_column``: a predicate bound by more than one map
    must not let one map's correction bleed into another's row.
    """
    by_column: dict[tuple[str, str], dict[str, Any]] = {}
    by_predicate: dict[str, dict[str, Any]] = {}
    for edit in edits:
        raw_pred = str(edit.get("predicate") or "")
        if not raw_pred:
            continue
        pred_iri = raw_pred if raw_pred.startswith(("http://", "https://")) else _expand(
            raw_pred, prefixes
        )
        column = str(edit.get("column") or "")
        if column:
            by_column[(pred_iri, column)] = edit
        by_predicate[pred_iri] = edit
    return by_column, by_predicate


def _read_artifact(dest: Path, filename: str) -> str:
    path = dest / filename
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _range_is_class_set(model_yaml_text: str, prefixes: dict[str, str]) -> set[str]:
    """Predicate IRIs whose ``model.yaml`` ``range:`` resolves to a class —
    §2's third ``kind`` rule ("range がクラス") for designs whose Mapping IR
    property row is a bare column reference (no ``object_template``)."""
    if not model_yaml_text.strip():
        return set()
    try:
        graph = project_model_yaml(model_yaml_text, dict(STANDARD_PREFIXES) | dict(prefixes))
    except Exception:  # best-effort, never blocks the response.
        return set()
    return {str(s) for s in graph.subjects(_RDFS_RANGE, None)}


def _load_tools(dest: Path) -> list[dict[str, Any]]:
    path = dest / _QUERY_TOOLS_FILE
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    raw_tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(raw_tools, list):
        return []
    return [annotate_output_kind(t) for t in raw_tools if isinstance(t, dict)]


def _model_yaml_class_label(registry_root: Path, class_iri: str) -> str | None:
    """registry の全 promoted データセットの ``model.yaml`` から
    ``classes.<curie>.label`` を探す（最初に見つかったもの — ``_promoted_metas``
    と同じ「新しい方が先」の並び）。CURIE の展開は同じデータセットの
    ``mapping.yaml`` の ``prefixes`` を（あれば）使う。"""
    for meta in _promoted_metas(registry_root):
        dataset_id = str(meta.get("id") or "")
        if not _ID_RE.fullmatch(dataset_id):
            continue
        dest = registry_root / dataset_id
        model_text = _read_artifact(dest, _MODEL_FILE)
        if not model_text.strip():
            continue
        prefixes = dict(STANDARD_PREFIXES)
        mapping_text = _read_artifact(dest, _MAPPING_FILE)
        if mapping_text.strip():
            try:
                ir = read_mapping_ir(mapping_text)
            except Exception:  # best-effort registry scan
                pass
            else:
                prefixes = dict(prefixes) | dict(BUILTIN_PREFIXES) | dict(ir.prefixes)
        try:
            data = yaml.safe_load(model_text)
        except yaml.YAMLError:
            continue
        classes = data.get("classes") if isinstance(data, dict) else None
        if not isinstance(classes, dict):
            continue
        for curie, spec in classes.items():
            if not isinstance(spec, dict):
                continue
            label = spec.get("label")
            if label and _expand(str(curie), prefixes) == class_iri:
                return str(label)
    return None


async def _ontology_class_label(client: SupportsSparql, class_iri: str) -> str | None:
    class_ref = _ref(class_iri)
    if class_ref is None:
        return None
    q = (
        "SELECT ?label WHERE { GRAPH ?g { "
        f"{class_ref} a {_RDFS_CLASS_REF} . "
        f"OPTIONAL {{ {class_ref} {_RDFS_LABEL_REF} ?label }} "
        "} "
        f'FILTER(STRSTARTS(STR(?g), "{ONTOLOGY_GRAPH_BASE}")) '
        "} ORDER BY ?label"
    )
    for row in await _run_select(client, q):
        v = _cell(row, "label")
        if v:
            return v
    return None


async def class_label(
    client: SupportsSparql, registry_root: Path | None, class_iri: str
) -> str:
    """1 件の種類の表示名（契約メモ §3。プロパティのラベル優先順位（§2/§6）とは
    別の、クラス名専用の優先順位）: registry の ``model.yaml``
    （``classes.<curie>.label``）→ ontology named graph の ``rdfs:label`` →
    ローカル名の人間化（K4: 生の識別子を見せない）。

    ``subjects/resolve`` の ``class_label``・``sets/resolve`` の
    ``title.class_label``・``place/inspect`` の ``signature_label``・
    ``class_schema`` の ``label`` は全てこの 1 関数を呼ぶ（実機所見: registry の
    ``model.yaml`` に ``periodic:Element: {label: "元素"}`` があるのに、
    Mapping IR のローカル名 "Element" が返っていた）。
    """
    safe_class_iri = _safe_iri(class_iri)
    if safe_class_iri is None:
        return _fallback_label(class_iri)
    if registry_root is not None:
        label = _model_yaml_class_label(registry_root, safe_class_iri)
        if label:
            return label
    label = await _ontology_class_label(client, safe_class_iri)
    if label:
        return label
    return _fallback_label(safe_class_iri)


async def _build_property(
    client: SupportsSparql,
    class_iri: str,
    prop: PropertyView,
    prefixes: dict[str, str],
    subject_columns: set[str],
    range_is_class: set[str],
    by_column: dict[tuple[str, str], dict[str, Any]],
    by_predicate: dict[str, dict[str, Any]],
    from_block: str,
    stats: dict[str, dict[str, int]],
) -> dict[str, Any] | None:
    """1 行の性質（§5）: ``kind`` はストアの実データ（``stats``、1 クラス 1 回の
    集計 SPARQL）で決める — このクラスが 1 件もストアに無い、またはこの述語が
    その集計に 1 件も出ていないときだけ Mapping IR / ``model.yaml`` へ倒す
    （:func:`_kind_from_ir`）。identifier（subject template の列）はどちらの
    場合でも最優先（ストアにまだ出ていない設計中の主語キーでも identifier）。
    """
    if not prop.predicate:
        return None
    pred_iri = _expand(prop.predicate, prefixes)
    if _ref(pred_iri) is None:
        # mapping.yaml の predicate が SPARQL に安全に埋め込める IRI ではない
        # （壊れた／悪意ある IR）— この 1 行だけ落とす。スキーマ全体は返す。
        logger.warning(
            "class_schema: dropping property with unsafe predicate IRI for %s", class_iri
        )
        return None
    column = prop.column
    edit = by_column.get((pred_iri, column or "")) or by_predicate.get(pred_iri) or {}

    label = edit.get("label") or prop.label or _fallback_label(pred_iri)
    unit = _normalize_unit(edit.get("unit") or prop.unit or None)
    datatype = prop.datatype

    if column and column in subject_columns:
        kind, distinct_count = "identifier", None
    else:
        s = stats.get(pred_iri)
        if s is not None and s["n"] > 0:
            kind, distinct_count = _kind_from_stats(s)
        else:
            kind, distinct_count = await _kind_from_ir(
                client, class_iri, pred_iri, prop, datatype, unit, range_is_class, from_block
            )

    return {
        "iri": pred_iri,
        "label": label,
        "kind": kind,
        "datatype": datatype,
        "unit": unit,
        "quantity_kind": None,
        "column": column,
        "distinct_count": distinct_count,
    }


async def _schema_from_registry(
    client: SupportsSparql, registry_root: Path, class_iri: str, match: _Match
) -> dict[str, Any]:
    dest = registry_root / match.dataset_id
    display_edits = _load_display_meta(dest)
    by_column, by_predicate = _index_display_meta(display_edits, match.prefixes)
    model_yaml_text = _read_artifact(dest, _MODEL_FILE)
    range_is_class = _range_is_class_set(model_yaml_text, match.prefixes)

    graphs = await canonical_graphs(client)
    # ストア基準の kind/properties（§5）は、この class を「所有」している
    # データセット（newest promoted_at が勝つ ``match``）の canonical グラフ
    # だけを見る — 同じクラスを宣言する 2 つ目のデータセットが後から
    # promote されても、その述語がこのクラスの properties に紛れ込まない
    # （既存の「newest promoted_at 勝ち」不変条件を壊さない）。
    own_graphs = [g for g in graphs if dataset_id_of_canonical_graph(g) == match.dataset_id]
    from_block = canonical_from_clauses(graphs)
    stats = await _class_property_stats(client, class_iri, canonical_from_clauses(own_graphs))

    seen: set[tuple[str, str]] = set()
    seen_predicates: set[str] = set()
    properties: list[dict[str, Any]] = []
    for tm in match.maps:
        subject_columns = set(tm.subject_columns)
        for prop in tm.properties:
            if not prop.predicate:
                continue
            pred_iri = _expand(prop.predicate, match.prefixes)
            key = (pred_iri, prop.column or "")
            if key in seen:
                continue
            seen.add(key)
            seen_predicates.add(pred_iri)
            entry = await _build_property(
                client,
                class_iri,
                prop,
                match.prefixes,
                subject_columns,
                range_is_class,
                by_column,
                by_predicate,
                from_block,
                stats,
            )
            if entry is not None:
                properties.append(entry)

    # §5(c): properties は「ストアに現れた述語（rdf:type を除く）∪ IR の行」の
    # 和集合。ストアにだけ現れる述語（IR がまだ拾っていない列）は IRI 辞書順で
    # 末尾に追加する（並びは常に「IR の順 → 残りは IRI 辞書順」で決定論）。
    extra_predicates = sorted(p for p in stats if p not in seen_predicates)
    if extra_predicates:
        onto_labels = await _ontology_labels(client, extra_predicates)
        for pred_iri in extra_predicates:
            kind, distinct_count = _kind_from_stats(stats[pred_iri])
            properties.append(
                {
                    "iri": pred_iri,
                    "label": onto_labels.get(pred_iri) or _fallback_label(pred_iri),
                    "kind": kind,
                    "datatype": None,
                    "unit": None,
                    "quantity_kind": None,
                    "column": None,
                    "distinct_count": distinct_count,
                }
            )

    version = match.meta.get("version")
    snapshot = f"v{version}" if isinstance(version, int) and version > 0 else None

    return {
        "class_iri": class_iri,
        "label": await class_label(client, registry_root, class_iri),
        "dataset_id": match.dataset_id,
        "snapshot": snapshot,
        "properties": properties,
        "tools": _load_tools(dest),
    }


# ---------------------------------------------------------------------------
# ④ どの registry データセットもこのクラスを宣言していないとき: promote 時に
#    投影された ontology named graph を直接読む（predicates とラベルだけ）。
# ---------------------------------------------------------------------------


async def _sample_kind(
    client: SupportsSparql, class_iri: str, predicate_iri: str, from_block: str
) -> tuple[str, int | None]:
    """Best-effort kind for a property this module has no design metadata for
    (no Mapping IR row) — inspect one live value's SPARQL-JSON binding shape."""
    class_ref = _ref(class_iri)
    pred_ref = _ref(predicate_iri)
    if class_ref is None or pred_ref is None:
        return "text", 0
    sq = (
        f"SELECT ?v\n{from_block}"
        f"WHERE {{ ?s a {class_ref} ; {pred_ref} ?v . }} LIMIT 1"
    )
    rows = await _run_select(client, sq)
    if not rows:
        return "text", 0
    node = rows[0].get("v") or {}
    if node.get("type") == "uri":
        return "link", None
    if _is_numeric_datatype(node.get("datatype")):
        return "quantity", None
    n = await _distinct_count(client, class_iri, predicate_iri, from_block)
    return ("category" if n <= 50 else "text"), n


async def _schema_from_ontology_graph(
    client: SupportsSparql, registry_root: Path | None, class_iri: str
) -> dict[str, Any] | None:
    class_ref = _ref(class_iri)
    if class_ref is None:
        return None
    q = (
        "SELECT ?g ?label WHERE { GRAPH ?g { "
        f"{class_ref} a {_RDFS_CLASS_REF} . "
        f"OPTIONAL {{ {class_ref} {_RDFS_LABEL_REF} ?label }} "
        "} "
        f'FILTER(STRSTARTS(STR(?g), "{ONTOLOGY_GRAPH_BASE}")) '
        "} ORDER BY ?g ?label"
    )
    rows = await _run_select(client, q)
    if not rows:
        return None
    graph_iri = _cell(rows[0], "g") or ""
    dataset_id = (
        graph_iri[len(ONTOLOGY_GRAPH_BASE) :] if graph_iri.startswith(ONTOLOGY_GRAPH_BASE) else None
    )

    # ``graph_iri`` は store が返した ?g（ontology graph の名前）— STRSTARTS で
    # ONTOLOGY_GRAPH_BASE 接頭辞は確認済みだが、それでも埋め込み前に
    # :func:`_ref` を通す（このクラスは「未公開」として扱う＝安全側）。
    graph_ref = _ref(graph_iri)
    if graph_ref is None:
        return None

    pq = (
        f"SELECT ?p ?label WHERE {{ GRAPH {graph_ref} {{ "
        f"?p a {_RDF_PROPERTY_REF} ; {_RDFS_DOMAIN_REF} {class_ref} . "
        f"OPTIONAL {{ ?p {_RDFS_LABEL_REF} ?label }} "
        "} } ORDER BY ?p ?label"
    )
    prows = await _run_select(client, pq)

    graphs = await canonical_graphs(client)
    from_block = canonical_from_clauses(graphs)

    seen_p: set[str] = set()
    properties: list[dict[str, Any]] = []
    for row in prows:
        pred_iri = _cell(row, "p")
        if not pred_iri or pred_iri in seen_p or _ref(pred_iri) is None:
            continue
        seen_p.add(pred_iri)
        label = _cell(row, "label") or _fallback_label(pred_iri)
        kind, distinct_count = await _sample_kind(client, class_iri, pred_iri, from_block)
        properties.append(
            {
                "iri": pred_iri,
                "label": label,
                "kind": kind,
                "datatype": None,
                "unit": None,
                "quantity_kind": None,
                "column": None,
                "distinct_count": distinct_count,
            }
        )

    tools: list[dict[str, Any]] = []
    if registry_root is not None and dataset_id and _ID_RE.fullmatch(dataset_id):
        tools = _load_tools(registry_root / dataset_id)

    return {
        "class_iri": class_iri,
        "label": await class_label(client, registry_root, class_iri),
        "dataset_id": dataset_id,
        "snapshot": None,
        "properties": properties,
        "tools": tools,
    }


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


async def class_schema(
    client: SupportsSparql, registry_root: Path | None, class_iri: str
) -> dict[str, Any] | None:
    """1 件の種類（class）のスキーマ。無ければ ``None``（API 側は 404）。

    §2 の優先順位: ① promoted データセットの ``mapping.yaml``（同じクラスを
    複数が宣言していれば ``promoted_at`` が新しい方）② ``display-meta.json`` の
    上書き ③ ``model.yaml`` の ``range`` による link 判定 ④ どれも無ければ
    store に投影済みの ontology named graph を直接読む。
    """
    safe_class_iri = _safe_iri(class_iri)
    if safe_class_iri is None:
        return None

    if registry_root is not None:
        match = _find_owning_dataset(registry_root, safe_class_iri)
        if match is not None:
            return await _schema_from_registry(client, registry_root, safe_class_iri, match)

    return await _schema_from_ontology_graph(client, registry_root, safe_class_iri)
