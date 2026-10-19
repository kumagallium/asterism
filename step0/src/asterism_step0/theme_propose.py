"""AI draft of a THEME — from the questions a person wants answered to a closed JSON of
terms, relations and selections (ui-redesign PR 4「知りたいことから作る」②).

What the model may do: read the question sentences, the names of terms that already
exist, and the names of the standard terms, and fill the closed shape below. What it may
not do: write SPARQL, IRIs or code (the shape has no place for them, and any string that
looks like one is refused), or see the data (nothing but those names is passed in).

The result is a DRAFT. A person confirms every row in the wizard; a deterministic
post-process (``asterism.themes``) then matches names against the standards and the
existing terms by exact label (that match beats the model's ``use``), checks kinds and
cycles, and drops operations the declarative tools cannot answer yet into "memo
questions". Nothing here touches the store.

The shape is enforced twice: :func:`theme_draft_json_schema` for guided-decoding
servers (the client degrades ``json_schema`` → ``json_object`` → off), and
:func:`validate_theme_draft` for every provider — the strict gate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from asterism_step0.language import language_instruction
from asterism_step0.llm import LLMClient, as_completion

__all__ = [
    "SELECTION_OPS",
    "THEME_DRAFT_SYSTEM_PROMPT",
    "ThemeDraftError",
    "propose_theme_draft",
    "theme_draft_json_schema",
    "theme_draft_user_message",
    "validate_theme_draft",
]

#: 数え方の閉集合。今の宣言ツールで答えられるのは先頭 3 つ（残りは後処理で「メモの問い」へ）。
SELECTION_OPS = ("count", "range", "top", "filter", "group", "trend", "join")
TERM_KINDS = ("class", "property")
RELATIONS = ("sub", "eq", "quantity")

_LEN_LABEL = 80
_LEN_ALIAS = 60
_LEN_REASON = 200
_LEN_QUESTION = 300
_MAX_TERMS = 40
_MAX_ALIASES = 12
_MAX_RELATIONS = 60
_MAX_SUGGESTED = 5

#: 文字列の中身に「コードや IRI を書かせない」ための検査（スキーマに無い欄は読まない・読んでも
#: 捨てるが、許した欄に紛れ込ませる抜け道も塞ぐ）。
_CODE_LIKE = re.compile(
    r"https?://|[<>{}`]|\b(?:SELECT|INSERT|DELETE|WHERE|PREFIX)\b", re.IGNORECASE
)
_USE_NEW = "new"
_USE_RE = re.compile(r"^(?:new|existing:[a-z][a-z0-9_]*|standard:[A-Za-z][\w.\-]*:[\w.\-]+)\Z")


class ThemeDraftError(ValueError):
    """The model's output is not the closed shape (the caller retries once, then 502)."""


THEME_DRAFT_SYSTEM_PROMPT = """\
You help a person turn the QUESTIONS they want answered into a vocabulary draft.
You never see their data. You are given: (1) their questions, (2) the names of terms
that already exist, (3) the names of standard terms. Fill ONE JSON object:

{"terms": [{"label_ja": "...", "label_en": "...", "aliases": ["..."],
            "kind": "class|property", "use": "new|existing:<slug>|standard:<curie>",
            "reason": "..."}],
 "relations": [{"subject": "<label_ja>", "object": "<label_ja or curie>",
                "relation": "sub|eq|quantity"}],
 "selections": [{"question": "<the question, copied verbatim>",
                 "op": "count|range|top|filter|group|trend|join",
                 "kind": "<label_ja of a class term>",
                 "property": "<label_ja of a property term> or null",
                 "value": "<a literal the question names> or null",
                 "fits": true, "reason": "..."}],
 "suggested_questions": ["..."]}

Meaning:
- A term is a CLASS (a kind of thing: a sample, a paper) or a PROPERTY (something said
  about a thing: a capacity, a temperature). Give a Japanese name and an English name,
  and "aliases": column names a data file would plausibly use for it (e.g. "cap",
  "capacity_mAh"). Keep the list short.
- "use": "existing:<slug>" / "standard:<curie>" ONLY when the name below says it is
  that exact term; otherwise "new". Copy slugs and curies verbatim from the lists.
  NEVER invent a slug, a curie or an IRI.
- "relations": "sub" = the subject is a kind of the object (same kind of term on both
  sides); "eq" = they are the same; "quantity" = the property measures that quantity
  (the object is a standard quantity-kind curie). Use a term's label_ja, or a curie.
- "selections": for EVERY question give how it is answered: "count" (how many of a
  kind), "range" (min and max of a property), "top" (the one with the largest value),
  "filter" (those whose property equals a value), "group" (count per property value),
  "trend" (a property in order), "join" (follow a property one step). Name the kind and
  the property by their label_ja from your own "terms". If none fits, set "fits": false
  and say why in "reason".
- "suggested_questions": at most 3 further questions that the same terms could answer.

Hard rules:
- Output ONLY the JSON object. No prose, no markdown fence.
- Do NOT write SPARQL, IRIs, URLs, code or anything between < > or { }. Names only.
- Do not use any value from a data file: you have none.
"""


def _string(max_length: int, *, nullable: bool = False) -> dict:
    t: dict[str, Any] = {"type": ["string", "null"] if nullable else "string"}
    t["maxLength"] = max_length
    return t


def theme_draft_json_schema() -> dict:
    """The closed shape, as a JSON Schema for guided-decoding servers. Bounded strings so
    a repetition loop cannot eat the completion; no free-form object anywhere."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["terms", "relations", "selections", "suggested_questions"],
        "properties": {
            "terms": {
                "type": "array",
                "maxItems": _MAX_TERMS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label_ja", "label_en", "aliases", "kind", "use", "reason"],
                    "properties": {
                        "label_ja": _string(_LEN_LABEL),
                        "label_en": _string(_LEN_LABEL),
                        "aliases": {
                            "type": "array",
                            "maxItems": _MAX_ALIASES,
                            "items": _string(_LEN_ALIAS),
                        },
                        "kind": {"type": "string", "enum": list(TERM_KINDS)},
                        "use": _string(_LEN_LABEL),
                        "reason": _string(_LEN_REASON),
                    },
                },
            },
            "relations": {
                "type": "array",
                "maxItems": _MAX_RELATIONS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["subject", "object", "relation"],
                    "properties": {
                        "subject": _string(_LEN_LABEL),
                        "object": _string(_LEN_LABEL),
                        "relation": {"type": "string", "enum": list(RELATIONS)},
                    },
                },
            },
            "selections": {
                "type": "array",
                "maxItems": _MAX_TERMS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question", "op", "kind", "property", "value", "fits", "reason"],
                    "properties": {
                        "question": _string(_LEN_QUESTION),
                        "op": {"type": "string", "enum": list(SELECTION_OPS)},
                        "kind": _string(_LEN_LABEL),
                        "property": _string(_LEN_LABEL, nullable=True),
                        "value": _string(_LEN_LABEL, nullable=True),
                        "fits": {"type": "boolean"},
                        "reason": _string(_LEN_REASON),
                    },
                },
            },
            "suggested_questions": {
                "type": "array",
                "maxItems": _MAX_SUGGESTED,
                "items": _string(_LEN_QUESTION),
            },
        },
    }


# ---------------------------------------------------------------------------
# The strict gate (every provider)
# ---------------------------------------------------------------------------


def _fail(path: str, why: str) -> ThemeDraftError:
    return ThemeDraftError(f"{path}: {why}")


def _str(v: Any, path: str, max_len: int, *, nullable: bool = False, allow_code: bool = False):
    if v is None and nullable:
        return None
    if not isinstance(v, str):
        raise _fail(path, "must be a string")
    s = " ".join(v.split())
    if len(s) > max_len:
        raise _fail(path, f"longer than {max_len} characters")
    if not allow_code and _CODE_LIKE.search(s):
        raise _fail(path, "looks like code or an IRI (names only)")
    return s


def _list(v: Any, path: str, max_items: int) -> list:
    if not isinstance(v, list):
        raise _fail(path, "must be a list")
    if len(v) > max_items:
        raise _fail(path, f"more than {max_items} items")
    return v


def validate_theme_draft(raw: Any) -> dict:
    """Check ``raw`` against the closed shape and return a normalised copy (only the
    keys of the shape; strings whitespace-folded). Raises :class:`ThemeDraftError`.

    Unknown keys are dropped, not read — the shape has no place for a query or an IRI.
    A string that looks like code or an IRI is refused outright."""
    if not isinstance(raw, dict):
        raise _fail("$", "must be an object")
    out: dict[str, list] = {
        "terms": [],
        "relations": [],
        "selections": [],
        "suggested_questions": [],
    }
    for i, t in enumerate(_list(raw.get("terms", []), "terms", _MAX_TERMS)):
        p = f"terms[{i}]"
        if not isinstance(t, dict):
            raise _fail(p, "must be an object")
        kind = t.get("kind")
        if kind not in TERM_KINDS:
            raise _fail(f"{p}.kind", f"must be one of {TERM_KINDS}")
        use = _str(t.get("use", _USE_NEW), f"{p}.use", _LEN_LABEL, allow_code=True)
        if not _USE_RE.match(use):
            raise _fail(f"{p}.use", "must be new, existing:<slug> or standard:<curie>")
        label_ja = _str(t.get("label_ja"), f"{p}.label_ja", _LEN_LABEL)
        if not label_ja:
            raise _fail(f"{p}.label_ja", "is empty")
        aliases = [
            _str(a, f"{p}.aliases[{j}]", _LEN_ALIAS)
            for j, a in enumerate(_list(t.get("aliases", []), f"{p}.aliases", _MAX_ALIASES))
        ]
        out["terms"].append(
            {
                "label_ja": label_ja,
                "label_en": _str(t.get("label_en", ""), f"{p}.label_en", _LEN_LABEL),
                "aliases": [a for a in aliases if a],
                "kind": kind,
                "use": use,
                "reason": _str(t.get("reason", ""), f"{p}.reason", _LEN_REASON),
            }
        )
    for i, r in enumerate(_list(raw.get("relations", []), "relations", _MAX_RELATIONS)):
        p = f"relations[{i}]"
        if not isinstance(r, dict):
            raise _fail(p, "must be an object")
        if r.get("relation") not in RELATIONS:
            raise _fail(f"{p}.relation", f"must be one of {RELATIONS}")
        out["relations"].append(
            {
                "subject": _str(r.get("subject"), f"{p}.subject", _LEN_LABEL),
                # 標準の語は curie（``prefix:name``）で書く。``://`` は禁止のまま。
                "object": _str(r.get("object"), f"{p}.object", _LEN_LABEL),
                "relation": r["relation"],
            }
        )
    for i, s in enumerate(_list(raw.get("selections", []), "selections", _MAX_TERMS)):
        p = f"selections[{i}]"
        if not isinstance(s, dict):
            raise _fail(p, "must be an object")
        if s.get("op") not in SELECTION_OPS:
            raise _fail(f"{p}.op", f"must be one of {SELECTION_OPS}")
        fits = s.get("fits", True)
        if not isinstance(fits, bool):
            raise _fail(f"{p}.fits", "must be a boolean")
        out["selections"].append(
            {
                # 問いの原文はユーザーの文（記号を含みうる）— 照合は原文との一致で行う。
                "question": _str(
                    s.get("question"), f"{p}.question", _LEN_QUESTION, allow_code=True
                ),
                "op": s["op"],
                "kind": _str(s.get("kind", ""), f"{p}.kind", _LEN_LABEL),
                "property": _str(s.get("property"), f"{p}.property", _LEN_LABEL, nullable=True),
                "value": _str(s.get("value"), f"{p}.value", _LEN_LABEL, nullable=True),
                "fits": fits,
                "reason": _str(s.get("reason", ""), f"{p}.reason", _LEN_REASON),
            }
        )
    for i, q in enumerate(
        _list(raw.get("suggested_questions", []), "suggested_questions", _MAX_SUGGESTED)
    ):
        s = _str(q, f"suggested_questions[{i}]", _LEN_QUESTION)
        if s:
            out["suggested_questions"].append(s)
    return out


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> dict:
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i == -1 or j <= i:
            raise ThemeDraftError("the model did not return a JSON object") from None
        try:
            obj = json.loads(t[i : j + 1])
        except json.JSONDecodeError as exc:
            raise ThemeDraftError(f"the model's JSON could not be read: {exc}") from None
    if not isinstance(obj, dict):
        raise ThemeDraftError("the model did not return a JSON object")
    return obj


def _lines(items: Iterable[str]) -> str:
    return "\n".join(f"- {s}" for s in items) or "(none)"


def theme_draft_user_message(
    questions: list[str],
    known_terms: list[dict],
    known_standards: list[dict],
    *,
    language: str | None = None,
    problem: str | None = None,
) -> str:
    """The user message: question sentences + term / standard NAMES only (never data).
    ``problem`` is the previous attempt's validation error, for the one retry."""
    terms = [
        f"{t.get('slug')} | {t.get('label_ja') or ''} | {t.get('label_en') or ''} | {t.get('kind')}"
        for t in known_terms
    ]
    stds = [
        f"{s.get('curie')} | {s.get('label') or ''} | {s.get('kind') or ''}"
        for s in known_standards
    ]
    parts = [
        "# Questions (one per line)\n" + _lines(questions),
        "# Existing terms (slug | 日本語 | English | kind)\n" + _lines(terms),
        "# Standard terms (curie | label | kind)\n" + _lines(stds),
    ]
    msg = "\n\n".join(parts)
    lang = language_instruction(language)
    if lang:
        msg += (
            f"\n\n{lang}\n\nIn THIS JSON the human-readable prose means the \"reason\" values:"
            ' write those in the language above. "label_ja" is Japanese and "label_en" is'
            " English whatever the language above says; slugs, curies and question texts"
            " are copied verbatim.\n"
        )
    if problem:
        msg += (
            f"\n\nYour previous answer was refused: {problem}\n"
            "Answer again with the JSON object only.\n"
        )
    return msg


def _complete_guided(llm: Any, system: str, user: str, schema: dict) -> str:
    """Set the guided-decoding schema on a client that has one (OpenAI-compatible), call
    once, put the previous value back. The Anthropic client has no ``response_schema`` —
    it is simply called, and :func:`validate_theme_draft` is the gate."""
    had_attr = hasattr(llm, "response_schema")
    prior = getattr(llm, "response_schema", None)
    try:
        if had_attr:
            llm.response_schema = schema
        return as_completion(llm.complete(system, user)).text
    finally:
        if had_attr:
            llm.response_schema = prior


def propose_theme_draft(
    llm: LLMClient,
    *,
    questions: list[str],
    known_terms: list[dict],
    known_standards: list[dict],
    language: str | None = None,
) -> dict:
    """One guided call (one retry when the shape is refused) → the validated closed JSON.

    Raises :class:`ThemeDraftError` when the second answer is also off-shape. Provider
    errors propagate (the api maps them to 502)."""
    schema = theme_draft_json_schema()
    problem: str | None = None
    last: ThemeDraftError | None = None
    for _attempt in range(2):
        user = theme_draft_user_message(
            questions, known_terms, known_standards, language=language, problem=problem
        )
        text = _complete_guided(llm, THEME_DRAFT_SYSTEM_PROMPT, user, schema)
        try:
            return validate_theme_draft(_extract_json_object(text))
        except ThemeDraftError as exc:
            last, problem = exc, str(exc)
    assert last is not None
    raise last

