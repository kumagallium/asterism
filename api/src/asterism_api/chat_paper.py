"""添えた論文から、用語・関係・問いを根拠の文つきで提案する（ui-redesign PR 12b・design.md §20）。

12a の走者（``chat_attach.run_document_attach``）に「提案」の段を 1 つ足す部品。流れ:

1. 下書きの版 graph の ``doco:Sentence`` を、出現順に ``s1``… の番号つきで並べる（参考文献・
   謝辞の節は除く・上限 300 文。決定論・:func:`read_sentences` / :func:`select_sentences`）。
2. ``graph_propose``（``screen="paper"``）に番号つきの文・既存の用語・標準の語の名前を渡す。
   AI は関係ごとに**文の番号**（``evidence``）を選ぶだけで、引用文は書かない。
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
from typing import TYPE_CHECKING, Any

from asterism import chat_sessions, graph_diff, shared_vocab, substrate, themes
from asterism import documents as doc
from asterism.paper_sections import _heading_key, _is_abstract, _is_conclusion
from asterism.shared_vocab import SharedTerm
from asterism_step0.graph_propose import GraphDiffError, propose_graph_diff
from asterism_step0.theme_propose import ThemeDraftError

from asterism_api import chat_seed, chat_turns, registry

if TYPE_CHECKING:
    from fastapi import FastAPI

    from asterism_api.main import Settings

__all__ = [
    "EVIDENCE_TEXT_MAX",
    "MAX_SENTENCES",
    "apply_proposal",
    "build_rows",
    "check_ops",
    "make_proposal",
    "mentions",
    "read_sentences",
    "select_sentences",
    "text",
]

logger = logging.getLogger(__name__)

#: AI に渡す文の上限（超えたら要旨・結果・考察・結論を優先して残す）
MAX_SENTENCES = 300
#: 下書きの根拠の文（evidence.text）の長さ
EVIDENCE_TEXT_MAX = 150
#: 参考文献・謝辞の節（見出しを ``_heading_key`` で整えて比べる）
_SKIP_TITLE = re.compile(
    r"^(references?|bibliography|literature cited|works cited|acknowledge?ments?"
    r"|acknowledgments?|参考文献|引用文献|文献|謝辞)$"
)
#: 節の DEO の役割（``documents._DEO_ROLES`` 以外が付いていたとき用）
_SKIP_ROLES = frozenset({"acknowledgements", "acknowledgments", "bibliography", "references"})
#: 切るときに優先する役割
_PREFER_ROLES = frozenset({"results", "discussion", "conclusion", "abstract"})

_ASK = (
    "Read the numbered sentences of the paper the person attached. Propose the terms, the "
    "relations between them (each with the numbers of the sentences that state it) and the "
    "questions the paper could answer."
)

_T: dict[str, dict[str, str]] = {
    "ja": {
        "summary": "用語 {c}・関係 {r}・問い {q} を提案しました。",
        "long": "論文が長いので、先頭から 300 文だけを読みました",
        "evidence_dropped": "根拠の文が合わない関係を {n} 件除きました",
        "propose_failed": "論文からの提案を作れませんでした（論文は添えました）",
        "commit_failed": "提案は確定できませんでした（論文は公開しました）",
        "unlinked": (
            "問いにつながらない用語があります（承認の前に、関係を足すか外してください）: {names}"
        ),
    },
    "en": {
        "summary": "Proposed {c} terms, {r} relations and {q} questions.",
        "long": "The paper is long, so only the first 300 sentences were read",
        "evidence_dropped": "Left out {n} relations whose evidence sentences do not fit",
        "propose_failed": "Could not make proposals from the paper (the paper is attached)",
        "commit_failed": "Could not confirm the proposals (the paper was published)",
        "unlinked": "Some terms do not connect to a question (add a relation or remove them before "
        "approving): {names}",
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


def select_sentences(rows: list[dict], limit: int = MAX_SENTENCES) -> tuple[list[dict], bool]:
    """AI に渡す文を選び、出現順に ``s1``… を振る → ``(行, 切ったか)``。純関数。

    参考文献・謝辞は除く。``limit`` を超えたら、要旨・結果・考察・結論の節の文を優先し、余りを
    先頭から詰める（役割が 1 つも取れなければ先頭から）。"""
    kept = [r for r in rows if not r["skip"]]
    truncated = len(kept) > limit
    if truncated:
        preferred = [r for r in kept if r["prefer"]]
        if preferred:
            head = preferred[:limit]
            taken = {r["iri"] for r in head}
            rest = [r for r in kept if r["iri"] not in taken][: limit - len(head)]
            kept = sorted([*head, *rest], key=lambda r: (r["begin"], r["iri"]))
        else:
            kept = kept[:limit]
    return [{**r, "id": f"s{i}"} for i, r in enumerate(kept, start=1)], truncated


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


def mentions(text_norm: str, name: str) -> bool:
    """正規化した文（``normalize_label``）に、用語の名前が現れるか。大文字小文字・全角半角・
    空白は正規化。英数字で始まる／終わる名前は語の境目で（``conductivity`` は
    ``conductivities`` に当たらない）。英数字の無い名前（日本語など）は、PDF の抽出で入る空白を
    無視して部分一致。"""
    n = shared_vocab.normalize_label(name)
    if not n:
        return False
    pat = re.escape(n)
    if re.match(r"[a-z0-9]", n[0]):
        pat = r"(?<![a-z0-9])" + pat
    if re.match(r"[a-z0-9]", n[-1]):
        pat += r"(?![a-z0-9])"
    if re.search(pat, text_norm):
        return True
    if not re.search(r"[a-z0-9]", n):
        return n.replace(" ", "") in text_norm.replace(" ", "")
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
        said = shared_vocab.normalize_label(" ".join(r["text"] for r in rows if r))

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


# ---- 提案を作る（AI の往復・ロックの外） ------------------------------------------------------


def _context(
    session: dict, root: Any, existing: list[SharedTerm]
) -> tuple[dict, list[dict], list[dict]]:
    """AI に見せる図 → ``(graph, standards, 概念の行)``。下書きがあればそれ・無くて確認済みの
    テーマがあればその種・どちらも無ければ空（``prepare_text_turn`` と同じ順）。"""
    draft = themes.load_draft(root, session["draft_id"]) if session.get("draft_id") else None
    if draft is not None:
        graph, standards = chat_turns.theme_context(draft)
        return graph, standards, list(draft.get("concepts") or [])
    theme = chat_turns._theme_of(session, root)
    if theme is not None:
        seed = chat_seed.seed_from_theme(root, existing, theme)
        graph, standards = chat_turns.theme_context(seed)
        return graph, standards, list(seed.get("concepts") or [])
    return (
        {"concepts": [], "relations": [], "questions": []},
        themes.default_standards([]),
        [],
    )


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
        lock: asyncio.Lock = app.state.themes_lock
        async with lock:
            session = chat_sessions.load_session(cfg.registry_root, session_id)
            if session is None:
                return {"ok": False}
            graph, standards, concepts = _context(session, cfg.registry_root, existing)
        terms = [
            {"slug": t.slug, "label_ja": t.label, "label_en": t.label_en or "", "kind": t.kind}
            for t in existing
        ]
        value = await asyncio.to_thread(
            propose_graph_diff,
            llm,
            screen="paper",
            graph=graph,
            existing_terms=terms,
            standards=standards,
            columns=None,
            messages=[],
            message=_ASK,
            lang=lang,
            sentences=[{"id": r["id"], "text": r["text"]} for r in rows],
        )
        if on_usage is not None:
            try:
                await asyncio.to_thread(on_usage)
            except Exception:
                logger.warning("paper propose: usage not recorded", exc_info=True)
        ops, dropped = check_ops(
            value.get("ops") or [],
            concepts=concepts,
            existing=existing,
            by_id={r["id"]: r for r in rows},
        )
        return {"ok": True, "ops": ops, "dropped": dropped, "truncated": truncated}
    except (GraphDiffError, ThemeDraftError) as exc:
        logger.warning("paper propose: shape refused: %s", exc)
    except Exception:
        logger.warning("paper propose failed", exc_info=True)
    return {"ok": False}


# ---- 下書きに当てる（ロックの中・読み直した話に） --------------------------------------------


def apply_proposal(
    session: dict,
    turn: dict,
    proposal: dict,
    *,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lang: str,
    label: str,
) -> None:
    """検査を通した差分を話の下書きに当て、論文の手番の返事を提案の札にする。``session`` と
    ``turn`` を書き換える（保存は呼ぶ側）。下書きに入れたものがあれば ``turn["paper"]`` に
    ``{draft_id, created, promoted}``・``turn["before"]`` に当てる前の下書き（取り消しで戻す）。

    下書きが無ければ、確認済みのテーマの種・それも無ければ空の下書きを作る（何も入らなければ
    作った下書きは捨てる）。呼び手は ``proposal["ok"]`` が真のときだけ呼ぶ。"""
    lg = "en" if lang == "en" else "ja"
    reply = turn["reply"]
    notes: list[dict] = list(reply.get("notes") or [])
    if proposal.get("truncated"):
        notes.append(chat_turns.note("info", text(lg, "long")))
    if proposal.get("dropped"):
        notes.append(
            chat_turns.note("info", text(lg, "evidence_dropped", n=proposal["dropped"]))
        )
    ops = list(proposal.get("ops") or [])
    draft = chat_turns._current_draft(session, root)
    created = False
    fresh_seed: dict | None = None
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
            if report["unlinked"]:  # 承認（commit_theme）が断る理由を、先に見せる
                by_key = {str(c.get("key")): c for c in new.get("concepts") or []}
                shown = [
                    str((by_key.get(k) or {}).get("label_ja") or k) for k in report["unlinked"]
                ]
                notes.append(
                    chat_turns.note("info", text(lg, "unlinked", names="、".join(shown[:8])))
                )
            before = None if created else copy.deepcopy(draft)
            try:
                saved = chat_turns._save(root, new, at)
            except chat_turns.TurnRefused:
                if created:
                    themes.delete_draft(root, draft["id"])
                raise
            chat_turns._adopt_draft(session, saved)
            if fresh_seed is not None:
                chat_turns._adopt_seed(session, saved, fresh_seed)
            turn["before"] = before
            turn["paper"] = {"draft_id": saved["id"], "created": created, "promoted": False}
        elif created:  # 何も入らなかった — 作った下書きは残さない
            themes.delete_draft(root, draft["id"])
    n_c = sum(1 for o in applied_ops if o.get("op") == "add_concept")
    n_r = sum(1 for o in applied_ops if o.get("op") == "add_relation")
    n_q = sum(1 for o in applied_ops if o.get("op") == "add_question")
    if n_c or n_r or n_q:
        body = str(reply.get("text") or "").strip()
        reply["text"] = f"{body} {text(lg, 'summary', c=n_c, r=n_r, q=n_q)}".strip()
    reply["ops"] = applied_ops
    reply["sentences"] = applied_sentences
    reply["notes"] = notes
