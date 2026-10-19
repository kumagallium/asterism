"""Themes — 「知りたいことから作る」(ui-redesign PR 4・design.md §3).

A *theme* is a named group of questions a person wants answered. It does NOT own anything
it names: terms (``sv:<slug>``), the lines between them and the competency questions
(CQs) live in the shared vocabulary and outlive the theme — deleting a theme only drops
its references. This module holds

* the stored shape (``registry/vocab-shared/themes.json`` + one working draft per theme
  being made under ``themes/<id>/draft.json``) and the idempotent first migration;
* the deterministic post-process of an AI draft (slug derivation, exact matches against
  the standards and the existing terms — which beat the model's own ``use`` — kind and
  cycle checks, operations the declarative tools cannot answer yet → memo questions);
* the ONE operation that turns a confirmed draft into stored terms, lines, CQs and a
  theme record (:func:`commit_theme`) — everything that can be refused is checked before
  the first write, and a failure while writing takes back what was written;
* the CSV "templates" (header rows only) a theme implies.

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
RELATION_KINDS = ("sub", "eq", "quantity")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
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
    """Drop the theme record — and ONLY that. Its terms, lines and CQs stay (a term is not
    the theme's possession). Also removes an unfinished draft of the same id."""
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
    try:
        path = _draft_path(root, draft_id)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ThemeError):
        return None
    return data if isinstance(data, dict) and data.get("id") == draft_id else None


def create_draft(
    root: Path | str, label: str, questions: list[str], *, at: str, extend: str | None = None
) -> dict:
    """A new draft with a fresh id (from the label; unique among themes and drafts).
    ``extend`` = the id of a theme the questions will be added to (「問いを足す」)."""
    label = _clean_label(label)
    taken = {t["id"] for t in ensure_migrated(root)["themes"]}
    taken |= {d["id"] for d in list_drafts(root)}
    draft_id = unique_slug(slug_from_text(label) or "theme", taken)
    draft = {
        "id": draft_id,
        "label": label,
        "step": 1,
        "questions": clean_questions(questions),
        "ai": None,
        "terms": [],
        "relations": [],
        "selections": [],
        "updated_at": at,
    }
    if extend:
        draft["extend"] = extend
    return save_draft(root, draft)


def save_draft(root: Path | str, draft: dict) -> dict:
    """Write a draft as given (the shape is the wizard's own; only the frame is checked)."""
    path = _draft_path(root, str(draft.get("id")))
    step = draft.get("step")
    if not isinstance(step, int) or not 1 <= step <= 6:
        raise ThemeError("step は 1〜6 です")
    out = {
        "id": draft["id"],
        "label": _clean_label(str(draft.get("label") or "")),
        "step": step,
        "questions": clean_questions(draft.get("questions") or []),
        "ai": draft.get("ai") if isinstance(draft.get("ai"), dict) else None,
        **({"extend": draft["extend"]} if _ID_RE.match(str(draft.get("extend") or "")) else {}),
        "terms": _list_of_dicts(draft.get("terms")),
        "relations": _list_of_dicts(draft.get("relations")),
        "selections": _list_of_dicts(draft.get("selections")),
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


def std_exact(
    label_ja: str, label_en: str, aliases: Iterable[str], kind: str
) -> StdRef | None:
    """The standard term whose name equals this row's English name (or an alias) exactly
    — after :func:`normalize_label`, never fuzzy. For a property row a QUDT quantity kind
    with the same name counts too (the line is then ``hasQuantityKind``). ``None`` when
    nothing matches or the name is too generic."""
    from asterism import grounding
    from asterism.grounding import quantity_kinds

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


def postprocess_draft(ai: dict, *, questions: list[str], existing: list[SharedTerm]) -> dict:
    """The AI's closed JSON (already validated) → the wizard's working rows.

    Machine facts beat the model's claims: an exact match with an existing shared term (ja
    / en / aliases, :func:`normalize_label`) or with a standard term decides ``use``; only
    when there is no such match does a ``use`` the model named stand — and then only if it
    points into the closed set. Returns ``{"terms", "relations", "selections",
    "suggested_questions", "standards", "groups"}``:

    * term rows ``{key, label_ja, label_en, aliases, kind, use, slug, reason, match, by}``
      (``match`` = what the machine found: ``{kind: existing|standard|quantity, ref, label,
      matched_by}`` or ``None``);
    * relations ``{subject: <key>, object: <key | curie>, relation, problem, by}`` where
      ``problem`` is ``same`` / ``mixed`` / ``cycle`` / ``quantity_target`` or ``None``;
    * one selection per question ``{question, op, kind, property, value, fits, memo,
      reason, by}`` (``kind`` / ``property`` are term keys; ``memo`` says why a question
      cannot be published: ``op`` = no declarative tool yet, ``ai`` = the model said it
      does not fit, ``none`` = nothing was chosen).
    """
    by_slug = {e.slug: e for e in existing}
    rows: list[dict] = []
    taken = set(by_slug)
    standards: dict[str, dict] = {}

    def add_row(**kw: Any) -> dict:
        row = {"key": f"t{len(rows) + 1}", **kw}
        rows.append(row)
        return row

    def find_row(label: str) -> dict | None:
        n = normalize_label(label)
        if not n:
            return None
        for r in rows:
            if n in _norm_set(r["label_ja"], r["label_en"]) or n == normalize_label(r["label_ja"]):
                return r
        return None

    def remember_std(ref: StdRef) -> None:
        standards[ref.curie] = {"label": ref.label, "kind": ref.kind}

    for t in ai.get("terms", []):
        label_ja = t["label_ja"]
        if find_row(label_ja) is not None:
            continue
        aliases = list(sv.clean_aliases(t.get("aliases")))
        label_en = t.get("label_en") or ""
        kind = t["kind"]
        match: dict | None = None
        use = "new"
        slug = ""
        ex = _match_existing(label_ja, label_en, aliases, kind, existing)
        if ex is not None:
            e, how = ex
            use, slug = f"{_USE_EXISTING}{e.slug}", e.slug
            match = {"kind": "existing", "ref": e.slug, "label": e.label, "matched_by": how}
        else:
            std = std_exact(label_ja, label_en, aliases, kind)
            if std is not None:
                use = f"{_USE_STANDARD}{std.curie}"
                match = {
                    "kind": "quantity" if std.kind == "quantity" else "standard",
                    "ref": std.curie,
                    "label": std.label,
                    "matched_by": "label",
                }
                remember_std(std)
            else:
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
            base = slug_from_text(label_en)
            if not base:
                base = next((s for a in aliases if (s := slug_from_text(a))), "")
            slug = unique_slug(base, taken) if base else ""
            taken.add(slug) if slug else None
        add_row(
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

    def resolve(label: str, kind_hint: str | None = None) -> dict | None:
        """A name the model used → a row (adding one for an existing shared term that the
        model referred to without listing it)."""
        row = find_row(label)
        if row is not None:
            return row
        n = _norm_set(label)
        for e in sorted(existing, key=lambda x: x.slug):
            if n & _term_norms(e) and (kind_hint is None or e.kind == kind_hint):
                return add_row(
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
                )
        return None

    # relations
    wanted: dict[str, dict] = {}  # a parent the model named but nobody has: label → children
    rels: list[dict] = []
    seen_rel: set[tuple[str, str, str]] = set()
    sub_edges: set[tuple[str, str]] = set()
    for r in ai.get("relations", []):
        subj = resolve(r["subject"])
        if subj is None:
            continue
        obj_ref = r["object"]
        obj_key: str | None = None
        std = std_by_curie(obj_ref) if ":" in obj_ref else None
        if std is not None:
            remember_std(std)
            obj_key = std.curie
        else:
            obj = resolve(obj_ref)
            if obj is None:
                if r["relation"] == "sub":  # "these need a parent that does not exist yet"
                    w = wanted.setdefault(
                        normalize_label(obj_ref), {"label_ja": obj_ref, "children": []}
                    )
                    if subj["key"] not in w["children"] and subj["kind"] == "class":
                        w["children"].append(subj["key"])
                continue
            obj_key = obj["key"]
        trip = (subj["key"], obj_key, r["relation"])
        if trip in seen_rel:
            continue
        seen_rel.add(trip)
        problem: str | None = None
        if obj_key == subj["key"]:
            problem = "same"
        elif r["relation"] == "quantity":
            if subj["kind"] != "property" or std is None or std.kind != "quantity":
                problem = "quantity_target"
        else:
            obj_kind = (
                std.kind
                if std is not None
                else next(x["kind"] for x in rows if x["key"] == obj_key)
            )
            if obj_kind != subj["kind"]:
                problem = "mixed"
            elif r["relation"] == "sub" and std is None:
                if _reaches(sub_edges, obj_key, subj["key"]):
                    problem = "cycle"
                else:
                    sub_edges.add((subj["key"], obj_key))
        rels.append(
            {
                "subject": subj["key"],
                "object": obj_key,
                "relation": r["relation"],
                "problem": problem,
                "by": "ai",
            }
        )

    # selections — one per question, in the person's order
    by_q = {normalize_label(s["question"]): s for s in ai.get("selections", [])}
    sels: list[dict] = []
    for q in questions:
        s = by_q.get(normalize_label(q))
        if s is None:
            sels.append(
                {
                    "question": q,
                    "op": "",
                    "kind": "",
                    "property": "",
                    "value": "",
                    "fits": False,
                    "memo": "none",
                    "reason": "",
                    "by": "ai",
                }
            )
            continue
        k_row = resolve(s["kind"], "class") if s.get("kind") else None
        p_row = resolve(s["property"], "property") if s.get("property") else None
        if k_row is not None and k_row["kind"] != "class":
            k_row = None
        if p_row is not None and p_row["kind"] != "property":
            p_row = None
        supported = s["op"] in FITTING_OPS
        memo = None if (s["fits"] and supported) else ("ai" if not s["fits"] else "op")
        sels.append(
            {
                "question": q,
                "op": s["op"],
                "kind": k_row["key"] if k_row else "",
                "property": p_row["key"] if p_row else "",
                "value": s.get("value") or "",
                "fits": memo is None,
                "memo": memo,
                "reason": s.get("reason") or "",
                "by": "ai",
            }
        )

    asked = {normalize_label(q) for q in questions}
    suggested = [
        q for q in ai.get("suggested_questions", []) if normalize_label(q) not in asked
    ]
    return {
        "terms": rows,
        "relations": rels,
        "selections": sels,
        "suggested_questions": suggested[:5],
        "standards": standards,
        # a grouping name is only SUGGESTED, and only when two or more terms want the same
        # parent that nobody has (the person decides whether to add it — never generated)
        "groups": [g for g in wanted.values() if len(g["children"]) >= 2],
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
    },
    "en": {
        "op": "The current ways of counting (count, range, top value) cannot answer it, "
        "so it stays as a memo.",
        "ai": "It does not fit a fixed shape, so it stays as a memo.",
        "none": "No way of counting was chosen, so it stays as a memo.",
        "human": "You chose to keep it as a memo.",
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


@dataclass
class _Plan:
    theme: dict
    rows: list[_Row]
    lines: list[_Line]
    #: term key → CQSpecs (title, op) to attach, in order
    cqs: dict[str, list[CQSpec]]
    #: term key → the planned CQ declarations (same order; only an existing term's are written
    #: as they stand — a new term's are rebuilt by ``mint_term`` to the same names)
    cq_tools: dict[str, list[dict]]
    draft_id: str | None
    #: ``themes.json`` as it was (or as the first migration makes it) BEFORE any write —
    #: so the CQs this commit mints are never swept into the migrated "first" theme.
    base: dict


def _display_label(row_label_ja: str, row_label_en: str, lang: str) -> str:
    return (row_label_en or row_label_ja) if lang.startswith("en") else row_label_ja


async def _plan_commit(client: Any, root: Path | str, body: dict, *, at: str) -> _Plan:
    """Everything that can be refused, checked with NO write. Raises :class:`ThemeError`."""
    lang = "en" if str(body.get("lang") or "ja").startswith("en") else "ja"
    label = _clean_label(str(body.get("label") or ""))
    questions = clean_questions(body.get("questions") or [])
    data, _fresh = _migration_view(root, at)  # reads only: a refused commit writes nothing
    if not data.get("migrated"):
        raise ThemeError("共有の問いの宣言（query_tools.yaml）が読めないため置けません", 409)
    theme_ids = {t["id"] for t in data["themes"]}
    draft_id = body.get("id") if isinstance(body.get("id"), str) and body.get("id") else None
    extend_id = body.get("extend") if isinstance(body.get("extend"), str) else None
    if draft_id is not None and not _ID_RE.match(draft_id):
        raise ThemeError(f"id must match {_ID_RE.pattern}: {draft_id!r}")
    if extend_id:  # 「問いを足す」: the questions join an existing theme instead of a new one
        theme_id = extend_id
        if extend_id not in theme_ids:
            raise ThemeError(f"テーマ {extend_id!r} はありません", 404)
    elif draft_id is not None:
        if draft_id in theme_ids:
            raise ThemeError(f"テーマ {draft_id!r} はすでにあります", 409)
        theme_id = draft_id
    else:
        theme_id = unique_slug(slug_from_text(label) or "theme", theme_ids)
    try:
        sv._read_cq_tools(root)
    except sv.TermError as exc:
        raise ThemeError(str(exc), 409) from exc

    existing = await sv._read_terms(client)
    by_slug = {t.slug: t for t in existing}
    ai_drafted = bool(body.get("ai_drafted"))

    # --- term rows -----------------------------------------------------------
    rows: list[_Row] = []
    by_key: dict[str, _Row] = {}
    planned_slugs: set[str] = set()
    for i, raw in enumerate(body.get("terms") or []):
        if not isinstance(raw, dict):
            raise ThemeError(f"terms[{i}] が不正です")
        key = str(raw.get("key") or f"t{i + 1}")
        if key in by_key:
            raise ThemeError(f"用語の key が重複しています: {key}")
        kind = raw.get("kind")
        if kind not in ("class", "property"):
            raise ThemeError(f"terms[{i}].kind は class か property です")
        label_ja = " ".join(str(raw.get("label_ja") or "").split())
        if not label_ja or len(label_ja) > _LABEL_MAX:
            raise ThemeError(f"terms[{i}] の日本語名が空か長すぎます")
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
            raise ThemeError(f"terms[{i}].use が不正です: {use!r}")
        if mint:
            slug = str(raw.get("slug") or "")
            if not re.match(sv.SLUG_RE, slug):
                raise ThemeError(
                    f"「{label_ja}」の ID は {sv.SLUG_RE} の形にしてください: {slug!r}"
                )
            if slug in by_slug or slug in planned_slugs:
                raise ThemeError(f"ID「{slug}」はすでに使われています。別の ID にしてください", 409)
            planned_slugs.add(slug)
        else:
            slug = use[len(_USE_EXISTING) :]
            e = by_slug.get(slug)
            if e is None:
                raise ThemeError(f"既にある用語「{slug}」が見つかりません")
            if e.kind != kind:
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
            iri=sv.term_iri(slug),
        )
        rows.append(row)
        by_key[key] = row

    # --- lines -----------------------------------------------------------------
    def rel_name(kind: str, relation: str) -> str:
        pair = {
            "sub": ("subClassOf", "subPropertyOf"),
            "eq": ("equivalentClass", "equivalentProperty"),
        }[relation]
        return pair[0] if kind == "class" else pair[1]

    lines: list[_Line] = []
    seen_lines: set[tuple[str, str, str]] = set()

    def add_line(source: str, target: str, relation: str) -> None:
        t = (source, relation, target)
        if t not in seen_lines:
            seen_lines.add(t)
            lines.append(_Line(source, target, relation))

    for row in rows:  # the machine's own "same as the standard" lines
        if row.std is not None:
            if row.std.kind == "quantity":
                add_line(row.iri, row.std.iri, "hasQuantityKind")
            else:
                add_line(row.iri, row.std.iri, rel_name(row.kind, "eq"))
    for i, raw in enumerate(body.get("relations") or []):
        if not isinstance(raw, dict):
            raise ThemeError(f"relations[{i}] が不正です")
        rel = raw.get("relation")
        if rel not in RELATION_KINDS:
            raise ThemeError(f"relations[{i}].relation は {RELATION_KINDS} のどれかです")
        subj = by_key.get(str(raw.get("subject")))
        if subj is None:
            raise ThemeError(f"relations[{i}] の主語が用語にありません")
        if not subj.mint:
            raise ThemeError(
                f"「{subj.label_ja}」は既にある用語です。既にある用語の関係は、ここでは足せません"
                "（用語のページで決められます）"
            )
        obj_ref = str(raw.get("object"))
        obj_row = by_key.get(obj_ref)
        obj_std = None if obj_row is not None else std_by_curie(obj_ref)
        if obj_row is None and obj_std is None:
            raise ThemeError(f"relations[{i}] の相手が見つかりません: {obj_ref!r}")
        target_iri = obj_row.iri if obj_row is not None else obj_std.iri  # type: ignore[union-attr]
        target_kind = obj_row.kind if obj_row is not None else obj_std.kind  # type: ignore[union-attr]
        if target_iri == subj.iri:
            raise ThemeError(f"「{subj.label_ja}」を自分自身には結べません")
        if rel == "quantity":
            if subj.kind != "property" or obj_std is None or obj_std.kind != "quantity":
                raise ThemeError("「量」の関係は、項目から量の種類（QUDT）へだけ結べます")
            add_line(subj.iri, target_iri, "hasQuantityKind")
        else:
            if target_kind != subj.kind:
                raise ThemeError(
                    f"「{subj.label_ja}」と相手は、種類と項目が合いません（種類と項目は結べません）"
                )
            add_line(subj.iri, target_iri, rel_name(subj.kind, rel))

    # shapes of the ends that already exist (the store's own check re-runs at write time)
    touched = {x for ln in lines for x in (ln.source, ln.target)}
    store_ends = sorted({e.iri for e in existing if e.iri in touched})
    shapes = await _declared_shapes(client, store_ends) if store_ends else {}
    for ln in lines:
        if ln.relation == "hasQuantityKind":
            continue
        wrong = "property" if ln.relation in ("equivalentClass", "subClassOf") else "class"
        for end in (ln.source, ln.target):
            if shapes.get(end) == {wrong}:
                raise ThemeError("種類と項目は結べません")
    # cycles: the stored lines plus the ones planned, asserted in order
    edges = set(await sv._alignment_lines(client))
    for ln in lines:
        if ln.relation in ("subClassOf", "subPropertyOf"):
            rels = sv._CLASS_RELS if ln.relation == "subClassOf" else sv._PROP_RELS
            if ln.target in sv._down_set(ln.source, edges, rels):
                raise ThemeError("その関係を置くと、一周してしまいます（循環）")
        edges.add((ln.source, ln.relation, ln.target))

    # --- selections → CQs --------------------------------------------------------
    sel_by_q = {
        normalize_label(str(s.get("question") or "")): s
        for s in body.get("selections") or []
        if isinstance(s, dict)
    }
    cqs: dict[str, list[CQSpec]] = {}
    hosted: list[tuple[str, str, dict]] = []  # (question, term key, selection summary)
    memo: list[dict] = []
    for q in questions:
        s = sel_by_q.get(normalize_label(q))
        why = "none"
        if s is not None:
            op = s.get("op")
            k = by_key.get(str(s.get("kind"))) if s.get("kind") else None
            p = by_key.get(str(s.get("property"))) if s.get("property") else None
            if (s.get("kind") and k is None) or (s.get("property") and p is None):
                raise ThemeError(f"問い「{q}」の種類か項目が用語にありません")
            if k is not None and k.kind != "class":
                raise ThemeError(f"問い「{q}」の種類に項目が選ばれています")
            if p is not None and p.kind != "property":
                raise ThemeError(f"問い「{q}」の項目に種類が選ばれています")
            flagged = s.get("memo")
            if flagged in _MEMO_REASONS["ja"]:
                why = flagged
            elif op in FITTING_OPS and s.get("fits", True):
                host: _Row | None = None
                cq_op = "count"
                if op == "count" and k is not None:
                    host = k
                elif op in ("range", "top") and p is not None:
                    host, cq_op = p, "values"
                if host is not None:
                    cqs.setdefault(host.key, []).append(CQSpec(q, cq_op))
                    hosted.append(
                        (
                            q,
                            host.key,
                            {
                                "op": op,
                                "kind": _display_label(k.label_ja, k.label_en, lang) if k else "",
                                "property": (
                                    _display_label(p.label_ja, p.label_en, lang) if p else ""
                                ),
                                "value": str(s.get("value") or ""),
                                "kind_slug": k.slug if k else "",
                                "property_slug": p.slug if p else "",
                            },
                        )
                    )
                    continue
            elif op in sv.SELECTION_OPS:
                why = "op" if op not in FITTING_OPS else "ai"
        memo.append(
            {
                "id": "m_" + hashlib.sha1(q.encode("utf-8")).hexdigest()[:8],
                "title": q,
                "reason": _MEMO_REASONS[lang][why],
            }
        )

    # --- CQ tool names, lint -------------------------------------------------------
    taken_names = {str(t.get("name")) for t in sv._read_cq_tools(root)}
    cq_tools: dict[str, list[dict]] = {}
    for row in rows:
        specs = list(cqs.get(row.key, []))
        if row.mint and not specs:  # a term is never made without a question (§2.1)
            shown = _display_label(row.label_ja, row.label_en, lang)
            title = default_cq_title(row.kind, shown, lang)
            specs = [CQSpec(title, "count" if row.kind == "class" else "values")]
            cqs[row.key] = specs
        if not specs:
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
    # the theme's own questions, in the person's order, with the tool each one became
    q_tools: list[str] = []
    q_sel: dict[str, dict] = {}
    cursor: dict[str, int] = {}
    for q, host_key, summary in hosted:
        specs = cqs[host_key]
        idx = next(
            i for i, sp in enumerate(specs) if sp.title == q and i >= cursor.get(host_key, 0)
        )
        cursor[host_key] = idx + 1
        name = cq_names[host_key][idx]
        q_tools.append(name)
        q_sel[name] = summary

    # a new term that no question of this theme is about carries a default question; the theme
    # remembers it (so the term is "needed" and the question is not listed as homeless) but
    # does not show it as one of the person's questions
    hosted_keys = {k for _, k, _ in hosted}
    support = [cq_names[r.key][0] for r in rows if r.mint and r.key not in hosted_keys]
    theme = {
        "id": theme_id,
        "label": label,
        "created_at": at,
        "questions": q_tools,
        "support_questions": support,
        "selections": q_sel,
        "memo_questions": memo,
    }
    if extend_id:
        prior = next(t for t in data["themes"] if t["id"] == extend_id)
        have_q = list(prior.get("questions") or [])
        have_m = list(prior.get("memo_questions") or [])
        have_m_ids = {m["id"] for m in have_m}
        theme = {
            **prior,
            "questions": [*have_q, *[n for n in q_tools if n not in have_q]],
            "support_questions": [
                *(prior.get("support_questions") or []),
                *[n for n in support if n not in (prior.get("support_questions") or [])],
            ],
            "selections": {**(prior.get("selections") or {}), **q_sel},
            "memo_questions": [*have_m, *[m for m in memo if m["id"] not in have_m_ids]],
        }
    return _Plan(
        theme=theme,
        rows=rows,
        lines=lines,
        cqs=cqs,
        cq_tools=cq_tools,
        draft_id=draft_id,
        base=data,
    )


async def _line_exists(client: Any, ln: _Line) -> bool:
    return await sv._ask(
        client,
        f"ASK {{ GRAPH <{ALIGNMENT_GRAPH}> {{ <{ln.source}> <{ALIGN_RELATIONS[ln.relation]}> "
        f"<{ln.target}> }} }}",
    )


async def commit_theme(client: Any, root: Path | str, body: dict, *, at: str) -> dict:
    """Turn a confirmed draft into stored terms, lines, CQs and a theme record — as ONE
    operation. Returns ``{"theme", "minted": [slug], "lines": n, "questions": [tool],
    "memo": n}``.

    Everything that can be refused is checked first (:func:`_plan_commit`, no write). Then
    the writes run in a fixed order — new terms (with their CQs) → CQs of existing terms →
    lines → ``themes.json`` → ``wired.json`` — and if any of them fails, what was written
    is taken back (lines, CQs, terms, ``themes.json``): a mint is never left without its
    lines. Raises :class:`ThemeError` (nothing written) or re-raises the write failure
    (after the rollback)."""
    plan = await _plan_commit(client, root, body, at=at)
    prior_themes_text = read_themes_text(root)
    minted: list[str] = []
    added_lines: list[_Line] = []
    added_tools: list[str] = []
    try:
        for row in plan.rows:
            if not row.mint:
                continue
            await sv.mint_term(
                client,
                root,
                slug=row.slug,
                kind=row.kind,
                label=row.label_ja,
                label_en=row.label_en or None,
                comment=row.comment,
                cqs=plan.cqs[row.key],
                aliases=row.aliases,
                drafted_by="ai" if row.ai else None,
                at=at,
            )
            minted.append(row.slug)
        for row in plan.rows:
            if row.mint or row.key not in plan.cq_tools:
                continue
            sv._write_cq_tools(root, plan.cq_tools[row.key])
            added_tools.extend(t["name"] for t in plan.cq_tools[row.key])
        for ln in plan.lines:
            if await _line_exists(client, ln):
                continue
            await assert_alignment(client, ln.source, ln.target, ln.relation, at=at)
            added_lines.append(ln)
        placed = [plan.theme if t["id"] == plan.theme["id"] else t for t in plan.base["themes"]]
        if plan.theme["id"] not in {t["id"] for t in plan.base["themes"]}:
            placed.append(plan.theme)
        _save_themes(root, {**plan.base, "themes": placed})
        await sv.recompute_wired(client, root, at=at)
    except BaseException:
        await _rollback(client, root, minted, added_lines, added_tools, prior_themes_text)
        raise
    if plan.draft_id:
        delete_draft(root, plan.draft_id)
    return {
        "theme": plan.theme,
        "minted": minted,
        "lines": len(plan.lines),
        "questions": plan.theme["questions"],
        "memo": len(plan.theme["memo_questions"]),
    }


async def _rollback(
    client: Any,
    root: Path | str,
    minted: list[str],
    added_lines: list[_Line],
    added_tools: list[str],
    prior_themes_text: str | None,
) -> None:
    """Take back what a failed commit wrote. Best effort and never raises: the original
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
        except Exception:
            logger.warning("themes: rollback of a term failed", exc_info=True)
    try:
        _write_themes_text(root, prior_themes_text)
        await sv.recompute_wired(client, root)
    except Exception:
        logger.warning("themes: rollback of themes.json / wired.json failed", exc_info=True)


# ---------------------------------------------------------------------------
# Templates (CSV header rows only)
# ---------------------------------------------------------------------------


def template_files(theme: dict, terms: list[dict], tools: list[dict]) -> dict[str, list[str]]:
    """The tables a theme implies: ``{filename: [header, …]}`` — one CSV per KIND the theme
    needs, the first column ``<kind>_id`` then the items tied to that kind (by the
    theme's own questions), named by their English name (else the slug). Items with no
    kind of their own go to ``other.csv``. Header rows only — a starting point for a
    person's file, never data."""
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
    "FIRST_THEME_ID",
    "FIRST_THEME_LABEL",
    "FITTING_OPS",
    "StdRef",
    "ThemeError",
    "add_questions_to_theme",
    "clean_questions",
    "commit_theme",
    "create_draft",
    "default_cq_title",
    "default_standards",
    "delete_draft",
    "ensure_migrated",
    "list_drafts",
    "load_draft",
    "load_themes_file",
    "postprocess_draft",
    "remove_theme",
    "save_draft",
    "slug_from_text",
    "std_by_curie",
    "std_exact",
    "template_files",
    "unique_slug",
]
