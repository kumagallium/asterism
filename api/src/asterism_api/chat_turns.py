"""話の手番の組み立て（ui-redesign PR 11a・§19）— 純粋寄りの関数。

``chat_routes`` が ``themes_lock`` の中で呼ぶ。ここは HTTP を知らない（拒むときは
:class:`TurnRefused`）。ストアの読み（既にある用語）は引数で渡され、下書きの読み書きは
``root`` を介して ``asterism.themes`` に任せる。話（``session``）は**書き換える**が、保存は
呼ぶ側（ルート）がする。

* :func:`run_text_turn` — 人の文の手番（AI 無し・11a）。文を問いとして下書きに入れ、機械の文を返す。
* :func:`prepare_text_turn` → :func:`ai_text_turn` → :func:`apply_text_turn` — 人の文の手番
  （AI あり・11b）。準備（ロックの中・読むだけ）→ AI（**ロックの外**・``asyncio.to_thread``）→
  適用（ロックの中で話と下書きを読み直して当てる）。下書きが無ければ ``propose_theme_draft``・あれば
  ``propose_graph_diff``。AI が失敗したら下書きも作らない。
* :func:`run_undo_turn` — 「さっきのを戻して」。AI を呼ばず決定論で最後の提案を戻す。
* :func:`run_ops_turn` — 人の直接編集（差分を当てる）。
* :func:`run_set_answer_turn` — 既にある問いの答え方を人が決める（AI 無しでも ✓ が通る）。
* :func:`commit_body` — ✓ の本文（UI の ``buildCommitBody`` の移植）。
* :func:`confirm` — ✓。未確認の返事をまとめて確認済みにする。
* :func:`revert` — 未確認の返事を控えに戻す。

AI は差分と文だけ（SPARQL・コード・IRI を書かせない・値は渡さない）。9 関係・種別・循環の検査は
``graph_diff`` と ``themes`` の既存の検査をそのまま通す。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from asterism import chat_sessions, crosswalk_runtime, graph_diff, query_tools, shared_vocab, themes
from asterism.shared_vocab import SharedTerm, normalize_label
from asterism_step0.graph_propose import (
    _QUESTION_LIKE,
    GraphDiffError,
    propose_graph_diff,
    validate_graph_diff,
)
from asterism_step0.theme_propose import ThemeDraftError, propose_theme_draft

from asterism_api import chat_intent, chat_seed

logger = logging.getLogger(__name__)

__all__ = [
    "PreparedTurn",
    "TurnRefused",
    "ai_text_turn",
    "apply_text_turn",
    "ask_history",
    "ask_remote",
    "blank_answer",
    "commit_body",
    "confirm",
    "confirm_only",
    "filter_ops_to_scope",
    "is_undo",
    "prepare_text_turn",
    "resolve_scope",
    "revert",
    "revert_confirmed",
    "run_ask_turn",
    "run_ask_unavailable_turn",
    "run_ops_turn",
    "run_scope_hint_turn",
    "run_set_answer_turn",
    "run_show_turn",
    "run_text_turn",
    "run_undo_turn",
    "scope_aliases",
    "scope_prompt",
    "sync_answers",
    "theme_context",
]

MAX_TEXT = 2000
MAX_OPS = 50
_QUOTE_MAX = 80


class TurnRefused(Exception):
    """手番を受けない（``status`` は HTTP の写像）。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _lg(lang: str | None) -> str:
    return "en" if (lang or "").lower().startswith("en") else "ja"


# ---- 返事・メモ ----------------------------------------------------------------------------


def note(kind: str, text: str, choice: dict | None = None) -> dict:
    return {"kind": kind, "text": text, "choice": choice}


def _reply(
    text: str,
    *,
    ops: list[dict] | None = None,
    sentences: list[str] | None = None,
    questions: list[str] | None = None,
    bindings: list[dict] | None = None,
    notes: list[dict] | None = None,
) -> dict:
    """返事 1 枚の形。欄は全部いつも入れる（空でも ``[]`` / ``None``）。"""
    return {
        "text": text,
        "ops": ops or [],
        "sentences": sentences or [],
        "questions": questions or [],
        "bindings": bindings or [],
        "notes": notes or [],
        "answer": None,
        "table": None,
    }


_AI_UNSET = {
    "ja": "AI が設定されていないので、用語と関係は知識グラフの下の欄で手で追加できます。"
    "設定 › AI で鍵を入れると AI が提案します",
    "en": "AI is not set up, so add terms and relations by hand in the panel "
    "under the knowledge graph. "
    "Add a key in Settings › AI and the AI will propose them.",
}

#: 落とした操作の理由（日本語の文・``themes:chat.dropped.*`` と同じ語彙）
_DROPPED = {
    "ja": {
        "duplicate": "すでにあります",
        "cycle": "輪になります",
        "mixed": "種類と項目が混ざります",
        "unknown_key": "知らない名前です",
        "dismissed": "前に削除したものです",
        "invalid": "形が合いません",
        "unsupported": "ここでは使えません",
        "dataset_only": "表の列だけに使えます",
        "same": "自分自身にはつなげません",
        "quantity_target": "量は、項目から量の種類へだけつなげます",
        "target_not_property": "下げる・上げる・変えるの相手は項目だけです",
        "not_found": "見つかりません",
        "limit": "上限に達しました",
        "not_renamable": "名前を修正できません",
    },
    "en": {
        "duplicate": "already there",
        "cycle": "it would loop",
        "mixed": "a kind and an item cannot be mixed",
        "unknown_key": "unknown name",
        "dismissed": "you deleted it before",
        "invalid": "the shape does not fit",
        "unsupported": "not usable here",
        "dataset_only": "only for table columns",
        "same": "cannot link to itself",
        "quantity_target": "a quantity links only from an item to a quantity kind",
        "target_not_property": "the other end must be an item",
        "not_found": "not found",
        "limit": "limit reached",
        "not_renamable": "cannot be renamed",
    },
}


def dropped_reason(reason: str, lang: str) -> str:
    table = _DROPPED[_lg(lang)]
    return table.get(reason) or table["invalid"]


def _quote(text: str) -> str:
    s = " ".join(text.split())
    return s if len(s) <= _QUOTE_MAX else s[:_QUOTE_MAX].rstrip() + "…"


# ---- 答え方の行（UI の blankAnswer / syncAnswers と同じ形） ------------------------------------


def blank_answer(question: str) -> dict:
    return {
        "question": question,
        "mode": "memo",
        "op": "",
        "target": "",
        "direction": "",
        "kind": "",
        "property": "",
        "value": "",
        "match": "",
        "property2": "",
        "kind2": "",
        "memo": "none",
        "wanted_data": "",
        "reason": "",
        "by": "human",
    }


def sync_answers(answers: list[dict], questions: list[str]) -> list[dict]:
    """答え方の行を問いの並びに合わせる（消えた問いの行は落とし、増えた問いは「まだ」の行）。"""
    by = {normalize_label(str(a.get("question") or "")): a for a in answers}
    return [by.get(normalize_label(q)) or blank_answer(q) for q in questions]


# ---- 手番の記録 ------------------------------------------------------------------------------


def _append_turn(
    session: dict,
    *,
    at: str,
    user: dict | None,
    reply: dict,
    state: str,
    before: dict | None,
    by: str,
    route: str | None = None,
) -> dict:
    turn_id = chat_sessions.new_turn_id(session)
    batch = f"{session['id']}:{turn_id}"
    turn = {
        "id": turn_id,
        "at": at,
        "user": user,
        "reply": reply,
        "batch": batch[:100],
        "state": state,
        "before": before if state == "proposed" else None,
        "commit": None,
        "by": by,
    }
    if route:  # 11m: 1 文の振り分け先（chat／ask）。UI が返事の見せ方を変える
        turn["route"] = route
    session["turns"].append(turn)
    return turn


def _current_draft(session: dict, root: Any) -> dict | None:
    """いま育てている下書き（消えていたら ``draft_id`` を外して ``None``）。"""
    draft_id = session.get("draft_id")
    if not draft_id:
        return None
    draft = themes.load_draft(root, draft_id)
    if draft is None:
        session["draft_id"] = None
    return draft


def _adopt_draft(session: dict, draft: dict) -> None:
    session["draft_id"] = draft["id"]
    if not (session.get("title") or "").strip():
        session["title"] = str(draft.get("label") or "")[:80]


def _save(root: Any, draft: dict, at: str) -> dict:
    try:
        return themes.save_draft(root, {**draft, "updated_at": at})
    except themes.ThemeError as exc:
        raise TurnRefused(exc.status, str(exc)) from exc


# ---- 文の手番 --------------------------------------------------------------------------------


def run_text_turn(
    session: dict,
    text: str,
    *,
    lang: str,
    root: Any,
    at: str,
) -> dict:
    """AI 無しの人の文の手番を 1 つ足す（``session`` を書き換える・保存はしない）。返り = 手番。

    文を問いとして下書きに入れる。下書きが無ければ作る（``extend`` = 話のテーマ）。"""
    lg = _lg(lang)
    text = text.strip()
    folded = " ".join(text.split())
    if len(folded) > themes._QUESTION_MAX:
        raise TurnRefused(
            422,
            f"問いは {themes._QUESTION_MAX} 字までです。短く書いてください"
            if lg == "ja"
            else f"A question is up to {themes._QUESTION_MAX} characters. Please shorten it.",
        )
    if _QUESTION_LIKE.search(folded):
        raise TurnRefused(
            422,
            "問いに IRI やコードは入れられません"
            if lg == "ja"
            else "A question cannot contain an IRI or code.",
        )
    draft = _current_draft(session, root)
    before: dict | None = None
    changed = True
    extra_notes: list[dict] = []
    try:
        if draft is None:
            extend, extra_notes = _resolve_extend(session, root, lg)
            made = themes.create_draft(root, "", [text], at=at, extend=extend)
            draft = _save(
                root,
                {**made, "step": 2, "answers": sync_answers([], made["questions"])},
                at,
            )
        else:
            before = copy.deepcopy(draft)
            questions = themes.clean_questions([*(draft.get("questions") or []), text])
            if questions == draft.get("questions"):
                changed = False
            else:
                draft = _save(
                    root,
                    {
                        **draft,
                        "step": max(2, int(draft.get("step") or 2)),
                        "questions": questions,
                        "answers": sync_answers(draft.get("answers") or [], questions),
                    },
                    at,
                )
    except themes.ThemeError as exc:
        raise TurnRefused(exc.status, str(exc)) from exc
    _adopt_draft(session, draft)
    if changed:
        said = (
            f"問い「{_quote(text)}」を覚えました。"
            if lg == "ja"
            else f"Noted the question: {_quote(text)}"
        )
    else:
        said = "その問いはもうあります。" if lg == "ja" else "That question is already there."
    return _append_turn(
        session,
        at=at,
        user={"text": text, "attachments": []},
        reply=_reply(said, notes=[note("ai_unset", _AI_UNSET[lg]), *extra_notes]),
        state="proposed" if changed else "none",
        before=before,
        by="human",
    )


# ---- 文の手番（AI あり・11b） -----------------------------------------------------------

_MAX_HISTORY = 12  # AI に見せる話の手番（最近）
_MAX_CONTENT = 2000


def is_undo(text: str) -> bool:
    """「さっきのを戻して」か（決定論・AI を呼ばない）。規則は ``chat_intent`` にある。"""
    return chat_intent.is_undo(text)


_ASKING = re.compile(r"(?:[？?]|とは|か)[\s。.!！]*\Z")


def is_question(text: str) -> bool:
    """文が問いか（末尾が ``？``/``?``・「〜とは」「〜か」）。それ以外は直し・外す・足す。"""
    return bool(_ASKING.search(text.strip()))


def _theme_of(session: dict, root: Any) -> dict | None:
    """話の確認済みのテーマ（無い・消えていたら ``None``）。"""
    tid = session.get("theme_id")
    if not tid:
        return None
    data = themes.load_themes_file(root)
    return next((t for t in (data or {}).get("themes") or [] if t.get("id") == tid), None)


def _resolve_extend(session: dict, root: Any, lg: str) -> tuple[str | None, list[dict]]:
    """話のテーマ（``extend``）がまだあるか。消えていたら外して一言（``session`` を書き換える）。"""
    extend = session.get("theme_id")
    if not extend:
        return None, []
    data = themes.load_themes_file(root)
    if data is None or not any(t.get("id") == extend for t in data["themes"]):
        session["theme_id"] = None
        return None, [
            note(
                "info",
                "前のテーマは消えています。新しいテーマとして続けます"
                if lg == "ja"
                else "The previous theme is gone. Continuing as a new theme.",
            )
        ]
    return str(extend), []


def theme_context(draft: dict) -> tuple[dict, list[dict]]:
    """下書きを AI に見せる形にする → ``(graph, standards)``。``/api/consult/graph`` の
    ``theme`` の枝と話の手番が共有する（名前だけ・データの値は無い）。"""
    graph = {
        "concepts": [
            {k: c.get(k) for k in ("key", "label_ja", "label_en", "kind", "use")}
            for c in draft.get("concepts") or []
        ],
        "relations": [
            {k: r.get(k) for k in ("from", "rel", "to")} for r in draft.get("relations") or []
        ],
        "questions": [
            {k: a.get(k) for k in ("question", "mode", "op", "direction", "target")}
            for a in draft.get("answers") or []
        ]
        or list(draft.get("questions") or []),
    }
    return graph, themes.default_standards(list(draft.get("questions") or []))


_SCOPE_GONE = {
    "ja": "選んだ対象が、このチャットの知識グラフにありません",
    "en": "The chosen scope is not in this chat's knowledge graph.",
}
_OUT_OF_SCOPE = {
    "ja": "対象外の変更 {n} 件を除きました",
    "en": "Left out {n} change(s) outside the scope.",
}
_NOTHING_IN_SCOPE = {
    "ja": "対象の中に更新するものがありませんでした",
    "en": "There was nothing to update inside the scope.",
}
_SCOPE_NEEDS_AI = {
    "ja": "対象つきの修正には AI の設定が要ります。行の「削除」は AI なしで使えます",
    "en": "A scoped edit needs the AI to be set up. Deleting a row works without it.",
}
_KEPT_APPROVED = {
    "ja": "承認済みの {n} 行は残っています（「関係」の行の「削除」で消せます）",
    "en": "{n} approved row(s) remain (delete them with Delete on the row in Relations).",
}
_ONLY_LAST = {
    "ja": "最後の提案の行だけ承認できます",
    "en": "Only a row of the latest proposal can be approved.",
}
_ALREADY_APPROVED = {
    "ja": "その行は承認済みです",
    "en": "That row is already approved.",
}
_ONE_HOP_MAX = 40


def _rel_short(rel: str) -> str:
    """関係名の正規化 — 述語の IRI／局所名 → 図の短い関係名（短い名はそのまま）。"""
    local = re.split(r"[/#]", rel.strip())[-1]
    return graph_diff.PRED_TO_REL.get(local) or rel


def _scope_gone(lg: str) -> TurnRefused:
    return TurnRefused(400, _SCOPE_GONE[lg])


def resolve_scope(
    scope: dict,
    graph: dict,
    existing: list[SharedTerm],
    lg: str,
    *,
    draft_id: str | None = None,
) -> dict:
    """範囲 ``{"node", "line"?}`` を、図（``theme_context`` の ``graph``）の鍵に直す。
    返り = ``{"keys": [鍵], "name": 表示名, "line": [from, rel, to] | None}``。
    点の id は ``t|<iri>``・``k|<データセット>|<iri>``（承認済みの用語 → ``existing:<slug>``）と
    ``draft|<下書き>|<鍵>``（``draft:`` の形も可）。図に無ければ 400。"""
    concepts = {str(c.get("key")): c for c in graph.get("concepts") or []}
    by_iri = {t.iri: t.slug for t in existing}
    slug_key = {
        str(c.get("use"))[len("existing:") :]: k
        for k, c in concepts.items()
        if str(c.get("use") or "").startswith("existing:")
    }
    cache: dict[str, Any] = {}

    def key_of_iri(iri: str) -> str | None:
        slug = by_iri.get(iri)
        if slug is not None:
            return slug_key.get(slug)
        ref = chat_seed._std_by_iri(iri, cache)  # 標準の語の端は curie が鍵
        return ref.curie if ref is not None else None

    def end(ref: str) -> str | None:
        if ref in concepts:
            return ref
        if ref.startswith("http"):
            return key_of_iri(ref)
        return ref if ":" in ref and not ref.startswith(("draft", "t|")) else None

    def label(k: str) -> str:
        c = concepts.get(k)
        if c is None:
            ref = themes.std_by_curie(k) if ":" in k else None
            return ref.label if ref is not None else k
        return _name(c, lg) or k

    line = scope.get("line")
    if line:
        f = end(str(line["from"]))
        t = end(str(line["to"]))
        rel = _rel_short(str(line["rel"]))
        has = any(
            (str(r.get("from")), str(r.get("rel")), str(r.get("to"))) in ((f, rel, t), (t, rel, f))
            if rel == "eq"  # 「同じ」は向きを問わない
            else (str(r.get("from")), str(r.get("rel")), str(r.get("to"))) == (f, rel, t)
            for r in graph.get("relations") or []
        )
        if f is None or t is None or not has:
            raise _scope_gone(lg)
        return {"keys": [f, t], "name": f"{label(f)} - {label(t)}", "line": [f, rel, t]}
    node = str(scope.get("node") or "")
    key: str | None
    if node.startswith(("draft|", "draft:")):
        parts = node.replace("draft:", "draft|", 1).split("|", 2)
        if len(parts) != 3 or (draft_id is not None and parts[1] != draft_id):
            raise _scope_gone(lg)
        key = parts[2] if parts[2] in concepts else None
    elif node.startswith("t|"):
        key = key_of_iri(node[2:])
    elif node.startswith("k|"):
        key = key_of_iri(node.split("|", 2)[-1])
    else:
        key = None
    if key is None:
        raise _scope_gone(lg)
    return {"keys": [key], "name": label(key), "line": None}


def scope_prompt(scope: dict, graph: dict) -> str:
    """prompt の「# Scope」節の本文（点の名前・その 1 ホップの文・行ならその 1 文）。"""
    names = {str(c.get("key")): c for c in graph.get("concepts") or []}

    def nm(k: str) -> str:
        c = names.get(k)
        return f"{k} ({(c or {}).get('label_ja') or (c or {}).get('label_en') or k})"

    keys = set(scope["keys"])
    line = scope.get("line")
    if line:
        head = f"Only this one relation: {nm(line[0])} -[{line[1]}]-> {nm(line[2])}"
        near = []
    else:
        head = f"Only this item: {nm(scope['keys'][0])}"
        near = [
            f"{nm(str(r.get('from')))} -[{r.get('rel')}]-> {nm(str(r.get('to')))}"
            for r in graph.get("relations") or []
            if keys & {str(r.get("from")), str(r.get("to"))}
        ][:_ONE_HOP_MAX]
    return (
        head
        + ("\nRelations touching it:\n" + "\n".join(near) if near else "")
        + "\nChange nothing that does not touch this. New items only if they connect to it."
    )


def scope_aliases(ops: list[dict], concepts: list[dict]) -> dict[str, str]:
    """差分の ``add_concept`` の鍵 → 図にある同名・同じ種類の概念の鍵（``graph_diff`` が既存に
    寄せるものと同じ判定）。絞り込みを、寄せたあとの鍵で行うために使う。"""
    out: dict[str, str] = {}
    for o in ops:
        if o.get("op") != "add_concept":
            continue
        n = normalize_label(str(o.get("label_ja") or ""))
        if not n:
            continue
        for c in concepts:
            if c.get("kind") == o.get("kind") and n in themes._norm_set(
                c.get("label_ja"), c.get("label_en")
            ):
                out[str(o.get("key"))] = str(c.get("key"))
                break
    return out


def filter_ops_to_scope(
    ops: list[dict], scope: dict | list | set, alias: dict[str, str] | None = None
) -> tuple[list[dict], list[dict]]:
    """差分を範囲に触れる操作だけにする（純関数）→ ``(残す, 捨てる)``。``scope`` = ``resolve_scope``
    の結果（または鍵の集まり）。点の範囲: 概念 = 鍵が範囲の点、関係 = 両端のどちらかが範囲、
    ``add_concept`` は範囲の点に結ぶ ``add_relation`` が同じ差分にあるときだけ。行の範囲
    （``scope["line"]``）: その線の ``remove``／``retarget``、同じ両端の ``add_relation``、
    両端の ``rename`` だけ。``alias`` = 新しい鍵 → 既存の鍵（寄せたあとの鍵で見る）。"""
    keys = set(scope["keys"] if isinstance(scope, dict) else scope)
    line = scope.get("line") if isinstance(scope, dict) else None
    al = alias or {}

    def k(x: Any) -> Any:
        return al.get(x, x)

    if line:
        f, rel, t = line
        ends = {(f, t), (t, f)} if rel == "eq" else {(f, t)}

        def keep_line(o: dict) -> bool:
            name = o.get("op")
            pair = (k(o.get("from")), k(o.get("to")))
            if name == "add_relation":
                return pair in {(f, t), (t, f)}
            if name == "retarget":
                return pair in ends and _rel_short(str(o.get("rel"))) == rel
            if name == "remove" and o.get("target") == "relation":
                return pair in ends and _rel_short(str(o.get("rel"))) == rel
            if name == "rename":
                return k(o.get("key")) in keys
            return False

        kept = [o for o in ops if keep_line(o)]
        return kept, [o for o in ops if o not in kept]

    kept_rel = [
        o
        for o in ops
        if o.get("op") == "add_relation" and keys & {k(o.get("from")), k(o.get("to"))}
    ]
    linked = {k(x) for o in kept_rel for x in (o.get("from"), o.get("to"))} - keys

    def keep(o: dict) -> bool:
        name = o.get("op")
        if name == "add_concept":
            return k(o.get("key")) in keys or k(o.get("key")) in linked
        if name in ("add_relation", "retarget") or (
            name == "remove" and o.get("target") == "relation"
        ):
            return bool(keys & {k(o.get("from")), k(o.get("to")), k(o.get("new_to"))})
        if name in ("remove", "rename"):
            return k(o.get("key")) in keys
        if name == "add_question":
            return k(o.get("target")) in keys
        return False

    kept = [o for o in ops if keep(o)]
    return kept, [o for o in ops if not keep(o)]


@dataclass
class PreparedTurn:
    """準備（ロックの中・読むだけ）の結果。``mode`` = ``create``（テーマも下書きも無い）/
    ``expand``（確認済みのテーマだけある・文が問い）/ ``seed_diff``（確認済みのテーマだけある・
    文が直し）/ ``diff``（下書きがある）/ ``refuse``（AI を呼ばず、``refusal`` の一言で終える）。
    ``expand`` と ``seed_diff`` は、確認済みのテーマを種にした下書きを作って当てる。"""

    mode: str
    text: str
    lang: str
    draft_id: str | None = None
    theme_id: str | None = None
    # create / expand
    known_terms: list[dict] = field(default_factory=list)
    known_standards: list[dict] = field(default_factory=list)
    # diff / seed_diff
    graph: dict = field(default_factory=dict)
    existing_terms: list[dict] = field(default_factory=list)
    standards: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    refusal: str = ""
    #: 11o: 範囲（``resolve_scope`` の結果）。あれば prompt に節を足し、返った差分を範囲に絞る
    scope: dict | None = None


def _history(session: dict) -> list[dict]:
    """話の手番を古い順に ``user`` → ``assistant``。
    ``user`` が無い・戻した・機械の手番・Ask の手番（``reply.answer``）は飛ばす（最近 12 件）。"""
    out: list[dict] = []
    kept = [
        t
        for t in (session.get("turns") or [])
        if t.get("state") != "reverted"
        and t.get("by") != "machine"
        and not (t.get("reply") or {}).get("answer")  # Ask の手番は図の AI に見せない
    ]
    for t in kept[-_MAX_HISTORY:]:
        said = (t.get("user") or {}).get("text")
        if not said:
            continue
        out.append({"role": "user", "content": str(said)[:_MAX_CONTENT]})
        reply = str((t.get("reply") or {}).get("text") or "").strip()
        if reply:
            out.append({"role": "assistant", "content": reply[:_MAX_CONTENT]})
    return out


def prepare_text_turn(
    session: dict,
    text: str,
    *,
    root: Any,
    existing: list[SharedTerm],
    lang: str,
    scope: dict | None = None,
) -> PreparedTurn:
    """AI に渡すものを組む（読むだけ・``session`` も下書きも書き換えない）。
    ``scope``（11o）= ``{"node": …, "line": {from, rel, to} | None}``。あれば問いとは読まず、
    下書き（無ければ確認済みのテーマの種）の中でその範囲を探す（無ければ 400）。"""
    lg = _lg(lang)
    text = text.strip()
    draft = themes.load_draft(root, session["draft_id"]) if session.get("draft_id") else None
    terms = [
        {"slug": t.slug, "label_ja": t.label, "label_en": t.label_en or "", "kind": t.kind}
        for t in existing
    ]
    theme = _theme_of(session, root) if draft is None else None
    if theme is not None and (scope is not None or not is_question(text)):
        # 確認済みのテーマへの直し（種つきの差分）
        seed = chat_seed.seed_from_theme(root, existing, theme)
        graph, standards = theme_context(seed)
        return PreparedTurn(
            "seed_diff",
            text,
            lg,
            theme_id=str(theme["id"]),
            graph=graph,
            existing_terms=terms,
            standards=standards,
            messages=_history(session),
            scope=resolve_scope(scope, graph, existing, lg, draft_id="")  # 下書きは無い
            if scope is not None
            else None,
        )
    if scope is not None and draft is None:
        raise TurnRefused(400, _SCOPE_GONE[lg])
    if draft is None:
        folded = " ".join(text.split())
        if len(folded) > themes._QUESTION_MAX:
            msg = (
                f"問いは {themes._QUESTION_MAX} 字までです。短く書いてください"
                if lg == "ja"
                else f"A question is up to {themes._QUESTION_MAX} characters. Please shorten it."
            )
            return PreparedTurn("refuse", text, lg, refusal=msg)
        if _QUESTION_LIKE.search(folded):
            msg = (
                "問いに IRI やコードは入れられません"
                if lg == "ja"
                else "A question cannot contain an IRI or code."
            )
            return PreparedTurn("refuse", text, lg, refusal=msg)
        return PreparedTurn(
            "expand" if theme is not None else "create",
            text,
            lg,
            theme_id=str(theme["id"]) if theme is not None else None,
            known_terms=terms,
            known_standards=themes.default_standards([text]),
        )
    graph, standards = theme_context(draft)
    return PreparedTurn(
        "diff",
        text,
        lg,
        draft_id=str(draft["id"]),
        graph=graph,
        existing_terms=terms,
        standards=standards,
        messages=_history(session),
        scope=resolve_scope(scope, graph, existing, lg, draft_id=str(draft["id"]))
        if scope is not None
        else None,
    )


async def ai_text_turn(prepared: PreparedTurn, llm: Any) -> dict:
    """AI を呼ぶ（**ロックの外**・純粋 — ストアにも話にも触らない）。
    返り = ``{"ok": True, "value": …}`` または ``{"ok": False, "reason": "shape"|"network",
    "detail": …}``（失敗は例外にしない）。"""
    try:
        if prepared.mode in ("create", "expand"):
            value = await asyncio.to_thread(
                propose_theme_draft,
                llm,
                questions=[prepared.text],
                known_terms=prepared.known_terms,
                known_standards=prepared.known_standards,
                language=prepared.lang,
                label="",
            )
        else:
            value = await asyncio.to_thread(
                propose_graph_diff,
                llm,
                screen="theme",
                graph=prepared.graph,
                existing_terms=prepared.existing_terms,
                standards=prepared.standards,
                columns=None,
                messages=prepared.messages,
                message=prepared.text,
                lang=prepared.lang,
                scope=scope_prompt(prepared.scope, prepared.graph) if prepared.scope else None,
            )
    except (ThemeDraftError, GraphDiffError) as exc:
        return {"ok": False, "reason": "shape", "detail": str(exc)}
    except Exception as exc:  # LLM に届かない・プロバイダのエラー
        return {"ok": False, "reason": "network", "detail": str(exc)}
    return {"ok": True, "value": value}


def _name(row: dict | None, lg: str) -> str:
    if not row:
        return ""
    if lg == "en":
        return str(row.get("label_en") or row.get("label_ja") or "")
    return str(row.get("label_ja") or row.get("label_en") or "")


def _answer_shape(answer: dict, concepts: list[dict], lg: str) -> str:
    """答え方の実行文（``themes_routes`` の ``question()`` と同じ語彙）。未決定は ``""``。"""
    if answer.get("mode") not in ("knowledge", "data"):
        return ""
    by_key = {str(c.get("key")): c for c in concepts}
    if answer["mode"] == "knowledge":
        sel = {
            "op": answer.get("op"),
            "target": _name(by_key.get(str(answer.get("target"))), lg),
            "direction": answer.get("direction"),
        }
    else:
        sel = {
            "op": answer.get("op"),
            "kind": _name(by_key.get(str(answer.get("kind"))), lg),
            "property": _name(by_key.get(str(answer.get("property"))), lg),
            "value": answer.get("value") or "",
            "match": answer.get("match") or "",
            "property2": _name(by_key.get(str(answer.get("property2"))), lg),
            "kind2": _name(by_key.get(str(answer.get("kind2"))), lg),
        }
    return shared_vocab.describe_selection(sel, lg)


def _draft_ops(draft: dict) -> list[dict]:
    """下書きの行を差分の形に写す（概念 → 関係（止めていないもの）→ 答え方）。

    返事の ``ops`` は**表示用**（再送しない・件数の上限は付けない）。``add_question`` は
    答え方が決まった問い（``mode`` が knowledge / data）だけ。決まっていない問いは
    :func:`_undecided_questions` が注記にする。"""
    ops: list[dict] = []
    for c in draft.get("concepts") or []:
        ops.append(
            {
                "op": "add_concept",
                "key": c.get("key"),
                "label_ja": c.get("label_ja") or "",
                "label_en": c.get("label_en") or "",
                "aliases": list(c.get("aliases") or []),
                "kind": c.get("kind"),
                "use": c.get("use") or "new",
                "reason": c.get("reason") or "",
            }
        )
    for r in draft.get("relations") or []:
        if r.get("problem") is None:
            ops.append(
                {
                    "op": "add_relation",
                    "from": r.get("from"),
                    "rel": r.get("rel"),
                    "to": r.get("to"),
                    "reason": r.get("reason") or "",
                }
            )
    for a in draft.get("answers") or []:
        if a.get("mode") not in ("knowledge", "data"):
            continue
        ops.append(
            {
                "op": "add_question",
                "question": a.get("question"),
                "mode": a.get("mode"),
                "op_kind": a.get("op") or "",
                "target": a.get("target") or "",
                "direction": a.get("direction") or "",
                "kind": a.get("kind") or "",
                "property": a.get("property") or "",
                "value": a.get("value") or "",
                "match": a.get("match") or "",
                "property2": a.get("property2") or "",
                "kind2": a.get("kind2") or "",
                "wanted_data": a.get("wanted_data") or "",
                "reason": a.get("reason") or "",
            }
        )
    return ops


def _undecided_questions(draft: dict) -> list[str]:
    """答え方がまだ決まっていない問い（``mode`` が knowledge / data でない）。"""
    return [
        str(a.get("question"))
        for a in draft.get("answers") or []
        if a.get("mode") not in ("knowledge", "data") and a.get("question")
    ]


def _chips(questions: list[str] | None) -> list[str]:
    """問いのチップは文だけ（IRI・コードに見えるものは落とす・最大 5）。"""
    out = [q for q in (questions or []) if isinstance(q, str) and not _QUESTION_LIKE.search(q)]
    return out[:5]


_AI_FAILED = {
    "ja": {
        "shape": "AI の答えが決まった形になりませんでした",
        "network": "AI に届きませんでした",
    },
    "en": {
        "shape": "The AI's answer did not take the expected shape",
        "network": "Could not reach the AI",
    },
}


_SHAPE_KEY = re.compile(r"^(?P<p>.+?): '(?P<k>.*)' is not a key on the graph")
_SHAPE_WHY = (  # (正規表現, ja) — 検査の文を利用者に見える語に。残りは英語のまま
    (re.compile(r"^the model did not return a JSON object"), "答えが JSON ではありませんでした"),
    (re.compile(r"^the model's JSON could not be read"), "答えの JSON が読めませんでした"),
    (re.compile(r"^focus_key '(?P<k>.*)' is not a concept on the map"), "知識グラフに無い用語です"),
    (re.compile(r"looks like code"), "コードや IRI に見えます"),
    (re.compile(r"must be one of"), "決まった値ではありません"),
    (re.compile(r"is required"), "足りません"),
    (re.compile(r"is empty"), "空です"),
    (re.compile(r"longer than"), "長すぎます"),
    (re.compile(r"more than"), "多すぎます"),
    (re.compile(r"is already used"), "鍵が重複しています"),
    (re.compile(r"must be"), "形が違います"),
)

_SHAPE_NEXT = {  # (下書きがあるとき, 無いとき)
    "ja": (
        "対象を 1 つに絞って言い直してください（例:「三斜晶系 は 空間群 の一種」を削除して）",
        "問いを 1 文で短く言い直してください",
    ),
    "en": (
        'Narrow it to one target and say it again (for example: delete "A is a kind of B")',
        "Please say the question again in one short sentence",
    ),
}
_SHAPE_MAX = 120
_KEY_MAX = 30


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _shape_why(raw: str, lg: str) -> str:
    """``GraphDiffError`` / ``ThemeDraftError`` の文 → 120 字までの理由（ja は見える語に）。"""
    raw = " ".join(raw.split())
    if lg == "ja":
        m = _SHAPE_KEY.match(raw)
        if m:
            return _cut(f'{m["p"]}: 知らない鍵 "{_cut(m["k"], _KEY_MAX)}"', _SHAPE_MAX)
        path = raw.split(": ", 1)[0] if ": " in raw else ""
        for pat, ja in _SHAPE_WHY:
            mm = pat.search(raw)
            if mm:
                if "k" in pat.groupindex:
                    return _cut(f"'{_cut(mm['k'], _KEY_MAX)}' は{ja}", _SHAPE_MAX)
                return _cut(f"{path}: {ja}" if path and path.count(" ") == 0 else ja, _SHAPE_MAX)
    return _cut(raw, _SHAPE_MAX)


def _shape_note(base: str, raw: str, lg: str, *, has_draft: bool = True) -> str:
    """形の誤りの注記 = 見出し + 理由の要約 + 次の一手（下書きが無いときは問いの言い直し）。"""
    why = _shape_why(raw, lg) if raw.strip() else ""
    head = f"{base}（{why}）" if why else base
    nxt = _SHAPE_NEXT[lg][0 if has_draft else 1]
    return f"{head}。{nxt}" if lg == "ja" else f"{head}. {nxt}"


_REMOVE_HINT = {
    "ja": "承認済みの用語は、その点を選んで「削除」で消せます",
    "en": "An approved term can be deleted: select its point and choose Delete",
}


def run_hint_turn(session: dict, text: str, *, at: str, lang: str) -> dict:
    """AI 未設定で「〜を外して」が下書きの何にも当たらなかったとき、外し方を案内する手番。"""
    lg = _lg(lang)
    said = _REMOVE_HINT[lg]
    return _machine_turn(session, text, said, lg, [note("info", said)], at)


def run_scope_hint_turn(session: dict, text: str, *, at: str, lang: str) -> dict:
    """AI 未設定で範囲つきの文が来たとき — 下書きを作らず、AI の設定が要ると案内する手番。"""
    lg = _lg(lang)
    said = _SCOPE_NEEDS_AI[lg]
    return _machine_turn(session, text, said, lg, [note("info", said)], at)


def _machine_turn(
    session: dict, text: str, said: str, lg: str, notes: list[dict], at: str, *, by: str = "machine"
) -> dict:
    """空振りの手番（図は変えない・``state: none``）。"""
    return _append_turn(
        session,
        at=at,
        user={"text": text, "attachments": []},
        reply=_reply(said, notes=notes),
        state="none",
        before=None,
        by=by,
    )


def apply_text_turn(
    session: dict,
    prepared: PreparedTurn,
    ai_result: dict | None,
    *,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lang: str,
) -> dict:
    """AI の答えを当てる（ロックの中・``session`` と下書きは**読み直した**もの）。返り = 手番。
    ``session`` を書き換える（保存は呼ぶ側）。AI を待つあいだに人が直していたら、最新に当てる。"""
    lg = _lg(lang)
    text = prepared.text
    if prepared.mode == "refuse":
        return _machine_turn(
            session, text, prepared.refusal, lg, [note("info", prepared.refusal)], at
        )
    assert ai_result is not None
    if not ai_result.get("ok"):
        reason = ai_result.get("reason") or "network"
        detail = _AI_FAILED[lg][reason]  # network の詳細は session.json に残さない
        logger.warning("chat turn: AI failed (%s): %s", reason, ai_result.get("detail") or "")
        said = (
            "今回は知識グラフを更新できませんでした。"
            if lg == "ja"
            else "Could not update the knowledge graph."
        )
        if reason == "shape":  # 11n: 理由の要約と次の一手を人に見せる
            detail = _shape_note(
                detail, str(ai_result.get("detail") or ""), lg,
                has_draft=prepared.mode != "create",
            )  # fmt: skip
        return _machine_turn(session, text, said, lg, [note("ai_failed", detail)], at)
    draft = _current_draft(session, root)
    theme = _theme_of(session, root)
    seeded = prepared.mode in ("expand", "seed_diff")
    went_on = (prepared.mode != "diff") != (draft is None)
    if seeded and (theme is None or theme.get("id") != prepared.theme_id):
        went_on = True
    if prepared.mode == "create" and theme is not None:
        went_on = True
    if prepared.mode == "diff" and (
        draft is None
        or prepared.draft_id != session.get("draft_id")
        or prepared.draft_id != draft.get("id")
    ):  # 待つあいだに別の下書きに替わった
        went_on = True
    if went_on:  # 待つあいだに話が進んだ
        said = (
            "別の手番が先に進みました。もう一度送ってください"
            if lg == "ja"
            else "Another step went ahead. Please send it again."
        )
        return _machine_turn(session, text, said, lg, [note("info", said)], at)
    scope_note: dict | None = None
    if prepared.scope is not None and prepared.mode in ("diff", "seed_diff"):
        value = ai_result["value"]
        all_ops = value.get("ops") or []
        kept_ops, dropped_ops = filter_ops_to_scope(
            all_ops, prepared.scope, scope_aliases(all_ops, prepared.graph.get("concepts") or [])
        )
        value = {**value, "ops": kept_ops}
        if dropped_ops and not kept_ops:  # 全部が範囲の外 — AI の本文は捨てる
            value["reply"] = _NOTHING_IN_SCOPE[lg]
        ai_result = {**ai_result, "value": value}
        if dropped_ops:
            scope_note = note("info", _OUT_OF_SCOPE[lg].format(n=len(dropped_ops)))
    turn = _apply_text(
        session, prepared, ai_result, theme=theme, draft=draft, root=root, existing=existing,
        dismissed=dismissed, at=at, lg=lg,
    )  # fmt: skip
    if scope_note is not None:
        turn["reply"]["notes"] = [*(turn["reply"].get("notes") or []), scope_note]
    return turn


def _apply_text(
    session: dict,
    prepared: PreparedTurn,
    ai_result: dict,
    *,
    theme: dict | None,
    draft: dict | None,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lg: str,
) -> dict:
    """``apply_text_turn`` の本体（AI の答えを当てる）。"""
    text = prepared.text
    if prepared.mode == "expand":
        assert theme is not None
        return _apply_expand(
            session, text, ai_result["value"], theme, root=root, existing=existing,
            dismissed=dismissed, at=at, lg=lg,
        )  # fmt: skip
    if prepared.mode == "seed_diff":
        assert theme is not None
        seeded_draft = _seeded_draft(theme, chat_seed.seed_from_theme(root, existing, theme),
                                     [], root=root, at=at)  # fmt: skip
        return _apply_diff(
            session, text, ai_result["value"], seeded_draft[0], root=root, existing=existing,
            dismissed=dismissed, at=at, lg=lg, fresh_seed=seeded_draft[1],
        )  # fmt: skip
    if prepared.mode == "create":
        return _apply_create(
            session, text, ai_result["value"], root=root, existing=existing, dismissed=dismissed,
            at=at, lg=lg,
        )  # fmt: skip
    assert draft is not None
    return _apply_diff(
        session, text, ai_result["value"], draft, root=root, existing=existing,
        dismissed=dismissed, at=at, lg=lg,
    )  # fmt: skip


def _seeded_draft(
    theme: dict, seed: dict, questions: list[str], *, root: Any, at: str
) -> tuple[dict, dict]:
    """確認済みのテーマを種にした下書きを作って保存する → ``(下書き, 種の控え)``。
    控えに ``draft_id`` を組にして持たせるのは呼ぶ側（採用したとき）。"""
    made: dict | None = None
    try:
        made = themes.create_draft(
            root, str(theme.get("label") or ""), questions, at=at, extend=str(theme["id"])
        )
        draft = _save(
            root,
            {
                **made,
                "step": 2,
                "concepts": seed["concepts"],
                "relations": seed["relations"],
                "standards": seed["standards"],
                "answers": sync_answers([], made["questions"]),
            },
            at,
        )
    except themes.ThemeError as exc:
        if made is not None:
            themes.delete_draft(root, made["id"])
        raise TurnRefused(exc.status, str(exc)) from exc
    except TurnRefused:
        if made is not None:
            themes.delete_draft(root, made["id"])
        raise
    return draft, seed["seed"]


def _adopt_seed(session: dict, draft: dict, seed: dict) -> None:
    session["seed"] = {"draft_id": draft["id"], **seed}


def _apply_expand(
    session: dict,
    text: str,
    ai: dict,
    theme: dict,
    *,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lg: str,
) -> dict:
    """確認済みのテーマを種にして、問いの答えを足す（「ここを広げる」と同じ合成・新しい行だけ）。"""
    seed = chat_seed.seed_from_theme(root, existing, theme)
    base, ctl = _seeded_draft(theme, seed, [text], root=root, at=at)
    try:
        out = themes.postprocess_draft(
            ai, questions=base["questions"], existing=existing, dismissed=dismissed, base=base
        )
        merged = themes.merge_expansion(base, out)
        draft = _save(
            root,
            {
                **merged,
                "answers": out["answers"],
                "suggested_questions": out["suggested_questions"],
            },
            at,
        )
    except themes.ThemeError as exc:
        themes.delete_draft(root, base["id"])
        raise TurnRefused(exc.status, str(exc)) from exc
    except TurnRefused:
        themes.delete_draft(root, base["id"])
        raise
    _adopt_draft(session, draft)
    _adopt_seed(session, draft, ctl)
    return _created_turn(
        session, text, draft, out, ops_of={
            "concepts": out["concepts"], "relations": out["relations"], "answers": out["answers"],
        }, extra_notes=[], at=at, lg=lg,
    )  # fmt: skip


def _apply_create(
    session: dict,
    text: str,
    ai: dict,
    *,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lg: str,
) -> dict:
    extend, extra_notes = _resolve_extend(session, root, lg)
    # 文が問いでなければ、人の文は問いにしない（✓ が「答え方が決まった問いが無い」で止まる）。
    # 下書きの問いは AI の答え（answers）＋ 提案の問いから作る。AI には文を文脈として渡し済み
    qs = [text]
    if not is_question(text):
        from_ai = [str(a.get("question") or "") for a in ai.get("answers") or []]
        from_ai += [str(q) for q in ai.get("suggested_questions") or []]
        qs = themes.clean_questions(from_ai) or [text]
    out = themes.postprocess_draft(ai, questions=qs, existing=existing, dismissed=dismissed)
    label = out["theme_label"] or themes.default_label(qs)
    if extend:
        data = themes.load_themes_file(root) or {"themes": []}
        label = next((t.get("label") for t in data["themes"] if t.get("id") == extend), label)
    made: dict | None = None
    try:
        made = themes.create_draft(root, "", qs, at=at, extend=extend)
        draft = _save(
            root,
            {
                **made,
                "label": str(label)[:80],
                "step": 2,
                "concepts": out["concepts"],
                "relations": out["relations"],
                "answers": out["answers"],
                "suggested_questions": out["suggested_questions"],
                "standards": out["standards"],
            },
            at,
        )
    except themes.ThemeError as exc:
        if made is not None:
            themes.delete_draft(root, made["id"])
        raise TurnRefused(exc.status, str(exc)) from exc
    except TurnRefused:
        if made is not None:
            themes.delete_draft(root, made["id"])
        raise
    _adopt_draft(session, draft)
    return _created_turn(
        session, text, draft, out, ops_of=draft, extra_notes=extra_notes, at=at, lg=lg
    )


def _created_turn(
    session: dict,
    text: str,
    draft: dict,
    out: dict,
    *,
    ops_of: dict,
    extra_notes: list[dict],
    at: str,
    lg: str,
) -> dict:
    """AI が図を足した手番の返事（``ops_of`` = 足した行・``draft`` = 図の全体）。"""
    ops = _draft_ops(ops_of)
    n_concepts = sum(1 for o in ops if o["op"] == "add_concept")
    n_rels = sum(1 for o in ops if o["op"] == "add_relation")
    sentences = graph_diff.ops_to_sentences(ops, {"concepts": draft["concepts"]}, lg)
    answer = (draft.get("answers") or [{}])[0]
    shape = _answer_shape(answer, draft["concepts"], lg)
    wanted = str(answer.get("wanted_data") or "").strip()
    if lg == "ja":
        said = f"知識グラフに 用語 {n_concepts}・関係 {n_rels} を追加しました。"
        said += f"答え方: {shape}。" if shape else "答え方: まだ決まっていません。"
        if wanted:
            said += f"欲しいデータ: {wanted}"
    else:
        said = f"Added {n_concepts} concepts and {n_rels} relations to the knowledge graph. "
        said += f"How to answer: {shape}. " if shape else "How to answer: not decided yet. "
        if wanted:
            said += f"Data needed: {wanted}"
    notes = list(extra_notes)
    undecided = _undecided_questions(ops_of)
    if undecided:
        joined = " / ".join(undecided)
        notes.append(
            note(
                "info",
                f"答え方がまだ決まっていない問い: {joined}"
                if lg == "ja"
                else f"Questions without a way to answer yet: {joined}",
            )
        )
    bad_rels = sum(1 for r in ops_of.get("relations") or [] if r.get("problem") is not None)
    if bad_rels:
        notes.append(
            note(
                "info",
                f"形が合わない関係を {bad_rels} 件除きました"
                if lg == "ja"
                else f"Left out {bad_rels} relations whose shape does not fit",
            )
        )
    gone = out["dropped"]
    gc, gr = len(gone.get("concepts") or []), len(gone.get("relations") or [])
    if gc or gr:
        notes.append(
            note(
                "dropped",
                f"前に削除したものは入れていません: 用語 {gc}・関係 {gr}"
                if lg == "ja"
                else f"Left out what you deleted before: {gc} concepts, {gr} relations",
            )
        )
    return _append_turn(
        session,
        at=at,
        user={"text": text, "attachments": []},
        reply=_reply(
            said.strip(),
            ops=ops,
            sentences=sentences,
            questions=_chips(draft.get("suggested_questions")),
            notes=notes,
        ),
        state="proposed",
        before=None,  # この手番で下書きを作った
        by="ai",
    )


def _apply_diff(
    session: dict,
    text: str,
    diff: dict,
    draft: dict,
    *,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    lg: str,
    fresh_seed: dict | None = None,
) -> dict:
    """``fresh_seed`` = 確認済みのテーマを種に、この手番で作った下書き（当たらなければ捨てる）。"""
    ops = list(diff.get("ops") or [])
    new, report = graph_diff.apply_to_draft(
        draft, ops, existing=existing, dismissed=dismissed, by="ai"
    )
    sentences = graph_diff.ops_to_sentences(ops, {"concepts": draft.get("concepts")}, lg)
    gone = {d["index"]: d["reason"] for d in report["dropped"]}
    kept = [i for i in range(len(ops)) if i not in gone]
    notes = [
        note("dropped", f"{sentences[i] if i < len(sentences) else ''}: {dropped_reason(r, lg)}")
        for i, r in gone.items()
    ]
    applied_ops = [ops[i] for i in kept]
    applied_sentences = [sentences[i] for i in kept if i < len(sentences)]
    before: dict | None = None
    state = "none"
    if report["applied"] > 0:
        before = None if fresh_seed is not None else copy.deepcopy(draft)
        chat = draft.get("chat") or {}
        new["chat"] = {
            "messages": [
                *(chat.get("messages") or []),
                {"role": "user", "content": text[:_MAX_CONTENT], "at": at},
            ],
            "proposals": [
                *(chat.get("proposals") or []),
                {  # 適用した操作だけを残す
                    "proposal_id": graph_diff.proposal_id(ops),
                    "ops": applied_ops,
                    "at": at,
                    "by": "ai",
                },
            ],
        }
        try:
            draft = _save(root, new, at)
        except TurnRefused:
            if fresh_seed is not None:  # 作った種つきの下書きを残さない（話は何も採用していない）
                themes.delete_draft(root, draft["id"])
                session["draft_id"] = None
                session.pop("seed", None)
            raise
        state = "proposed"
        _adopt_draft(session, draft)
        if fresh_seed is not None:
            _adopt_seed(session, draft, fresh_seed)
    elif fresh_seed is not None:
        themes.delete_draft(root, draft["id"])
    said = str(diff.get("reply") or "").strip()
    if not said:
        n = report["applied"]
        if lg == "ja":
            said = f"知識グラフを {n} 件更新しました。" if n else "知識グラフは更新していません。"
        else:
            said = (
                f"Updated {n} things in the knowledge graph."
                if n
                else "The knowledge graph is unchanged."
            )
    return _append_turn(
        session,
        at=at,
        user={"text": text, "attachments": []},
        reply=_reply(said, ops=applied_ops, sentences=applied_sentences, notes=notes),
        state=state,
        before=before,
        by="ai",
    )


def run_undo_turn(session: dict, text: str, *, root: Any, at: str, lang: str) -> dict:
    """「さっきのを戻して」— AI を呼ばず、最後の未確認の返事を戻す（決定論）。無ければ一言。"""
    lg = _lg(lang)
    text = text.strip()
    proposed = [t for t in session.get("turns") or [] if _is_theme_proposal(t)]
    if proposed:
        try:
            revert(session, proposed[-1]["id"], root=root, at=at, lang=lang)
        except TurnRefused as exc:
            return _machine_turn(session, text, str(exc), lg, [note("info", str(exc))], at)
        said = "1 つ前を取り消しました" if lg == "ja" else "Undid the last step"
        return _machine_turn(session, text, said, lg, [], at)
    said = "取り消すものがありません" if lg == "ja" else "There is nothing to undo"
    return _machine_turn(session, text, said, lg, [note("info", said)], at)


# ---- 手で足す手番 ------------------------------------------------------------------------------


def run_ops_turn(
    session: dict,
    ops: list[dict],
    *,
    lang: str,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
    user_text: str | None = None,
) -> dict:
    """人の直接編集（差分）を下書きに当てる手番。``user_text`` があれば人の文として残す
    （11n: 「〈文〉を外して」を決定論で remove にしたとき）。検査は提案のときと同じ閉じた検査
    （``validate_graph_diff`` → ``graph_diff.apply_to_draft``）。"""
    lg = _lg(lang)
    draft = _current_draft(session, root)
    if draft is None:
        raise TurnRefused(422, "先に問いを書いてください")
    keys = [str(c.get("key")) for c in draft.get("concepts") or []]
    try:
        clean = validate_graph_diff({"ops": ops}, known_keys=keys, allow_taken_keys=True)["ops"]
    except GraphDiffError as exc:
        raise TurnRefused(422, f"差分が決まった形ではありません: {exc}") from exc
    new, report = graph_diff.apply_to_draft(
        draft, clean, existing=existing, dismissed=dismissed, by="human"
    )
    sentences = graph_diff.ops_to_sentences(clean, {"concepts": draft.get("concepts")}, lg)
    gone = {d["index"]: d["reason"] for d in report["dropped"]}
    kept = [i for i in range(len(clean)) if i not in gone]
    notes = [
        note("dropped", f"{sentences[i] if i < len(sentences) else ''}: {dropped_reason(r, lg)}")
        for i, r in gone.items()
    ]
    applied_sentences = [sentences[i] for i in kept if i < len(sentences)]
    before: dict | None = None
    state = "none"
    if report["applied"] > 0:
        before = copy.deepcopy(draft)
        draft = _save(root, new, at)
        state = "proposed"
    _adopt_draft(session, draft)
    if applied_sentences:
        said = "・".join(applied_sentences)
    else:
        said = "追加できるものがありませんでした。" if lg == "ja" else "Nothing could be added."
    return _append_turn(
        session,
        at=at,
        user={"text": user_text, "attachments": []} if user_text else None,
        reply=_reply(
            said,
            ops=[clean[i] for i in kept],
            sentences=applied_sentences,
            notes=notes,
        ),
        state=state,
        before=before,
        by="human",
    )


# ---- 点から外す手番（11d） ---------------------------------------------------------------------

_REMOVE_TEXT = {
    "ja": {
        "draft": "承認していないものは、その提案を取り消してください",
        "std": "標準の語は削除できません",
        "no_term": "その用語はありません",
        "no_relation": "その関係はありません",
        "bad_relation": "その関係は削除できません",
        "outside": "この用語・関係が入っている知識グラフが見つかりません",
        "not_here": "いま開いている提案には入っていません",
    },
    "en": {
        "draft": "Something not yet approved is undone by undoing its proposal",
        "std": "A standard term cannot be deleted",
        "no_term": "No such term",
        "no_relation": "No such relation",
        "bad_relation": "That relation cannot be deleted",
        "outside": "No knowledge graph holds this term or relation",
        "not_here": "It is not in the proposal that is open",
    },
}


def _concept_slug(c: dict) -> str:
    use = str(c.get("use") or "")
    return str(c.get("slug") or (use[len("existing:") :] if use.startswith("existing:") else ""))


def _remove_op(draft: dict, target: dict) -> dict | None:
    """``target``（確認済みの用語 ``{"slug"}``／関係 ``{"from", "relation", "to"}`` の IRI）に
    当たる ``remove`` の操作。この図に無ければ ``None``。"""
    rows = [c for c in draft.get("concepts") or [] if isinstance(c, dict)]
    slug_of = {str(c.get("key")): _concept_slug(c) for c in rows}
    if "slug" in target:
        key = next((k for k, sl in slug_of.items() if sl and sl == target["slug"]), None)
        return None if key is None else {"op": "remove", "target": "concept", "key": key}
    kinds = {_concept_slug(c): c.get("kind") for c in rows}
    for r in draft.get("relations") or []:
        f = slug_of.get(str(r.get("from")), "")
        if not f:
            continue
        end = slug_of.get(str(r.get("to"))) or str(r.get("to"))
        iris = chat_seed._line_iris({"kinds": kinds}, (f, str(r.get("rel")), end))
        if iris == (target["from"], target["relation"], target["to"]):
            return {
                "op": "remove",
                "target": "relation",
                "from": str(r.get("from")),
                "rel": str(r.get("rel")),
                "to": str(r.get("to")),
            }
    return None


def run_remove_turn(
    session: dict,
    remove: dict,
    *,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    published_kinds: set[str],
    at: str,
    lang: str,
) -> dict:
    """点から外す — 確認済みの用語・関係を**外す提案**にする（✓ でストアからも外れる・11b-2）。

    いま開いている提案があればそこへ、無ければ確認済みのテーマを種にした下書き
    （:func:`_seeded_draft`）を作って、その概念／関係に ``remove``（``by: human``）を当てる。
    未確認のもの（``draft:``）・公開した種類・標準の語は 422。"""
    lg = _lg(lang)
    t = _REMOVE_TEXT[lg]
    iri = remove.get("iri")
    ends = [remove.get(k) for k in ("from", "to")]
    if any(isinstance(x, str) and x.startswith("draft:") for x in (iri, *ends)):
        raise TurnRefused(422, t["draft"])
    target: dict
    if iri is not None:
        if iri in published_kinds:
            raise TurnRefused(422, _DATA_NO_REVERT[lg])
        if any(iri.startswith(ns) for ns in shared_vocab.known_namespaces()):
            raise TurnRefused(422, t["std"])
        term = next((x for x in existing if x.iri == iri), None)
        if term is None:
            raise TurnRefused(404, t["no_term"])
        target = {"slug": term.slug}
    else:
        rel = graph_diff.PRED_TO_REL.get(str(remove.get("relation")))
        if rel is None:
            raise TurnRefused(422, t["bad_relation"])
        if any(not isinstance(x, str) or not x for x in ends):
            raise TurnRefused(422, t["no_relation"])
        target = {"from": ends[0], "relation": remove["relation"], "to": ends[1]}
    draft = _current_draft(session, root)
    fresh: dict | None = None
    if draft is None:
        data = themes.load_themes_file(root) or {}
        mine = _theme_of(session, root)
        others = [x for x in data.get("themes") or [] if x is not mine]
        found: tuple[dict, dict] | None = None
        for theme in ([mine] if mine else []) + others:
            seed = chat_seed.seed_from_theme(root, existing, theme)
            if _remove_op(seed, target) is not None:
                found = (theme, seed)
                break
        if found is None:
            raise TurnRefused(404, t["outside"])
        draft, fresh = _seeded_draft(found[0], found[1], [], root=root, at=at)
    op = _remove_op(draft, target)
    if op is None:
        if fresh is not None:
            themes.delete_draft(root, draft["id"])
        raise TurnRefused(409, t["not_here"])
    keys = [str(c.get("key")) for c in draft.get("concepts") or []]
    try:
        clean = validate_graph_diff({"ops": [op]}, known_keys=keys, allow_taken_keys=True)["ops"]
    except GraphDiffError as exc:
        if fresh is not None:
            themes.delete_draft(root, draft["id"])
        raise TurnRefused(422, f"差分が決まった形ではありません: {exc}") from exc
    new, report = graph_diff.apply_to_draft(
        draft, clean, existing=existing, dismissed=dismissed, by="human"
    )
    sentences = graph_diff.ops_to_sentences(clean, {"concepts": draft.get("concepts")}, lg)
    if report["applied"] < 1:  # 当たらなかった（作った種つきの下書きは残さない）
        if fresh is not None:
            themes.delete_draft(root, draft["id"])
        raise TurnRefused(409, t["not_here"])
    before = None if fresh is not None else copy.deepcopy(draft)
    try:
        saved = _save(root, new, at)
    except TurnRefused:
        if fresh is not None:
            themes.delete_draft(root, draft["id"])
        raise
    _adopt_draft(session, saved)
    if fresh is not None:
        _adopt_seed(session, saved, fresh)
    return _append_turn(
        session,
        at=at,
        user=None,
        reply=_reply(
            "・".join(sentences), ops=clean, sentences=sentences, notes=[]
        ),
        state="proposed",
        before=before,
        by="human",
    )


# ---- 答え方を決める手番 --------------------------------------------------------------------------

#: データの答え方で、op ごとに要る欄（``themes.data_answer_complete`` と同じ）
_DATA_NEEDS = {
    "count": ("kind",),
    "range": ("property",),
    "top": ("property",),
    "filter": ("kind", "property", "value"),
    "group": ("kind", "property"),
    "trend": ("kind", "property", "property2"),
    "join": ("kind", "property", "kind2"),
}


def _undecided(field: str) -> TurnRefused:
    return TurnRefused(422, f"答え方が決まっていません: {field}")


def run_set_answer_turn(
    session: dict,
    body: dict,
    *,
    root: Any,
    at: str,
    lang: str,
) -> dict:
    """既にある問いの答え方を人が決める手番（``session`` を書き換える・保存は呼ぶ側）。

    検査は ``graph_diff._Applier.add_question`` と同じ規則。通れば答えの行を置き換えて下書きを
    保存する。"""
    lg = _lg(lang)
    draft = _current_draft(session, root)
    if draft is None:
        raise TurnRefused(422, "先に問いを書いてください")
    want = normalize_label(str(body.get("question") or ""))
    answers = list(draft.get("answers") or [])
    idx = next(
        (i for i, a in enumerate(answers) if normalize_label(str(a.get("question") or "")) == want),
        None,
    )
    if idx is None:
        raise TurnRefused(422, "その問いはこの話にありません")
    old = answers[idx]
    concepts = {str(c.get("key")): c for c in draft.get("concepts") or [] if isinstance(c, dict)}

    def pick(field: str, kind: str | None = None) -> dict | None:
        ref = body.get(field)
        if not ref:
            return None
        row = concepts.get(str(ref))
        if row is None or (kind is not None and row.get("kind") != kind):
            raise _undecided(field)
        return row

    mode, op = body.get("mode"), str(body.get("op") or "")
    direction = body.get("direction")
    if mode == "knowledge":
        if op not in themes.KNOWLEDGE_OPS:
            raise _undecided("op")
        t_row = pick("target")
        if t_row is None:
            raise _undecided("target")
        if op == "factors" and direction not in themes.KNOWLEDGE_DIRECTIONS:
            raise _undecided("direction")
        row = themes._answer_row(
            old["question"],
            "knowledge",
            op=op,
            target=str(t_row["key"]),
            direction=direction if op == "factors" else "",
            memo=None,
            wanted_data=old.get("wanted_data") or "",
            reason=old.get("reason") or "",
        )
        shown = (
            (t_row.get("label_en") or t_row.get("label_ja") or "")
            if lg == "en"
            else (t_row.get("label_ja") or t_row.get("label_en") or "")
        )
        selection: dict = {"op": op, "target": shown, "direction": row["direction"]}
    elif mode == "data":
        if op not in _DATA_NEEDS:
            raise _undecided("op")
        k_row = pick("kind", "class")
        p_row = pick("property", "property")
        p2_row = pick("property2", "property")
        k2_row = pick("kind2", "class")
        value = themes._clean_value(body.get("value"))
        if not themes.data_answer_complete(
            op, kind=k_row, prop=p_row, prop2=p2_row, kind2=k2_row, value=value
        ):
            have = {
                "kind": k_row,
                "property": p_row,
                "property2": p2_row,
                "kind2": k2_row,
                "value": value,
            }
            missing = next((f for f in _DATA_NEEDS[op] if not have[f]), op)
            raise _undecided(missing)
        row = themes._answer_row(
            old["question"],
            "data",
            op=op,
            kind=str(k_row["key"]) if k_row else "",
            prop=str(p_row["key"]) if p_row else "",
            value=value if op == "filter" else "",
            match=themes._clean_match(body.get("match")) if op == "filter" else "",
            prop2=str(p2_row["key"]) if p2_row and op == "trend" else "",
            kind2=str(k2_row["key"]) if k2_row and op == "join" else "",
            memo=None,
            wanted_data=old.get("wanted_data") or "",
            reason=old.get("reason") or "",
        )

        def name(r: dict | None) -> str:
            if not r:
                return ""
            if lg == "en":
                return str(r.get("label_en") or r.get("label_ja") or "")
            return str(r.get("label_ja") or r.get("label_en") or "")

        selection = {
            "op": op,
            "kind": name(k_row),
            "property": name(p_row),
            "value": row["value"],
            "match": row["match"],
            "property2": name(p2_row) if op == "trend" else "",
            "kind2": name(k2_row) if op == "join" else "",
        }
    else:
        raise _undecided("mode")
    row["by"] = "human"
    before = copy.deepcopy(draft)
    answers[idx] = row
    draft = _save(root, {**draft, "answers": answers}, at)
    _adopt_draft(session, draft)
    shape = shared_vocab.describe_selection(selection, lg)
    question = str(old["question"])
    said = (
        f"問い「{_quote(question)}」の答え方: {shape}"
        if lg == "ja"
        else f'Answer for "{_quote(question)}": {shape}'
    )
    return _append_turn(
        session,
        at=at,
        user=None,
        reply=_reply(said, sentences=[said]),
        state="proposed",
        before=before,
        by="human",
    )


# ---- ✓ ---------------------------------------------------------------------------------------


def _answer_complete(a: dict) -> bool:
    """答え方が決まっているか（UI の ``answerComplete``）。"""
    mode, op = a.get("mode"), a.get("op")
    if mode == "knowledge":
        if not a.get("target"):
            return False
        if op == "factors":
            return a.get("direction") in ("lowers", "raises", "affects")
        return op in ("parts", "kinds")
    if mode == "data":
        kind, prop = a.get("kind"), a.get("property")
        if op == "count":
            return bool(kind)
        if op in ("range", "top"):
            return bool(prop)
        if op == "filter":
            return bool(kind and prop and str(a.get("value") or "").strip())
        if op == "group":
            return bool(kind and prop)
        if op == "trend":
            return bool(kind and prop and a.get("property2"))
        if op == "join":
            return bool(kind and prop and a.get("kind2"))
    return False


def _is_existing(concept: dict) -> bool:
    return str(concept.get("use") or "new").startswith("existing:")


def commit_body(draft: dict, lang: str) -> dict:
    """✓ の本文（``themes.commit_theme`` が受ける形）。UI の ``buildCommitBody`` の移植。

    概念は全部送る（話には「問いにつながるものだけ」の段が無い・つながらなければ
    ``commit_theme`` が理由つきで断る）。関係は機械が止めていないもの（``problem`` が無い）だけ。
    答え方が決まっていない（足りない）問いは、メモの問いとして送る（問いは捨てない）。"""
    concepts = [c for c in draft.get("concepts") or [] if isinstance(c, dict)]
    keys = {str(c.get("key")) for c in concepts}
    relations = [
        r
        for r in draft.get("relations") or []
        if isinstance(r, dict) and r.get("problem") is None and str(r.get("from")) in keys
    ]
    questions = list(draft.get("questions") or [])
    body: dict[str, Any] = {
        "id": draft["id"],
        "label": str(draft.get("label") or "").strip() or themes.default_label(questions),
        "lang": _lg(lang),
        "ai_drafted": any(x.get("by") == "ai" for x in (*concepts, *relations)),
        "questions": questions,
        "concepts": [
            {
                "key": c.get("key"),
                "label_ja": c.get("label_ja") or "",
                "label_en": c.get("label_en") or "",
                "aliases": c.get("aliases") or [],
                "kind": c.get("kind"),
                "use": c.get("use") or "new",
                "slug": "" if _is_existing(c) else c.get("slug") or "",
                "by": c.get("by") or "human",
            }
            for c in concepts
        ],
        "relations": [
            {
                "from": r.get("from"),
                "rel": r.get("rel"),
                "to": r.get("to"),
                "reason": r.get("reason") or "",
                "by": r.get("by") or "human",
            }
            for r in relations
        ],
        "answers": [],
    }
    if draft.get("extend"):
        body["extend"] = draft["extend"]
    for a in draft.get("answers") or []:
        refs = [a.get(k) for k in ("target", "kind", "property", "property2", "kind2")]
        if _answer_complete(a) and all(not k or k in keys for k in refs):
            body["answers"].append({**a, "memo": None})
        else:
            body["answers"].append({**a, "mode": "memo", "memo": a.get("memo") or "none"})
    return body


def _is_theme_proposal(turn: dict) -> bool:
    """テーマの下書きに属する未確認の返事か。📎 の表の返事・機械の「結べそうな関係」の手番は別の道
    （``chat_attach``）で ✓／戻すので、テーマの ✓ や「戻して」の数に入れない。"""
    return (
        turn.get("state") == "proposed"
        and not turn.get("attachment")
        and not turn.get("attach_run")
        and not (turn.get("reply") or {}).get("suggestions")
    )


def _theme_by_id(root: Any, theme_id: Any) -> dict | None:
    if not theme_id:
        return None
    data = themes.load_themes_file(root)
    return next((t for t in (data or {}).get("themes") or [] if t.get("id") == theme_id), None)


def _find(session: dict, turn_id: str) -> dict:
    for t in session.get("turns") or []:
        if t.get("id") == turn_id:
            return t
    raise TurnRefused(404, "その手番はありません")


async def confirm_only(
    session: dict,
    turn_id: str,
    only: dict,
    *,
    client: Any,
    lang: str,
    root: Any,
    existing: list[SharedTerm],
    fetch_terms: Any,
    at: str,
) -> tuple[dict, dict | None]:
    """1 行の承認（11o）— 選んだ行と、その両端のうち未承認の概念だけを承認する。
    ``only`` = ``{"relations": [{from, rel, to}], "concepts": [鍵]}``（下書きの鍵・短い関係名）。

    承認済みのテーマがあるときだけ（無ければ 409）。絞った下書き = テーマの種（下書きに残って
    いるもの）＋選んだ行＋その両端の未承認の概念。問いは入れない。``commit_theme`` が断れば
    手番の ``notes`` に ``commit_error`` を足して ``commit = None``。通れば、元の下書きは残り
    （新しく承認した用語は ``existing:`` に直す）、手番の ``confirmed`` に ``only`` を記録する。"""
    lg = _lg(lang)
    turn = _find(session, turn_id)
    theme = _theme_of(session, root)
    if theme is None:
        raise TurnRefused(
            409,
            "返事の承認で全部をまとめて承認してください"
            if lg == "ja"
            else "Please approve the whole reply instead.",
        )
    draft = _current_draft(session, root)
    if not _is_theme_proposal(turn) or draft is None:
        raise TurnRefused(
            409, "承認するものがありません" if lg == "ja" else "Nothing to approve."
        )
    if [t for t in session["turns"] if _is_theme_proposal(t)][-1] is not turn:
        raise TurnRefused(409, _ONLY_LAST[lg])
    rows = [c for c in draft.get("concepts") or [] if isinstance(c, dict)]
    by_key = {str(c.get("key")): c for c in rows}
    uses = set(theme.get("uses") or [])
    approved = {
        k for k, c in by_key.items() if _concept_slug(c) and _concept_slug(c) in uses
    }
    slug_of = {k: _concept_slug(c) for k, c in by_key.items()}
    seed = chat_seed.seed_from_theme(root, existing, theme)
    seed_rels = {tuple(t) for t in seed["seed"]["relations"]}

    def is_seed_rel(r: dict) -> bool:
        f, t = str(r.get("from")), str(r.get("to"))
        return (slug_of.get(f, ""), str(r.get("rel")), slug_of.get(t) or t) in seed_rels

    wanted: list[dict] = []
    for w in only.get("relations") or []:
        rel = _rel_short(str(w["rel"]))
        hit = next(
            (
                r
                for r in draft.get("relations") or []
                if (str(r.get("from")), str(r.get("rel")), str(r.get("to")))
                == (str(w["from"]), rel, str(w["to"]))
            ),
            None,
        )
        if hit is None:
            raise TurnRefused(
                409, "その行は、いまの提案にありません" if lg == "ja" else "That row is gone."
            )
        if is_seed_rel(hit):  # 承認済み（二重に承認しない）
            raise TurnRefused(409, _ALREADY_APPROVED[lg])
        wanted.append(hit)
    extra = [str(k) for k in only.get("concepts") or []]
    if any(k not in by_key for k in extra) or not (wanted or extra):
        raise TurnRefused(
            409, "承認する行が選ばれていません" if lg == "ja" else "No row is chosen."
        )
    chosen = {
        *approved,
        *extra,
        *(str(r.get(x)) for r in wanted for x in ("from", "to") if str(r.get(x)) in by_key),
    }
    reduced_rels = [
        r
        for r in draft.get("relations") or []
        if isinstance(r, dict)
        and (any(r is w for w in wanted) or (is_seed_rel(r) and r.get("problem") is None))
    ]
    reduced = {
        **draft,
        "concepts": [c for c in rows if str(c.get("key")) in chosen],
        "relations": reduced_rels,
        "questions": [],
        "answers": [],
    }
    body = commit_body(reduced, lang)
    body["batch"] = turn["batch"]
    notes = [n for n in turn["reply"].get("notes") or [] if n.get("kind") != "commit_error"]
    snapshot = copy.deepcopy(draft)  # commit_theme は通ると下書きを消す。後段で落ちたら書き戻す
    try:
        out = await themes.commit_theme(client, root, body, at=at)
    except themes.ThemeError as exc:
        turn["reply"]["notes"] = [*notes, note("commit_error", str(exc))]
        return turn, None
    turn["reply"]["notes"] = notes
    try:
        # 新しく承認した用語は「既にある用語」に直す（残りの提案が、あとで二重に鋳造しない）。
        # 承認した行・概念は下書きに残す（種の控えと突き合わせるため。印 ``from_seed`` で濃く出る）
        fresh = await fetch_terms()
        minted = iter(out["minted"])
        fixed: list[dict] = []
        for c in rows:
            if str(c.get("key")) in chosen and not _is_existing(c) and str(c.get("use")) == "new":
                slug = next(minted, "")
                c = {**c, "use": f"existing:{slug}", "slug": slug} if slug else c
            fixed.append(c)
        draft = _save(root, {**draft, "concepts": fixed}, at)
        seeded = chat_seed.seed_from_theme(root, fresh, out["theme"])
        session["seed"] = {"draft_id": draft["id"], **seeded["seed"]}
    except Exception:
        try:  # 下書きを書き戻す（書き戻しの失敗は元の例外を隠さない）
            themes.save_draft(root, snapshot)
        except Exception:
            logger.warning("chat confirm_only: could not restore the draft", exc_info=True)
        raise
    record = {
        "only": {
            "relations": [
                {"from": w["from"], "rel": _rel_short(str(w["rel"])),
                 "to": w["to"]} for w in only.get("relations") or []
            ],
            "concepts": extra,
        },
        "theme_id": out["theme"]["id"],
        "minted": list(out["minted"]),
        "lines": out["lines"],
        "batch": out["batch"],
        "at": at,
    }  # fmt: skip
    turn["confirmed"] = [*(turn.get("confirmed") or []), record]
    return turn, record


async def confirm(
    session: dict,
    turn_id: str,
    *,
    client: Any,
    lang: str,
    root: Any,
    at: str,
) -> tuple[dict, dict | None]:
    """✓ — その時点の下書き全部を確認済みにする。返り = ``(手番, commit | None)``。

    ``ThemeError``（検査で断られた）は例外にせず、手番の ``notes`` に ``commit_error`` を足して
    ``commit = None`` で返す（会話の中で読める）。書き込みの失敗は例外のまま上へ。"""
    turn = _find(session, turn_id)
    draft = _current_draft(session, root)
    if not _is_theme_proposal(turn) or draft is None:
        raise TurnRefused(
            409, "承認するものがありません" if _lg(lang) == "ja" else "Nothing to approve."
        )
    last = [t for t in session["turns"] if _is_theme_proposal(t)][-1]
    if last is not turn:
        raise TurnRefused(
            409,
            "最後の提案を承認してください"
            if _lg(lang) == "ja"
            else "Please approve the latest proposal.",
        )
    body = commit_body(draft, lang)
    body["batch"] = turn["batch"]
    prior = _theme_by_id(root, draft.get("extend"))
    notes = [n for n in turn["reply"].get("notes") or [] if n.get("kind") != "commit_error"]
    try:
        out = await themes.commit_theme(client, root, body, at=at)
    except themes.ThemeError as exc:
        turn["reply"]["notes"] = [*notes, note("commit_error", str(exc))]
        return turn, None
    turn["reply"]["notes"] = notes
    seed = session.get("seed")
    removed, kept = {"lines": 0, "terms": 0}, []
    if isinstance(seed, dict) and seed.get("draft_id") == draft["id"]:
        # 種にあって下書きに無いものは、ストアからも外す
        removed, kept = await chat_seed.apply_removals(
            client, root, out["theme"]["id"], seed, draft, lang=lang, at=at
        )
    session.pop("seed", None)
    record = {
        "theme_id": out["theme"]["id"],
        "minted": list(out["minted"]),
        "lines": out["lines"],
        "questions": list(out["questions"]),
        "batch": out["batch"],
        "removed": removed,
        "kept": kept,
        "at": at,
        # 戻す（11d）が取り消せるように、この ✓ で増えた問い（前のテーマに無かったもの）を控える
        "new_questions": [
            n for n in out["questions"] if n not in ((prior or {}).get("questions") or [])
        ],
        "new_memo": [
            str(m["id"])
            for m in out["theme"].get("memo_questions") or []
            if m.get("id") not in {x.get("id") for x in (prior or {}).get("memo_questions") or []}
        ],
    }
    # 1 行の承認（11o）で鋳造した分も、この承認の記録に足す（戻す・来歴が全部拾えるように）
    earlier = [
        m
        for t in session["turns"]
        if _is_theme_proposal(t)
        for rec in t.get("confirmed") or []
        if isinstance(rec, dict)
        for m in rec.get("minted") or []
    ]
    record["minted"] = list(dict.fromkeys([*earlier, *record["minted"]]))
    record["lines"] = int(record["lines"] or 0) + sum(
        int(rec.get("lines") or 0)
        for t in session["turns"]
        if _is_theme_proposal(t)
        for rec in t.get("confirmed") or []
        if isinstance(rec, dict)
    )
    session["theme_id"] = record["theme_id"]
    session["draft_id"] = None
    for t in session["turns"]:
        if _is_theme_proposal(t):
            t["state"] = "confirmed"
            t["before"] = None
            t["commit"] = dict(record)
    return turn, record


def revert(
    session: dict, turn_id: str, *, root: Any, at: str, lang: str = "ja"
) -> tuple[dict, list[str]]:
    """未確認の返事を、その返事の前の下書き（控え）に戻す。あとの未確認の返事も一緒に取り消す。
    返り = ``(手番, 取り消した手番の id)``。"""
    turn = _find(session, turn_id)
    if turn.get("state") != "proposed":
        raise TurnRefused(409, "取り消せるのは承認していない返事だけです")
    turns = session["turns"]
    start = turns.index(turn)
    undone = [t for t in turns[start:] if _is_theme_proposal(t)]
    before = turn.get("before")
    # 確認で消えた下書きを、控えから作り直してはいけない
    owner = before.get("id") if before is not None else session.get("draft_id")
    if not owner or themes.load_draft(root, owner) is None:
        raise TurnRefused(
            409,
            "この提案は承認済みです"
            if _lg(lang) == "ja"
            else "This proposal is already approved.",
        )
    approved_n = sum(
        len(rec.get("only", {}).get("relations") or [])
        for t in undone
        for rec in t.get("confirmed") or []
        if isinstance(rec, dict)
    )
    now = themes.load_draft(root, owner)
    if approved_n and now is not None:
        # 1 行の承認（11o）をした返事 — 承認済みの行・概念（種の控えにあるもの）は戻さない
        keys, rels = chat_seed.seed_marks(session.get("seed"), now)
        base = copy.deepcopy(before) if before is not None else {**now, "concepts": [],
                                                                  "relations": []}
        have_c = {str(c.get("key")) for c in base.get("concepts") or []}
        have_r = {(str(r.get("from")), str(r.get("rel")), str(r.get("to")))
                  for r in base.get("relations") or []}  # fmt: skip
        base["concepts"] = [
            *(base.get("concepts") or []),
            *(c for c in now.get("concepts") or [] if str(c.get("key")) in keys - have_c),
        ]
        base["relations"] = [
            *(base.get("relations") or []),
            *(
                r
                for r in now.get("relations") or []
                if (str(r.get("from")), str(r.get("rel")), str(r.get("to"))) in rels - have_r
            ),
        ]
        base["label"] = now.get("label", base.get("label"))
        saved = _save(root, base, at)
        session["draft_id"] = saved["id"]
        note_text = _KEPT_APPROVED[_lg(lang)].format(n=approved_n)
        turn["reply"]["notes"] = [*(turn["reply"].get("notes") or []), note("info", note_text)]
    elif before is None:  # この手番で下書きを作った — 下書きごと消す
        if session.get("draft_id"):
            themes.delete_draft(root, session["draft_id"])
        session["draft_id"] = None
        session.pop("seed", None)
    else:
        restored = copy.deepcopy(before)
        if now is not None:  # 直した名前は戻さない
            restored["label"] = now.get("label", restored.get("label"))
        saved = _save(root, restored, at)
        session["draft_id"] = saved["id"]
    for t in undone:
        t["state"] = "reverted"
        t["before"] = None
    return turn, [t["id"] for t in undone]


# ---- 「見せて」の表・Ask（11d） ----------------------------------------------------------------

_ASK_HISTORY = 6  # demo-agent に見せる、話の Ask の手番（最近）
_ASK_TIMEOUT = httpx.Timeout(600, connect=10)
_LLM_HEADERS = ("x-api-key", "x-llm-provider", "x-llm-model", "x-llm-api-base", "x-llm-max-tokens")

_ASK_TEXT = {
    "ja": {
        "unavailable": "質問の答えはこの環境では出せません",
        "failed": "答えを取れませんでした。もう一度試してください",
        "no_table": "表にできるものが見つかりませんでした",
        "table": "「{name}」の表（{shown} 件・全 {total} 件）",
        "via": "用語「{term}」に入っている種類「{kind}」の表",
    },
    "en": {
        "unavailable": "Answers to questions are not available in this environment",
        "failed": "Could not get an answer. Please try again",
        "no_table": "Nothing to show as a table was found",
        "table": "Table of \"{name}\" ({shown} of {total})",
        "via": "Table of the kind \"{kind}\" inside the term \"{term}\"",
    },
}


def run_show_turn(
    session: dict,
    text: str,
    table: dict | None,
    *,
    via_term: str | None = None,
    at: str,
    lang: str,
) -> dict:
    """「<名前>を見せて」— AI を呼ばず、閉じたテンプレートの表を返事にする（``state: none``・
    ``by: machine``）。``table`` が ``None`` なら一言だけ。用語から引いたときは ``via_term``
    （用語の名前）。"""
    lg = _lg(lang)
    t = _ASK_TEXT[lg]
    notes: list[dict] = []
    if table is None:
        said = t["no_table"]
        notes.append(note("info", said))
    else:
        name = str(table["kind"]["label"])
        said = t["table"].format(name=name, shown=len(table["rows"]), total=table["total"])
        if via_term:
            notes.append(note("info", t["via"].format(term=via_term, kind=name)))
    reply = _reply(said, notes=notes)
    reply["table"] = table
    return _append_turn(
        session,
        at=at,
        user={"text": text.strip(), "attachments": []},
        reply=reply,
        state="none",
        before=None,
        by="machine",
    )


def ask_history(session: dict) -> list[dict]:
    """話の Ask の手番だけ（``reply.answer`` がある・戻していない）を古い順に
    ``user`` → ``assistant``。最近 6 件。"""
    kept = [
        t
        for t in session.get("turns") or []
        if (t.get("reply") or {}).get("answer") and t.get("state") != "reverted"
    ]
    out: list[dict] = []
    for t in kept[-_ASK_HISTORY:]:
        said = (t.get("user") or {}).get("text")
        answer = str(((t.get("reply") or {}).get("answer") or {}).get("text") or "").strip()
        if said and answer:
            out.append({"role": "user", "content": str(said)[:_MAX_CONTENT]})
            out.append({"role": "assistant", "content": answer[:_MAX_CONTENT]})
    return out


async def ask_remote(
    url: str,
    question: str,
    history: list[dict],
    llm_headers: dict[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """demo-agent の ``POST /demo/ask``（**ロックの外**）。返り = ``{"ok": True, "data": …}`` または
    ``{"ok": False, "reason": "network"|"status"|"shape"}``（失敗は例外にしない・詳細はログ）。"""
    headers = {k: v for k, v in llm_headers.items() if k.lower() in _LLM_HEADERS and v}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=_ASK_TIMEOUT) as http:
            res = await http.post(
                url.rstrip("/") + "/demo/ask",
                json={"question": question, "history": history},
                headers=headers,
            )
    except httpx.HTTPError:
        logger.warning("chat ask: demo-agent unreachable", exc_info=True)
        return {"ok": False, "reason": "network"}
    if res.status_code != 200:
        logger.warning("chat ask: demo-agent answered %s: %s", res.status_code, res.text[:200])
        return {"ok": False, "reason": "status"}
    try:
        data = res.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        logger.warning("chat ask: unexpected body from demo-agent")
        return {"ok": False, "reason": "shape"}
    return {"ok": True, "data": data}


def _short(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/#"))[-1] or iri


def _answer_of(data: dict, cited: dict[str, dict[str, str]]) -> dict:
    """demo-agent の返事 → ``reply.answer``（出典の名前とデータセット名は ``cited`` から）。"""
    citations = []
    for c in data.get("citations") or []:
        if not isinstance(c, dict) or not c.get("iri"):
            continue
        iri = str(c["iri"])
        got = cited.get(iri) or {}
        citations.append(
            {
                "iri": iri,
                "label": str(c.get("label") or got.get("label") or _short(iri)),
                "dataset_id": str(c.get("dataset_id") or got.get("dataset_id") or ""),
                "dataset_label": str(c.get("dataset_label") or got.get("dataset_label") or ""),
            }
        )
    warnings = []
    for w in data.get("warnings") or []:
        msg = w if isinstance(w, str) else (w or {}).get("message") or (w or {}).get("text")
        if msg:
            warnings.append({"message": str(msg)})
    tools = []
    for v in data.get("verified_tools") or []:
        name = v if isinstance(v, str) else (v or {}).get("title") or (v or {}).get("name")
        if name:
            tools.append(str(name))
    return {
        "text": str(data.get("answer") or ""),
        "citations": citations,
        "notes": [str(n) for n in data.get("notes") or [] if isinstance(n, str)],
        "warnings": warnings,
        "verified_tools": tools,
        "unverified_sparql": bool(data.get("unverified_sparql")),
        "answered": bool(data.get("answered", True)),
    }


def run_ask_turn(
    session: dict,
    text: str,
    outcome: dict,
    *,
    cited: dict[str, dict[str, str]] | None = None,
    at: str,
    lang: str,
) -> dict:
    """Ask の手番を足す。``outcome`` = :func:`ask_remote` の返り。何も保存しない（返事の記録だけ・
    ``state: none``）。失敗は ``notes.ask_failed``（種別だけ）。"""
    lg = _lg(lang)
    user = {"text": text.strip(), "attachments": []}
    if not outcome.get("ok"):
        said = _ASK_TEXT[lg]["failed"]
        return _append_turn(
            session,
            at=at,
            user=user,
            reply=_reply(said, notes=[note("ask_failed", said)]),
            state="none",
            before=None,
            by="machine",
            route="ask",
        )
    answer = _answer_of(outcome["data"], cited or {})
    reply = _reply(answer["text"])
    reply["answer"] = answer
    return _append_turn(
        session, at=at, user=user, reply=reply, state="none", before=None, by="ai", route="ask"
    )


def run_chat_turn(session: dict, text: str, reply: str, *, at: str, lang: str) -> dict:
    """会話の手番（11m）— 地の文だけの返事。札も承認も出ない（``ops: []``・``state: none``）。

    ``route: "chat"`` を手番に持たせ、UI が返事の下に「知識グラフに足す」チップを出す。"""
    return _append_turn(
        session,
        at=at,
        user={"text": text.strip(), "attachments": []},
        reply=_reply(reply, ops=[]),
        state="none",
        before=None,
        by="ai",
        route="chat",
    )


def run_ask_unavailable_turn(session: dict, text: str, *, at: str, lang: str) -> dict:
    """demo-agent の URL が無い環境 — 一言だけ（``notes.ask_unavailable``・``state: none``）。"""
    said = _ASK_TEXT[_lg(lang)]["unavailable"]
    return _append_turn(
        session,
        at=at,
        user={"text": text.strip(), "attachments": []},
        reply=_reply(said, notes=[note("ask_unavailable", said)]),
        state="none",
        before=None,
        by="machine",
        route="ask",
    )


# ---- 確認済みの戻す（11d） ---------------------------------------------------------------------

_DATA_NO_REVERT = {
    "ja": "公開したデータは取り消せません（データの削除で）",
    "en": "Published data cannot be undone (delete it from Data)",
}


_REVERT_FAILED = {"ja": "削除できませんでした", "en": "could not be deleted"}


async def _term_name(client: Any, slug: str) -> str:
    """外せなかった用語の名前（引けなければ slug）。"""
    try:
        iri = shared_vocab.term_iri(slug)
        return next((t.label for t in await shared_vocab._read_terms(client) if t.iri == iri), slug)
    except Exception:
        return slug


async def _line_name(client: Any, ln: dict, lg: str) -> str:
    """外せなかった関係の一言（用語の名前で「A は B を下げる」の形）。引けなければ末尾の名前。"""
    rel = graph_diff.PRED_TO_REL.get(str(ln.get("relation")))
    try:
        names = {t.iri: t.label for t in await shared_vocab._read_terms(client)}
    except Exception:
        names = {}

    def name(iri: str) -> str:
        return names.get(iri) or re.split(r"[#/]", iri.rstrip("/#"))[-1] or iri

    if rel is None or rel not in graph_diff.REL_SENTENCE[lg]:
        return f"{name(ln['source'])} - {name(ln['target'])}"
    return graph_diff.REL_SENTENCE[lg][rel].format(s=name(ln["source"]), o=name(ln["target"]))


async def revert_confirmed(
    session: dict, turn_id: str, *, client: Any, root: Any, at: str, lang: str = "ja"
) -> tuple[dict, dict, list[str]]:
    """確認済みの手番を、``commit`` の記録から取り消す。返り = ``(手番, undone, 戻した手番の id)``。

    線（同じ ``batch`` で足したもの）→ ``remove_alignment``・この ✓ で増えた問い → テーマから外す
    （道具も外す）・この ✓ で鋳造した用語 → ``remove_term``（問い・ほかの関係が使っていれば残して
    ``kept``）。同じ ``commit``（``batch``）を共有する手番は全部 ``reverted``。話の ``theme_id`` は
    残す。公開したデータの手番は 409。"""
    lg = _lg(lang)
    turn = _find(session, turn_id)
    if turn.get("attachment") or turn.get("attach_run"):
        raise TurnRefused(409, _DATA_NO_REVERT[lg])
    commit = turn.get("commit")
    if turn.get("state") != "confirmed" or not isinstance(commit, dict):
        raise TurnRefused(
            409,
            "取り消せるのは承認済みの手番だけです"
            if lg == "ja"
            else "Only approved turns can be undone",
        )
    batch = commit.get("batch")
    theme_id = commit.get("theme_id")
    undone: dict = {"lines": 0, "terms": 0, "questions": 0, "kept": [], "failed": []}
    minted = [str(x) for x in commit.get("minted") or []]
    failed_why = _REVERT_FAILED[lg]

    # 1. 線 — この ✓ の batch で足したものだけ
    gone_lines: set[tuple[str, str, str]] = set()
    # 1 行の承認（11o）の batch の線も、この返事の単位として外す
    batches = {batch} if batch else set()
    for t in session["turns"]:
        c = t.get("commit")
        if isinstance(c, dict) and c.get("batch") == batch:
            batches |= {
                str(r["batch"])
                for r in t.get("confirmed") or []
                if isinstance(r, dict) and r.get("batch")
            }
    if batches:
        for ln in await crosswalk_runtime.list_alignments(client):
            if ln.get("batch") not in batches:
                continue
            try:
                await crosswalk_runtime.remove_alignment(
                    client, ln["source"], ln["target"], ln["relation"]
                )
            except Exception:
                logger.warning("chat revert: remove_alignment failed", exc_info=True)
                undone["failed"].append(
                    {"what": await _line_name(client, ln, lg), "reason": failed_why}
                )
                continue
            gone_lines.add((ln["source"], ln["relation"], ln["target"]))
    undone["lines"] = len(gone_lines)

    # 2. 問い — テーマの記録から外す（この ✓ で増えたものだけ）
    new_q = [str(n) for n in commit.get("new_questions") or []]
    new_memo = {str(m) for m in commit.get("new_memo") or []}
    data = themes.load_themes_file(root)
    if data is not None and theme_id:
        others = [t for t in data["themes"] if t.get("id") != theme_id]
        elsewhere = {
            n
            for t in others
            for n in (*(t.get("questions") or []), *(t.get("support_questions") or []))
        }
        for t in data["themes"]:
            if t.get("id") != theme_id:
                continue
            had = len(t.get("questions") or []) + len(t.get("memo_questions") or [])
            t["questions"] = [n for n in t.get("questions") or [] if n not in new_q]
            t["memo_questions"] = [
                m for m in t.get("memo_questions") or [] if str(m.get("id")) not in new_memo
            ]
            for n in new_q:
                (t.get("selections") or {}).pop(n, None)
                (t.get("wanted") or {}).pop(n, None)
            undone["questions"] = had - len(t["questions"]) - len(t["memo_questions"])
            t["relations"] = [
                r
                for r in t.get("relations") or []
                if (str(r.get("from")), str(r.get("relation")), str(r.get("to"))) not in gone_lines
            ]
        themes._save_themes(root, data)
        drop = [n for n in new_q if n not in elsewhere]
        if drop:
            query_tools.remove_registry_query_tools_by_name(root, shared_vocab.REGISTRY_ID, drop)

    # 3. 用語 — ほかが使っていれば残す
    msg = chat_seed._KEPT[lg]
    blocked = chat_seed._question_users(root) & set(minted)
    gone_slugs: set[str] = set()
    for slug in minted:
        if slug in blocked:
            undone["kept"].append({"slug": slug, "reason": msg["question"]})
            continue
        try:
            await shared_vocab.remove_term(client, root, slug)
        except shared_vocab.TermNotFound:
            gone_slugs.add(slug)
        except shared_vocab.TermHasLines:
            undone["kept"].append({"slug": slug, "reason": msg["linked"]})
        except Exception:
            logger.warning("chat revert: remove_term failed", exc_info=True)
            undone["failed"].append(
                {"what": await _term_name(client, slug), "reason": failed_why}
            )
        else:
            undone["terms"] += 1
            gone_slugs.add(slug)
    if gone_slugs:
        data = themes.load_themes_file(root)
        if data is not None:
            for t in data["themes"]:
                if t.get("id") == theme_id:
                    for k in ("uses", "minted"):
                        if k in t:
                            t[k] = [s for s in t[k] if s not in gone_slugs]
            themes._save_themes(root, data)
    try:
        await shared_vocab.recompute_wired(client, root, at=at)
    except Exception:
        logger.warning("chat revert: recompute_wired failed", exc_info=True)

    reverted: list[str] = []
    if undone["failed"]:  # 1 つでも外せなければ手番は戻さない（もう一度押せる）
        return turn, undone, reverted
    for t in session["turns"]:
        c = t.get("commit")
        if t.get("state") == "confirmed" and isinstance(c, dict) and c.get("batch") == batch:
            t["state"] = "reverted"
            reverted.append(t["id"])
    return turn, undone, reverted
