"""添えた論文から、用語・関係・問いを根拠の文つきで提案する（ui-redesign PR 12b・design.md §20）。

12a の走者（``chat_attach.run_document_attach``）に「提案」の段を 1 つ足す部品。流れ:

1. 下書きの版 graph の ``doco:Sentence`` を、出現順に ``s1``… の番号つきで並べる（参考文献・
   謝辞の節は除く・上限 150 文。決定論・:func:`read_sentences` / :func:`select_sentences`）。
2. ``paper_propose`` に番号つきの文・既存の用語の名前を渡す。AI が書くのは「名前 → 関係 →
   名前＋根拠の文の番号」だけ。用語の鍵・種類・問いは機械が組む（:func:`build_ops` /
   :func:`add_questions`）。引用文は書かせない。
3. 機械が確かめる（:func:`check_ops`）— 番号の文が実在する・両端の用語の名前のどれかがその文に
   現れる。合わない関係は落とす。残った関係の ``evidence`` は、**ストアから引いた文**
   （IRI・150 字・節の path）に置き換える（AI の文字列は使わない）。
4. 話の下書きに当てる（:func:`apply_proposal`）。確定は ``chat_turns.confirm`` と同じ
   （``commit_theme`` が関係の来歴に ``prov:wasDerivedFrom <文 IRI>`` を書く）。

AI が無い・失敗したら提案の段を飛ばして 12a のまま（論文だけ）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import copy
import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from asterism import documents as doc
from asterism import graph_diff, shared_vocab, substrate, themes
from asterism.paper_sections import _heading_key, _is_abstract, _is_conclusion
from asterism.shared_vocab import SharedTerm
from asterism_step0.paper_propose import PaperProposeError, propose_paper_relations

from asterism_api import chat_seed, chat_turns, registry

if TYPE_CHECKING:
    from fastapi import FastAPI

    from asterism_api.main import Settings

__all__ = [
    "EVIDENCE_TEXT_MAX",
    "MAX_PROMPT_CHARS",
    "MAX_SENTENCES",
    "apply_proposal",
    "build_rows",
    "check_ops",
    "make_proposal",
    "mentions",
    "prep_text",
    "read_sentences",
    "select_sentences",
    "text",
]

logger = logging.getLogger(__name__)

#: AI に渡す文の上限（超えたら要旨・結果・考察・結論を優先して残す）
MAX_SENTENCES = 150
#: 下書きの根拠の文（evidence.text）の長さ（札は 150 字で切って「…」・title に全文）
EVIDENCE_TEXT_MAX = 1000
#: AI に渡す文の合計の長さ（超えたら文の上限と同じ優先順で切る）
MAX_PROMPT_CHARS = 30_000
_PROMPT_SENTENCE_MAX = 600  # step0 が 1 文をプロンプトに載せる長さ
#: 参考文献・謝辞の節（見出しを ``_heading_key`` で整えて比べる）
_SKIP_TITLE = re.compile(
    r"^(references?|bibliography|literature cited|works cited|acknowledge?ments?"
    r"|acknowledgments?|参考文献|引用文献|文献|謝辞)$"
)
#: 節の DEO の役割（``documents._DEO_ROLES`` 以外が付いていたとき用）
_SKIP_ROLES = frozenset({"acknowledgements", "acknowledgments", "bibliography", "references"})
#: 切るときに優先する役割
_PREFER_ROLES = frozenset({"results", "discussion", "conclusion", "abstract"})

_T: dict[str, dict[str, str]] = {
    "ja": {
        "summary": "用語 {c}・関係 {r}{k}・問い {q} を提案しました。",
        "supports_k": "（うち既にある関係を支える {k}）",
        "long": "論文が長いので、{n} 文だけを読みました（要旨・結果・考察・結論を優先）",
        "evidence_dropped": "根拠の文が合わない関係を {n} 件除きました",
        "kind_dropped": "用語の種類が合わない関係を {n} 件除きました",
        "q_limit": "問いの数の上限（5）を超えたまとまりの関係を {n} 件除きました",
        "rel_limit": "関係の数の上限（20）を超えた分を {n} 件除きました",
        "propose_failed": "論文からの提案を作れませんでした（論文は添えました）",
        "commit_failed": "提案は確定できませんでした（論文は公開しました）",
        "terms_dropped": "どの関係にも問いにも出ない用語を {n} 件除きました",
        "shape_dropped": "形の合わない提案を {n} 件除きました",
        "cannot_link": "論文からの提案を、問いにつながる形にできませんでした（論文は添えました）",
        "undone_published": "提案は取り消しました（論文は公開済みです）",
        "empty": "論文から、根拠の文つきで提案できる関係が見つかりませんでした（論文は添えました）",
        "unlinked": (
            "この話の下書きに、問いにつながらない用語があります: {names}"
            "（承認の前に、関係を足すか外してください）"
        ),
    },
    "en": {
        "summary": "Proposed {c} terms, {r} relations{k} and {q} questions.",
        "supports_k": " (of which {k} support existing relations)",
        "long": (
            "The paper is long, so only {n} sentences were read "
            "(abstract, results, discussion and conclusion first)"
        ),
        "evidence_dropped": "Left out {n} relations whose evidence sentences do not fit",
        "kind_dropped": "Left out {n} relations whose term kinds do not fit",
        "q_limit": "Left out {n} relations of groups beyond the limit of 5 questions",
        "rel_limit": "Left out {n} relations beyond the limit of 20",
        "propose_failed": "Could not make proposals from the paper (the paper is attached)",
        "commit_failed": "Could not confirm the proposals (the paper was published)",
        "terms_dropped": "Left out {n} terms that appear in no relation or question",
        "shape_dropped": "Left out {n} proposals whose shape did not fit",
        "cannot_link": "Could not shape the proposals so that they connect to a question "
        "(the paper is attached)",
        "undone_published": "The proposals were undone (the paper stays published)",
        "empty": "Found no relation that the paper's sentences support (the paper is attached)",
        "unlinked": (
            "This chat's draft has terms that do not connect to a question: {names} "
            "(add a relation or remove them before approving)"
        ),
    },
}


def text(lang: str | None, key: str, **kw: Any) -> str:
    return _T["en" if lang == "en" else "ja"][key].format(**kw)


# ---- 文を引く（決定論） -----------------------------------------------------------------------


def _v(b: dict, k: str) -> str | None:
    return (b.get(k) or {}).get("value")


def build_rows(sentence_bindings: list[dict], section_bindings: list[dict]) -> list[dict]:
    """SPARQL の結果 → 文の行（出現順）。純関数。

    行 = ``{iri, text, begin, path, skip, prefer}``。``skip`` = 参考文献・謝辞の節（その節か
    上の節の題名／DEO の役割で判定・取れなければ立てない）。``prefer`` = 要旨・結果・考察・
    結論の節（切るときに優先）。``path`` = 文の属する節の構造のパス（無ければ ``None``）。"""
    secs: dict[str, dict] = {}
    for b in section_bindings:
        iri = _v(b, "sec")
        if not iri:
            continue
        s = secs.setdefault(iri, {"title": "", "path": "", "roles": set(), "children": set()})
        if _v(b, "title"):
            s["title"] = _v(b, "title") or ""
        if _v(b, "path"):
            s["path"] = _v(b, "path") or ""
        if _v(b, "role"):
            s["roles"].add((_v(b, "role") or "").rsplit("/", 1)[-1].lower())
        if _v(b, "child"):
            s["children"].add(_v(b, "child"))
    parent = {c: p for p, s in secs.items() for c in s["children"]}

    def chain(iri: str) -> list[dict]:
        out, seen = [], set()
        while iri in secs and iri not in seen:
            seen.add(iri)
            out.append(secs[iri])
            iri = parent.get(iri, "")
        return out

    rows: dict[str, dict] = {}
    for b in sentence_bindings:
        iri, body = _v(b, "s"), _v(b, "t")
        if not iri or iri in rows or not body or not body.strip():
            continue
        try:
            begin = int(_v(b, "b") or 0)
        except ValueError:
            begin = 0
        up = chain(_v(b, "sec") or "")
        roles = {r for s in up for r in s["roles"]}
        keys = [_heading_key(s["title"]) for s in up if s["title"]]
        rows[iri] = {
            "iri": iri,
            "text": " ".join(body.split()),
            "begin": begin,
            "path": (up[0]["path"] or None) if up else None,
            "skip": any(_SKIP_TITLE.match(k) for k in keys) or bool(roles & _SKIP_ROLES),
            "prefer": bool(roles & _PREFER_ROLES)
            or any(_is_abstract(s["title"]) or _is_conclusion(s["title"]) for s in up),
        }
    return sorted(rows.values(), key=lambda r: (r["begin"], r["iri"]))


def select_sentences(
    rows: list[dict], limit: int = MAX_SENTENCES, budget: int = MAX_PROMPT_CHARS
) -> tuple[list[dict], bool]:
    """AI に渡す文を選び、出現順に ``s1``… を振る → ``(行, 切ったか)``。純関数。

    参考文献・謝辞は除く。文の数が ``limit`` を、文字の合計が ``budget`` を超えたら、要旨・結果・
    考察・結論の節の文を優先し、余りを先頭から詰める（役割が 1 つも取れなければ先頭から）。"""
    kept = [r for r in rows if not r["skip"]]

    def cost(r: dict) -> int:
        return min(len(r["text"]), _PROMPT_SENTENCE_MAX)

    if len(kept) > limit or sum(cost(r) for r in kept) > budget:
        preferred = [r for r in kept if r["prefer"]]
        order = [*preferred, *(r for r in kept if not r["prefer"])] if preferred else kept
        taken: list[dict] = []
        used = 0
        for r in order:  # 入りきらなくなったところで止める（先頭から）
            if len(taken) >= limit or used + cost(r) > budget:
                break
            taken.append(r)
            used += cost(r)
        kept = sorted(taken, key=lambda r: (r["begin"], r["iri"]))
        return [{**r, "id": f"s{i}"} for i, r in enumerate(kept, start=1)], True
    return [{**r, "id": f"s{i}"} for i, r in enumerate(kept, start=1)], False


async def read_sentences(app: FastAPI, cfg: Settings, dataset_id: str) -> list[dict]:
    """下書き（取り込み直後・公開前）の版 graph の文を引く。graph は ``draft-stats`` と同じ解決。"""
    data = registry.load_dataset(cfg.registry_root, dataset_id) or {}
    meta = data.get("meta") or {}
    graph = (
        meta.get("graph_iri")
        or meta.get("live_graph")
        or substrate.canonical_graph_iri(dataset_id)
    )
    if not re.match(r'^[^\s<>"{}|\\^`]+$', str(graph)):
        return []
    po, nif, lit = doc.PO, doc.NIF, doc.LIT
    section = f"<{doc.DOCO}Section>"
    client = app.state.client
    sent = await client.sparql_select(
        f"SELECT ?s ?t ?b ?sec WHERE {{ GRAPH <{graph}> {{ "
        f"?s a <{doc.DOCO}Sentence> ; <{nif}anchorOf> ?t ; <{nif}beginIndex> ?b . "
        f"?p <{po}contains> ?s . ?sec <{po}contains> ?p . ?sec a {section} }} }}"
    )
    secs = await client.sparql_select(
        f"SELECT ?sec ?title ?path ?role ?child WHERE {{ GRAPH <{graph}> {{ ?sec a {section} . "
        f"OPTIONAL {{ ?sec <{doc.DCTERMS}title> ?title }} "
        f"OPTIONAL {{ ?sec <{lit}structuralPath> ?path }} "
        f'OPTIONAL {{ ?sec a ?role . FILTER(STRSTARTS(STR(?role), "{doc.DEO}")) }} '
        f"OPTIONAL {{ ?sec <{po}contains> ?child . ?child a {section} }} }} }}"
    )

    def rows_of(res: Any) -> list[dict]:
        results = res.get("results") if isinstance(res, dict) else None
        return list(results.get("bindings", [])) if isinstance(results, dict) else []

    return build_rows(rows_of(sent), rows_of(secs))


# ---- 機械の検査（落とす理由） -----------------------------------------------------------------


_CJK_FROM = 0x2E80  # これ以上（日本語など）は語の境目を見ない
_QUOTES = "'\u2019"
_HYPHENS = "-\u2010\u2011\u2012\u2013\u00ad"


def _fold(s: str) -> str:
    """大文字小文字を残して整える（NFKC・改行前のハイフンをつなぐ・ハイフンと空白を 1 つに）。"""
    s = re.sub(rf"[{_HYPHENS}]\s*\n\s*", "", s)
    s = unicodedata.normalize("NFKC", s)
    return " ".join(re.sub(rf"[{_HYPHENS}]+", " ", s).split())


def prep_text(s: str) -> str:
    """照合の前の整え方: 改行の前のハイフン（``thermo-\\nelectric``）はつなぎ、NFKC・大文字小文字・
    空白・ハイフンを 1 つの空白に揃える。"""
    return _fold(s).casefold()


def _is_word(c: str) -> bool:
    return (c.isalnum() and ord(c) < _CJK_FROM) or c in _QUOTES


#: 単数と複数が同じ形の語・``s`` で終わるが単数の語（小さな表）
_INVARIANT = frozenset({
    "series", "species", "news", "bias", "gas", "lens", "chaos", "atlas", "canvas", "axis",
    "basis", "analysis", "synthesis", "hypothesis", "thesis", "diagnosis", "crisis", "physics",
    "mathematics", "statistics", "mechanics", "kinetics", "thermodynamics",
})  # fmt: skip
#: 不規則な複数形 → 単数形
_IRREGULAR = {
    "analyses": "analysis", "syntheses": "synthesis", "hypotheses": "hypothesis",
    "theses": "thesis", "diagnoses": "diagnosis", "crises": "crisis", "matrices": "matrix",
    "indices": "index", "vertices": "vertex", "appendices": "appendix", "gases": "gas",
    "axes": "axis", "biases": "bias",
}  # fmt: skip


def _singular(w: str) -> str:
    """小文字の 1 語を単数形に寄せる（規則は 1 つ — :func:`term_key` と :func:`mentions` が共有）。
    数字を含む語・3 文字以下の語は触らない。``ies→y``・``zes→ze``・``xes/ches/shes`` は語幹・
    それ以外の末尾 ``s`` は 1 文字落とす（``ss``/``us``/``is`` 終わりと表の語は落とさない）。"""
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    if len(w) <= 3 or not re.fullmatch(r"[a-z]+", w) or w in _INVARIANT:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("zes"):
        return w[:-1]
    if re.search(r"(xes|ches|shes|sses)$", w):
        return w[:-2]
    if w.endswith("s") and not re.search(r"(ss|us|is)$", w):
        return w[:-1]
    return w


def _plurals(w: str) -> set[str]:
    out = {w + "s"}
    if re.search(r"(s|x|z|ch|sh)$", w):
        out.add(w + "es")
    if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
        out.add(w[:-1] + "ies")
    if w.endswith("is"):
        out.add(w[:-2] + "es")
    out |= {k for k, v in _IRREGULAR.items() if v == w}
    return out


def _word(raw: str) -> tuple[str, set[str]]:
    """(元の大文字小文字のままの 1 語) → ``(単数形の鍵, 同じ語とみなす表面の形)``。数字を含む語は
    そのまま。全部大文字の語（化学式・略号）は単数化せず、末尾に小文字の ``s`` 1 つが付いた形
    （``ZTs``）だけを同じとみなす。"""
    low = raw.casefold()
    if any(c.isdigit() for c in raw) or not re.fullmatch(r"[A-Za-z]+", raw):
        return low, {low}
    if re.fullmatch(r"[A-Z]{2,}s?", raw):
        base = low[:-1] if raw.endswith("s") else low
        return base, {base, base + "s"}
    sing = _singular(low)
    return sing, {low, sing, *_plurals(sing)}


def term_key(name: str) -> str:
    """名前を 1 つの用語にまとめるための鍵: 大文字小文字・全角半角・空白・ハイフンを揃え、英語の
    語は単数形に寄せる（:func:`_word` の規則）。"""
    return " ".join(_word(w)[0] for w in _fold(name).split(" "))


def mentions(text_norm: str, name: str) -> bool:
    """整えた文（:func:`prep_text`）に、用語の名前が現れるか。語の境目は Unicode の文字・数字
    （アポストロフィも語の一部。日本語などは境目を見ず部分一致）。英語は語ごとに単数・複数を同じと
    みなす（:func:`_word`）。ラテン文字だけで 3 文字未満の名前（``ZT``）は照合に使わない。英数字の
    無い名前は、PDF の抽出で入る空白を無視して比べる。"""
    n = _fold(name)
    bare = n.replace(" ", "")
    if not bare or (all(ord(c) < 0x250 for c in bare) and len(bare) < 3):
        return False
    pat = " ".join(
        "(?:" + "|".join(re.escape(f) for f in sorted(_word(w)[1], key=len, reverse=True)) + ")"
        for w in n.split(" ")
    )
    for m in re.finditer(pat, text_norm):
        a, b = m.start(), m.end()
        if _is_word(text_norm[a]) and a > 0 and _is_word(text_norm[a - 1]):
            continue
        if _is_word(text_norm[b - 1]) and b < len(text_norm) and _is_word(text_norm[b]):
            continue
        return True
    if not any(c.isalnum() and ord(c) < _CJK_FROM for c in n):
        return bare.casefold() in text_norm.replace(" ", "")
    return False


def _names_of(row: dict, by_slug: dict[str, SharedTerm]) -> list[str]:
    """概念の名前 = ``label_ja`` ・``label_en``・別名。既存の用語（``use: existing:<slug>``）は
    ストアの名前と別名も足す。"""
    names = [row.get("label_ja"), row.get("label_en"), *(row.get("aliases") or [])]
    use = str(row.get("use") or "")
    term = by_slug.get(use[len("existing:") :]) if use.startswith("existing:") else None
    if term is not None:
        names += [term.label, term.label_en, *term.aliases]
    return [n for n in names if isinstance(n, str) and n.strip()]


def check_ops(
    ops: list[dict],
    *,
    concepts: list[dict],
    existing: list[SharedTerm],
    by_id: dict[str, dict],
) -> tuple[list[dict], int]:
    """AI の差分を機械で確かめる → ``(残した操作, 落とした関係の数)``。

    落とす関係（``add_relation``）: ① ``evidence`` の番号が渡した文（``by_id``）に無い ② 両端の
    用語の名前のどれかが、根拠の文（複数なら連結）に現れない（:func:`mentions`）。残した関係の
    ``evidence`` は ``[{iri, text, path}]`` に置き換える（文はストアから引いた行から）。
    概念・問いは触らない（関係から参照されなくなっても概念は残す）。"""
    by_slug = {t.slug: t for t in existing}
    names = {str(c.get("key")): _names_of(c, by_slug) for c in concepts}
    for o in ops:
        if o.get("op") == "add_concept":
            names[str(o.get("key"))] = _names_of(o, by_slug)
    kept: list[dict] = []
    dropped = 0
    for o in ops:
        if o.get("op") != "add_relation":
            kept.append(o)
            continue
        ids = [str(i) for i in o.get("evidence") or []]
        rows = [by_id.get(i) for i in ids]
        if not ids or any(r is None for r in rows):
            dropped += 1
            continue
        said = prep_text(" ".join(r["text"] for r in rows if r))

        def present(ref: str, said: str = said) -> bool:
            ends = names.get(str(ref))
            if ends is None:  # 標準の語（curie）= 標準の語の名前
                std = themes.std_by_curie(str(ref))
                ends = [std.label, std.curie.partition(":")[2]] if std else []
            return any(mentions(said, n) for n in ends)

        if not (present(str(o.get("from"))) and present(str(o.get("to")))):
            dropped += 1
            continue
        seen: set[str] = set()
        evidence = []
        for r in rows:
            if r and r["iri"] not in seen:
                seen.add(r["iri"])
                evidence.append(
                    {"iri": r["iri"], "text": r["text"][:EVIDENCE_TEXT_MAX], "path": r["path"]}
                )
        kept.append({**o, "evidence": evidence})
    return kept, dropped


_Q_REFS = ("target", "kind", "property", "property2", "kind2")


def prune_unlinked(ops: list[dict], *, include_existing: bool = False) -> tuple[list[dict], int]:
    """検査で関係を落としたあとの後処理 → ``(操作, 落とした用語の数)``。どの関係にも問いにも出ない
    **新しい**用語（``use`` が既存でない ``add_concept``）を落とし、それを指す問いも落とす。
    ``include_existing=True`` は、関係を失った既存の用語の ``add_concept`` も外す。"""
    used: set[str] = set()
    for o in ops:
        if o.get("op") == "add_relation":
            used |= {str(o.get("from")), str(o.get("to"))}
        elif o.get("op") == "add_question":
            used |= {str(o[k]) for k in _Q_REFS if o.get(k)}
    gone = {
        str(o.get("key"))
        for o in ops
        if o.get("op") == "add_concept"
        and (include_existing or not str(o.get("use") or "new").startswith("existing:"))
        and str(o.get("key")) not in used
    }
    out = [
        o
        for o in ops
        if not (o.get("op") == "add_concept" and str(o.get("key")) in gone)
        and not (o.get("op") == "add_question" and any(str(o.get(k)) in gone for k in _Q_REFS))
    ]
    return out, len(gone)


# ---- 提案を作る（AI の往復・ロックの外） ------------------------------------------------------


_Q_MAX = 5
_FACTOR_DIR = {"lowers": "lowers", "raises": "raises", "affects": "affects"}
_Q_TEXT = {
    "ja": {
        "part": "{to} の一部は？",
        "sub": "{to} の種類は？",
        "lowers": "{to} を下げるものは？",
        "raises": "{to} を上げるものは？",
        "affects": "{to} を変えるものは？",
    },
    "en": {
        "part": "What are the parts of {to}?",
        "sub": "What are the kinds of {to}?",
        "lowers": "What lowers {to}?",
        "raises": "What raises {to}?",
        "affects": "What changes {to}?",
    },
}


def _is_japanese(name: str) -> bool:
    return any(ord(c) >= _CJK_FROM for c in name)


def _join_evidence(a: list[str], b: list[str]) -> list[str]:
    return list(dict.fromkeys([*a, *b]))[:3]


def build_ops(
    relations: list[dict], existing: list[SharedTerm]
) -> tuple[list[dict], int]:
    """AI の関係（``from``/``to``/``rel``/``evidence`` と任意の ``*_ja``/``*_en``）→ 差分の ops と、
    用語の種類が合わなくて落とした関係の数。決定論。

    - 名前を :func:`term_key` で 1 つの用語にまとめる。既存の用語の名前・別名に当たれば
      ``use: existing:<slug>``・既存の種類・**既存の名前**（札・問い文もそれ）。同じ名前の既存の
      用語が複数あれば、関係の位置に合う種類（lowers 系の相手 = property・part/sub は相手側の種類）
      を選ぶ（合うものが無ければ先勝ち）。標準の語は、適用の段の照合が当てる。
    - 種類: ``lowers``/``raises``/``affects`` の相手（to）= ``property``、それ以外 = ``class``。
      食い違う関係（``part``/``sub`` の両端の種類が違う・相手が class の ``lowers`` 系・自己参照）
      は落とす。
    - 名前: ``label_en`` = ``*_en`` → ラテン文字の名前 → 空。``label_ja`` = ``*_ja`` → 日本語の
      名前 → 名前。書かれた別の名前は別名に足す（照合にも使う）。
    - 同じ (from, rel, to) は 1 本にまとめ、根拠の番号は出現順に最大 3 つ。
    - 鍵は ``n1``… を出てきた順に振る。``add_concept`` → ``add_relation`` の順。問いは含めない。"""
    by_name: dict[str, list[SharedTerm]] = {}
    for t in sorted(existing, key=lambda x: x.slug):
        for nm in (t.label, t.label_en, *t.aliases):
            if nm:
                lst = by_name.setdefault(term_key(nm), [])
                if t not in lst:
                    lst.append(t)
    terms: dict[str, dict] = {}  # 用語の鍵 → {en, ja, aliases, kind, cands, existing}

    def end(name: str, ja: str | None, en: str | None) -> str:
        k = term_key(name)
        cands = by_name.get(k) or next(
            (by_name[term_key(x)] for x in (ja, en) if x and term_key(x) in by_name), []
        )
        if cands:
            k = "existing:" + "|".join(t.slug for t in cands)  # 言語が違っても同じ既存の用語
        t = terms.get(k)
        if t is None:
            t = terms[k] = {
                "en": en or ("" if _is_japanese(name) else name),
                "ja": ja or name,
                "aliases": [], "kind": None, "cands": cands, "existing": None,
            }  # fmt: skip
        for nm in (name, ja, en):
            if nm and nm not in (t["en"], t["ja"]) and nm not in t["aliases"]:
                t["aliases"].append(nm)  # 単数・複数・別の言語など、同じ用語の別の書き方
        return k

    pairs = [
        (end(r["from"], r.get("from_ja"), r.get("from_en")),
         end(r["to"], r.get("to_ja"), r.get("to_en")), r)
        for r in relations
    ]  # fmt: skip
    factor_to = {to for _f, to, r in pairs if r["rel"] in _FACTOR_DIR}
    for k, t in terms.items():  # 既存の用語を選ぶ（1 つに決まらなければ関係の位置の種類で）
        if len(t["cands"]) == 1:
            t["existing"] = t["cands"][0]
        elif t["cands"] and k in factor_to:
            t["existing"] = next((c for c in t["cands"] if c.kind == "property"), t["cands"][0])
        if t["existing"] is not None:
            t["kind"] = t["existing"].kind
        elif not t["cands"]:
            t["kind"] = "property" if k in factor_to else "class"
    for _ in range(2):  # 残り（同名・種類違い）は、part/sub の相手側の種類に合わせる
        for k, t in terms.items():
            if t["cands"] and t["existing"] is None:
                partner = [
                    terms[b if a == k else a]["kind"] for a, b, r in pairs
                    if r["rel"] in ("part", "sub") and k in (a, b) and a != b
                ]  # fmt: skip
                pick = next((c for c in t["cands"] if c.kind in partner), None)
                if pick is not None:
                    t["existing"], t["kind"] = pick, pick.kind
    for t in terms.values():
        if t["existing"] is None and t["kind"] is None:
            t["existing"] = t["cands"][0]
            t["kind"] = t["existing"].kind
        if t["existing"] is not None:  # 札・問い文は既存の名前（書かれた名前は別名に回す）
            ex = t["existing"]
            for nm in (t["en"], t["ja"]):
                if nm and nm not in (ex.label, ex.label_en) and nm not in t["aliases"]:
                    t["aliases"].append(nm)
            t["ja"], t["en"] = ex.label, ex.label_en or ""
    dropped = 0
    merged: dict[tuple[str, str, str], list[str]] = {}
    for f, to, r in pairs:
        kf, kt = terms[f]["kind"], terms[to]["kind"]
        same = r["rel"] in ("part", "sub") and kf == kt
        factor = r["rel"] in _FACTOR_DIR and kt == "property"
        if f == to or not (same or factor):
            dropped += 1
            continue
        merged[(f, r["rel"], to)] = _join_evidence(merged.get((f, r["rel"], to), []), r["evidence"])
    keys: dict[str, str] = {}
    ops: list[dict] = []
    for f, _rel, to in merged:
        for k in (f, to):
            if k not in keys:
                keys[k] = f"n{len(keys) + 1}"
                t = terms[k]
                ops.append(
                    {
                        "op": "add_concept", "key": keys[k], "label_ja": t["ja"],
                        "label_en": t["en"], "aliases": t["aliases"][:12], "kind": t["kind"],
                        "use": f"existing:{t['existing'].slug}" if t["existing"] else "new",
                        "reason": "",
                    }
                )  # fmt: skip
    for (f, rel, to), ev in merged.items():
        ops.append(
            {
                "op": "add_relation", "from": keys[f], "rel": rel, "to": keys[to],
                "evidence": ev, "reason": "",
            }
        )  # fmt: skip
    return ops, dropped


def add_questions(ops: list[dict], lang: str) -> tuple[list[dict], int]:
    """残った関係から問いを機械が組んで ops の末尾に足す → ``(ops, 5 つを超えて落とした関係の数)``。

    相手（to）ごとの問い（part → 一部・sub → 種類・lowers/raises/affects → 下げる／上げる／
    変えるもの）。関係を向きなしの網として、新しい用語を含む連結成分ごとに、相手の多い用語の問いを
    1 つ選ぶ（= 全部の用語が問いの対象から関係でたどれる最小の数）。最大 5。5 を超える成分の
    関係は落とす（用語は ``prune_unlinked`` 相当で残らない）。"""
    lg = "en" if lang == "en" else "ja"
    rels = [o for o in ops if o.get("op") == "add_relation"]
    concepts = {str(o["key"]): o for o in ops if o.get("op") == "add_concept"}
    parent: dict[str, str] = {k: k for k in concepts}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rels:
        parent[find(str(r["from"]))] = find(str(r["to"]))
    comps: dict[str, list[dict]] = {}
    for r in rels:
        comps.setdefault(find(str(r["from"])), []).append(r)
    chosen: list[dict] = []
    for members in sorted(comps.values(), key=len, reverse=True):
        if not any(str(c["use"]) == "new" for k, c in concepts.items()
                   if find(k) == find(str(members[0]["from"]))):  # fmt: skip
            continue  # 既存の用語だけの成分は、承認に問いを要らない
        incoming: dict[str, int] = {}
        for r in members:
            incoming[str(r["to"])] = incoming.get(str(r["to"]), 0) + 1
        target = max(incoming, key=lambda k: (incoming[k], -int(k[1:])))
        chosen.append(next(r for r in members if str(r["to"]) == target))
    dropped = 0
    if len(chosen) > _Q_MAX:
        left = {find(str(r["from"])) for r in chosen[_Q_MAX:]}
        chosen = chosen[:_Q_MAX]
        before = len(rels)
        ops = [o for o in ops if o.get("op") != "add_relation" or find(str(o["from"])) not in left]
        dropped = before - sum(1 for o in ops if o.get("op") == "add_relation")
    questions = []
    for r in chosen:
        tgt = concepts[str(r["to"])]
        name = (tgt["label_en"] or tgt["label_ja"]) if lg == "en" else tgt["label_ja"]
        rel = str(r["rel"])
        questions.append(
            {
                "op": "add_question", "question": _Q_TEXT[lg][rel].format(to=name),
                "mode": "knowledge",
                "op_kind": "parts" if rel == "part" else "kinds" if rel == "sub" else "factors",
                "target": str(r["to"]), "direction": _FACTOR_DIR.get(rel),
                "kind": None, "property": None, "value": "", "match": None, "property2": None,
                "kind2": None, "wanted_data": "", "reason": "",
            }
        )  # fmt: skip
    return [*ops, *questions], dropped


async def mark_supports(client: Any, ops: list[dict]) -> list[dict]:
    """関係の両端が既存の用語で、その 3 つ組が既に確定しているなら、op に ``supports: True``
    を付ける（13a: 承認すると新しい線は増えず、この論文の根拠の文だけが足される）。
    関係が無い・検索に失敗したときは ops をそのまま返す（札が出ないだけ）。"""
    from asterism.crosswalk_runtime import ALIGN_RELATIONS

    concepts = {str(o.get("key")): o for o in ops if o.get("op") == "add_concept"}
    out: list[dict] = []
    for o in ops:
        if o.get("op") == "add_relation":
            a, b = concepts.get(str(o.get("from"))), concepts.get(str(o.get("to")))
            ua, ub = str((a or {}).get("use") or ""), str((b or {}).get("use") or "")
            if a and b and ua.startswith("existing:") and ub.startswith("existing:"):
                try:
                    pred = themes.relation_predicate(str(o.get("rel")), str(a.get("kind")))
                    src = shared_vocab.term_iri(ua.split(":", 1)[1])
                    dst = shared_vocab.term_iri(ub.split(":", 1)[1])
                    if await shared_vocab._ask(
                        client,
                        f"ASK {{ GRAPH <{substrate.ALIGNMENT_GRAPH}> {{ <{src}> <{ALIGN_RELATIONS[pred]}> "
                        f"<{dst}> }} }}",
                    ):
                        o = {**o, "supports": True}
                except Exception:
                    logger.warning("paper propose: supports check failed", exc_info=True)
        out.append(o)
    return out


async def make_proposal(
    app: FastAPI,
    cfg: Settings,
    session_id: str,
    dataset_id: str,
    *,
    llm: Any,
    on_usage: Any,
    lang: str,
) -> dict:
    """論文の文を AI に渡して、検査を通した差分を返す。**ロックの外**で呼ぶ（読むときだけ
    ``themes_lock`` を短く取る）。返り = ``{"ok": True, "ops": […], "dropped": n, "truncated":
    bool}`` または ``{"ok": False}``（AI が形を守れない・届かない・文が無い）。例外は投げない。"""
    try:
        rows, truncated = select_sentences(await read_sentences(app, cfg, dataset_id))
        if not rows:
            return {"ok": False}
        existing = await shared_vocab._read_terms(app.state.client)
        terms = [{"label_ja": t.label, "label_en": t.label_en or ""} for t in existing]
        value = await asyncio.to_thread(
            propose_paper_relations,
            llm,
            [{"id": r["id"], "text": r["text"]} for r in rows],
            terms,
            lang,
        )
        if on_usage is not None:
            try:
                await asyncio.to_thread(on_usage)
            except Exception:
                logger.warning("paper propose: usage not recorded", exc_info=True)
        ops, kind_gone = build_ops(value.get("relations") or [], existing)
        ops, evidence_gone = check_ops(
            ops, concepts=[], existing=existing, by_id={r["id"]: r for r in rows}
        )
        ops, terms_dropped = prune_unlinked(ops, include_existing=True)
        ops, q_gone = add_questions(ops, lang)
        ops, more_terms = prune_unlinked(ops, include_existing=True)
        ops = await mark_supports(app.state.client, ops)
        return {
            "ok": True, "ops": ops, "dropped": evidence_gone, "kind_dropped": kind_gone,
            "q_dropped": q_gone, "over_limit": int(value.get("over_limit") or 0),
            "truncated": truncated, "terms_dropped": terms_dropped + more_terms,
            "shape_dropped": int(value.get("shape_dropped") or 0),
        }  # fmt: skip
    except PaperProposeError as exc:
        logger.warning("paper propose: shape refused: %s", exc)
    except Exception:
        logger.warning("paper propose failed", exc_info=True)
    return {"ok": False}


# ---- 下書きに当てる（ロックの中・読み直した話に） --------------------------------------------


async def _draft_refused(
    client: Any, root: Any, draft: dict, lang: str, turn: dict, at: str
) -> bool:
    """この下書き（提案を当てる前）が、すでに ``commit_theme`` の検査で断られるか。"""
    try:
        body = chat_turns.commit_body(draft, lang)
        body["batch"] = turn["batch"]
        await themes.check_commit(client, root, body, at=at)
    except themes.ThemeError:
        return True
    return False


async def apply_proposal(
    session: dict,
    turn: dict,
    proposal: dict,
    *,
    client: Any,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lang: str,
    label: str,
) -> None:
    """検査を通した差分を話の下書きに当て、論文の手番の返事を提案の札にする。``session`` と
    ``turn`` を書き換える（保存は呼ぶ側）。下書きに入れたものがあれば ``turn["paper"]`` に
    ``{draft_id, created, promoted, concepts, questions}``（この手番が足した用語の鍵と問い）・
    ``turn["before"]`` に当てる前の下書き（取り消しで戻す）。

    下書きが無ければ、確認済みのテーマの種・それも無ければ空の下書きを作る（何も入らなければ・
    例外が出たら作った下書きは捨てる）。当てた下書きが ``commit_theme`` の検査（書かずに走らせる）を
    通らないときは、提案を出さず論文だけにして注記を付ける。呼び手は ``proposal["ok"]`` が真の
    ときだけ呼ぶ。"""
    lg = "en" if lang == "en" else "ja"
    reply = turn["reply"]
    notes: list[dict] = list(reply.get("notes") or [])
    if proposal.get("truncated"):
        notes.append(chat_turns.note("info", text(lg, "long", n=MAX_SENTENCES)))
    if proposal.get("dropped"):
        notes.append(
            chat_turns.note("info", text(lg, "evidence_dropped", n=proposal["dropped"]))
        )
    for key, field in (("kind_dropped", "kind_dropped"), ("q_limit", "q_dropped"),
                       ("rel_limit", "over_limit")):  # fmt: skip
        if proposal.get(field):
            notes.append(chat_turns.note("info", text(lg, key, n=proposal[field])))
    if proposal.get("shape_dropped"):
        notes.append(
            chat_turns.note("info", text(lg, "shape_dropped", n=proposal["shape_dropped"]))
        )
    if proposal.get("terms_dropped"):
        notes.append(
            chat_turns.note("info", text(lg, "terms_dropped", n=proposal["terms_dropped"]))
        )
    ops = list(proposal.get("ops") or [])
    if not ops:  # 検査と後処理で何も残らなかった — 黙らずに言う
        notes.append(chat_turns.note("info", text(lg, "empty")))
    draft = chat_turns._current_draft(session, root)
    created = False
    fresh_seed: dict | None = None
    try:
        if ops and draft is None:
            created = True
            theme = chat_turns._theme_of(session, root)
            if theme is not None:
                seed = chat_seed.seed_from_theme(root, existing, theme)
                draft, fresh_seed = chat_turns._seeded_draft(theme, seed, [], root=root, at=at)
            else:
                made = themes.create_draft(root, label[:80], [], at=at)
                draft = chat_turns._save(root, {**made, "step": 2}, at)
        applied_ops: list[dict] = []
        applied_sentences: list[str] = []
        saved_ok = False
        if ops and draft is not None:
            new, report = graph_diff.apply_to_draft(
                draft, ops, existing=existing, dismissed=dismissed, by="ai"
            )
            sentences = graph_diff.ops_to_sentences(ops, {"concepts": draft.get("concepts")}, lg)
            gone = {d["index"]: d["reason"] for d in report["dropped"]}
            for i, reason in gone.items():
                said = sentences[i] if i < len(sentences) else ""
                notes.append(
                    chat_turns.note("dropped", f"{said}: {chat_turns.dropped_reason(reason, lg)}")
                )
            kept = [i for i in range(len(ops)) if i not in gone]
            applied_ops = [ops[i] for i in kept]
            applied_sentences = [sentences[i] for i in kept if i < len(sentences)]
            if report["applied"] > 0:
                try:  # commit_theme と同じ検査を、書かずに走らせる
                    body = chat_turns.commit_body(new, lang)
                    body["batch"] = turn["batch"]
                    await themes.check_commit(client, root, body, at=at)
                except themes.ThemeError:
                    # 当てる前の下書きでも断られるなら、原因は前からの下書き — 提案は出して注記する
                    old_keys = {str(c.get("key")) for c in draft.get("concepts") or []}
                    names = [
                        str(c.get("label_ja") or c.get("key"))
                        for c in new.get("concepts") or []
                        if str(c.get("key")) in report["unlinked"]
                        and str(c.get("key")) in old_keys
                    ]
                    if (
                        not created
                        and names
                        and await _draft_refused(client, root, draft, lang, turn, at)
                    ):
                        saved_ok = True
                        report = {**report, "unlinked": [
                            k for k in report["unlinked"] if k in old_keys
                        ]}  # fmt: skip
                    else:
                        notes.append(chat_turns.note("info", text(lg, "cannot_link")))
                        applied_ops, applied_sentences = [], []
                else:
                    saved_ok = True
            if saved_ok:
                if report["unlinked"]:  # 検査は通ったが孤立がある、または前からの孤立
                    by_key = {str(c.get("key")): c for c in new.get("concepts") or []}
                    shown = [
                        str((by_key.get(k) or {}).get("label_ja") or k)
                        for k in report["unlinked"]
                    ]
                    notes.append(
                        chat_turns.note("info", text(lg, "unlinked", names="、".join(shown[:8])))
                    )
                chat = draft.get("chat") or {}
                new["chat"] = {
                    "messages": list(chat.get("messages") or []),
                    "proposals": [
                        *(chat.get("proposals") or []),
                        {
                            "proposal_id": graph_diff.proposal_id(ops),
                            "ops": applied_ops,
                            "at": at,
                            "by": "ai",
                        },
                    ],
                }
                before = None if created else copy.deepcopy(draft)
                saved = chat_turns._save(root, new, at)
                chat_turns._adopt_draft(session, saved)
                if fresh_seed is not None:
                    chat_turns._adopt_seed(session, saved, fresh_seed)
                had_c = {str(c.get("key")) for c in draft.get("concepts") or []}
                had_q = set(draft.get("questions") or [])
                turn["before"] = before
                turn["paper"] = {
                    "draft_id": saved["id"],
                    "created": created,
                    "promoted": False,
                    "concepts": [
                        str(c.get("key")) for c in saved.get("concepts") or []
                        if str(c.get("key")) not in had_c
                    ],
                    "questions": [q for q in saved.get("questions") or [] if q not in had_q],
                }  # fmt: skip
        if created and not saved_ok and draft is not None:  # 何も入らなかった — 下書きは残さない
            themes.delete_draft(root, draft["id"])
    except BaseException:
        if created and draft is not None:  # 作った下書きを孤児にしない
            themes.delete_draft(root, draft["id"])
            if session.get("draft_id") == draft["id"]:
                session["draft_id"] = None
                session.pop("seed", None)
        raise
    n_c = sum(1 for o in applied_ops if o.get("op") == "add_concept")
    n_r = sum(1 for o in applied_ops if o.get("op") == "add_relation")
    n_q = sum(1 for o in applied_ops if o.get("op") == "add_question")
    if n_c or n_r or n_q:
        body_text = str(reply.get("text") or "").strip()
        n_k = sum(1 for o in applied_ops if o.get("op") == "add_relation" and o.get("supports"))
        k_txt = text(lg, "supports_k", k=n_k) if n_k else ""
        reply["text"] = f"{body_text} {text(lg, 'summary', c=n_c, r=n_r, k=k_txt, q=n_q)}".strip()
    reply["ops"] = applied_ops
    reply["sentences"] = applied_sentences
    reply["notes"] = notes
