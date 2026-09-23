"""エージェント束の生成（契約メモ §3・ADR object-cards-ui.md O12/O23、担当
D1-export）。

``POST /api/subjects/export``（api 層・``export_routes.py``）が呼ぶ、たった
1 つの入口 :func:`build_export_bundle` を持つ。1 件／絞り込みページがいま
並べているカードを実行し、その結果だけから自己完結した zip を組み立てる:

* ``facts/facts.trig`` / ``facts/control.trig`` — 使った事実の切り出し
  （版グラフごとの ``GRAPH`` ブロック）と、束の store でそれを読むのに要る
  control グラフの三つ組（``liveGraph`` ポインタ・``promoted`` 印）。
* ``tools/<dataset_id>/`` — このページのカードが使った宣言ツールだけ。
* ``tools/_builtin/`` — 組み込みツールを、subject を焼き込んだパラメータ
  なし宣言ツールに落としたもの。
* ``cards/<card_id>.json`` / ``.mmd`` — 表示に使った ``CardSpec``。
* ``materials.json`` / ``AGENT.md`` / ``mcp.json`` / ``README.md``。

**Asterism への HTTP 依存を持たない**束を作る（§0/§5.7）: 実行系は同梱 stdio
MCP（``asterism-agent serve`` — 別ファイル ``mcp/src/asterism_mcp/agent_cli.py``、
D1-serve）が own の Oxigraph を起動して読む。ここで書き出す SPARQL は普通の
文字列で、実行はしない（生成コード実行ゼロ・§0）。

**配れる判定は保守側**（O13）: ``share="shareable"`` は各カードの
``shareable`` フラグ（:mod:`asterism.materials` の判定そのもの）で絞り込み、
1 枚も残らなければ :class:`NotShareableError`（api 層で 409 に写す）。

LLM 呼び出しは無い（``AGENT.md`` も :mod:`asterism.agent_doc` の決定論
テンプレート）。分野固有の名詞はここに書かない。
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml

from asterism import class_schema as class_schema_mod
from asterism import subject_tools, substrate
from asterism import subjects as subjects_mod
from asterism.agent_doc import AgentDocCard, AgentDocSubject, render_agent_doc
from asterism.default_view import default_view_for
from asterism.materials import Material
from asterism.materials import shareable as _materials_shareable
from asterism.materials import shareable_reasons as _materials_shareable_reasons
from asterism.mermaid_flow import to_mermaid
from asterism.query_tools import (
    QueryTool,
    QueryToolError,
    lint_query_tool,
    load_query_tools,
    parse_query_tools,
)
from asterism.substrate import SupportsSparql

logger = logging.getLogger(__name__)

__all__ = [
    "SUBJECT_KINDS",
    "ExportError",
    "ExportedBundle",
    "NotShareableError",
    "build_export_bundle",
]

#: ``share`` の語彙（契約メモ §3 body）。
SHARE_MODES: tuple[str, ...] = ("full", "shareable")
#: ``lang`` の語彙。未知の値は :mod:`asterism.agent_doc` 側で ``ja`` に倒れる。
LANGS: tuple[str, ...] = ("ja", "en")
SUBJECT_KINDS: tuple[str, ...] = ("individual", "set")

#: facts.trig に書く三つ組の上限（契約メモ §3: 超えたら打ち切り）。
DEFAULT_MAX_TRIPLES = 20_000

#: 組み込みツール（subject_tools の 5 関数）-> 束での名前・パラメータ束縛の要否。
#: subject_flow は宣言できない output_kind（flow・DECLARABLE_OUTPUT_KINDS の
#: 外）なので対象外 — その代わり cards/<id>.mmd が直接 flow を運ぶ。
_BUILTIN_TOOL_NAMES: dict[str, str] = {
    "subject_facts": "page_facts",
    "subject_sources": "page_sources",
    "set_members": "page_members",
    "set_breakdown": "page_breakdown",
    "set_count": "page_count",
}

_UNSAFE_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


class ExportError(Exception):
    """束の生成が続けられない（api 層が 4xx に写す）。"""


class NotShareableError(ExportError):
    """``share="shareable"`` で 1 枚も配れるカードが無かった（→ 409）。"""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(f"no card is shareable: {reasons}")


@dataclass(frozen=True)
class ExportedBundle:
    """:func:`build_export_bundle` の返り値。"""

    zip_bytes: bytes
    filename: str
    slug: str
    truncated: bool


# ----------------------------------------------------------------------------
# small local helpers (§0 の既存の流儀: 担当ファイルをまたぐ private ヘルパの
# 共有はせず、必要な分だけ手元に持つ — subject_tools.py 冒頭のコメント参照)
# ----------------------------------------------------------------------------


def _rows(raw: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return list(results.get("bindings", []) if isinstance(results, dict) else [])


def _cell_value(row: dict[str, dict[str, Any]], var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


def _slugify(label: str) -> str:
    """人が読めるラベル -> zip のトップフォルダ名（英数字とハイフンだけ）。
    非 ASCII だけのラベル（例: 日本語のみ）は安全な文字が 1 つも残らないの
    で、契約メモ §3 の「無ければ subject」どおり既定へ落ちる。"""
    lowered = label.strip().lower()
    slug = _UNSAFE_SLUG_CHARS.sub("-", lowered).strip("-")
    return slug or "subject"


def _now_iso(now: datetime | None) -> str:
    dt = now or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _asterism_version() -> str:
    """リポジトリ直下の ``VERSION`` ファイル（無ければ ``"unknown"``）。best
    effort — 束の生成そのものを壊さない。"""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "VERSION"
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                return "unknown"
            return text or "unknown"
    return "unknown"


# ----------------------------------------------------------------------------
# subject / class label (cards_routes._entity_label と同じ規則— §2: 共通の
# 優先順位関数 subjects.pick_label 経由。ここは api 層に依存しないための小さな
# 複製 — §0 の "担当ファイル別・共有 module を増やさない" 既存の流儀)
# ----------------------------------------------------------------------------


async def _entity_label(client: SupportsSparql, iri: str) -> str:
    canon = await substrate.canonical_graphs(client)
    onto = await substrate.ontology_graphs(client)
    graphs = sorted(set(canon) | set(onto))
    labels = await subject_tools._label_lookup(client, graphs, {iri})
    return labels.get(iri) or subject_tools._local_name(iri)


async def _subject_label_and_class(
    client: SupportsSparql, registry_root: Path | str | None, subject: dict[str, Any]
) -> tuple[str, str | None]:
    """``(表示ラベル, 種類のラベル)``。個体は自分自身のラベル、絞り込みは
    種類のラベルをそのままページの見出しに使う（§3: 「set は <title>」の
    簡約 — 条件節までの完全な文は組まない。設計判断・逸脱として報告する）。
    """
    if subject.get("kind") == "individual":
        iri = subject["iri"]
        types = await subject_tools.subject_types(client, iri)
        class_iri = await subject_tools.pick_class_iri(client, types)
        class_label = (
            await class_schema_mod.class_label(client, registry_root, class_iri)
            if class_iri
            else None
        )
        label = await _entity_label(client, iri)
        return label, class_label
    spec = subject["spec"]
    class_label = await class_schema_mod.class_label(client, registry_root, spec["class"])
    return class_label, class_label


# ----------------------------------------------------------------------------
# dataset meta (tools/<id>/meta.json の最小情報)
# ----------------------------------------------------------------------------


def _read_dataset_meta(registry_root: Path | str | None, dataset_id: str) -> dict[str, Any]:
    if registry_root is None:
        return {}
    meta_path = Path(registry_root) / dataset_id / "meta.json"
    if not meta_path.is_file():
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return meta if isinstance(meta, dict) else {}


async def _control_info(
    client: SupportsSparql, dataset_ids: set[str]
) -> dict[str, dict[str, str | None]]:
    """dataset_id -> ``{canonical_graph, live_graph, effective_graph}``
    （control グラフの ``promoted``/``liveGraph`` を読む — facts/control.trig
    と tools/<id>/meta.json の両方が要る情報を 1 回のラウンドトリップで）。"""
    if not dataset_ids:
        return {}
    keys = {
        did: safe
        for did in sorted(dataset_ids)
        if (safe := subjects_mod.safe_iri(substrate.canonical_graph_iri(did))) is not None
    }
    if not keys:
        return {}
    values = " ".join(f"<{iri}>" for iri in keys.values())
    query = (
        "SELECT ?c ?lg WHERE { "
        f"GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f"VALUES ?c {{ {values} }} "
        f'?c <{substrate.STATUS_PREDICATE}> "{substrate.STATUS_PROMOTED}" . '
        f"OPTIONAL {{ ?c <{substrate.LIVE_GRAPH_PREDICATE}> ?lg }} "
        "} }"
    )
    raw = await client.sparql_select(query)
    live_by_key: dict[str, str | None] = {}
    for row in _rows(raw):
        c = _cell_value(row, "c")
        if c is None:
            continue
        live_by_key[c] = subjects_mod.safe_iri(_cell_value(row, "lg"))
    out: dict[str, dict[str, str | None]] = {}
    for did, key_iri in keys.items():
        live = live_by_key.get(key_iri)
        out[did] = {
            "canonical_graph": key_iri,
            "live_graph": live,
            "effective_graph": live or key_iri,
        }
    return out


# ----------------------------------------------------------------------------
# facts.trig / control.trig
# ----------------------------------------------------------------------------


def _escape_turtle_literal(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _turtle_term(node: dict[str, Any]) -> str | None:
    node_type = node.get("type")
    value = node.get("value")
    if value is None:
        return None
    if node_type == "uri":
        safe = subjects_mod.safe_iri(value)
        return f"<{safe}>" if safe is not None else None
    if node_type == "bnode":
        # 束はスナップショット。ブランクノードの同一性は保たなくてよい —
        # 決定論のために subject/object どちらでも安定な文字列にする。
        local = re.sub(r"[^A-Za-z0-9_]", "_", str(value)) or "b"
        return f"_:{local}"
    literal = _escape_turtle_literal(str(value))
    lang = node.get("xml:lang")
    if lang:
        return f'"{literal}"@{lang}'
    datatype = node.get("datatype")
    if datatype and datatype != "http://www.w3.org/2001/XMLSchema#string":
        safe_dt = subjects_mod.safe_iri(datatype)
        if safe_dt is not None:
            return f'"{literal}"^^<{safe_dt}>'
    return f'"{literal}"'


def _iris_from_card(result: dict[str, Any]) -> set[str]:
    """カードの結果に現れる IRI（``role: subject`` と ``_iri`` 終端の値、＋
    flow カードなら prov_graph の節）。契約メモ §3。"""
    out: set[str] = set()
    item_map = result.get("item") or {}
    keys_of_interest = {
        key
        for key, spec in item_map.items()
        if spec.get("role") == "subject" or str(spec.get("var", "")).endswith("_iri")
    }
    for row in result.get("items") or []:
        if not isinstance(row, dict):
            continue
        for key in keys_of_interest:
            value = row.get(key)
            if isinstance(value, str) and value:
                out.add(value)
    graph = result.get("graph")
    if isinstance(graph, dict):
        for node in graph.get("nodes") or []:
            node_id = node.get("id") if isinstance(node, dict) else None
            if isinstance(node_id, str) and node_id:
                out.add(node_id)
    return out


async def _describe_one_hop(
    client: SupportsSparql, allowed_graphs: list[str], iris: set[str]
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """``[(graph, s, p, o_node), …]`` — ``iris`` それぞれの 1 hop 分の外向き
    三つ組を、``allowed_graphs`` の範囲だけで（version graph ごとの
    ``GRAPH`` を保ったまま）読む。"""
    if not iris or not allowed_graphs:
        return []
    safe_iris = sorted({v for i in iris if (v := subjects_mod.safe_iri(i)) is not None})
    if not safe_iris:
        return []
    named = substrate.canonical_from_clauses(allowed_graphs, named=True)
    values = " ".join(f"<{i}>" for i in safe_iris)
    query = (
        f"SELECT ?g ?s ?p ?o\n{named}"
        f"WHERE {{ GRAPH ?g {{ VALUES ?s {{ {values} }} ?s ?p ?o }} }}"
    )
    raw = await client.sparql_select(query)
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for row in _rows(raw):
        g = _cell_value(row, "g")
        s = _cell_value(row, "s")
        p = _cell_value(row, "p")
        o = row.get("o")
        if not g or not s or not p or o is None:
            continue
        out.append((g, s, p, o))
    return out


async def _label_triples(
    client: SupportsSparql, allowed_graphs: list[str], iris: set[str]
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """発見した IRI 値それぞれの表示ラベル述語（:data:`subjects.LABEL_PREDICATES`）
    の三つ組も添える — facts.trig だけを読む相手にも人が読める名前が残る
    ように（契約メモ §3「?o のラベル述語」）。"""
    if not iris or not allowed_graphs:
        return []
    safe_iris = sorted({v for i in iris if (v := subjects_mod.safe_iri(i)) is not None})
    if not safe_iris:
        return []
    named = substrate.canonical_from_clauses(allowed_graphs, named=True)
    values = " ".join(f"<{i}>" for i in safe_iris)
    preds = " ".join(f"<{p}>" for p in subjects_mod.LABEL_PREDICATES)
    query = (
        f"SELECT ?g ?s ?p ?o\n{named}"
        f"WHERE {{ GRAPH ?g {{ VALUES ?s {{ {values} }} VALUES ?p {{ {preds} }} ?s ?p ?o }} }}"
    )
    raw = await client.sparql_select(query)
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for row in _rows(raw):
        g = _cell_value(row, "g")
        s = _cell_value(row, "s")
        p = _cell_value(row, "p")
        o = row.get("o")
        if not g or not s or not p or o is None:
            continue
        out.append((g, s, p, o))
    return out


def _triple_sort_key(t: tuple[str, str, str, dict[str, Any]]) -> tuple[str, ...]:
    g, s, p, o = t
    return (
        g,
        s,
        p,
        str(o.get("type") or ""),
        str(o.get("value") or ""),
        str(o.get("xml:lang") or ""),
        str(o.get("datatype") or ""),
    )


def _render_facts_trig(
    triples: list[tuple[str, str, str, dict[str, Any]]], *, max_triples: int
) -> tuple[str, bool]:
    """三つ組（重複あり得る）を版グラフごとの ``GRAPH`` ブロックへ。決定論
    （sorted+dedup）。``max_triples`` を超えたら切り詰め、``truncated=True``。
    """
    seen: set[tuple[str, ...]] = set()
    unique: list[tuple[str, str, str, dict[str, Any]]] = []
    for t in sorted(triples, key=_triple_sort_key):
        key = _triple_sort_key(t)
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)
    truncated = len(unique) > max_triples
    unique = unique[:max_triples]
    by_graph: dict[str, list[str]] = {}
    for g, s, p, o in unique:
        safe_s = subjects_mod.safe_iri(s)
        safe_p = subjects_mod.safe_iri(p)
        term = _turtle_term(o)
        if safe_s is None or safe_p is None or term is None:
            continue
        by_graph.setdefault(g, []).append(f"  <{safe_s}> <{safe_p}> {term} .")
    lines: list[str] = []
    for g in sorted(by_graph):
        lines.append(f"GRAPH <{g}> {{")
        lines.extend(sorted(by_graph[g]))
        lines.append("}")
    return "\n".join(lines) + ("\n" if lines else ""), truncated


def _render_control_trig(control: dict[str, dict[str, str | None]]) -> str:
    """束に含めた各データセットの ``canonical/{id}`` -> ``liveGraph`` ->
    版グラフの三つ組 ＋ ``promoted`` 印（契約メモ §3: これが無いと束の store
    で宣言ツールが 0 件を返す — :func:`asterism.substrate.canonical_merge_query`
    / ``readable_graph_iris`` が control グラフの ``promoted``/``liveGraph``
    を読むため）。"""
    lines: list[str] = []
    for did in sorted(control):
        info = control[did]
        key = subjects_mod.safe_iri(info["canonical_graph"])
        if key is None:
            continue
        lines.append(f"  <{key}> <{substrate.STATUS_PREDICATE}> \"{substrate.STATUS_PROMOTED}\" .")
        live = subjects_mod.safe_iri(info.get("live_graph"))
        if live is not None:
            lines.append(f"  <{key}> <{substrate.LIVE_GRAPH_PREDICATE}> <{live}> .")
    if not lines:
        return ""
    return f"GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{\n" + "\n".join(sorted(lines)) + "\n}\n"


# ----------------------------------------------------------------------------
# tools/_builtin — 組み込みツールを、subject を焼き込んだパラメータなし宣言
# ツールに落とす
# ----------------------------------------------------------------------------


#: subject_tools の組み込みツールが自前で組む SPARQL は必ず
#: ``asterism.substrate.canonical_from_clauses`` の出力（``FROM <g>\n`` /
#: ``FROM NAMED <g>\n`` を、SELECT 行の直後・WHERE の手前に、1 行 1 節で
#: 連続して差し込む）をそのまま埋め込む — 他のどの行もこの形にはならない
#: （IRI を含む行は必ず ``<`` の手前に別の SPARQL トークンが付く）。束に
#: 書く query はこの FROM/FROM NAMED 節を持たない素の問い合わせでなければ
#: ならない（契約メモ §3 追補: 束の store でこの query を動かすのは
#: ``asterism-agent serve``＝実行時に自分の版グラフで FROM を注入する側の
#: 仕事。書き出し時点で「いま繋がっている実 Asterism の」版グラフを列挙
#: した FROM を焼き込むと、束にない他データセットの版グラフを含むままに
#: なり、束の store（許可リストがその束のデータセットしかない）が
#: ``canonical_merge_query`` の「呼び手の FROM は許可リストに無ければ
#: 拒否」規則で毎回弾く）。
_FROM_CLAUSE_LINE_RE = re.compile(r"(?m)^FROM(?: NAMED)? <[^>\n]*>\n")


def _strip_from_clauses(query: str) -> str:
    """``canonical_from_clauses`` が差し込んだ ``FROM``/``FROM NAMED`` 行を
    落とした、束に書いてよい素の問い合わせ。"""
    return _FROM_CLAUSE_LINE_RE.sub("", query)


#: 組み込みツールの ``result["item"]`` は「加工後（ラベル付与・重複除去
#: 済み）の行」の形を記述している — ``subject_facts`` の ``property``/
#: ``value_iri`` のように、生の SPARQL の SELECT 射影には存在しない列を
#: 指すキーもある（Python 側の後処理でだけ作られる）。束に書く宣言ツール
#: は ``query`` に **生の bare SPARQL**（``tool_raw`` の元）をそのまま
#: 置くので、``result.item`` の ``var`` は生クエリの SELECT 射影変数と
#: 一致していなければならない（実機所見: ずれていると全行が
#: ``None`` になる）。ここが唯一の対応表 — bundle 名 -> ``{item key:
#: ItemSpec}``（各 subject_tools 関数の SELECT を読んで決定論的に対応
#: させたもの。実際の変数名は下記コメントに `SELECT` 行を引用する）。
#:
#: * ``page_facts``     <- subject_facts    ``SELECT DISTINCT ?p ?o``
#: * ``page_sources``   <- subject_sources  ``SELECT ?g (COUNT(*) AS ?cnt)``
#: * ``page_members``   <- set_members      ``SELECT ?s`` (+ ``?value`` は
#:   ``order_by`` があるときだけ射影される — 無い変数は下で自動的に外す)
#: * ``page_breakdown`` <- set_breakdown    ``SELECT ?cat (COUNT(DISTINCT ?s) AS ?cnt)``
#: * ``page_count``     <- set_count        ``SELECT (COUNT(DISTINCT ?s) AS ?value)``
_BUILTIN_ITEM_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "page_facts": {
        "property_iri": {"var": "p", "number": False},
        "value": {"var": "o", "number": False, "role": "value"},
    },
    "page_sources": {
        # subject_sources の「加工後」の category は「ラベル (版)」の
        # 合成文字列だが、生クエリはグラフ IRI（?g）しか射影しない —
        # 束では生の版グラフ IRI をそのまま category に出す（保守側）。
        "category": {"var": "g", "number": False, "role": "category"},
        "count": {"var": "cnt", "number": True, "role": "count"},
    },
    "page_members": {
        "subject_iri": {"var": "s", "number": False, "role": "subject"},
        "value": {"var": "value", "number": True, "role": "value"},
    },
    "page_breakdown": {
        "category": {"var": "cat", "number": False, "role": "category"},
        "count": {"var": "cnt", "number": True, "role": "count"},
    },
    "page_count": {
        "value": {"var": "value", "number": True, "role": "value"},
    },
}

#: SELECT 節（``PREFIX``/``FROM``/``FROM NAMED`` の後・``WHERE`` の前）に
#: 現れる変数名を拾う（``AS ?alias`` の別名も ``?`` トークンとして拾える
#: ので、集計クエリの alias も同じ正規表現で検出できる）。
_SELECT_CLAUSE_RE = re.compile(r"SELECT\b(.*?)(?:\nFROM\b|\nWHERE\b)", re.IGNORECASE | re.DOTALL)
_VAR_TOKEN_RE = re.compile(r"\?([A-Za-z_][A-Za-z0-9_]*)")


def _select_projected_vars(query: str) -> set[str]:
    """``query`` の SELECT 節に現れる変数名（alias 込み）。``FROM``/
    ``WHERE`` のどちらも見つからないとき（既に FROM を剥がした後の素の
    クエリ）は末尾まで見る。安全側: 見つからなければ空集合（=item は
    1 つも通らない）。"""
    match = _SELECT_CLAUSE_RE.search(query)
    if match is None:
        idx = query.upper().find("SELECT")
        if idx == -1:
            return set()
        match_text = query[idx + len("SELECT") :]
    else:
        match_text = match.group(1)
    return set(_VAR_TOKEN_RE.findall(match_text))


def _builtin_tool_raw(bundle_name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """組み込みツール 1 つ分の raw YAML エントリ、または ``None``（最終
    SPARQL が無い・対応表を持たない・lint が通らないときは書かない —
    保守側: 壊れたツールを束に入れるより、そのツールを省く）。

    ``result["item"]``（加工後の行の形）ではなく :data:`_BUILTIN_ITEM_OVERRIDES`
    （生の SELECT 射影変数への対応表）を使う — 生クエリの射影に無い変数を
    指す item キーは書かない（安全側）。"""
    sparql = result.get("sparql")
    output_kind = result.get("output_kind") or "facts"
    overrides = _BUILTIN_ITEM_OVERRIDES.get(bundle_name)
    if not sparql or not overrides:
        return None
    sparql = _strip_from_clauses(sparql)
    projected = _select_projected_vars(sparql)
    item = {
        key: dict(spec) for key, spec in overrides.items() if spec.get("var") in projected
    }
    if not item:
        return None
    raw: dict[str, Any] = {
        "name": bundle_name,
        "title": bundle_name,
        "query": sparql,
        "output_kind": output_kind,
        "result": {"item": item},
    }
    try:
        tools = parse_query_tools({"tools": [raw]})
    except QueryToolError:
        logger.debug("agent_bundle: builtin tool %r failed to parse, skipping", bundle_name)
        return None
    if not tools:
        return None
    lint = lint_query_tool(tools[0])
    if lint.errors:
        logger.debug(
            "agent_bundle: builtin tool %r failed lint (%s), skipping", bundle_name, lint.errors
        )
        return None
    return raw


def _query_tool_to_raw(tool: QueryTool) -> dict[str, Any]:
    """:class:`QueryTool` -> YAML に書ける生 dict（往復用）。"""
    raw: dict[str, Any] = {
        "name": tool.name,
        "title": tool.title,
        "query": tool.query,
        "output_kind": tool.output_kind,
    }
    if tool.description:
        raw["description"] = tool.description
    if tool.params:
        params_raw = []
        for p in tool.params:
            entry: dict[str, Any] = {"name": p.name, "type": p.type}
            if p.required:
                entry["required"] = True
            if p.default is not None:
                entry["default"] = p.default
            if p.description:
                entry["description"] = p.description
            if p.minimum is not None:
                entry["minimum"] = p.minimum
            if p.maximum is not None:
                entry["maximum"] = p.maximum
            if p.enum:
                entry["enum"] = list(p.enum)
            params_raw.append(entry)
        raw["parameters"] = params_raw
    if tool.item:
        raw["result"] = {"item": {k: dict(v) for k, v in tool.item.items()}}
    return raw


# ----------------------------------------------------------------------------
# cards/<card_id>.json / .mmd
# ----------------------------------------------------------------------------


def _safe_mermaid_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """IRI を id に持つ ``GraphSpec``（``asterism.prov_graph`` の節）を、
    Mermaid の id 文法（英数字・``_``・``-``）を満たす安全な id へ置き換えた
    コピーにする。ラベルはそのまま（人が読む値はここに残る）— IRI は
    facts.trig 側に別途残るので、``.mmd`` の中で読み直す必要は無い。"""
    id_map: dict[str, str] = {}
    used: set[str] = set()
    nodes_out = []
    for i, node in enumerate(graph.get("nodes") or []):
        raw_id = str(node.get("id", f"n{i}"))
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_id).strip("_") or f"n{i}"
        candidate = base
        suffix = 1
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        id_map[raw_id] = candidate
        nodes_out.append(
            {
                "id": candidate,
                "label": node.get("label", raw_id),
                "kind": node.get("kind", "entity"),
            }
        )
    edges_out = []
    for edge in graph.get("edges") or []:
        frm = id_map.get(str(edge.get("from")), str(edge.get("from")))
        to = id_map.get(str(edge.get("to")), str(edge.get("to")))
        entry: dict[str, Any] = {"from": frm, "to": to}
        if edge.get("label"):
            entry["label"] = edge["label"]
        edges_out.append(entry)
    return {"direction": graph.get("direction") or "LR", "nodes": nodes_out, "edges": edges_out}


def _card_spec(
    card_id: str, subject: dict[str, Any], tool: str, params: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    output_kind = result.get("output_kind") or "facts"
    contract = {
        "name": tool,
        "title": tool,
        "output_kind": output_kind,
        "item": result.get("item") or {},
    }
    if output_kind == "flow":
        view = {"lang": "graph", "spec": result.get("graph") or {"nodes": [], "edges": []}}
    else:
        view = default_view_for(contract, result.get("items") or [])
    return {
        "id": card_id,
        "subject": subject,
        "tool": tool,
        "params": params,
        "view": view,
        "materials": result.get("materials") or [],
        "shareable": result.get("shareable"),
    }


# ----------------------------------------------------------------------------
# materials aggregation
# ----------------------------------------------------------------------------


def _aggregate_materials(per_card_materials: list[list[dict[str, Any]]]) -> list[Material]:
    """カードをまたいだ材料表（dataset_id ごとに 1 行、count は合算）。最初に
    見た順で並べる（決定論）。"""
    order: list[str] = []
    by_dataset: dict[str, Material] = {}
    for materials in per_card_materials:
        for raw in materials:
            if not isinstance(raw, dict):
                continue
            try:
                mat = Material(
                    kind=raw["kind"],
                    dataset_id=raw["dataset_id"],
                    dataset_label=raw["dataset_label"],
                    snapshot=raw.get("snapshot"),
                    license=raw.get("license"),
                    redistributable=raw.get("redistributable"),
                    count=int(raw.get("count") or 0),
                )
            except (KeyError, TypeError, ValueError):
                continue
            did = mat.dataset_id
            if did not in by_dataset:
                order.append(did)
                by_dataset[did] = mat
            else:
                prior = by_dataset[did]
                by_dataset[did] = Material(
                    kind=prior.kind,
                    dataset_id=prior.dataset_id,
                    dataset_label=prior.dataset_label,
                    snapshot=prior.snapshot,
                    license=prior.license,
                    redistributable=prior.redistributable,
                    count=prior.count + mat.count,
                )
    return [by_dataset[did] for did in order]


def _dedup_reasons(per_card_reasons: list[list[str]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for reasons in per_card_reasons:
        for r in reasons:
            if r not in seen:
                seen.add(r)
                out.append(r)
    return out


# ----------------------------------------------------------------------------
# AGENT.md / mcp.json / README.md
# ----------------------------------------------------------------------------

def _render_readme(*, slug: str, lang: str) -> str:
    if lang == "en":
        return (
            f"# {slug} agent bundle\n\n"
            "No network access to Asterism is required — this folder is self-contained.\n\n"
            "## Claude Code\n\n"
            f"    claude mcp add {slug} -- asterism-agent serve /abs/path/to/{slug}\n\n"
            "## Claude Desktop\n\n"
            "Paste the contents of `mcp.json` into `claude_desktop_config.json`'s "
            f"`mcpServers`, replacing `.` with the absolute path to this `{slug}` folder.\n\n"
            "## A local LLM (any MCP-compatible client)\n\n"
            f"    asterism-agent serve /abs/path/to/{slug}\n\n"
            "If a tool call comes back `{\"error\": ...}`, the bundle's `tools/` and "
            "`facts/` may be out of sync — re-export this bundle rather than editing "
            "the files by hand.\n"
        )
    return (
        f"# {slug} エージェント束\n\n"
        "Asterism への通信は要らない(このフォルダだけで完結する)。\n\n"
        "## Claude Code\n\n"
        f"    claude mcp add {slug} -- asterism-agent serve /abs/path/to/{slug}\n\n"
        "## Claude Desktop\n\n"
        "`mcp.json` の中身を `claude_desktop_config.json` の `mcpServers` に貼り、"
        f"`.` をこの `{slug}` フォルダの絶対パスに書き換える。\n\n"
        "## ローカル LLM(MCP 対応クライアント全般)\n\n"
        f"    asterism-agent serve /abs/path/to/{slug}\n\n"
        "ツール呼び出しが `{\"error\": ...}` を返したときは、`tools/` と `facts/` の"
        "整合が崩れている疑いがある(手で編集せず、束を作り直すこと)。\n"
    )


def _render_mcp_json(slug: str) -> str:
    return json.dumps(
        {"mcpServers": {slug: {"command": "asterism-agent", "args": ["serve", "."]}}},
        ensure_ascii=False,
        indent=2,
    )


# ----------------------------------------------------------------------------
# public entry point
# ----------------------------------------------------------------------------


async def build_export_bundle(
    client: SupportsSparql,
    registry_root: Path | str | None,
    *,
    subject: dict[str, Any],
    cards: list[dict[str, Any]],
    share: str,
    lang: str,
    now: datetime | None = None,
    max_triples: int = DEFAULT_MAX_TRIPLES,
) -> ExportedBundle:
    """``subject`` が既に並べている ``cards`` を実行し、エージェント束の
    zip を組み立てる（契約メモ §3）。

    ``subject`` は :func:`asterism.subjects.validate_subject_key` 済みの正規
    形。``cards`` は ``[{card_id, tool, params}, …]``。``share`` は
    ``"full"``/``"shareable"``、``lang`` は ``"ja"``/``"en"``。

    カードの実行時エラー（未知のツール・種類の不一致・不正な spec など）は
    :mod:`asterism.subject_tools`/:mod:`asterism.query_tools` の例外のまま
    伝播する（api 層が既存の cards_run と同じ規則で 4xx に写す）。
    ``share="shareable"`` で配れるカードが 1 枚も無いときは
    :class:`NotShareableError`。
    """
    if share not in SHARE_MODES:
        raise ValueError(f"share must be one of {SHARE_MODES}, got {share!r}")
    if subject.get("kind") not in SUBJECT_KINDS:
        raise ValueError(f"subject.kind must be one of {SUBJECT_KINDS}")

    ran: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for card in cards:
        card_id = str(card["card_id"])
        tool = str(card["tool"])
        params = dict(card.get("params") or {})
        result = await subject_tools.run_subject_tool(client, registry_root, subject, tool, params)
        ran.append((card_id, tool, params, result))

    if share == "shareable":
        # own の材料を含む結果でも、own を除いた残りが全部 open＋再配布可なら
        # 配れる（契約メモ §3: own の材料に依存するカードを外し、facts も
        # open のみ）。own を除いた"あと"の materials で shareable() を評価
        # し直す — own を含んだままの判定で全滅させない。own しか材料が無い
        # カードは own を除くと材料 0 件になり、そのまま shareable() の
        # 「材料 0 件は配れない」規則で自然に落ちる。
        filtered_ran: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        per_card_reasons: list[list[str]] = []
        for card_id, tool, params, result in ran:
            raw_materials = [m for m in (result.get("materials") or []) if isinstance(m, dict)]
            kept_raw = [m for m in raw_materials if m.get("kind") != "own"]
            try:
                kept_mats = [Material(**m) for m in kept_raw]
            except (TypeError, KeyError):
                kept_mats = []
                kept_raw = []
            card_reasons = _materials_shareable_reasons(kept_mats)
            per_card_reasons.append(card_reasons)
            if not _materials_shareable(kept_mats):
                continue
            new_result = {
                **result,
                "materials": kept_raw,
                "shareable": True,
                "shareable_reasons": [],
            }
            filtered_ran.append((card_id, tool, params, new_result))
        if not filtered_ran:
            reasons = _dedup_reasons(per_card_reasons)
            raise NotShareableError(reasons or ["no_materials"])
        ran = filtered_ran

    label, class_label = await _subject_label_and_class(client, registry_root, subject)
    slug = _slugify(label)

    dataset_ids: set[str] = set()
    for _cid, _tool, _params, result in ran:
        for mat in result.get("materials") or []:
            did = mat.get("dataset_id") if isinstance(mat, dict) else None
            if did:
                dataset_ids.add(did)

    control = await _control_info(client, dataset_ids)
    all_canonical = await substrate.canonical_graphs(client)
    allowed_graphs = [
        g for g in all_canonical if substrate.dataset_id_of_canonical_graph(g) in dataset_ids
    ]

    # ---- facts to describe: every card's IRIs + the subject itself -------
    all_iris: set[str] = set()
    for _cid, _tool, _params, result in ran:
        all_iris |= _iris_from_card(result)
    if subject.get("kind") == "individual":
        all_iris.add(subject["iri"])

    one_hop = await _describe_one_hop(client, allowed_graphs, all_iris)
    object_iris = {o.get("value") for _g, _s, _p, o in one_hop if o.get("type") == "uri"}
    object_iris.discard(None)
    labels = await _label_triples(client, allowed_graphs, object_iris)  # type: ignore[arg-type]
    facts_trig, truncated = _render_facts_trig(one_hop + labels, max_triples=max_triples)
    control_subset = {did: control[did] for did in dataset_ids if did in control}
    control_trig = _render_control_trig(control_subset)

    # ---- tools/<dataset_id>/ ----------------------------------------------
    dataset_files: dict[str, str] = {}
    builtin_raws: dict[str, dict[str, Any]] = {}
    per_dataset_tools: dict[str, dict[str, dict[str, Any]]] = {}
    materials_by_dataset: dict[str, Material] = {}
    for _cid, tool, _params, result in ran:
        for raw_mat in result.get("materials") or []:
            if not isinstance(raw_mat, dict):
                continue
            did = raw_mat.get("dataset_id")
            if did and did not in materials_by_dataset:
                with contextlib.suppress(KeyError, TypeError, ValueError):
                    materials_by_dataset[did] = Material(
                        kind=raw_mat["kind"],
                        dataset_id=raw_mat["dataset_id"],
                        dataset_label=raw_mat["dataset_label"],
                        snapshot=raw_mat.get("snapshot"),
                        license=raw_mat.get("license"),
                        redistributable=raw_mat.get("redistributable"),
                        count=int(raw_mat.get("count") or 0),
                    )
        builtin_bundle_name = _BUILTIN_TOOL_NAMES.get(tool)
        if builtin_bundle_name is not None:
            raw = _builtin_tool_raw(builtin_bundle_name, result)
            if raw is not None:
                builtin_raws[builtin_bundle_name] = raw
            continue
        if "/" in tool:
            dataset_id, tool_name = tool.split("/", 1)
            if dataset_id not in dataset_ids:
                continue
            bucket = per_dataset_tools.setdefault(dataset_id, {})
            if tool_name in bucket:
                continue
            declared = {t.name: t for t in load_query_tools(dataset_id, root=registry_root)}
            qt = declared.get(tool_name)
            if qt is not None:
                bucket[tool_name] = _query_tool_to_raw(qt)

    for dataset_id, tool_map in per_dataset_tools.items():
        doc = {"tools": list(tool_map.values())}
        dataset_files[f"tools/{dataset_id}/query_tools.yaml"] = yaml.safe_dump(
            doc, allow_unicode=True, sort_keys=False
        )
        meta = _read_dataset_meta(registry_root, dataset_id)
        mat = materials_by_dataset.get(dataset_id)
        info = control.get(dataset_id, {})
        meta_out = {
            "id": dataset_id,
            "name": meta.get("name") or dataset_id,
            "promoted": bool(meta.get("promoted")),
            "canonical_graph": info.get("canonical_graph"),
            "live_graph": info.get("live_graph"),
            "classes": [str(c) for c in (meta.get("classes") or [])],
            "origin": mat.kind if mat else None,
            "license": mat.license if mat else None,
        }
        dataset_files[f"tools/{dataset_id}/meta.json"] = json.dumps(
            meta_out, ensure_ascii=False, indent=2, sort_keys=True
        )

    builtin_files: dict[str, str] = {}
    if builtin_raws:
        doc = {"tools": [builtin_raws[name] for name in sorted(builtin_raws)]}
        builtin_files["tools/_builtin/query_tools.yaml"] = yaml.safe_dump(
            doc, allow_unicode=True, sort_keys=False
        )
        builtin_files["tools/_builtin/meta.json"] = json.dumps(
            {
                "id": "_builtin",
                "name": "built-in page tools",
                "promoted": True,
                "canonical_graph": None,
                "live_graph": None,
                "classes": [],
                "origin": None,
                "license": None,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    # ---- cards/ -------------------------------------------------------
    card_files: dict[str, str] = {}
    doc_cards: list[AgentDocCard] = []
    dataset_names: list[str] = []
    seen_dataset_names: set[str] = set()
    for did in sorted(dataset_ids):
        mat = materials_by_dataset.get(did)
        if mat is None:
            continue
        name = f"{mat.dataset_label} ({mat.snapshot})" if mat.snapshot else mat.dataset_label
        if name not in seen_dataset_names:
            seen_dataset_names.add(name)
            dataset_names.append(name)

    for card_id, tool, params, result in ran:
        spec = _card_spec(card_id, subject, tool, params, result)
        card_files[f"cards/{card_id}.json"] = json.dumps(spec, ensure_ascii=False, indent=2)
        if spec["view"]["lang"] == "graph":
            safe_graph = _safe_mermaid_graph(spec["view"]["spec"])
            card_files[f"cards/{card_id}.mmd"] = to_mermaid(safe_graph)
        doc_cards.append(
            AgentDocCard(
                title=str(spec.get("tool")),
                output_kind=result.get("output_kind") or "facts",
                tool=tool,
            )
        )

    # ---- materials.json -------------------------------------------------
    aggregated = _aggregate_materials([entry[3].get("materials") or [] for entry in ran])
    generated_at = _now_iso(now)
    asterism_version = _asterism_version()
    materials_doc = {
        "subject": subject,
        "cards": [entry[0] for entry in ran],
        "materials": [m.to_dict() for m in aggregated],
        "shareable": _materials_shareable(aggregated),
        "shareable_reasons": _materials_shareable_reasons(aggregated),
        "share": share,
        "generated_at": generated_at,
        "asterism_version": asterism_version,
        "truncated": truncated,
    }

    # ---- AGENT.md / mcp.json / README.md --------------------------------
    agent_subject = AgentDocSubject(
        label=label,
        is_set=subject.get("kind") == "set",
        class_label=class_label,
        dataset_names=dataset_names,
    )
    agent_md = render_agent_doc(
        agent_subject,
        doc_cards,
        lang=lang,
        generated_at=generated_at,
        asterism_version=asterism_version,
        slug=slug,
    )

    # ---- assemble the zip -------------------------------------------------
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}/AGENT.md", agent_md)
        zf.writestr(f"{slug}/mcp.json", _render_mcp_json(slug))
        zf.writestr(f"{slug}/README.md", _render_readme(slug=slug, lang=lang))
        materials_json = json.dumps(materials_doc, ensure_ascii=False, indent=2)
        zf.writestr(f"{slug}/materials.json", materials_json)
        zf.writestr(f"{slug}/facts/facts.trig", facts_trig)
        zf.writestr(f"{slug}/facts/control.trig", control_trig)
        if not dataset_files and not builtin_files:
            # asterism-agent serve (D1-serve) の check_bundle は `tools/` が
            # ディレクトリとして存在することを見る — 材料 0 件（例: flow
            # カードだけの束）でも空のディレクトリ印は必ず残す。
            zf.writestr(f"{slug}/tools/", "")
        for path, content in {**dataset_files, **builtin_files, **card_files}.items():
            zf.writestr(f"{slug}/{path}", content)
    return ExportedBundle(
        zip_bytes=buf.getvalue(), filename=f"{slug}-agent.zip", slug=slug, truncated=truncated
    )
