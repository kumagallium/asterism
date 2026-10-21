"""話（チャットのセッション）の保存 — ``<registry>/chat/<id>.json``（ui-redesign PR 11a・§19）。

1 つの話 = 1 つのファイル。話は ``theme_id``（✓ で置いたテーマ・最初は ``None``）と
``draft_id``（いま育てている未確認の下書き・無ければ ``None``）を持ち、手番（``turns``）を
古い順に積む。未確認の中身はテーマの下書き（``themes/<id>/draft.json``）の側にあり、ここには
**手番の記録**（人の文・返事・控え・✓ の記録）だけが入る。

形（version 1）::

    {"version": 1, "id": "c…", "title": str, "created_at": iso, "updated_at": iso,
     "theme_id": str|None, "draft_id": str|None,
     "turns": [{"id": "t1", "at": iso, "user": {"text", "attachments"}|None,
                "reply": {text, ops, sentences, questions, bindings, notes, answer, table},
                "batch": "<話の id>:<手番の id>",
                "state": "none"|"proposed"|"confirmed"|"reverted",
                "before": <下書きの控え>|None, "commit": {…}|None,
                "by": "ai"|"human"|"machine"}]}

``notes[]`` の 1 件は ``{"kind", "text", "choice"}``。``kind`` は閉集合 :data:`NOTE_KINDS`。
壊れた・version 違いのファイルは読めない扱い（``None``・一覧にも出さない）。書き込みは一時
ファイル→rename（途中で落ちても前の中身が残る）。決定論・LLM なし。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism import shared_vocab as sv

logger = logging.getLogger(__name__)

__all__ = [
    "NOTE_KINDS",
    "SESSION_VERSION",
    "TURN_STATES",
    "delete_session",
    "list_sessions",
    "load_session",
    "new_session",
    "new_session_id",
    "new_turn_id",
    "save_session",
    "session_of_draft",
]

SESSION_VERSION = 1
CHAT_DIR = "chat"
ID_RE = re.compile(r"^c[a-z0-9]{8,12}$")

#: ``notes[].kind`` の閉集合（11c で増える）
NOTE_KINDS = frozenset({"ai_unset", "dropped", "no_question", "commit_error", "info"})
TURN_STATES = ("none", "proposed", "confirmed", "reverted")

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _B36[r] + out
    return out or "0"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dir(root: Path | str) -> Path:
    return sv._dir(root) / CHAT_DIR


def _path(root: Path | str, session_id: str) -> Path | None:
    if not ID_RE.match(session_id or ""):
        return None
    return _dir(root) / f"{session_id}.json"


def new_session_id() -> str:
    """``c`` + 時刻（秒・base36）+ 乱数 4 字（base36）。"""
    rand = "".join(secrets.choice(_B36) for _ in range(4))
    return "c" + _b36(int(time.time())) + rand


def new_session(title: str = "", *, at: str | None = None) -> dict:
    """空の話（保存はしない）。"""
    now = at or _now()
    return {
        "version": SESSION_VERSION,
        "id": new_session_id(),
        "title": title,
        "created_at": now,
        "updated_at": now,
        "theme_id": None,
        "draft_id": None,
        "turns": [],
    }


def new_turn_id(session: dict) -> str:
    """次の手番の id（``t<連番>``）。"""
    top = 0
    for t in session.get("turns") or []:
        m = re.fullmatch(r"t(\d+)", str(t.get("id") or ""))
        if m:
            top = max(top, int(m.group(1)))
    return f"t{top + 1}"


def load_session(root: Path | str, session_id: str) -> dict | None:
    """話。無い・壊れている・version 違いは ``None``。"""
    path = _path(root, session_id)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("id") != session_id:
        return None
    if data.get("version") != SESSION_VERSION or not isinstance(data.get("turns"), list):
        return None
    return data


def save_session(root: Path | str, session: dict) -> dict:
    """書く（``updated_at`` を進める・一時ファイル→rename）。書いた形を返す。"""
    path = _path(root, str(session.get("id")))
    if path is None:
        raise ValueError(f"不正な話の id: {session.get('id')!r}")
    out = {**session, "version": SESSION_VERSION, "updated_at": _now()}
    sv._atomic_write(path, json.dumps(out, indent=2, ensure_ascii=False))
    return out


def delete_session(root: Path | str, session_id: str) -> bool:
    path = _path(root, session_id)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


def _all(root: Path | str) -> list[dict]:
    base = _dir(root)
    if not base.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(base.glob("*.json")):
        s = load_session(root, p.stem)
        if s is not None:
            out.append(s)
    return out


def _summary(s: dict) -> dict:
    turns: list[Any] = s.get("turns") or []
    return {
        "id": s["id"],
        "title": s.get("title") or "",
        "created_at": s.get("created_at"),
        "updated_at": s.get("updated_at"),
        "theme_id": s.get("theme_id"),
        "draft_id": s.get("draft_id"),
        "turns": len(turns),
        "proposed": sum(1 for t in turns if t.get("state") == "proposed"),
    }


def list_sessions(root: Path | str) -> list[dict]:
    """``updated_at`` の新しい順。壊れたファイルは出さない。"""
    rows = [_summary(s) for s in _all(root)]
    rows.sort(key=lambda r: str(r["updated_at"] or ""), reverse=True)
    return rows


def session_of_draft(root: Path | str, draft_id: str) -> str | None:
    """その下書きを育てている話の id（無ければ ``None``）。"""
    for s in _all(root):
        if s.get("draft_id") == draft_id:
            return str(s["id"])
    return None
