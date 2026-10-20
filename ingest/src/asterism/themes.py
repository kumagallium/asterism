"""Themes — 「知りたいことから作る」(ui-redesign PR 4・PR 6a v2・design.md §15).

A *theme* is a named group of questions a person wants answered. A question is answered by
the DATA (a count / range / top over declared tools) or by KNOWLEDGE (the confirmed
relations — part of / lowers / raises / affects / kind of — read by ``asterism.knowledge``).
A theme does not own the terms (``sv:<slug>``), lines and competency questions (CQs) it
names in the way a dataset does, but it remembers what it MADE (``minted`` terms, the
``relations`` it added) so that taking the theme away takes back what nobody else uses. This
module holds

* the stored shape (``registry/vocab-shared/themes.json`` + one working draft per theme
  being made under ``themes/<id>/draft.json``, v2) and the idempotent first migration;
* the deterministic post-process of an AI draft (concepts by ``key``, slug derivation, exact
  matches against the standards and the existing terms — which beat the model's ``use`` —,
  a kind check per relation, cycle checks, dismissed concepts / relations dropped, one
  answer per question; operations that cannot be run yet → memo questions);
* the ONE operation that turns a confirmed draft into stored terms, lines (with who / when /
  which batch / why), CQs and a theme record (:func:`commit_theme`) — everything that can be
  refused is checked before the first write, and a failure while writing takes back what was
  written — and its inverse for a whole theme (:func:`remove_theme_cascade`);
* the CSV "templates" (header rows only) a theme implies — from the questions' targets only.

Nothing here calls a model or writes generated code: the AI only ever fills the closed
JSON of ``asterism_step0.theme_propose``; every IRI used below comes from the store, the
minted slugs or the curated standards catalog.
"""

# ruff: noqa: RUF001
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import (
    ALIGN_RELATIONS,
    ALIGNMENT_GRAPH,
    REASON_MAX,
    _check_same_shape,
    _declared_shapes,
    assert_alignment,
    remove_alignment,
)
from asterism.query_tools import remove_registry_query_tools_by_name
from asterism.shared_vocab import CQSpec, SharedTerm, normalize_label

logger = logging.getLogger(__name__)

THEMES_FILE = "themes.json"
DRAFTS_DIR = "themes"
DRAFT_FILE = "draft.json"
FIRST_THEME_ID = "first"
FIRST_THEME_LABEL = "はじめの問い"

#: 今の宣言ツールで答えられる数え方（``api/questions_routes.QUESTION_OPS`` と同じ・api の
#: テストが固定する）。これ以外は公開せず「メモの問い」に残す（PR 5 で広げる）。
FITTING_OPS = ("count", "range", "top")
#: 知識で答える（``asterism.knowledge`` の走者・``theme_propose.KNOWLEDGE_OPS`` と同じ）。
KNOWLEDGE_OPS = ("factors", "parts", "kinds")
KNOWLEDGE_DIRECTIONS = ("lowers", "raises", "affects")
#: 下書きの関係の名前（``theme_propose.RELATIONS`` と同じ）。ストアの述語には
#: :func:`relation_predicate` で写す。
RELATION_KINDS = ("sub", "eq", "quantity", "part", "lowers", "raises", "affects")
#: 1 つのテーマに置ける概念の上限（累積）。
MAX_CONCEPTS = 120
_MAX_DRAFT_RELATIONS = 400
DRAFT_VERSION = 2
#: 知識の問いのツール名の頭（データの宣言ツール ``cq_`` とは別・ツールは作らない）。
KNOWLEDGE_TOOL_PREFIX = "kq_"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
_ID_MAX = 40
#: ルート ``#/questions/<…>`` の予約語（用語のページ・下書き）と、``/api/themes/drafts/sky`` の
#: ``sky``。テーマ id・下書き id にしない。
RESERVED_IDS = frozenset({"term", "drafts", "draft", "sky"})
_LABEL_MAX = 80
_QUESTION_MAX = 300
_MAX_QUESTIONS = 40
_QK_BASE = sv._QUDT_QK


class ThemeError(ValueError):
    """A theme operation was refused. ``status`` is the HTTP status the api answers."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# slug — the SAME rule as ``ui/src/vocabQuestion.ts`` ``slugFromText``
# ---------------------------------------------------------------------------


def slug_from_text(text: str) -> str:
    """English text → a slug (``^[a-z][a-z0-9_]*$``), or ``""`` when none can be made.

    NFKD → camelCase split → lower → every non-``[a-z0-9]`` run becomes ``_`` → trim
    ``_`` → a leading digit gets ``x_``. Mirrors ``slugFromText`` in
    ``ui/src/vocabQuestion.ts`` step for step (both test files pin the same examples),
    so a slug the screen suggests is the slug the server derives. Japanese alone gives
    ``""`` — a person types the slug then."""
    s = unicodedata.normalize("NFKD", text or "")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    if not s:
        return ""
    if s[0].isdigit():
        s = f"x_{s}"
    return s if re.match(sv.SLUG_RE, s) else ""


def unique_slug(base: str, taken: Iterable[str]) -> str:
    """``base`` or ``base_2`` / ``base_3`` … the first one not in ``taken``."""
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def new_theme_id(label: str, taken: Iterable[str]) -> str:
    """A fresh theme/draft id from ``label``: at most 40 chars (the ``_ID_RE`` limit),
    never a reserved route word, unique among ``taken`` (``_2`` … keeps the length)."""
    used = set(taken) | RESERVED_IDS
    base = slug_from_text(label)[:_ID_MAX].rstrip("_") or "theme"
    if base not in used:
        return base
    n = 2
    while True:
        suffix = f"_{n}"
        cand = base[: _ID_MAX - len(suffix)].rstrip("_") + suffix
        if cand not in used:
            return cand
        n += 1


def _norm_set(*texts: str | None) -> set[str]:
    return {k for t in texts if t and (k := normalize_label(t)) and k not in sv.GENERIC_LABELS}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _themes_path(root: Path | str) -> Path:
    return sv._dir(root) / THEMES_FILE


def _draft_path(root: Path | str, draft_id: str) -> Path:
    if not _ID_RE.match(draft_id or ""):
        raise ThemeError(f"id must match {_ID_RE.pattern}: {draft_id!r}")
    return sv._dir(root) / DRAFTS_DIR / draft_id / DRAFT_FILE


def _now_default() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def read_themes_text(root: Path | str) -> str | None:
    """The raw ``themes.json`` text (``None`` when absent) — what a rollback restores."""
    try:
        return _themes_path(root).read_text(encoding="utf-8")
    except OSError:
        return None


def _write_themes_text(root: Path | str, text: str | None) -> None:
    path = _themes_path(root)
    if text is None:
        path.unlink(missing_ok=True)
        return
    sv._atomic_write(path, text)


def _wired_path(root: Path | str) -> Path:
    return sv._dir(root) / sv._WIRED_FILE


def _read_wired_text(root: Path | str) -> str | None:
    try:
        return _wired_path(root).read_text(encoding="utf-8")
    except OSError:
        return None


def _write_wired_text(root: Path | str, text: str | None) -> None:
    path = _wired_path(root)
    if text is None:
        path.unlink(missing_ok=True)
        return
    sv._atomic_write(path, text)


def load_themes_file(root: Path | str) -> dict | None:
    """``themes.json`` as ``{"themes": [...], "migrated": bool}``; ``None`` when absent or
    unreadable (so a broken file is never mistaken for "no themes yet")."""
    text = read_themes_text(root)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        return None
    themes = [t for t in data["themes"] if isinstance(t, dict) and isinstance(t.get("id"), str)]
    return {"themes": themes, "migrated": bool(data.get("migrated", True))}


def _save_themes(root: Path | str, data: dict) -> None:
    sv._scaffold(root)
    sv._atomic_write(_themes_path(root), json.dumps(data, indent=2, ensure_ascii=False))


def _migration_view(root: Path | str, at: str | None) -> tuple[dict, bool]:
    """``(themes.json as it is or as the first migration would make it, whether the file
    still has to be written)``. Reads only."""
    data = load_themes_file(root)
    if data is not None:
        return data, False
    if read_themes_text(root) is not None:  # present but broken — never overwrite it
        return {"themes": [], "migrated": False}, False
    tools = sv.read_registry_raw_tools(root, sv.REGISTRY_ID)
    if tools is None:
        return {"themes": [], "migrated": False}, False
    names = [str(t.get("name")) for t in tools if t.get("for_terms")]
    out_themes: list[dict] = []
    if names:
        out_themes.append(
            {
                "id": FIRST_THEME_ID,
                "label": FIRST_THEME_LABEL,
                "created_at": at or _now_default(),
                "questions": names,
                "memo_questions": [],
            }
        )
    return {"themes": out_themes, "migrated": True}, True


def ensure_migrated(root: Path | str, *, at: str | None = None) -> dict:
    """Idempotent first migration. When ``themes.json`` is absent: every existing shared CQ
    (a tool with ``for_terms`` in ``vocab-shared/query_tools.yaml``) goes into ONE theme
    ``first`` (「はじめの問い」); with none, an empty file is written. Either way
    ``migrated: true`` is recorded, so a second call never makes another theme.

    A ``query_tools.yaml`` that cannot be read writes nothing (returns an empty,
    un-migrated view) — a damaged file must not be recorded as "no questions"."""
    data, fresh = _migration_view(root, at)
    if fresh:
        _save_themes(root, data)
    return data


def add_questions_to_theme(
    root: Path | str,
    theme_id: str,
    tool_names: list[str],
    selections: dict[str, dict] | None = None,
) -> dict:
    """Put already-minted CQs (by tool name) into an existing theme
    （かんたん ⑥「テーマに入れる」）.
    Idempotent. ``selections`` (tool name → ``{op, kind, property, value}``) is kept to
    phrase the execution sentence later. Unknown tool names are a 422."""
    data = ensure_migrated(root)
    theme = next((t for t in data["themes"] if t["id"] == theme_id), None)
    if theme is None:
        raise ThemeError(f"テーマ {theme_id!r} はありません", 404)
    known = {
        str(t.get("name"))
        for t in (sv.read_registry_raw_tools(root, sv.REGISTRY_ID) or [])
        if t.get("for_terms")
    }
    missing = [n for n in tool_names if n not in known]
    if missing:
        raise ThemeError(f"共有の問いにありません: {', '.join(missing)}")
    qs = theme.setdefault("questions", [])
    for n in tool_names:
        if n not in qs:
            qs.append(n)
    if selections:
        sel = theme.setdefault("selections", {})
        for name, s in selections.items():
            if name in tool_names and isinstance(s, dict):
                sel[name] = {k: s.get(k) for k in ("op", "kind", "property", "value") if s.get(k)}
    _save_themes(root, data)
    return theme


def remove_theme(root: Path | str, theme_id: str) -> None:
    """Drop the theme record — and ONLY that. Its terms, lines and CQs stay. Also removes an
    unfinished draft of the same id. (:func:`remove_theme_cascade` is the theme-level undo
    that also takes back what only this theme needed; it calls this one.)"""
    data = ensure_migrated(root)
    kept = [t for t in data["themes"] if t["id"] != theme_id]
    if len(kept) == len(data["themes"]):
        raise ThemeError(f"テーマ {theme_id!r} はありません", 404)
    _save_themes(root, {**data, "themes": kept})
    delete_draft(root, theme_id)


# ---- drafts (a wizard in progress; closing the screen keeps it) ---------------


def list_drafts(root: Path | str) -> list[dict]:
    base = sv._dir(root) / DRAFTS_DIR
    out: list[dict] = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        draft = load_draft(root, d.name) if _ID_RE.match(d.name) else None
        if draft:
            out.append(
                {
                    "id": draft["id"],
                    "label": draft["label"],
                    "step": draft["step"],
                    "updated_at": draft["updated_at"],
                }
            )
    return out


def load_draft(root: Path | str, draft_id: str) -> dict | None:
    """The working draft, or ``None`` when absent, unreadable or NOT v2.

    A v1 ``draft.json`` (steps 1–6, ``terms`` / ``selections``, the model's raw ``ai``) is
    dropped here and not migrated: its rows were the six-step wizard's table, which the
    three-step wizard no longer has, and a half-made draft is cheap to write again (the
    questions are one line each). It is left on disk untouched; a new draft with the same
    id replaces it."""
    try:
        path = _draft_path(root, draft_id)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ThemeError):
        return None
    if not isinstance(data, dict) or data.get("id") != draft_id:
        return None
    if data.get("version") != DRAFT_VERSION:
        return None
    return data


def default_label(questions: Iterable[str]) -> str:
    """テーマ名が空のとき（AI 未設定・AI が名前を返さなかったとき）の名前 — 最初の問いの
    先頭 20 字。問いも無ければ空。"""
    for q in clean_questions(questions):
        return q[:20].rstrip()
    return ""


def _label_or_default(label: str, questions: Iterable[str]) -> str:
    folded = " ".join((label or "").split())
    return _clean_label(folded or default_label(questions) or "")


def create_draft(
    root: Path | str, label: str, questions: list[str], *, at: str, extend: str | None = None
) -> dict:
    """A new (v2) draft with a fresh id (from the label; unique among themes and drafts).
    The label may be empty: the first question's first 20 characters stand in for it.
    ``extend`` = the id of a theme the questions will be added to (「問いを足す」)."""
    cleaned = clean_questions(questions)
    label = _label_or_default(label, cleaned)
    taken = {t["id"] for t in ensure_migrated(root)["themes"]}
    taken |= {d["id"] for d in list_drafts(root)}
    draft_id = new_theme_id(label, taken)
    draft = {
        "id": draft_id,
        "label": label,
        "step": 1,
        "questions": cleaned,
        "concepts": [],
        "relations": [],
        "answers": [],
        "suggested_questions": [],
        "standards": {},
        "updated_at": at,
    }
    if extend:
        draft["extend"] = extend
    return save_draft(root, draft)


def _standards_of(v: Any) -> dict[str, dict]:
    if not isinstance(v, dict):
        return {}
    return {
        str(k): {
            "label": str(d.get("label") or ""),
            "iri": str(d.get("iri") or ""),
            "kind": str(d.get("kind") or ""),
        }
        for k, d in v.items()
        if isinstance(d, dict)
    }


_CHAT_MESSAGES_MAX = 200
_CHAT_CONTENT_MAX = 2000
_CHAT_PROPOSALS_MAX = 100
_CHAT_OPS_MAX = 30


def _chat_of(v: Any) -> dict:
    """The conversation kept with a draft (ui-redesign PR 7a・design.md §16): what the PERSON
    said and the diffs they accepted — never the model's prose. ``{messages: [{role: "user",
    content, at}], proposals: [{proposal_id, ops, at, by}]}``; ``{}`` when there is nothing."""
    if not isinstance(v, dict):
        return {}
    messages = [
        {
            "role": "user",
            "content": " ".join(str(m["content"]).split())[:_CHAT_CONTENT_MAX],
            "at": str(m.get("at") or ""),
        }
        for m in v.get("messages") or []
        if isinstance(m, dict)
        and m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ][-_CHAT_MESSAGES_MAX:]
    proposals = [
        {
            "proposal_id": str(p["proposal_id"]),
            "ops": [o for o in p.get("ops") or [] if isinstance(o, dict)][:_CHAT_OPS_MAX],
            "at": str(p.get("at") or ""),
            "by": "human" if p.get("by") == "human" else "ai",
        }
        for p in v.get("proposals") or []
        if isinstance(p, dict) and isinstance(p.get("proposal_id"), str) and p["proposal_id"]
    ][-_CHAT_PROPOSALS_MAX:]
    return {"messages": messages, "proposals": proposals} if messages or proposals else {}


def save_draft(root: Path | str, draft: dict) -> dict:
    """Write a (v2) draft as given (the shape is the wizard's own; only the frame is
    checked). The model's raw output is NEVER stored — a caller that passes ``ai`` has it
    ignored here (design.md §15.3: the draft keeps the post-processed rows, nothing else).

    ``chat`` (the conversation with the workspace's partner — :func:`_chat_of`) is kept as
    given when the caller passes it, and carried over from the saved draft when the caller
    does not (an ordinary save of the rows must not lose the conversation)."""
    path = _draft_path(root, str(draft.get("id")))
    step = draft.get("step")
    if not isinstance(step, int) or not 1 <= step <= 3:
        raise ThemeError("step は 1〜3 です")
    questions = clean_questions(draft.get("questions") or [])
    concepts = _list_of_dicts(draft.get("concepts"))
    relations = _list_of_dicts(draft.get("relations"))
    answers = _list_of_dicts(draft.get("answers"))
    if len(concepts) > MAX_CONCEPTS:
        raise ThemeError(f"概念は {MAX_CONCEPTS} 件までです")
    if len(relations) > _MAX_DRAFT_RELATIONS:
        raise ThemeError(f"関係は {_MAX_DRAFT_RELATIONS} 件までです")
    if len(answers) > _MAX_QUESTIONS:
        raise ThemeError(f"問いの答え方は {_MAX_QUESTIONS} 件までです")
    suggested = [str(q) for q in (draft.get("suggested_questions") or []) if isinstance(q, str)]
    if "chat" in draft:
        chat = _chat_of(draft["chat"])
    else:
        prior = load_draft(root, str(draft.get("id")))
        chat = _chat_of((prior or {}).get("chat"))
    out = {
        "version": DRAFT_VERSION,
        "id": draft["id"],
        "label": _label_or_default(str(draft.get("label") or ""), questions),
        "step": step,
        "questions": questions,
        **({"extend": draft["extend"]} if _ID_RE.match(str(draft.get("extend") or "")) else {}),
        "concepts": concepts,
        "relations": relations,
        "answers": answers,
        "suggested_questions": suggested[:5],
        "standards": _standards_of(draft.get("standards")),
        **({"chat": chat} if chat else {}),
        "updated_at": str(draft.get("updated_at") or _now_default()),
    }
    sv._scaffold(root)
    sv._atomic_write(path, json.dumps(out, indent=2, ensure_ascii=False))
    return out


def delete_draft(root: Path | str, draft_id: str) -> bool:
    try:
        path = _draft_path(root, draft_id)
    except ThemeError:
        return False
    if not path.is_file():
        return False
    path.unlink()
    with contextlib.suppress(OSError):
        path.parent.rmdir()
    return True


def _list_of_dicts(v: Any) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _clean_label(label: str) -> str:
    s = " ".join((label or "").split())
    if not s:
        raise ThemeError("テーマの名前が空です")
    if len(s) > _LABEL_MAX:
        raise ThemeError(f"テーマの名前は {_LABEL_MAX} 文字までです")
    return s


def clean_questions(questions: Iterable[Any]) -> list[str]:
    """問いの文: 空白を整え、空・重複（:func:`normalize_label` が同じ）を除く。"""
    out: list[str] = []
    seen: set[str] = set()
    for q in questions or ():
        s = " ".join(str(q).split())
        key = normalize_label(s)
        if not s or key in seen:
            continue
        if len(s) > _QUESTION_MAX:
            raise ThemeError(f"問いは {_QUESTION_MAX} 文字までです")
        seen.add(key)
        out.append(s)
    if len(out) > _MAX_QUESTIONS:
        raise ThemeError(f"問いは {_MAX_QUESTIONS} 件までです")
    return out


# ---------------------------------------------------------------------------
# Standards (the closed catalog — an IRI is only ever read from it)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StdRef:
    curie: str
    iri: str
    label: str
    #: ``"class"`` | ``"property"`` | ``"quantity"`` (a QUDT quantity kind)
    kind: str


def std_by_curie(curie: str) -> StdRef | None:
    """The catalog term (or QUDT quantity kind) a curie names; ``None`` when it is not in
    the closed set — a model-made curie is therefore never turned into an IRI."""
    from asterism import grounding
    from asterism.grounding import quantity_kinds

    prefix, _, name = (curie or "").partition(":")
    if not prefix or not name:
        return None
    if prefix == "quantitykind":
        cat = quantity_kinds._catalog()
        entry = (cat.get("quantity_kinds") or {}).get(name)
        if entry is None and name not in (cat.get("quantity_kinds") or {}):
            return None
        label = str((entry or {}).get("label") or name)
        return StdRef(curie, str(cat.get("namespace", _QK_BASE)) + name, label, "quantity")
    for t in grounding.catalog_terms():
        if t.curie == curie:
            return StdRef(curie, t.iri, t.label or t.name, t.kind)
    return None


def _too_short_ascii(q: str) -> bool:
    t = q.strip()
    return len(t) <= 2 and t.isascii()


def std_exact(label_ja: str, label_en: str, aliases: Iterable[str], kind: str) -> StdRef | None:
    """The standard term whose name equals this row's English name (or an alias) exactly
    — after :func:`normalize_label`, never fuzzy. For a property row a QUDT quantity kind
    with the same name counts too (the line is then ``hasQuantityKind``). ``None`` when
    nothing matches or the name is too generic."""
    from asterism import grounding
    from asterism.grounding import quantity_kinds

    # 2 文字以下の ASCII の別名（"n" など）は短すぎて何にでも当たる（#636 と同じ趣旨）
    aliases = [a for a in aliases if not _too_short_ascii(a)]
    queries = [q for q in (label_en, *aliases, label_ja) if q and q.strip()]
    for q in queries:
        if normalize_label(q) in sv.GENERIC_LABELS:
            continue
        for c in grounding.ground_terms(q, kind=kind, limit=8):
            if c.score >= 100:
                return StdRef(c.curie, c.iri, c.label or c.name, c.kind)
        if kind == "property":
            for qc in quantity_kinds.resolve_quantity_kind(q, limit=4):
                if qc.score >= 100:
                    return StdRef(qc.curie, qc.iri, qc.label, "quantity")
    return None


def default_standards(questions: list[str], *, limit: int = 120) -> list[dict]:
    """The standard-term NAMES handed to the model: a small curated core (the materials
    ontology and schema.org) plus anything the questions' own English words match exactly.
    Names only — never an IRI, never data."""
    from asterism import grounding

    out: dict[str, dict] = {}
    for t in grounding.catalog_terms():
        if t.prefix in ("cmso", "schema"):
            out[t.curie] = {"curie": t.curie, "label": t.label or t.name, "kind": t.kind}
    for q in questions:
        for word in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", q):
            for c in grounding.ground_terms(word, limit=3):
                if c.score >= 100:
                    out.setdefault(c.curie, {"curie": c.curie, "label": c.label, "kind": c.kind})
    return list(out.values())[:limit]


# ---------------------------------------------------------------------------
# Post-process of an AI draft (deterministic)
# ---------------------------------------------------------------------------

_USE_EXISTING = "existing:"
_USE_STANDARD = "standard:"


def _term_norms(t: SharedTerm) -> set[str]:
    return _norm_set(t.label, t.label_en, *t.aliases)


def _match_existing(
    label_ja: str, label_en: str, aliases: Iterable[str], kind: str, existing: list[SharedTerm]
) -> tuple[SharedTerm, str] | None:
    mine = _norm_set(label_ja, label_en, *aliases)
    if not mine:
        return None
    for e in sorted(existing, key=lambda x: x.slug):
        if e.kind != kind:
            continue
        if mine & _norm_set(e.label, e.label_en):
            return e, "label"
        if mine & _norm_set(*e.aliases):
            return e, "alias"
    return None


def _machine_match(
    label_ja: str, label_en: str, aliases: Iterable[str], kind: str, existing: list[SharedTerm]
) -> tuple[dict | None, str, str, StdRef | None]:
    """What the machine can say about one row by exact name alone (never fuzzy): an
    existing shared term first, then a standard term. → ``(match, use, slug, std)``;
    ``match`` is ``None`` (and ``use`` is ``"new"``) when nothing matches exactly."""
    ex = _match_existing(label_ja, label_en, aliases, kind, existing)
    if ex is not None:
        e, how = ex
        match = {"kind": "existing", "ref": e.slug, "label": e.label, "matched_by": how}
        return match, f"{_USE_EXISTING}{e.slug}", e.slug, None
    std = std_exact(label_ja, label_en, aliases, kind)
    if std is not None:
        match = {
            "kind": "quantity" if std.kind == "quantity" else "standard",
            "ref": std.curie,
            "label": std.label,
            "matched_by": "label",
        }
        return match, f"{_USE_STANDARD}{std.curie}", "", std
    return None, "new", "", None


def derive_slug(label_en: str, aliases: Iterable[str], taken: Iterable[str], n: int) -> str:
    """The slug a new concept gets when nobody typed one: from the English name, else the
    first alias that gives one, else ``t<n>`` (a placeholder the person can fix in the
    last step). Unique among ``taken`` and never a reserved slug. Always returns a slug."""
    base = slug_from_text(label_en) or next(
        (x for a in aliases if (x := slug_from_text(a))), ""
    )
    return unique_slug(base or f"t{n}", {*taken, *sv.RESERVED_SLUGS})


def match_rows(rows: Iterable[dict], *, existing: list[SharedTerm]) -> list[dict]:
    """Rows the person wrote by hand (``{key?, label_ja, label_en, aliases, kind}``) →
    ``[{key, match, use, slug}]``: the same exact-name check an AI draft gets (ja / en /
    aliases through :func:`normalize_label` — a part of a name never matches). ``use`` is the
    machine's advice (``existing:<slug>`` / ``standard:<curie>`` / ``new``); ``slug`` is the
    existing term's, or the one :func:`derive_slug` would give a new concept (``t<n>`` when
    there is no English name). Read-only; no model is involved."""
    out: list[dict] = []
    taken = {e.slug for e in existing}
    for i, r in enumerate(rows):
        kind = "property" if r.get("kind") == "property" else "class"
        aliases = list(sv.clean_aliases(r.get("aliases")))
        match, use, slug, _std = _machine_match(
            str(r.get("label_ja") or ""), str(r.get("label_en") or ""), aliases, kind, existing
        )
        if not slug:
            slug = derive_slug(str(r.get("label_en") or ""), aliases, taken, i + 1)
            taken.add(slug)
        key = r.get("key") if r.get("key") is not None else f"t{i + 1}"
        out.append({"key": key, "match": match, "use": use, "slug": slug})
    return out


def relation_predicate(rel: str, kind: str) -> str:
    """A draft relation (``sub`` / ``eq`` / ``quantity`` / ``part`` / ``lowers`` / ``raises``
    / ``affects``) → the key of :data:`ALIGN_RELATIONS` (the standard predicate), by the kind
    (``class`` | ``property``) of the subject."""
    if rel == "sub":
        return "subClassOf" if kind == "class" else "subPropertyOf"
    if rel == "eq":
        return "equivalentClass" if kind == "class" else "equivalentProperty"
    try:
        return {
            "quantity": "hasQuantityKind",
            "part": "isPartOf",
            "lowers": "lowers",
            "raises": "raises",
            "affects": "affects",
        }[rel]
    except KeyError:
        raise ThemeError(f"関係は {RELATION_KINDS} のどれかです: {rel!r}") from None


def concept_end_key(row: dict) -> str:
    """The key under which a dismissal names one end of a relation: the IRI of an existing /
    standard term the row is tied to, else ``concept:<normalized Japanese name>`` (the
    concept is not placed yet). The same function on the way in (``dismissed.json``) and on
    the way out (the post-process), so a ✕ stays a ✕."""
    use = str(row.get("use") or "")
    if use.startswith(_USE_EXISTING) and row.get("slug"):
        return sv.term_iri(str(row["slug"]))
    m = row.get("match") or {}
    if m.get("kind") in ("standard", "quantity") and m.get("ref"):
        ref = std_by_curie(str(m["ref"]))
        if ref is not None:
            return ref.iri
    return sv.concept_subject(str(row.get("label_ja") or ""))


def _fresh_key(taken: set[str], prefix: str = "c") -> str:
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    taken.add(f"{prefix}{n}")
    return f"{prefix}{n}"


def postprocess_draft(
    ai: dict,
    *,
    questions: list[str],
    existing: list[SharedTerm],
    dismissed: Iterable[dict] = (),
    base: dict | None = None,
) -> dict:
    """The AI's closed JSON v2 (already validated) → the wizard's working rows.

    Machine facts beat the model's claims: an exact match with an existing shared term (ja
    / en / aliases, :func:`normalize_label`) or with a standard term decides ``use``; only
    when there is no such match does a ``use`` the model named stand — and then only if it
    points into the closed set. References between rows are by ``key``.

    ``dismissed`` = :func:`~asterism.shared_vocab.load_dismissed`: a NEW concept the person
    dismissed before (``concept_subject`` of its Japanese name) and a relation they
    dismissed (:func:`concept_end_key` of both ends + the predicate) are dropped, and
    counted in ``dropped``. ``base`` (「ここを広げる」) = the draft being grown: its rows are
    the references the addition may point at, its keys are never reused, and the result
    holds only what is NEW.

    Returns ``{"theme_label", "concepts", "relations", "answers", "suggested_questions",
    "standards", "dropped"}``:

    * concept rows ``{key, label_ja, label_en, aliases, kind, use, slug, reason, match, by}``
      (``match`` = what the machine found: ``{kind: existing|standard|quantity, ref, label,
      matched_by}`` or ``None``; ``slug`` is never empty — ``t<n>`` stands in);
    * relations ``{from: <key>, rel, to: <key | curie>, reason, problem, by}`` where
      ``problem`` is ``same`` / ``mixed`` (kinds do not fit) / ``cycle`` / ``quantity_target``
      / ``target_not_property`` (lowers / raises / affects need a property) or ``None``;
    * one answer per question ``{question, mode, op, target, direction, kind, property,
      value, memo, wanted_data, reason, by}`` — ``mode`` is ``knowledge`` / ``data`` /
      ``memo``; ``memo`` says why a question cannot be answered (``op`` = it needs a way of
      counting that is not there yet, ``ai`` = the model's answer does not fit the closed
      shape, ``none`` = no answer came for it)."""
    dismissed = list(dismissed)
    base_rows = [r for r in (base or {}).get("concepts") or [] if isinstance(r, dict)]
    base_rels = [r for r in (base or {}).get("relations") or [] if isinstance(r, dict)]
    by_slug = {e.slug: e for e in existing}
    rows: list[dict] = []  # the NEW rows (the references also see base_rows)
    taken_keys = {str(r.get("key")) for r in base_rows}
    taken = set(by_slug) | {str(r.get("slug")) for r in base_rows if r.get("slug")}
    standards: dict[str, dict] = {}
    dropped: dict[str, list] = {"concepts": [], "relations": []}
    keymap: dict[str, str | None] = {}  # the model's key → our key (None = dismissed)

    def all_rows() -> list[dict]:
        return [*base_rows, *rows]

    def add_row(*, key: str | None = None, **kw: Any) -> dict:
        if key is None or key in taken_keys:
            key = _fresh_key(taken_keys)
        taken_keys.add(key)
        row = {"key": key, **kw}
        rows.append(row)
        return row

    def find_row(label: str, kind: str | None = None) -> dict | None:
        n = normalize_label(label)
        if not n:
            return None
        for r in all_rows():
            if kind is not None and r.get("kind") != kind:  # 同名でも種類と項目は別の行
                continue
            if n in _norm_set(r["label_ja"], r.get("label_en")) or n == normalize_label(
                r["label_ja"]
            ):
                return r
        return None

    def remember_std(ref: StdRef) -> None:
        standards[ref.curie] = {"label": ref.label, "iri": ref.iri, "kind": ref.kind}

    for t in ai.get("concepts", []):
        label_ja = t["label_ja"]
        dup = find_row(label_ja, t["kind"])
        if dup is not None:
            keymap[t["key"]] = dup["key"]
            continue
        aliases = list(sv.clean_aliases(t.get("aliases")))
        label_en = t.get("label_en") or ""
        kind = t["kind"]
        match, use, slug, std = _machine_match(label_ja, label_en, aliases, kind, existing)
        if match is None and sv.is_dismissed(
            dismissed, sv.concept_subject(label_ja), sv.CONCEPT_DISMISS_TERM
        ):
            keymap[t["key"]] = None
            dropped["concepts"].append(label_ja)
            continue
        if std is not None:
            remember_std(std)
        if match is None:
            claimed = t.get("use") or "new"
            if claimed.startswith(_USE_EXISTING):
                e2 = by_slug.get(claimed[len(_USE_EXISTING) :])
                if e2 is not None and e2.kind == kind:
                    use, slug = claimed, e2.slug
            elif claimed.startswith(_USE_STANDARD):
                ref = std_by_curie(claimed[len(_USE_STANDARD) :])
                if ref is not None and (
                    ref.kind == kind or (ref.kind == "quantity" and kind == "property")
                ):
                    use = claimed
                    remember_std(ref)
        if not slug:
            slug = derive_slug(label_en, aliases, taken, len(base_rows) + len(rows) + 1)
            taken.add(slug)
        row = add_row(
            key=t["key"],  # the model's own key when it is free (a stable reading)
            label_ja=label_ja,
            label_en=label_en,
            aliases=aliases,
            kind=kind,
            use=use,
            slug=slug,
            reason=t.get("reason") or "",
            match=match,
            by="ai",
        )
        keymap[t["key"]] = row["key"]

    def row_by_key(key: str) -> dict | None:
        return next((r for r in all_rows() if r["key"] == key), None)

    def resolve(ref: str, kind_hint: str | None = None) -> tuple[dict | None, bool]:
        """A reference the model made → ``(row, dismissed)``. By key first, then by name
        (a lenient fallback: the model named a concept instead of giving its key), adding a
        row for an existing shared term that the model referred to without listing it."""
        if ref in keymap:
            k = keymap[ref]
            return (row_by_key(k) if k is not None else None), k is None
        row = row_by_key(ref) or find_row(ref, kind_hint)
        if row is not None:
            return row, False
        n = _norm_set(ref)
        for e in sorted(existing, key=lambda x: x.slug):
            if n and n & _term_norms(e) and (kind_hint is None or e.kind == kind_hint):
                return (
                    add_row(
                        label_ja=e.label,
                        label_en=e.label_en or "",
                        aliases=list(e.aliases),
                        kind=e.kind,
                        use=f"{_USE_EXISTING}{e.slug}",
                        slug=e.slug,
                        reason="",
                        match={
                            "kind": "existing",
                            "ref": e.slug,
                            "label": e.label,
                            "matched_by": "label",
                        },
                        by="ai",
                    ),
                    False,
                )
        return None, False

    # relations — the draft's own sub / part edges (and the base's) feed the cycle check
    rels: list[dict] = []
    seen_rel: set[tuple[str, str, str]] = {
        (str(r.get("from")), str(r.get("to")), str(r.get("rel"))) for r in base_rels
    }
    edges: dict[str, set[tuple[str, str]]] = {"sub": set(), "part": set()}
    for r in base_rels:
        if r.get("rel") in edges and not r.get("problem"):
            edges[r["rel"]].add((str(r["from"]), str(r["to"])))
    for r in ai.get("relations", []):
        rel = r["rel"]
        subj, gone_s = resolve(r["from"])
        obj_ref = r["to"]
        std = std_by_curie(obj_ref) if ":" in obj_ref and obj_ref not in keymap else None
        obj, gone_o = (None, False) if std is not None else resolve(obj_ref)
        if gone_s or gone_o:
            dropped["relations"].append({"from": r["from"], "rel": rel, "to": obj_ref})
            continue
        if subj is None or (obj is None and std is None):
            continue
        if std is not None:
            remember_std(std)
            obj_key = std.curie
        else:
            assert obj is not None
            obj_key = obj["key"]
        trip = (subj["key"], obj_key, rel)
        if trip in seen_rel:
            continue
        seen_rel.add(trip)
        obj_kind = std.kind if std is not None else obj["kind"]  # type: ignore[index]
        problem: str | None = None
        if obj_key == subj["key"]:
            problem = "same"
        elif rel == "quantity":
            if subj["kind"] != "property" or std is None or std.kind != "quantity":
                problem = "quantity_target"
        elif rel in ("lowers", "raises", "affects"):
            if obj_kind != "property":
                problem = "target_not_property"
        elif obj_kind != subj["kind"]:  # sub / eq / part join the same kind of thing
            problem = "mixed"
        end = concept_end_key(obj) if std is None else std.iri  # type: ignore[arg-type]
        if sv.is_dismissed(
            dismissed,
            concept_end_key(subj),
            end,
            relation=relation_predicate(rel, subj["kind"]),
        ):
            dropped["relations"].append({"from": r["from"], "rel": rel, "to": obj_ref})
            continue
        if problem is None and rel in edges and std is None:
            if _reaches(edges[rel], obj_key, subj["key"]):
                problem = "cycle"
            else:
                edges[rel].add((subj["key"], obj_key))
        rels.append(
            {
                "from": subj["key"],
                "rel": rel,
                "to": obj_key,
                "reason": r.get("reason") or "",
                "problem": problem,
                "by": "ai",
            }
        )

    # answers — exactly one per question, in the person's order
    by_q = {normalize_label(a["question"]): a for a in ai.get("answers", [])}
    out_answers: list[dict] = []
    for q in questions:
        a = by_q.get(normalize_label(q))
        if a is None:
            out_answers.append(_answer_row(q, "memo", memo="none"))
            continue
        mode, op = a["mode"], a.get("op")
        t_row = resolve(a["target"])[0] if a.get("target") else None
        k_row = resolve(a["kind"], "class")[0] if a.get("kind") else None
        p_row = resolve(a["property"], "property")[0] if a.get("property") else None
        if k_row is not None and k_row["kind"] != "class":
            k_row = None
        if p_row is not None and p_row["kind"] != "property":
            p_row = None
        direction = a.get("direction")
        memo: str | None
        if mode == "knowledge":
            good = op in KNOWLEDGE_OPS and t_row is not None
            if op == "factors":
                good = good and direction in KNOWLEDGE_DIRECTIONS
            memo = None if good else "ai"
        elif mode == "data":
            good = (
                (op == "count" and k_row is not None)
                or (op == "range" and p_row is not None)
                or (op == "top" and p_row is not None)
            )
            memo = None if good else "ai"
        else:
            memo = "op" if op is not None and op not in (*FITTING_OPS, *KNOWLEDGE_OPS) else "ai"
        out_answers.append(
            _answer_row(
                q,
                "memo" if memo else mode,
                op=op,
                target=t_row["key"] if t_row and mode == "knowledge" else "",
                direction=direction if mode == "knowledge" and op == "factors" else "",
                kind=k_row["key"] if k_row and mode == "data" else "",
                prop=p_row["key"] if p_row and mode == "data" else "",
                memo=memo,
                wanted_data=a.get("wanted_data") or "",
                reason=a.get("reason") or "",
            )
        )

    asked = {normalize_label(q) for q in questions}
    suggested = [q for q in ai.get("suggested_questions", []) if normalize_label(q) not in asked]
    return {
        "theme_label": ai.get("theme_label") or "",
        "concepts": rows,
        "relations": rels,
        "answers": out_answers,
        "suggested_questions": suggested[:5],
        "standards": standards,
        "dropped": dropped,
    }


def _answer_row(
    question: str,
    mode: str,
    *,
    op: str | None = None,
    target: str = "",
    direction: str | None = "",
    kind: str = "",
    prop: str = "",
    memo: str | None = None,
    wanted_data: str = "",
    reason: str = "",
) -> dict:
    return {
        "question": question,
        "mode": mode,
        "op": op or "",
        "target": target,
        "direction": direction or "",
        "kind": kind,
        "property": prop,
        "value": "",
        "memo": memo,
        "wanted_data": wanted_data,
        "reason": reason,
        "by": "ai",
    }


def merge_expansion(draft: dict, added: dict) -> dict:
    """「ここを広げる」: the post-processed addition (``postprocess_draft(..., base=draft)``)
    joined to the draft — concepts and relations appended, standards merged. Refuses a
    draft that would pass :data:`MAX_CONCEPTS`. Returns a new dict (nothing is saved)."""
    concepts = [*(draft.get("concepts") or []), *added.get("concepts", [])]
    if len(concepts) > MAX_CONCEPTS:
        raise ThemeError(f"概念は {MAX_CONCEPTS} 件までです（これ以上は広げられません）")
    return {
        **draft,
        "concepts": concepts,
        "relations": [*(draft.get("relations") or []), *added.get("relations", [])],
        "standards": {**(draft.get("standards") or {}), **added.get("standards", {})},
    }


def _reaches(edges: set[tuple[str, str]], start: str, goal: str) -> bool:
    """Is ``goal`` reachable from ``start`` going up over ``(child, parent)`` edges?"""
    up: dict[str, set[str]] = {}
    for c, p in edges:
        up.setdefault(c, set()).add(p)
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == goal:
            return True
        for nxt in up.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

_MEMO_REASONS = {
    "ja": {
        "op": "いまの数え方（件数・範囲・上位の値）では答えられないため、メモとして残します。",
        "ai": "決めた型に合わない問いのため、メモとして残します。",
        "none": "数え方が決まっていないため、メモとして残します。",
        "human": "あなたの判断で、メモとして残します。",
        "duplicate": "同じ答え方の問いが既にあります。メモとして残します。",
    },
    "en": {
        "op": "The current ways of counting (count, range, top value) cannot answer it, "
        "so it stays as a memo.",
        "ai": "It does not fit a fixed shape, so it stays as a memo.",
        "none": "No way of counting was chosen, so it stays as a memo.",
        "human": "You chose to keep it as a memo.",
        "duplicate": "A question with the same way of answering already exists, "
        "so it stays as a memo.",
    },
}


def default_cq_title(kind: str, label: str, lang: str = "ja") -> str:
    """The default question a term carries when no question of the theme is about it (the
    same sentences as ``vocab:question.defaultCq``)."""
    if lang.startswith("en"):
        return (
            f"How many {label} does each dataset have"
            if kind == "class"
            else f"How many values of {label} does each dataset have"
        )
    return (
        f"{label}は、どのデータセットに何件あるか"
        if kind == "class"
        else f"{label}の値は、どのデータセットにいくつあるか"
    )


@dataclass
class _Row:
    key: str
    label_ja: str
    label_en: str
    aliases: tuple[str, ...]
    kind: str
    use: str
    slug: str
    ai: bool
    comment: str | None
    mint: bool  # a new sv: term is made
    std: StdRef | None  # the standard the new term is tied to
    iri: str


@dataclass
class _Line:
    source: str
    target: str
    relation: str  # a key of ALIGN_RELATIONS
    reason: str | None = None
    ai: bool = False


@dataclass
class _Plan:
    theme: dict
    rows: list[_Row]
    lines: list[_Line]
    #: term key → CQSpecs (title, op) to attach, in order (only data questions have any)
    cqs: dict[str, list[CQSpec]]
    #: term key → the planned CQ declarations (same order; only an existing term's are written
    #: as they stand — a new term's are rebuilt by ``mint_term`` to the same names)
    cq_tools: dict[str, list[dict]]
    draft_id: str | None
    #: ``themes.json`` as it was (or as the first migration makes it) BEFORE any write —
    #: so the CQs this commit mints are never swept into the migrated "first" theme.
    base: dict
    batch: str
    #: lines other themes already list (a line that exists and is theirs is shared)
    shared_lines: set[tuple[str, str, str]]


def _display_label(row_label_ja: str, row_label_en: str, lang: str) -> str:
    return (row_label_en or row_label_ja) if lang.startswith("en") else row_label_ja


def _line_reason(text: Any) -> str | None:
    """A relation's one-line reason → what ``assert_alignment`` takes: line breaks folded to
    spaces and CUT at :data:`REASON_MAX` (that function refuses a longer one — the AI's
    reason is only ever shortened here, never rejected)."""
    one = " ".join(str(text or "").split())[:REASON_MAX].rstrip()
    return one or None


def knowledge_tool_name(target_slug: str, op: str, direction: str | None = None) -> str:
    """The name a knowledge question is kept under in the theme: ``kq_<target>_<op>`` plus
    ``_<direction>`` for ``factors``. Not a declared tool — the name only keys the theme's
    ``selections`` / ``wanted``."""
    base = f"{KNOWLEDGE_TOOL_PREFIX}{target_slug}_{op}"
    return f"{base}_{direction}" if op == "factors" and direction else base


def _check_line_shapes(lines: list[_Line], shapes: dict[str, set[str]]) -> None:
    for ln in lines:
        try:  # 述語ごとの種別表（crosswalk_runtime.RELATION_SHAPES）
            _check_same_shape(ln.relation, ln.source, ln.target, shapes)
        except ValueError as e:
            if ln.relation in ("lowers", "raises", "affects"):
                raise ThemeError("下げる・上げる・変えるの相手は、項目だけです") from e
            raise ThemeError("種類と項目は結べません") from e


def _connected(start: set[str], edges: Iterable[tuple[str, str]]) -> set[str]:
    """Everything reachable from ``start`` over ``edges`` taken in either direction."""
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen = set(start)
    stack = list(start)
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


async def _plan_commit(client: Any, root: Path | str, body: dict, *, at: str) -> _Plan:
    """Everything that can be refused, checked with NO write. Raises :class:`ThemeError`.

    ``body`` (v2): ``{id?, extend?, label?, lang, ai_drafted, batch?, questions[],
    concepts[{key, label_ja, label_en, aliases, kind, use, slug?, by?, comment?}],
    relations[{from, rel, to, reason?, by?}], answers[{question, mode, op, target,
    direction, kind, property, memo?, wanted_data?}]}``."""
    lang = "en" if str(body.get("lang") or "ja").startswith("en") else "ja"
    questions = clean_questions(body.get("questions") or [])
    label = _label_or_default(str(body.get("label") or ""), questions)
    data, _fresh = _migration_view(root, at)  # reads only: a refused commit writes nothing
    if not data.get("migrated"):
        raise ThemeError("共有の問いの宣言（query_tools.yaml）が読めないため置けません", 409)
    theme_ids = {t["id"] for t in data["themes"]}
    draft_id = body.get("id") if isinstance(body.get("id"), str) and body.get("id") else None
    extend_id = body.get("extend") if isinstance(body.get("extend"), str) else None
    if draft_id is not None and not _ID_RE.match(draft_id):
        raise ThemeError(f"id must match {_ID_RE.pattern}: {draft_id!r}")
    if draft_id in RESERVED_IDS:
        raise ThemeError(f"id {draft_id!r} は予約語です（別の id にしてください）")
    if extend_id:  # 「問いを足す」: the questions join an existing theme instead of a new one
        theme_id = extend_id
        if extend_id not in theme_ids:
            raise ThemeError(f"テーマ {extend_id!r} はありません", 404)
    elif draft_id is not None:
        if draft_id in theme_ids:
            raise ThemeError(f"テーマ {draft_id!r} はすでにあります", 409)
        theme_id = draft_id
    else:
        theme_id = new_theme_id(label, theme_ids)
    batch = " ".join(str(body.get("batch") or "").split()) or f"{theme_id}@{at}"[:100]
    if len(batch) > 100:
        raise ThemeError("batch は 100 文字までです")
    try:
        sv._read_cq_tools(root)
    except sv.TermError as exc:
        raise ThemeError(str(exc), 409) from exc

    existing = await sv._read_terms(client)
    by_slug = {t.slug: t for t in existing}
    ai_drafted = bool(body.get("ai_drafted"))
    prior = next((t for t in data["themes"] if t["id"] == extend_id), None) if extend_id else None

    # --- concept rows ----------------------------------------------------------
    rows: list[_Row] = []
    by_key: dict[str, _Row] = {}
    planned_slugs: set[str] = set()
    derived: list[tuple[_Row, str, tuple[str, ...]]] = []  # rows that still need a slug
    for i, raw in enumerate(body.get("concepts") or []):
        if not isinstance(raw, dict):
            raise ThemeError(f"concepts[{i}] が不正です")
        key = str(raw.get("key") or f"t{i + 1}")
        if key in by_key:
            raise ThemeError(f"概念の key が重複しています: {key}")
        kind = raw.get("kind")
        if kind not in ("class", "property"):
            raise ThemeError(f"concepts[{i}].kind は class か property です")
        label_ja = " ".join(str(raw.get("label_ja") or "").split())
        if not label_ja or len(label_ja) > _LABEL_MAX:
            raise ThemeError(f"concepts[{i}] の日本語名が空か長すぎます")
        label_en = " ".join(str(raw.get("label_en") or "").split())[:_LABEL_MAX]
        aliases = sv.clean_aliases(raw.get("aliases"))
        use = str(raw.get("use") or "new")
        std: StdRef | None = None
        if use == "new":
            mint = True
        elif use.startswith(_USE_STANDARD):
            mint = True
            std = std_by_curie(use[len(_USE_STANDARD) :])
            if std is None:
                raise ThemeError(f"「{label_ja}」の標準の語が見つかりません: {use}")
            if std.kind == "quantity":
                if kind != "property":
                    raise ThemeError(f"「{label_ja}」は種類なので、量の種類とは結べません")
            elif std.kind != kind:
                raise ThemeError(f"「{label_ja}」と標準の語 {std.curie} は種類と項目が合いません")
        elif use.startswith(_USE_EXISTING):
            mint = False
        else:
            raise ThemeError(f"concepts[{i}].use が不正です: {use!r}")
        slug = str(raw.get("slug") or "")
        if mint and slug:
            if not re.match(sv.SLUG_RE, slug):
                raise ThemeError(
                    f"「{label_ja}」の ID は {sv.SLUG_RE} の形にしてください: {slug!r}"
                )
            if slug in sv.RESERVED_SLUGS:
                raise ThemeError(f"ID「{slug}」は予約語です。別の ID にしてください")
            if slug in by_slug or slug in planned_slugs:
                raise ThemeError(f"ID「{slug}」はすでに使われています。別の ID にしてください", 409)
            planned_slugs.add(slug)
        elif not mint:
            slug = use[len(_USE_EXISTING) :]
            e = by_slug.get(slug)
            if e is None:
                raise ThemeError(f"既にある用語「{slug}」が見つかりません")
            if e.kind != kind:  # an existing term's kind is never changed
                raise ThemeError(
                    f"「{label_ja}」と既にある用語「{e.label}」は種類と項目が合いません"
                )
        row = _Row(
            key=key,
            label_ja=label_ja,
            label_en=label_en,
            aliases=aliases,
            kind=kind,
            use=use,
            slug=slug,
            ai=ai_drafted and raw.get("by") == "ai",
            comment=(str(raw.get("comment") or "").strip() or None),
            mint=mint,
            std=std,
            iri=sv.term_iri(slug) if slug else "",
        )
        rows.append(row)
        by_key[key] = row
        if mint and not slug:
            derived.append((row, label_en, aliases))
    # a typed-in row with no ID gets the one the machine would suggest (``t<n>`` when there is
    # no English name) — the last step shows it and lets the person fix it
    for n, (row, label_en, aliases) in enumerate(derived, start=1):
        row.slug = derive_slug(label_en, aliases, {*by_slug, *planned_slugs}, n)
        row.iri = sv.term_iri(row.slug)
        planned_slugs.add(row.slug)
    have = set(prior.get("uses") or []) if prior is not None else set()
    if len(have | {r.slug for r in rows}) > MAX_CONCEPTS:
        raise ThemeError(f"概念は 1 つのテーマに {MAX_CONCEPTS} 件までです")

    # --- lines -----------------------------------------------------------------
    lines: list[_Line] = []
    seen_lines: set[tuple[str, str, str]] = set()

    def add_line(
        source: str, target: str, relation: str, *, reason: str | None = None, ai: bool = False
    ) -> None:
        t = (source, relation, target)
        if t not in seen_lines:
            seen_lines.add(t)
            lines.append(_Line(source, target, relation, reason, ai))

    for row in rows:  # the machine's own "same as the standard" lines
        if row.std is not None:
            pred = (
                "hasQuantityKind"
                if row.std.kind == "quantity"
                else relation_predicate("eq", row.kind)
            )
            add_line(row.iri, row.std.iri, pred, ai=row.ai)
    std_ends: dict[str, StdRef] = {}
    for i, raw in enumerate(body.get("relations") or []):
        if not isinstance(raw, dict):
            raise ThemeError(f"relations[{i}] が不正です")
        rel = raw.get("rel")
        if rel not in RELATION_KINDS:
            raise ThemeError(f"relations[{i}].rel は {RELATION_KINDS} のどれかです")
        subj = by_key.get(str(raw.get("from")))
        if subj is None:
            raise ThemeError(f"relations[{i}] の from が概念にありません")
        to_ref = str(raw.get("to"))
        obj_row = by_key.get(to_ref)
        obj_std = None if obj_row is not None else std_by_curie(to_ref)
        if obj_row is None and obj_std is None:
            raise ThemeError(f"relations[{i}] の to が見つかりません: {to_ref!r}")
        target_iri = obj_row.iri if obj_row is not None else obj_std.iri  # type: ignore[union-attr]
        if obj_std is not None:
            std_ends[obj_std.iri] = obj_std
        if target_iri == subj.iri:
            raise ThemeError(f"「{subj.label_ja}」を自分自身には結べません")
        if rel == "quantity":
            if subj.kind != "property" or obj_std is None or obj_std.kind != "quantity":
                raise ThemeError("「量」の関係は、項目から量の種類（QUDT）へだけ結べます")
        elif obj_std is not None and obj_std.kind == "quantity":
            # 量の種類（QUDT）へ結べるのは「量」だけ（postprocess の target_not_property /
            # mixed と同じ判定を、確定でも検証し直す）
            raise ThemeError(
                f"「{subj.label_ja}」から標準の量の種類へは「量」の関係でしか結べません"
                f"（{rel} は使えません）"
            )
        elif obj_row is not None and rel in ("sub", "eq", "part") and obj_row.kind != subj.kind:
            raise ThemeError(
                f"「{subj.label_ja}」と相手は、種類と項目が合いません（種類と項目は結べません）"
            )
        add_line(
            subj.iri,
            target_iri,
            relation_predicate(rel, subj.kind),
            reason=_line_reason(raw.get("reason")),
            ai=ai_drafted and raw.get("by") == "ai",
        )

    # shapes: the store's declarations for the ends that already exist; the rows this commit
    # makes are declared here (they are in the store only after the write), and a standard's
    # kind comes from the catalog
    minted_iris = {r.iri for r in rows if r.mint}
    touched = {x for ln in lines for x in (ln.source, ln.target)} - minted_iris
    shapes = await _declared_shapes(client, sorted(touched)) if touched else {}
    for r in rows:
        if r.mint:
            shapes.setdefault(r.iri, set()).add(r.kind)
    for iri, ref in std_ends.items():
        if ref.kind in ("class", "property"):
            shapes.setdefault(iri, set()).add(ref.kind)
    _check_line_shapes(lines, shapes)
    # cycles: the stored lines plus the ones planned, asserted in order
    edges = set(await sv._alignment_lines(client))
    for ln in lines:
        rels = sv.cycle_rels(ln.relation)  # sub / isPartOf だけ（lowers 系は循環を許す）
        if rels is not None and ln.target in sv._down_set(ln.source, edges, rels):
            raise ThemeError("その関係を置くと、一周してしまいます（循環）")
        edges.add((ln.source, ln.relation, ln.target))

    # --- answers → CQs / knowledge questions / memos -----------------------------
    ans_by_q = {
        normalize_label(str(a.get("question") or "")): a
        for a in body.get("answers") or []
        if isinstance(a, dict)
    }
    cqs: dict[str, list[CQSpec]] = {}
    #: (question, ("data", host_key, summary, wanted) | ("knowledge", name, summary))
    ordered: list[tuple[str, tuple]] = []
    memo: list[dict] = []
    wanted: dict[str, str] = {}
    target_iris: set[str] = set()  # the terms the questions are about
    knowledge_seen: dict[str, dict] = {}
    for q in questions:
        a = ans_by_q.get(normalize_label(q))
        why = "none"
        want = " ".join(str((a or {}).get("wanted_data") or "").split())[:_QUESTION_MAX]
        if a is not None:
            mode, op = a.get("mode"), a.get("op")
            k = by_key.get(str(a.get("kind"))) if a.get("kind") else None
            p = by_key.get(str(a.get("property"))) if a.get("property") else None
            t = by_key.get(str(a.get("target"))) if a.get("target") else None
            if (a.get("kind") and k is None) or (a.get("property") and p is None):
                raise ThemeError(f"問い「{q}」の種類か項目が概念にありません")
            if a.get("target") and t is None:
                raise ThemeError(f"問い「{q}」の対象が概念にありません")
            if k is not None and k.kind != "class":
                raise ThemeError(f"問い「{q}」の種類に項目が選ばれています")
            if p is not None and p.kind != "property":
                raise ThemeError(f"問い「{q}」の項目に種類が選ばれています")
            flagged = a.get("memo")
            if mode == "memo" or flagged in _MEMO_REASONS["ja"]:
                why = flagged if flagged in _MEMO_REASONS["ja"] else (
                    "op" if op not in (None, "", *FITTING_OPS, *KNOWLEDGE_OPS) else "ai"
                )
            elif mode == "knowledge":
                direction = a.get("direction") or None
                if op not in KNOWLEDGE_OPS:
                    raise ThemeError(f"問い「{q}」の答え方は {KNOWLEDGE_OPS} のどれかです")
                if t is None:
                    raise ThemeError(f"問い「{q}」の対象の概念がありません")
                if op == "factors" and direction not in KNOWLEDGE_DIRECTIONS:
                    raise ThemeError(f"問い「{q}」の向きは {KNOWLEDGE_DIRECTIONS} のどれかです")
                name = knowledge_tool_name(t.slug, op, direction)
                summary = {
                    "mode": "knowledge",
                    "op": op,
                    "target_slug": t.slug,
                    "target": _display_label(t.label_ja, t.label_en, lang),
                    "title": q,
                    **({"direction": direction} if op == "factors" else {}),
                }
                if name in knowledge_seen:
                    # 同じ（対象, 向き, 操作）の 2 つ目の問い: 黙って消さず、メモに落とす
                    why = "duplicate"
                else:
                    knowledge_seen[name] = summary
                    ordered.append((q, ("knowledge", name, summary)))
                    if want:
                        wanted.setdefault(name, want)
                    target_iris.add(t.iri)
                    continue
            elif mode == "data" and op in FITTING_OPS:
                host: _Row | None = None
                cq_op = "count"
                if op == "count" and k is not None:
                    host = k
                elif op in ("range", "top") and p is not None:
                    host, cq_op = p, "values"
                if host is not None:
                    cqs.setdefault(host.key, []).append(CQSpec(q, cq_op))
                    summary = {
                        "op": op,
                        "kind": _display_label(k.label_ja, k.label_en, lang) if k else "",
                        "property": _display_label(p.label_ja, p.label_en, lang) if p else "",
                        "value": str(a.get("value") or ""),
                        "kind_slug": k.slug if k else "",
                        "property_slug": p.slug if p else "",
                    }
                    ordered.append((q, ("data", host.key, summary, want)))
                    target_iris.update(x.iri for x in (k, p) if x is not None)
                    continue
                why = "ai"
            elif mode == "data":
                why = "op" if op in sv.SELECTION_OPS else "ai"
        item = {
            "id": "m_" + hashlib.sha1(q.encode("utf-8")).hexdigest()[:8],
            "title": q,
            "reason": _MEMO_REASONS[lang][why],
        }
        if want:
            item["wanted_data"] = want
        memo.append(item)

    # --- a concept that no question's target can be reached from is refused -------
    # (design.md §15.2: "テーマの問いに関係でつながらない用語は作らない"). Relations are read
    # as a graph, direction ignored: the planned lines plus the stored ones.
    if any(r.mint for r in rows):
        anchors = set(target_iris)
        if prior is not None:
            for sel in (prior.get("selections") or {}).values():
                if isinstance(sel, dict) and sel.get("target_slug"):
                    anchors.add(sv.term_iri(str(sel["target_slug"])))
            raw_tools = sv.read_registry_raw_tools(root, sv.REGISTRY_ID) or []
            prior_tools = {str(t.get("name")): t for t in raw_tools}
            for n in prior.get("questions") or []:
                anchors.update(str(i) for i in (prior_tools.get(n) or {}).get("for_terms") or [])
        if not anchors:  # 答え方の決まった問いが 1 つも無い（メモの問いだけ）
            raise ThemeError(
                "答え方が決まった問いが無いため、用語を置けません。先に問いの答え方を選んでください"
            )
        reach = _connected(anchors, [(a, c) for a, _r, c in edges])
        lonely = [r.label_ja for r in rows if r.mint and r.iri not in reach]
        if lonely:
            raise ThemeError(
                "問いに関係でつながらない概念があります（問いの対象か、それにつながる関係を"
                f"足してください）: {'、'.join(lonely[:5])}",
            )

    # --- CQ tool names, lint -------------------------------------------------------
    taken_names = {str(t.get("name")) for t in sv._read_cq_tools(root)}
    cq_tools: dict[str, list[dict]] = {}
    for row in rows:
        specs = list(cqs.get(row.key, []))
        if not specs:  # a factor is placed by its relations alone — no default question
            continue
        shared_term = SharedTerm(
            iri=row.iri,
            slug=row.slug,
            kind=row.kind,
            label=row.label_ja,
            label_en=row.label_en or None,
            comment=None,
            created_at=at,
        )
        if row.mint:
            tools = sv.default_cq_tools(shared_term, specs)
        else:
            tools, used = [], set(taken_names)
            for spec in specs:
                cand = None
                for n in range(1, 100):
                    cand = sv.default_cq_tools(shared_term, [spec] * n)[-1]
                    if cand["name"] not in used:
                        break
                assert cand is not None
                used.add(cand["name"])
                tools.append(cand)
        try:
            sv._lint_or_raise(tools)
        except sv.TermError as exc:
            raise ThemeError(str(exc)) from exc
        cq_tools[row.key] = tools
    cq_names = {k: [t["name"] for t in v] for k, v in cq_tools.items()}
    # the theme's own questions, in the person's order, with the name each one became
    q_tools: list[str] = []
    q_sel: dict[str, dict] = {}
    cursor: dict[str, int] = {}
    for q, item in ordered:
        if item[0] == "knowledge":
            _, name, summary = item
            q_tools.append(name)
            q_sel[name] = summary
            continue
        _, host_key, summary, want = item
        specs = cqs[host_key]
        idx = next(
            i for i, sp in enumerate(specs) if sp.title == q and i >= cursor.get(host_key, 0)
        )
        cursor[host_key] = idx + 1
        name = cq_names[host_key][idx]
        q_tools.append(name)
        q_sel[name] = summary
        if want:
            wanted[name] = want

    theme = {
        "id": theme_id,
        "label": label,
        "created_at": at,
        "questions": q_tools,
        "support_questions": [],
        "selections": q_sel,
        "memo_questions": memo,
        "wanted": wanted,
        "minted": [r.slug for r in rows if r.mint],
        "uses": [r.slug for r in rows],
        "relations": [],  # filled by commit_theme with the lines it really added
    }
    if prior is not None:
        have_q = list(prior.get("questions") or [])
        have_m = list(prior.get("memo_questions") or [])
        have_m_ids = {m["id"] for m in have_m}
        theme = {
            **prior,
            "questions": [*have_q, *[n for n in q_tools if n not in have_q]],
            "selections": {**(prior.get("selections") or {}), **q_sel},
            "memo_questions": [*have_m, *[m for m in memo if m["id"] not in have_m_ids]],
            "wanted": {**(prior.get("wanted") or {}), **wanted},
            "minted": [*(prior.get("minted") or []), *theme["minted"]],
            "uses": sorted({*(prior.get("uses") or []), *theme["uses"]}),
            "relations": list(prior.get("relations") or []),
        }
    shared_lines = {
        (str(ln.get("from")), str(ln.get("relation")), str(ln.get("to")))
        for t in data["themes"]
        if t["id"] != theme["id"]
        for ln in t.get("relations") or []
        if isinstance(ln, dict)
    }
    return _Plan(
        theme=theme,
        rows=rows,
        lines=lines,
        cqs=cqs,
        cq_tools=cq_tools,
        draft_id=draft_id,
        base=data,
        batch=batch,
        shared_lines=shared_lines,
    )


async def _line_exists(client: Any, ln: _Line) -> bool:
    return await sv._ask(
        client,
        f"ASK {{ GRAPH <{ALIGNMENT_GRAPH}> {{ <{ln.source}> <{ALIGN_RELATIONS[ln.relation]}> "
        f"<{ln.target}> }} }}",
    )


async def commit_theme(client: Any, root: Path | str, body: dict, *, at: str) -> dict:
    """Turn a confirmed draft into stored terms, lines, CQs and a theme record — as ONE
    operation. Returns ``{"theme", "minted": [slug], "lines": n, "questions": [name],
    "memo": n, "batch": id}``.

    Everything that can be refused is checked first (:func:`_plan_commit`, no write). Then
    the writes run in a fixed order — new terms (with their CQs, if any) → CQs of existing
    terms → lines (each with who / when / which batch / why) → ``themes.json`` →
    ``wired.json`` and ``knowledge.json`` — and if any of them fails, what was written is
    taken back in the reverse order (lines → CQs → terms → ``themes.json`` → the two
    counts): a mint is never left without its lines. A knowledge question writes no tool;
    it is kept in the theme record only. Raises :class:`ThemeError` (nothing written) or
    re-raises the write failure (after the rollback)."""
    plan = await _plan_commit(client, root, body, at=at)
    prior_themes_text = read_themes_text(root)
    prior_counts = (_read_wired_text(root), _read_knowledge_text(root))
    minted: list[str] = []
    added_lines: list[_Line] = []
    added_tools: list[str] = []
    try:
        for row in plan.rows:
            if not row.mint:
                continue
            minted.append(row.slug)  # 鋳造の前に積む（途中で落ちても巻き戻しが拾う）
            await sv.mint_term(
                client,
                root,
                slug=row.slug,
                kind=row.kind,
                label=row.label_ja,
                label_en=row.label_en or None,
                comment=row.comment,
                cqs=plan.cqs.get(row.key, []),
                aliases=row.aliases,
                drafted_by="ai" if row.ai else None,
                theme_id=plan.theme["id"],
                at=at,
            )
        for row in plan.rows:
            if row.mint or row.key not in plan.cq_tools:
                continue
            sv._write_cq_tools(root, plan.cq_tools[row.key])
            added_tools.extend(t["name"] for t in plan.cq_tools[row.key])
        owned = list(plan.theme.get("relations") or [])
        for ln in plan.lines:
            trip = (ln.source, ln.relation, ln.target)
            if await _line_exists(client, ln):
                if trip in plan.shared_lines:  # another theme's line this one relies on too
                    owned.append(_line_record(ln))
                continue
            await assert_alignment(
                client,
                ln.source,
                ln.target,
                ln.relation,
                at=at,
                drafted_by="ai" if ln.ai else "human",
                batch=plan.batch,
                reason=ln.reason,
            )
            added_lines.append(ln)
            owned.append(_line_record(ln))
        plan.theme["relations"] = _dedupe_records(owned)
        placed = [plan.theme if t["id"] == plan.theme["id"] else t for t in plan.base["themes"]]
        if plan.theme["id"] not in {t["id"] for t in plan.base["themes"]}:
            placed.append(plan.theme)
        _save_themes(root, {**plan.base, "themes": placed})
        await sv.recompute_wired(client, root, at=at)
    except BaseException:
        await _rollback(
            client, root, minted, added_lines, added_tools, prior_themes_text, prior_counts
        )
        raise
    if plan.draft_id:
        delete_draft(root, plan.draft_id)
    return {
        "theme": plan.theme,
        "minted": minted,
        "lines": len(plan.lines),
        "questions": plan.theme["questions"],
        "memo": len(plan.theme["memo_questions"]),
        "batch": plan.batch,
    }


def _line_record(ln: _Line) -> dict:
    return {"from": ln.source, "relation": ln.relation, "to": ln.target}


def _dedupe_records(recs: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for r in recs:
        k = (r["from"], r["relation"], r["to"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _knowledge_path(root: Path | str) -> Path:
    return sv._dir(root) / sv._KNOWLEDGE_FILE


def _read_knowledge_text(root: Path | str) -> str | None:
    try:
        return _knowledge_path(root).read_text(encoding="utf-8")
    except OSError:
        return None


def _write_knowledge_text(root: Path | str, text: str | None) -> None:
    path = _knowledge_path(root)
    if text is None:
        path.unlink(missing_ok=True)
        return
    sv._atomic_write(path, text)


async def _rollback(
    client: Any,
    root: Path | str,
    minted: list[str],
    added_lines: list[_Line],
    added_tools: list[str],
    prior_themes_text: str | None,
    prior_counts: tuple[str | None, str | None],
) -> None:
    """Take back what a failed commit wrote: lines, then CQs, then terms (a term cannot be
    withdrawn while a line touches it), then ``themes.json`` and the two counts
    (``wired.json`` / ``knowledge.json``). Best effort and never raises: the original
    failure is what the caller must see."""
    for ln in reversed(added_lines):
        try:
            await remove_alignment(client, ln.source, ln.target, ln.relation)
        except Exception:
            logger.warning("themes: rollback of a line failed", exc_info=True)
    if added_tools:
        try:
            remove_registry_query_tools_by_name(root, sv.REGISTRY_ID, added_tools)
        except Exception:
            logger.warning("themes: rollback of CQs failed", exc_info=True)
    for slug in reversed(minted):
        try:
            await sv.remove_term(client, root, slug)
        except sv.TermNotFound:  # 鋳造の前に落ちた: 無い語は消さなくてよい
            pass
        except Exception:
            logger.warning("themes: rollback of a term failed", exc_info=True)
    try:
        _write_themes_text(root, prior_themes_text)
        # 元の写しへ戻す（数え直すと、無かったファイルが空で出来てしまう）
        _write_wired_text(root, prior_counts[0])
        _write_knowledge_text(root, prior_counts[1])
    except Exception:
        logger.warning("themes: rollback of themes.json / wired.json failed", exc_info=True)


# ---------------------------------------------------------------------------
# Taking a whole theme away
# ---------------------------------------------------------------------------


async def remove_theme_cascade(client: Any, root: Path | str, theme_id: str) -> dict:
    """Take a theme away AND what only it needed (テーマ単位の取り消し・design.md §15.2).

    The theme record goes (:func:`remove_theme`: its unfinished draft too). Then, of what
    the theme made:

    * each LINE it added is withdrawn unless another theme lists it too;
    * each term it MINTED is withdrawn unless another theme uses it, a question outside this
      theme is about it (a CQ tool still names it), or a line nobody here owns still touches it
      (a dataset's column joined to it) — that is the data's use, and it stays.

    A term withdrawn takes its own CQs with it (``remove_term``). Returns
    ``{"removed_concepts": [{slug, label}], "kept_concepts": [{slug, label, reason}],
    "removed_relations": [{from, relation, to}], "kept_relations": [...]}``
    (``reason`` = ``other_theme`` / ``other_question`` / ``has_lines``). Raises
    :class:`ThemeError` 404 for an unknown theme."""
    data = ensure_migrated(root)
    theme = next((t for t in data["themes"] if t["id"] == theme_id), None)
    if theme is None:
        raise ThemeError(f"テーマ {theme_id!r} はありません", 404)
    others = [t for t in data["themes"] if t["id"] != theme_id]
    other_lines = {
        (str(r.get("from")), str(r.get("relation")), str(r.get("to")))
        for t in others
        for r in t.get("relations") or []
        if isinstance(r, dict)
    }
    other_uses = {s for t in others for s in (t.get("uses") or [])}
    own_tools = {*(theme.get("questions") or []), *(theme.get("support_questions") or [])}
    tools = sv.read_registry_raw_tools(root, sv.REGISTRY_ID) or []
    labels = {t.slug: t.label for t in await sv._read_terms(client)}

    remove_theme(root, theme_id)  # the record (and its draft) first: the rest is best effort
    removed_rel: list[dict] = []
    kept_rel: list[dict] = []
    for r in theme.get("relations") or []:
        if not isinstance(r, dict):
            continue
        rec = {"from": r.get("from"), "relation": r.get("relation"), "to": r.get("to")}
        if (str(rec["from"]), str(rec["relation"]), str(rec["to"])) in other_lines:
            kept_rel.append(rec)
            continue
        try:
            await remove_alignment(client, str(rec["from"]), str(rec["to"]), str(rec["relation"]))
            removed_rel.append(rec)
        except Exception:
            logger.warning("themes: removing a line of a theme failed", exc_info=True)
            kept_rel.append(rec)
    removed_c: list[dict] = []
    kept_c: list[dict] = []
    for slug in theme.get("minted") or []:
        iri = sv.term_iri(slug)
        row = {"slug": slug, "label": labels.get(slug, slug)}
        if slug in other_uses:
            kept_c.append({**row, "reason": "other_theme"})
            continue
        if any(
            iri in [str(x) for x in (t.get("for_terms") or [])]
            and str(t.get("name")) not in own_tools
            for t in tools
        ):
            kept_c.append({**row, "reason": "other_question"})
            continue
        try:
            await sv.remove_term(client, root, slug)
            removed_c.append(row)
        except sv.TermHasLines:
            kept_c.append({**row, "reason": "has_lines"})
        except sv.TermNotFound:
            continue
    if removed_rel and not removed_c:
        await sv.recompute_wired(client, root)  # a withdrawn line changes the knowledge counts
    return {
        "removed_concepts": removed_c,
        "kept_concepts": kept_c,
        "removed_relations": removed_rel,
        "kept_relations": kept_rel,
    }


# ---------------------------------------------------------------------------
# Templates (CSV header rows only)
# ---------------------------------------------------------------------------


def template_files(theme: dict, terms: list[dict], tools: list[dict]) -> dict[str, list[str]]:
    """The tables a theme implies: ``{filename: [header, …]}`` — one CSV per KIND the theme
    needs, the first column ``<kind>_id`` then the items tied to that kind (by the
    theme's own questions), named by their English name (else the slug). Items with no
    kind of their own go to ``other.csv``. Header rows only — a starting point for a
    person's file, never data.

    Built from the questions' TARGETS only (design.md §15.2): the term a data question is
    about, and the term a knowledge question is about (「熱伝導率を下げるもの」→ the
    thermal conductivity column). A factor that the theme only places by its relations
    (a scattering mechanism) gets no table of its own."""
    by_iri = {t["iri"]: t for t in terms}
    name = lambda t: (t.get("label_en") or t["slug"]).strip()  # noqa: E731
    need: dict[str, dict] = {}
    mine = [*(theme.get("questions") or []), *(theme.get("support_questions") or [])]
    for tool in tools:
        if str(tool.get("name")) not in mine:
            continue
        for iri in tool.get("for_terms") or []:
            if iri in by_iri:
                need[iri] = by_iri[iri]
    sels = theme.get("selections") or {}
    by_slug = {t["slug"]: t for t in terms}
    for s in sels.values():  # a knowledge question's target is a column / a table too
        if isinstance(s, dict) and s.get("target_slug") in by_slug:
            t = by_slug[s["target_slug"]]
            need[t["iri"]] = t
    kind_cols: dict[str, list[str]] = {}
    loose: list[str] = []
    for t in need.values():
        if t["kind"] == "class":
            kind_cols.setdefault(t["slug"], [])
    for s in sels.values():
        k, p = by_slug.get(s.get("kind_slug") or ""), by_slug.get(s.get("property_slug") or "")
        if k is not None and k["kind"] == "class":
            kind_cols.setdefault(k["slug"], [])
            if p is not None and name(p) not in kind_cols[k["slug"]]:
                kind_cols[k["slug"]].append(name(p))
        elif p is not None and name(p) not in loose:
            loose.append(name(p))
    tied = {c for cols in kind_cols.values() for c in cols}
    for t in need.values():
        if t["kind"] == "property" and name(t) not in tied and name(t) not in loose:
            loose.append(name(t))
    out: dict[str, list[str]] = {}
    for slug, cols in sorted(kind_cols.items()):
        out[f"{slug}.csv"] = [f"{slug}_id", *cols]
    if loose:
        out["other.csv"] = ["id", *loose]
    return out


__all__ = [
    "DRAFT_VERSION",
    "FIRST_THEME_ID",
    "FIRST_THEME_LABEL",
    "FITTING_OPS",
    "KNOWLEDGE_DIRECTIONS",
    "KNOWLEDGE_OPS",
    "MAX_CONCEPTS",
    "RELATION_KINDS",
    "RESERVED_IDS",
    "StdRef",
    "ThemeError",
    "add_questions_to_theme",
    "clean_questions",
    "commit_theme",
    "concept_end_key",
    "create_draft",
    "default_cq_title",
    "default_label",
    "default_standards",
    "delete_draft",
    "derive_slug",
    "ensure_migrated",
    "knowledge_tool_name",
    "list_drafts",
    "load_draft",
    "load_themes_file",
    "match_rows",
    "merge_expansion",
    "new_theme_id",
    "postprocess_draft",
    "relation_predicate",
    "remove_theme",
    "remove_theme_cascade",
    "save_draft",
    "slug_from_text",
    "std_by_curie",
    "std_exact",
    "template_files",
    "unique_slug",
]
