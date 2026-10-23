"""1 文ごとの振り分け（11m）— 会話／答える／提案 を AI が 1 回の短い呼び出しで決める。

決定論の規則（戻す・見せて・答えて・図の言葉）のあと、AI があれば呼ぶ。
返りは ``{"mode": "chat"|"ask"|"propose", "reply": "…"}`` だけ。壊れていたら :class:`RouteError`
（呼ぶ側は ``propose`` に落とす）。純関数 — 同期の ``llm.complete`` を呼ぶので、
呼び出し側は ``asyncio.to_thread`` で**ロックの外**から呼ぶ。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from asterism_step0.llm import as_completion

from asterism_api.chat_intent import _QUESTION_MARK

Mode = Literal["chat", "ask", "propose"]
_MODES = ("chat", "ask", "propose")

#: system の先頭の固定の印（スタブとテストが見分ける）
ROUTER_MARK = "# Asterism chat router"

ROUTER_SYSTEM = f"""{ROUTER_MARK}
You route ONE message from a person who is chatting in Asterism, a tool that turns their
questions and tables into a knowledge graph. Decide what the message is, and answer in JSON.

Modes:
- "chat": small talk, thanks, greetings, opinions, or questions about how to use the product.
  Nothing should be added to the knowledge graph. Write a short, friendly reply in plain
  sentences (no lists, no markdown).
- "ask": a question that can be answered from the data they already loaded or from the
  approved terms (for example "which shop sells potatoes cheapest?"). Leave reply empty.
- "propose": something they want to know, a piece of knowledge they want to add, or the
  design of their data (terms, relations, questions). Leave reply empty.

Never ask the person to explain more in a "chat" reply (no "please tell me more", no
"could you be specific", no "詳しく教えて"). If something is missing, name the ONE thing that is
missing and choose "propose" instead.

When you are unsure, choose "chat" and end the reply with a short sentence saying they can
also add it to the knowledge graph. Write that sentence in the person's language:
Japanese "知識グラフに足すこともできます", English "You can also add it to the knowledge graph".

The context lines tell you the state of the conversation; use them only to decide.
Reply in the language named in "Reply language".

Output ONLY a JSON object, no markdown fence:
{{"mode": "chat" | "ask" | "propose", "reply": "..."}}
"""

#: 未承認の提案がある話で、疑問でない文に付ける注意書き
DRAFT_NOTE = (
    "Note: this conversation has an unapproved proposal. If the message is a reply to it "
    "(a correction, an addition or an objection), choose propose. Greetings, thanks, short "
    "acknowledgements (OK, that is fine) and questions about how to use the product stay chat."
)

#: 会話の返事が聞き返しになっている印（決定論の網）。返事が疑問で終わるときだけ見る
_ASKS_BACK = (
    "いただけますか", "教えてください", "教えて下さい", "could you", "can you", "more details",
)  # fmt: skip

#: 履歴に渡す#: 履歴に渡す手番の数と、AI の返事の長さ
_MAX_TURNS = 6
_MAX_REPLY = 200


class RouteError(Exception):
    """振り分けの返りが読めない（形が違う・chat なのに reply が空）。"""


@dataclass(frozen=True)
class RouteResult:
    mode: Mode
    reply: str = ""


def history_of(session: dict) -> list[dict]:
    """直近 6 手番の人の文と AI の返事の先頭 200 字（戻した手番は飛ばす）。"""
    out: list[dict] = []
    kept = [t for t in (session.get("turns") or []) if t.get("state") != "reverted"]
    for t in kept[-_MAX_TURNS:]:
        said = str((t.get("user") or {}).get("text") or "").strip()
        if not said:
            continue
        reply = str((t.get("reply") or {}).get("text") or "").strip()
        out.append({"user": said, "assistant": reply[:_MAX_REPLY]})
    return out


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _read(text: str) -> RouteResult:
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    i, j = t.find("{"), t.rfind("}")  # 前置き・後書きがあっても本文の {…} を読む
    if i == -1 or j <= i:
        raise RouteError("the router did not return JSON")
    try:
        obj = json.loads(t[i : j + 1])
    except json.JSONDecodeError:
        raise RouteError("the router did not return JSON") from None
    if not isinstance(obj, dict):
        raise RouteError("the router did not return a JSON object")
    raw = obj.get("mode")
    mode = raw.strip().lower() if isinstance(raw, str) else raw
    if mode not in _MODES:
        raise RouteError(f"unknown mode: {raw!r}")
    if mode != "chat":  # reply は chat のときだけ見る
        return RouteResult(mode, "")
    reply = obj.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise RouteError("chat needs a reply")
    low = reply.lower()
    # 聞き返しは返事にしない（呼ぶ側が propose に落とす）
    if _QUESTION_MARK.search(reply) and any(w in low for w in _ASKS_BACK):
        raise RouteError("chat reply asks the person to explain more")
    return RouteResult("chat", reply.strip())


def route_message(
    llm: Any,
    *,
    text: str,
    history: list[dict],
    lang: str,
    theme_label: str | None,
    has_draft: bool,
    n_terms: int,
    n_datasets: int | None = None,
) -> RouteResult:
    """1 文の振り分け。``llm.complete`` を 1 回呼ぶ。読めなければ :class:`RouteError`。"""
    lines = [
        f"Reply language: {'English' if lang == 'en' else 'Japanese'}",
        f"Theme: {theme_label or '(none)'}",
        f"Draft in progress: {'yes' if has_draft else 'no'}",
        f"Approved terms: {n_terms}",
    ]
    if n_datasets is not None:
        lines.append(f"Datasets: {n_datasets}")
    if has_draft and not _QUESTION_MARK.search(text.strip()):
        lines.append(DRAFT_NOTE)
    lines.append("")
    lines.append("# Recent turns")
    if history:
        for h in history:
            lines.append(f"- person: {h['user']}")
            if h["assistant"]:
                lines.append(f"  assistant: {h['assistant']}")
    else:
        lines.append("(none)")
    lines += ["", "# Message", text]
    completion = as_completion(llm.complete(ROUTER_SYSTEM, "\n".join(lines)))
    return _read(completion.text)
