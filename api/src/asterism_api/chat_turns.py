"""話の手番の組み立て（ui-redesign PR 11a・§19）— 純粋寄りの関数。

``chat_routes`` が ``themes_lock`` の中で呼ぶ。ここは HTTP を知らない（拒むときは
:class:`TurnRefused`）。ストアの読み（既にある用語）は引数で渡され、下書きの読み書きは
``root`` を介して ``asterism.themes`` に任せる。話（``session``）は**書き換える**が、保存は
呼ぶ側（ルート）がする。

* :func:`run_text_turn` — 人の文の手番。11a は ``ai is None`` の枝だけ（文を問いとして下書きに
  入れ、機械の文を返す）。11b が ``ai`` の枝を足す（下書きが無ければ ``propose_theme_draft``・
  あれば ``propose_graph_diff``）。**AI を呼ぶ口はこの関数の引数 ``ai`` 1 つ**。
* :func:`run_ops_turn` — 人の直接編集（差分を当てる）。
* :func:`commit_body` — ✓ の本文（UI の ``buildCommitBody`` の移植）。
* :func:`confirm` — ✓。未確認の返事をまとめて確認済みにする。
* :func:`revert` — 未確認の返事を控えに戻す。

AI は差分と文だけ（SPARQL・コード・IRI を書かせない・値は渡さない）。9 関係・種別・循環の検査は
``graph_diff`` と ``themes`` の既存の検査をそのまま通す。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy
from typing import Any

from asterism import chat_sessions, graph_diff, themes
from asterism.shared_vocab import SharedTerm, normalize_label
from asterism_step0.graph_propose import GraphDiffError, validate_graph_diff

__all__ = [
    "TurnRefused",
    "blank_answer",
    "commit_body",
    "confirm",
    "revert",
    "run_ops_turn",
    "run_text_turn",
    "sync_answers",
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
    "ja": "AI が設定されていないので、概念と関係は右の欄で手で足せます。"
    "設定 › AI で鍵を入れると AI が提案します",
    "en": "AI is not set up, so add concepts and relations by hand on the right. "
    "Add a key in Settings › AI and the AI will propose them.",
}

#: 落とした操作の理由（日本語の文・``themes:chat.dropped.*`` と同じ語彙）
_DROPPED = {
    "ja": {
        "duplicate": "すでにあります",
        "cycle": "輪になります",
        "mixed": "種類と項目が混ざります",
        "unknown_key": "知らない名前です",
        "dismissed": "前に外したものです",
        "invalid": "形が合いません",
        "unsupported": "ここでは使えません",
        "dataset_only": "表の列だけに使えます",
        "same": "自分自身にはつなげません",
        "quantity_target": "量は、項目から量の種類へだけつなげます",
        "target_not_property": "下げる・上げる・変えるの相手は項目だけです",
        "not_found": "見つかりません",
        "limit": "上限に達しました",
        "not_renamable": "名前を直せません",
    },
    "en": {
        "duplicate": "already there",
        "cycle": "it would loop",
        "mixed": "a kind and an item cannot be mixed",
        "unknown_key": "unknown name",
        "dismissed": "you removed it before",
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
    ai: Any | None,
    lang: str,
    root: Any,
    existing: list[SharedTerm],
    dismissed: list[dict],
    at: str,
) -> dict:
    """人の文の手番を 1 つ足す（``session`` を書き換える・保存はしない）。返り = 手番。

    ``ai is None``（11a）: 文を問いとして下書きに入れる。下書きが無ければ作る
    （``extend`` = 話のテーマ）。11b は ``ai is not None`` の枝を足す（``existing`` ・
    ``dismissed`` はそのとき使う）。"""
    if ai is not None:
        raise NotImplementedError("AI の道は 11b で足す")
    lg = _lg(lang)
    text = text.strip()
    draft = _current_draft(session, root)
    before: dict | None = None
    changed = True
    try:
        if draft is None:
            made = themes.create_draft(root, "", [text], at=at, extend=session.get("theme_id"))
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
        reply=_reply(said, notes=[note("ai_unset", _AI_UNSET[lg])]),
        state="proposed" if changed else "none",
        before=before,
        by="human",
    )


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
) -> dict:
    """人の直接編集（差分）を下書きに当てる手番。検査は提案のときと同じ閉じた検査
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
        said = "足せるものがありませんでした。" if lg == "ja" else "Nothing could be added."
    return _append_turn(
        session,
        at=at,
        user=None,
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


def _find(session: dict, turn_id: str) -> dict:
    for t in session.get("turns") or []:
        if t.get("id") == turn_id:
            return t
    raise TurnRefused(404, "その手番はありません")


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
    if turn.get("state") != "proposed" or draft is None:
        raise TurnRefused(409, "確認するものがありません")
    body = commit_body(draft, lang)
    body["batch"] = turn["batch"]
    notes = [n for n in turn["reply"].get("notes") or [] if n.get("kind") != "commit_error"]
    try:
        out = await themes.commit_theme(client, root, body, at=at)
    except themes.ThemeError as exc:
        turn["reply"]["notes"] = [*notes, note("commit_error", str(exc))]
        return turn, None
    turn["reply"]["notes"] = notes
    record = {
        "theme_id": out["theme"]["id"],
        "minted": list(out["minted"]),
        "lines": out["lines"],
        "questions": list(out["questions"]),
        "batch": out["batch"],
        "at": at,
    }
    session["theme_id"] = record["theme_id"]
    session["draft_id"] = None
    for t in session["turns"]:
        if t.get("state") == "proposed":
            t["state"] = "confirmed"
            t["before"] = None
            t["commit"] = dict(record)
    return turn, record


def revert(session: dict, turn_id: str, *, root: Any, at: str) -> tuple[dict, list[str]]:
    """未確認の返事を、その返事の前の下書き（控え）に戻す。あとの未確認の返事も一緒に取り消す。
    返り = ``(手番, 取り消した手番の id)``。"""
    turn = _find(session, turn_id)
    if turn.get("state") != "proposed":
        raise TurnRefused(409, "取り消せるのは未確認の返事だけです")
    turns = session["turns"]
    start = turns.index(turn)
    undone = [t for t in turns[start:] if t.get("state") == "proposed"]
    before = turn.get("before")
    if before is None:  # この手番で下書きを作った — 下書きごと消す
        if session.get("draft_id"):
            themes.delete_draft(root, session["draft_id"])
        session["draft_id"] = None
    else:
        saved = _save(root, copy.deepcopy(before), at)
        session["draft_id"] = saved["id"]
    for t in undone:
        t["state"] = "reverted"
        t["before"] = None
    return turn, [t["id"] for t in undone]
