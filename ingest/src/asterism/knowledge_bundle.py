"""「知識の束」（``asterism-knowledge/1``）を、1 つのデータセットになる写しのファイルにする。

束は取り込み元（ノートのアプリなど）が出す JSON 1 本。ここでは AI を呼ばず、決まった規則だけで

1. 束の形と IRI の基底を検査し、壊れた行は落として数える（:func:`parse_bundle`）
2. 語の候補を鍵で束ね（:func:`merge_terms`）、知見の題に語が出るかを照合し（:func:`link_terms`）、
   取り込み元の語どうしの関係を写す（:func:`merge_relations`）
3. 6 つのファイルの行にそろえ（:func:`prepare_files`）、組み込みの Mapping IR を組む
   （:func:`design_document`）
4. 写したあとの読み口（:func:`read_copy`・:func:`term_pairs`）

を行う。取り込み元ごとの規則（道具名・基底・題の割り方）は持たない。すべて束から来る。

束は外のアプリが作る信頼できない入力として扱う: 主語になる id・IRI を取る列・基底は形を検査し、
文字の値からは制御文字を除く。悪い行・悪い値は落として ``dropped`` に数え、束全体は落とさない
（1 行の細工で写しが丸ごと作れなくなるのを防ぐ）。
"""

# ruff: noqa: RUF001
# 文字列に全角の括弧・記号を含むのは意図（利用者向けの文言と YAML のコメント）。IRI ではない。
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from asterism.shared_vocab import normalize_label
from asterism.term_match import NAME_MAX, match_terms, merge_key, name_variants
from asterism.themes import RELATION_KINDS

__all__ = [
    "FORMAT",
    "Bundle",
    "BundleError",
    "KnowledgeCopy",
    "Prepared",
    "design_document",
    "link_terms",
    "merge_relations",
    "merge_terms",
    "parse_bundle",
    "prepare_files",
    "read_copy",
    "term_pairs",
]

FORMAT = "asterism-knowledge/1"

#: 束の 6 つのファイル名（知らない名前は無視する）
BUNDLE_FILES = (
    "claims.json",
    "topics.json",
    "sources.json",
    "term_candidates.json",
    "term_claims.json",
    "term_relations.json",
)
#: 写しとして書く 6 つのファイル名（``term_candidates.json`` の代わりに ``terms.json``）
COPY_FILES = (
    "claims.json",
    "topics.json",
    "sources.json",
    "terms.json",
    "term_claims.json",
    "term_relations.json",
)
BASE_NAMES = ("claim", "source", "term")

#: ``kn:`` の名前空間（写しの行の述語と種類だけ）
KN_NS = "https://kumagallium.github.io/asterism/vocab/knowledge#"

_ID_RE = re.compile(r"[A-Za-z0-9._~-]{1,200}")
_BAD_BASE_CHARS = re.compile(r"[\s<>\"{}|\\^`\x00-\x1f\x7f]")
#: ``%`` の後ろが 16 進 2 桁でない（壊れたパーセント符号化）
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
#: 目的語として置いてよい IRI（絶対・http(s)・IRI に使えない文字なし）
_IRI_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\x00-\x20\x7f]+")
#: 文字の値から除く制御文字（タブ・改行は空白にする）
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
#: 知見の題のうち、語の照合に使う先頭の文字数
_TEXT_MAX = 5000
_PLACEHOLDER_HOSTS = ("example.org", "example.com", "example.net", "example.edu")
_ROLES = ("material", "method", "concept", "other")


class BundleError(ValueError):
    """束の形・基底・ファイルが約束に合わない。"""


@dataclass
class Bundle:
    """検査を通った束。``files`` は 6 つのファイル名すべてをキーに持つ（壊れた行は落とす）。"""

    title: str
    exported_at: str
    bases: dict[str, str]
    files: dict[str, list[dict]]
    dropped: dict[str, int] = field(default_factory=dict)


@dataclass
class Prepared:
    """:func:`prepare_files` の返り。``files`` は 6 ファイル名 → 行・``counts`` は行数。"""

    files: dict[str, list[dict]]
    counts: dict[str, int]
    dropped: dict[str, int]


@dataclass
class KnowledgeCopy:
    """写したデータセットの 6 つのファイルの行（14c の読み口）。"""

    claims: list[dict] = field(default_factory=list)
    topics: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    terms: list[dict] = field(default_factory=list)
    term_claims: list[dict] = field(default_factory=list)
    term_relations: list[dict] = field(default_factory=list)


# ---- 検査 ---------------------------------------------------------------------------------------


def _check_base(label: str, value: Any) -> str:
    """IRI の基底の検査。合わなければ、どの基底かを入れた :class:`BundleError`。"""
    if not isinstance(value, str) or not value:
        raise BundleError(f"bases.{label} が文字列ではありません")
    if not value.startswith(("http://", "https://")):
        raise BundleError(f"bases.{label} は http:// か https:// で始めてください: {value!r}")
    if _BAD_BASE_CHARS.search(value):
        raise BundleError(
            f"bases.{label} に使えない文字（空白・制御文字・<>\"{{}}|\\^`）があります"
        )
    if _BAD_PERCENT.search(value):
        raise BundleError(f"bases.{label} の % の後ろが 16 進 2 桁ではありません")
    if not value.endswith(("/", "#")):
        raise BundleError(f"bases.{label} は / か # で終えてください: {value!r}")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError as e:
        raise BundleError(f"bases.{label} を URL として読めません: {value!r}") from e
    if not host:
        raise BundleError(f"bases.{label} にホストがありません: {value!r}")
    if "@" in parts.netloc:
        raise BundleError(f"bases.{label} に利用者名やパスワードを入れないでください")
    placeholder = any(host == h or host.endswith("." + h) for h in _PLACEHOLDER_HOSTS)
    if host == "localhost" or placeholder:
        raise BundleError(f"bases.{label} のホストが仮のものです（{host}）")
    return value


def _is_id(v: Any) -> bool:
    """主語の IRI にそのまま入る id か。``.`` だけ（``.``・``..``）は拒む。"""
    return isinstance(v, str) and _ID_RE.fullmatch(v) is not None and v.strip(".") != ""


def _clean(v: Any) -> Any:
    """文字の値を掃除する: 対にならないサロゲートを除き、制御文字を空白にする。配列は要素ごと。
    それ以外の型はそのまま。"""
    if isinstance(v, str):
        v = v.encode("utf-8", "ignore").decode("utf-8")
        return _CONTROL.sub(" ", v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def _is_iri(v: Any) -> bool:
    return isinstance(v, str) and _IRI_RE.fullmatch(v) is not None


#: ファイル名 → 行を一意にする鍵の列（全部が id の形であること）
_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "claims.json": ("id",),
    "topics.json": ("id",),
    "sources.json": ("id",),
    "term_candidates.json": ("id",),
    "term_claims.json": ("term_id", "claim_id"),
    "term_relations.json": ("from_term", "rel", "to_term"),
}


def _clean_rows(name: str, rows: list[Any], dropped: dict[str, int]) -> list[dict]:
    keys = _KEY_COLUMNS[name]
    out: list[dict] = []
    seen: set[tuple] = set()
    for raw in rows:
        if not isinstance(raw, dict) or not all(_is_id(raw.get(k)) for k in keys):
            dropped[f"{name}:bad_row"] = dropped.get(f"{name}:bad_row", 0) + 1
            continue
        row = {str(k): _clean(v) for k, v in raw.items()}
        if name == "claims.json":
            t = row.get("title")
            if not isinstance(t, str) or not t.strip():
                dropped[f"{name}:bad_row"] = dropped.get(f"{name}:bad_row", 0) + 1
                continue
        if name == "term_candidates.json":
            n = row.get("name")
            if not isinstance(n, str) or not n.strip() or len(n) > NAME_MAX:
                dropped[f"{name}:bad_row"] = dropped.get(f"{name}:bad_row", 0) + 1
                continue
        key = tuple(row[k] for k in keys)
        if key in seen:
            dropped[f"{name}:duplicate"] = dropped.get(f"{name}:duplicate", 0) + 1
            continue
        seen.add(key)
        out.append(row)
    return out


def parse_bundle(raw: Any) -> Bundle:
    """束（JSON を読んだ dict）を検査して :class:`Bundle` にする。

    形（``format``・``files``・基底）が悪ければ :class:`BundleError`。行が悪いときは、その行を
    落として ``dropped["<ファイル名>:bad_row"]``（同じ鍵の 2 行目以降は ``:duplicate``）に
    数える。"""
    if not isinstance(raw, dict):
        raise BundleError("束が JSON のオブジェクトではありません")
    if raw.get("format") != FORMAT:
        raise BundleError(f"format が {FORMAT!r} ではありません: {raw.get('format')!r}")
    bases_raw = raw.get("bases")
    if not isinstance(bases_raw, dict):
        raise BundleError("bases がありません")
    bases = {label: _check_base(label, bases_raw.get(label)) for label in BASE_NAMES}
    if len(set(bases.values())) != len(bases):
        # 同じ基底だと、別の種類の行（資料と語など）が同じ主語に重なる
        raise BundleError("bases の claim・source・term は別々の値にしてください")
    files_raw = raw.get("files")
    if not isinstance(files_raw, dict):
        raise BundleError("files がオブジェクトではありません")
    dropped: dict[str, int] = {}
    files: dict[str, list[dict]] = {}
    for name in BUNDLE_FILES:
        rows = files_raw.get(name, [])
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise BundleError(f"files[{name!r}] が配列ではありません")
        files[name] = _clean_rows(name, rows, dropped)
    # 知見とトピックは同じ基底なので、同じ id は同じ主語になる。知見を残してトピックを落とす
    claim_ids = {r["id"] for r in files["claims.json"]}
    kept = [r for r in files["topics.json"] if r["id"] not in claim_ids]
    if len(kept) != len(files["topics.json"]):
        dropped["topics.json:id_collision"] = len(files["topics.json"]) - len(kept)
        files["topics.json"] = kept
    title = _clean(raw.get("title"))
    title = " ".join(title.split())[:60] if isinstance(title, str) else ""
    if not title:
        title = "knowledge"
    exported_at = raw.get("exported_at")
    return Bundle(
        title=title.strip(),
        exported_at=exported_at if isinstance(exported_at, str) else "",
        bases=bases,
        files=files,
        dropped=dropped,
    )


# ---- 語を束ねる ---------------------------------------------------------------------------------


def _strs(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    return []


def merge_terms(
    candidates: Iterable[Mapping[str, Any]], *, term_base: str
) -> tuple[list[dict], dict[str, str]]:
    """語の候補を鍵（:func:`~asterism.term_match.merge_key`）で束ねる。

    返り: ``(語の行, 候補の id → 語の id)``。鍵が空になる名前（記号だけ）の候補は落とす
    （対応表にも入らない）。語の ``id`` は ``"t-" + sha1(鍵)[:12]``・``iri`` は ``term_base + id``。
    名前はいちばん多い表記（同数なら短い方・さらに同じなら文字の順）、``aliases`` はそれ以外の表記と
    候補の別名（:func:`~asterism.shared_vocab.normalize_label` が同じものは 1 つ）。種別は候補に
    ``property`` が 1 つでもあれば ``property``（役割 None）、無ければ ``class`` で役割は多い順
    （同数は material → method → concept → other・札が全部 None なら None）。``origin_iri`` は
    候補の元（重複なし・候補の id の順）。入力の順に依らない。語の行は id の順。"""
    ordered = sorted(
        (c for c in candidates if isinstance(c.get("name"), str)), key=lambda c: str(c.get("id"))
    )
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for c in ordered:
        key = merge_key(c["name"])
        if key:
            groups.setdefault(key, []).append(c)
    terms: list[dict] = []
    term_of: dict[str, str] = {}
    for key, members in groups.items():
        tid = "t-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        for m in members:
            term_of[str(m["id"])] = tid
        surface = Counter(" ".join(m["name"].split()) for m in members)
        name = sorted(surface, key=lambda s: (-surface[s], len(s), s))[0]
        names = [s for s in surface if s != name]
        for m in members:
            names.extend(a for a in _strs(m.get("aliases")) if len(a) <= NAME_MAX)
        aliases: list[str] = []
        seen = {normalize_label(name)}
        for a in sorted(" ".join(x.split()) for x in names):
            k = normalize_label(a)
            if a and k not in seen:
                seen.add(k)
                aliases.append(a)
        if any(m.get("kind") == "property" for m in members):
            kind, role = "property", None
        else:
            kind = "class"
            roles = Counter(m.get("role") for m in members if m.get("role") in _ROLES)
            role = (
                sorted(roles, key=lambda r: (-roles[r], _ROLES.index(r)))[0] if roles else None
            )
        origins: list[str] = []
        for m in members:
            o = m.get("origin_iri")
            if _is_iri(o) and o not in origins:
                origins.append(o)
        terms.append({
            "id": tid,
            "iri": term_base + tid,
            "name": name,
            "kind": kind,
            "role": role,
            "aliases": aliases,
            "origin_iri": origins,
            "claim_count": 0,
        })
    terms.sort(key=lambda t: t["id"])
    return terms, term_of


def link_terms(
    terms: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    term_claims: Sequence[Mapping[str, Any]],
    term_of: Mapping[str, str],
) -> tuple[list[dict], list[dict]]:
    """語 ↔ 知見。返りは ``(語と知見の行, claim_count を付けた語の行)``。

    取り込み元の対応（``term_claims``・候補の id）を ``term_of`` で束ねた語の id に写す
    （``by: "origin"``。語か知見が無い行は捨てる）。照合は、語の名前・別名とその変形
    （:func:`~asterism.term_match.name_variants`）のうち使えるものを知見の ``title`` に当てる
    （:func:`~asterism.term_match.match_terms`・``by: "text"``）。同じ（語, 知見）は 1 行で
    ``origin`` が勝つ。行は（語の id, 知見の id）の順。"""
    term_ids = {str(t["id"]) for t in terms}
    claim_ids = {str(c["id"]) for c in claims}
    pairs: dict[tuple[str, str], str] = {}
    for r in term_claims:
        tid = term_of.get(str(r.get("term_id")))
        cid = str(r.get("claim_id"))
        if tid in term_ids and cid in claim_ids:
            pairs[(tid, cid)] = "origin"
    names_by_term: dict[str, list[str]] = {}
    for t in terms:
        names: list[str] = []
        for n in [t["name"], *t.get("aliases", [])]:
            for v in name_variants(n):
                if v not in names:
                    names.append(v)
        names_by_term[str(t["id"])] = names
    texts = {str(c["id"]): str(c.get("title") or "")[:_TEXT_MAX] for c in claims}
    for cid, tids in match_terms(names_by_term, texts).items():
        for tid in tids:
            pairs.setdefault((tid, cid), "text")
    rows = [{"term_id": t, "claim_id": c, "by": by} for (t, c), by in sorted(pairs.items())]
    counts = Counter(r["term_id"] for r in rows)
    counted = [{**dict(t), "claim_count": counts.get(str(t["id"]), 0)} for t in terms]
    return rows, counted


def merge_relations(
    relations: Sequence[Mapping[str, Any]], term_of: Mapping[str, str]
) -> tuple[list[dict], dict[str, int]]:
    """取り込み元の語の関係を、束ねた語の id に写す。返りは ``(行, 落とした数)``。

    ``rel`` が下書きの集合（``themes.RELATION_KINDS``）に無い（``term_relations.json:bad_rel``）・
    両端が束に無い（``:unknown_term``）・両端が同じ語（``:same_term``）の行は落として数える。同じ
    （from, rel, to）は 1 行にまとめ、``origin_iri`` を配列（重複なし・順は文字の順）で持つ。"""
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        k = f"term_relations.json:{reason}"
        dropped[k] = dropped.get(k, 0) + 1

    merged: dict[tuple[str, str, str], list[str]] = {}
    for r in relations:
        rel = r.get("rel")
        if rel not in RELATION_KINDS:
            drop("bad_rel")
            continue
        a, b = term_of.get(str(r.get("from_term"))), term_of.get(str(r.get("to_term")))
        if a is None or b is None:
            drop("unknown_term")
            continue
        if a == b:
            drop("same_term")
            continue
        origins = merged.setdefault((a, str(rel), b), [])
        o = r.get("origin_iri")
        if _is_iri(o) and o not in origins:
            origins.append(o)
    rows = [
        {
            "id": f"{a}.{rel}.{b}",
            "from_term": a,
            "rel": rel,
            "to_term": b,
            "origin_iri": sorted(origins),
        }
        for (a, rel, b), origins in sorted(merged.items())
    ]
    return rows, dropped


# ---- 写しのファイル -----------------------------------------------------------------------------

#: ファイル名 → (列, IRI の配列の列, 主語の基底)。束の行をこの列だけに絞る
#: （欠けた列は None・配列の列は []）
# fmt: off
_COLUMNS: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {
    "claims.json": (
        (
            "id", "iri", "source_url", "title", "review_state", "epistemic_status",
            "modal_qualifier", "level", "source_iri", "generated_at", "exported_at",
        ),
        frozenset({"source_iri"}),
        "claim",
    ),
    "topics.json": (
        (
            "id", "iri", "source_url", "title", "topic_type", "material", "property",
            "claim_iri", "source_iri", "generated_at", "exported_at",
        ),
        frozenset({"claim_iri", "source_iri"}),
        "claim",
    ),
    "sources.json": (
        ("id", "iri", "name", "kind", "exported_at"),
        frozenset(),
        "source",
    ),
}
# fmt: on


def _shape(
    name: str, rows: Sequence[Mapping[str, Any]], bases: Mapping[str, str], dropped: dict[str, int]
) -> list[dict]:
    """束の行を写しの列にそろえる。``iri`` は束の値を信じず、基底 + id で作り直す。IRI を取る列
    （配列の列と ``source_url``）は、絶対 IRI の形のものだけ残す（悪い値は落として
    ``<ファイル名>:bad_iri`` に数える — そのまま置くとストアが版ごと受け付けない）。"""
    cols, arrays, base = _COLUMNS[name]
    bad = 0
    out = []
    for r in sorted(rows, key=lambda r: str(r["id"])):
        row: dict[str, Any] = {}
        for c in cols:
            v = r.get(c)
            if c == "iri":
                row[c] = bases[base] + str(r["id"])
            elif c in arrays:
                given = _strs(v)
                row[c] = list(dict.fromkeys(x for x in given if _is_iri(x)))
                bad += sum(1 for x in given if not _is_iri(x))
            elif c == "source_url":
                row[c] = v if _is_iri(v) else None
                bad += 1 if v not in (None, "") and not _is_iri(v) else 0
            else:
                row[c] = v
        out.append(row)
    if bad:
        dropped[f"{name}:bad_iri"] = dropped.get(f"{name}:bad_iri", 0) + bad
    return out


def prepare_files(bundle: Bundle) -> Prepared:
    """束を、写しの 6 ファイルの行にする（``term_candidates.json`` は ``terms.json`` にする）。

    ``claims``／``topics``／``sources`` は契約の列だけに絞る（全行が同じキー集合・同じ順）。行の順は
    id の順で安定。``dropped`` は :func:`parse_bundle` の数に、語・関係で落とした数を足したもの。"""
    f = bundle.files
    dropped = dict(bundle.dropped)
    claims = _shape("claims.json", f["claims.json"], bundle.bases, dropped)
    topics = _shape("topics.json", f["topics.json"], bundle.bases, dropped)
    sources = _shape("sources.json", f["sources.json"], bundle.bases, dropped)

    terms, term_of = merge_terms(f["term_candidates.json"], term_base=bundle.bases["term"])
    lost = len(f["term_candidates.json"]) - len(term_of)
    if lost:
        dropped["term_candidates.json:empty_key"] = lost
    claim_ids = {str(c["id"]) for c in claims}
    origin_rows = []
    for r in f["term_claims.json"]:
        if str(r["claim_id"]) in claim_ids and str(r["term_id"]) in term_of:
            origin_rows.append(r)
        else:
            dropped["term_claims.json:unknown_ref"] = (
                dropped.get("term_claims.json:unknown_ref", 0) + 1
            )
    links, terms = link_terms(terms, claims, origin_rows, term_of)
    relations, rel_dropped = merge_relations(f["term_relations.json"], term_of)
    dropped.update(rel_dropped)

    files = {
        "claims.json": claims,
        "topics.json": topics,
        "sources.json": sources,
        "terms.json": terms,
        "term_claims.json": links,
        "term_relations.json": relations,
    }
    return Prepared(
        files=files, counts={k: len(v) for k, v in files.items()}, dropped=dropped
    )


# ---- 組み込みの設計 -----------------------------------------------------------------------------


def _q(s: str) -> str:
    """YAML の二重引用の文字列（JSON の文字列は YAML として同じ意味）。"""
    return json.dumps(s, ensure_ascii=False)


def _prop(
    predicate: str,
    label: str,
    *,
    column: str | None = None,
    function: str | None = None,
    iri: bool = False,
    datatype: str | None = None,
    template: str | None = None,
) -> str:
    lines = [f"      - predicate: {predicate}"]
    if column:
        lines.append(f"        column: {column}")
    if template:
        lines.append(f"        object_template: {_q(template)}")
    if function:
        lines.append(f"        function: {function}")
    if iri:
        lines.append("        object_type: iri")
    if datatype:
        lines.append(f"        datatype: {datatype}")
    lines.append(f"        label: {label}")
    return "\n".join(lines)


def _map(
    name: str, source: str, template: str, classes: str | None, label: str, props: list[str]
) -> str:
    head = [
        f"  - name: {name}",
        f"    source: {source}",
        "    optional: true",
        "    subject:",
        f"      template: {_q(template)}",
    ]
    if classes:
        head.append(f"      classes: [{classes}]")
    head.append(f"      label: {label}")
    head.append("    properties:")
    return "\n".join(head + props)


def design_document(bundle: Bundle, *, name: str) -> str:
    """materialize に渡す文書（``## Schema proposal``・Class diagram・MIE・§9 の IR）を返す。

    IR は束の基底を差し込んだ組み込みの設計（LLM 0 回・生成コードなし）。基底は
    :func:`parse_bundle` を通ったものだけを差し込む。``### MIE`` の ``title`` は ``name``。"""
    claim, source, term = (bundle.bases[k] for k in BASE_NAMES)
    # fmt: off
    maps = [
        _map("claim", "claims.csv", claim + "{id}", "kn:Claim", "知見", [
            _prop("dcterms:identifier", "知見の id", column="id"),
            _prop("rdfs:label", "題", column="title"),
            _prop("kn:sourceUrl", "取り込み元のページ", column="source_url",
                  function="iri_safe", iri=True),
            _prop("kn:reviewState", "確からしさ", column="review_state"),
            _prop("kn:epistemicStatus", "認識の状態", column="epistemic_status"),
            _prop("kn:modalQualifier", "言い切りの強さ", column="modal_qualifier"),
            _prop("kn:level", "段階", column="level"),
            _prop("prov:wasDerivedFrom", "出典の資料", column="source_iri",
                  function="json_array", iri=True),
            _prop("prov:generatedAtTime", "生成した時刻", column="generated_at",
                  datatype="xsd:dateTime"),
            _prop("kn:exportedAt", "書き出した時刻", column="exported_at",
                  datatype="xsd:dateTime"),
        ]),
        _map("topic", "topics.csv", claim + "{id}", "skos:Concept", "トピック", [
            _prop("dcterms:identifier", "トピックの id", column="id"),
            _prop("skos:prefLabel", "名前", column="title"),
            _prop("kn:sourceUrl", "取り込み元のページ", column="source_url",
                  function="iri_safe", iri=True),
            _prop("kn:topicType", "種類の札", column="topic_type"),
            _prop("kn:material", "材料", column="material"),
            _prop("kn:property", "物性", column="property"),
            _prop("kn:member", "含まれる知見", column="claim_iri", function="json_array", iri=True),
            _prop("prov:wasDerivedFrom", "出典の資料", column="source_iri",
                  function="json_array", iri=True),
            _prop("prov:generatedAtTime", "生成した時刻", column="generated_at",
                  datatype="xsd:dateTime"),
            _prop("kn:exportedAt", "書き出した時刻", column="exported_at",
                  datatype="xsd:dateTime"),
        ]),
        _map("source", "sources.csv", source + "{id}", "kn:Source", "資料", [
            _prop("dcterms:identifier", "資料の id", column="id"),
            _prop("rdfs:label", "名前", column="name"),
            _prop("kn:sourceKind", "資料の種類", column="kind"),
        ]),
        _map("term", "terms.csv", term + "{id}", "kn:Term", "語", [
            _prop("dcterms:identifier", "語の id", column="id"),
            _prop("skos:prefLabel", "名前", column="name"),
            _prop("skos:altLabel", "別名", column="aliases", function="json_array"),
            _prop("kn:termKind", "種別", column="kind"),
            _prop("kn:role", "役割", column="role"),
            _prop("prov:wasDerivedFrom", "元のトピック", column="origin_iri",
                  function="json_array", iri=True),
            _prop("kn:claimCount", "出てくる知見の数", column="claim_count",
                  datatype="xsd:integer"),
        ]),
        _map("term_claim", "term_claims.csv", claim + "{claim_id}", None, "知見と語の対応", [
            _prop("kn:mentions", "出てくる語", template=term + "{term_id}", iri=True),
        ]),
        _map("term_relation", "term_relations.csv", term + "{id}", "kn:TermRelation",
             "語の関係の候補", [
            _prop("kn:from", "関係の元", template=term + "{from_term}", iri=True),
            _prop("kn:to", "関係の先", template=term + "{to_term}", iri=True),
            _prop("kn:relation", "関係の名前", column="rel"),
            _prop("prov:wasDerivedFrom", "元のトピック", column="origin_iri",
                  function="json_array", iri=True),
        ]),
    ]
    # fmt: on
    ir = "\n".join([
        "# 知識の束（asterism-knowledge/1）を写す組み込みの設計。機械が組む（人の設計なし）。",
        "# 主語は束が名乗る基底＋id。source は JSON を表にした名前（claims.json → claims.csv）。",
        "version: 1",
        "prefixes:",
        f"  kn: {_q(KN_NS)}",
        '  prov: "http://www.w3.org/ns/prov#"',
        '  rdfs: "http://www.w3.org/2000/01/rdf-schema#"',
        '  dcterms: "http://purl.org/dc/terms/"',
        '  skos: "http://www.w3.org/2004/02/skos/core#"',
        "maps:",
        *maps,
    ])
    return "\n".join([
        "## Schema proposal",
        "",
        "### Class diagram",
        "```mermaid",
        "classDiagram",
        "    class Claim",
        "    class Topic",
        "    class Source",
        "    class Term",
        "    class TermRelation",
        "    Claim --> Source : wasDerivedFrom",
        "    Topic --> Source : wasDerivedFrom",
        "    Topic --> Claim : member",
        "    Claim --> Term : mentions",
        "    TermRelation --> Term : from",
        "    TermRelation --> Term : to",
        "```",
        "",
        "### MIE",
        "```yaml",
        "schema_info:",
        f"  title: {_q(name)}",
        "  keywords: [claim, topic, source, term]",
        "  categories: []",
        "```",
        "",
        "### 9. Declarative mapping spec",
        "",
        "```yaml",
        ir,
        "```",
        "",
    ])


# ---- 写したあとの読み口 -------------------------------------------------------------------------


def read_copy(source_dir: Path) -> KnowledgeCopy:
    """写したデータセットの 6 つの JSON を読む。無いファイルは ``[]``・読めなければ
    :class:`BundleError`。"""
    out: dict[str, list[dict]] = {}
    for fname in COPY_FILES:
        path = Path(source_dir) / fname
        rows: Any = []
        if path.is_file():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise BundleError(f"{fname} を JSON として読めません: {e}") from e
            if not isinstance(rows, list):
                raise BundleError(f"{fname} が配列ではありません")
        out[fname.removesuffix(".json")] = [r for r in rows if isinstance(r, dict)]
    return KnowledgeCopy(**out)


def term_pairs(copy: KnowledgeCopy) -> dict[tuple[str, str], list[str]]:
    """同じ知見に出る語の組（a < b の文字列順）→ 知見の id（id の順）。"""
    by_claim: dict[str, set[str]] = {}
    for r in copy.term_claims:
        by_claim.setdefault(str(r["claim_id"]), set()).add(str(r["term_id"]))
    pairs: dict[tuple[str, str], list[str]] = {}
    for cid in sorted(by_claim):
        for a, b in combinations(sorted(by_claim[cid]), 2):
            pairs.setdefault((a, b), []).append(cid)
    return pairs
