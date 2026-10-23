"""AI draft of a THEME v2 — from the questions a person wants answered to a closed JSON
knowledge map: concepts, relations (incl. part-of and lowers / raises / affects), how each
question is answered, and one line of "data I would want" per question
(ui-redesign PR 6a・design.md §15.2 / §15.3).

What the model may do: read the question sentences, the names of terms that already exist,
the names of the standard terms (and, when asked to expand one place, the names + keys of the
concepts already on the map), and fill the closed shape below. When a question asks for
factors ("what lowers X?") or parts, it is told to draft the domain KNOWLEDGE (the factors,
the parts) as concepts and relations — not just pull the nouns out of the question. What it
may not do: write SPARQL, IRIs or code (the shape has no place for them, and a name / alias /
question that looks like one is refused), or see the data (nothing but those names is passed
in). References between rows are by ``key`` (a short label the model chooses), so a class and
a property that share a name stay apart.

The result is a DRAFT. A person confirms it in the wizard; a deterministic post-process
(``asterism.themes``) then matches names against the standards and the existing terms by
exact label (that match beats the model's ``use``), checks kinds per relation and cycles,
drops what was dismissed before, and keeps operations the machine cannot answer yet as
"memo questions". Nothing here touches the store.

The shape is enforced twice: :func:`theme_draft_json_schema` for guided-decoding servers (the
client degrades ``json_schema`` → ``json_object`` → off), and :func:`validate_theme_draft` for
every provider — the strict gate. ``reason`` and ``wanted_data`` are checked for LENGTH only
(cut, never refused); the code / IRI check is for names, aliases and the question text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from asterism_step0.language import language_instruction
from asterism_step0.llm import LLMClient, as_completion

__all__ = [
    "ANSWER_MODES",
    "DATA_OPS",
    "DIRECTIONS",
    "KNOWLEDGE_OPS",
    "MATCH_MODES",
    "RELATIONS",
    "SELECTION_OPS",
    "THEME_DRAFT_SYSTEM_PROMPT",
    "THEME_EXPAND_SYSTEM_PROMPT",
    "ThemeDraftError",
    "propose_theme_draft",
    "propose_theme_expand",
    "theme_draft_json_schema",
    "theme_draft_user_message",
    "theme_expand_user_message",
    "validate_theme_draft",
]

#: 知識で答える（決定論の走者 ``asterism.knowledge``）。
KNOWLEDGE_OPS = ("factors", "parts", "kinds")
#: データで答える（決定論のテンプレートで宣言ツールになる・``asterism.shared_vocab``）。
#: ``filter`` 値で絞る／``group`` 〜ごと／``trend`` 推移／``join`` 結合 1 段。
DATA_OPS = ("count", "range", "top", "filter", "group", "trend", "join")
#: 受けるが実行しない数え方（今は無い。検証は通し、``mode="memo"`` に正規化する）。
_MEMO_OPS: tuple[str, ...] = ()
#: 後方互換の名前 — 数え方の閉集合（データ側）。
SELECTION_OPS = (*DATA_OPS, *_MEMO_OPS)
#: ``filter`` の一致のしかた。
MATCH_MODES = ("contains", "exact")
ANSWER_MODES = ("knowledge", "data", "memo")
DIRECTIONS = ("lowers", "raises", "affects")
TERM_KINDS = ("class", "property")
#: ``sub`` 一種／``eq`` 同じ／``quantity`` 量は／``part`` 一部／``lowers`` 下げる／
#: ``raises`` 上げる／``affects`` 変える（``asterism.themes`` が標準の述語 9 つに写す）。
RELATIONS = ("sub", "eq", "quantity", "part", "lowers", "raises", "affects")

_LEN_LABEL = 80
_LEN_ALIAS = 60
_LEN_REASON = 200
_LEN_QUESTION = 300
_LEN_KEY = 24
#: ``filter`` の値（問いの文に書かれていた語）の長さ。``asterism.shared_vocab.VALUE_MAX`` と同じ。
_LEN_VALUE = 120
_MAX_CONCEPTS = 40
_MAX_ALIASES = 12
_MAX_RELATIONS = 60
_MAX_ANSWERS = 40
_MAX_SUGGESTED = 5
#: 「ここを広げる」の足し分の上限。
_MAX_EXPAND_CONCEPTS = 10
_MAX_EXPAND_RELATIONS = 20
_LEN_THEME_LABEL = 80

#: 文字列の中身に「コードや IRI を書かせない」ための検査（スキーマに無い欄は読まない・読んでも
#: 捨てるが、許した欄に紛れ込ませる抜け道も塞ぐ）。名前・別名・問いだけに当てる。
_CODE_LIKE = re.compile(
    r"https?://|[<>{}`]|\b(?:SELECT|INSERT|DELETE|WHERE|PREFIX)\b", re.IGNORECASE
)
#: 問いの文・テーマ名に当てる検査。IRI（``https://``・``urn:``）とコードの記号だけ（"Where is the
#: cheapest shop?" のような英語の普通の文を SQL の語で落とさない）。
#: 名前・別名・値には上の ``_CODE_LIKE`` を当てる。
_QUESTION_LIKE = re.compile(r"https?://|\burn:|[<>{}`]", re.IGNORECASE)
_USE_NEW = "new"
_USE_RE = re.compile(r"^(?:new|existing:[a-z][a-z0-9_]*|standard:[A-Za-z][\w.\-]*:[\w.\-]+)\Z")
_KEY_RE = re.compile(rf"^[A-Za-z][A-Za-z0-9_]{{0,{_LEN_KEY - 1}}}\Z")


class ThemeDraftError(ValueError):
    """The model's output is not the closed shape (the caller retries once, then 502)."""


_SHAPE = """\
{"theme_label": "<a short theme name, 20 characters at most; empty if the person gave one>",
 "concepts": [{"key": "c1", "label_ja": "...", "label_en": "...", "aliases": ["..."],
               "kind": "class|property", "use": "new|existing:<slug>|standard:<curie>",
               "reason": "..."}],
 "relations": [{"from": "<key>", "rel": "sub|eq|quantity|part|lowers|raises|affects",
                "to": "<key or curie>", "reason": "..."}],
 "answers": [{"question": "<the question, copied verbatim>",
              "mode": "knowledge|data|memo",
              "op": "factors|parts|kinds|count|range|top|filter|group|trend|join or null",
              "target": "<key> or null", "direction": "lowers|raises|affects or null",
              "kind": "<key of a class> or null", "property": "<key of a property> or null",
              "value": "<a word from the question> or null",
              "match": "contains|exact or null",
              "property2": "<key of a property> or null", "kind2": "<key of a class> or null",
              "wanted_data": "...", "reason": "..."}],
 "suggested_questions": ["..."]}"""

_MEANING = """\
Meaning:
- A concept is a CLASS (a kind of thing: a sample, a scattering mechanism) or a PROPERTY
  (something said about a thing: a thermal conductivity, a carrier concentration). Give a
  Japanese name and an English name, and "aliases": column names a data file would plausibly
  use for it. Keep the list short. "key" is a short id you choose (c1, c2, ...), unique in
  this object; relations and answers refer to concepts by key.
- "use": "existing:<slug>" / "standard:<curie>" ONLY when the name below says it is that
  exact term; otherwise "new". Copy slugs and curies verbatim from the lists.
  NEVER invent a slug, a curie or an IRI.
- "relations" (every one has a one-line "reason"):
  "sub" = from is a kind of to (both classes, or both properties); "eq" = they are the same;
  "quantity" = the property from measures the standard quantity kind to (a curie);
  "part" = from is a part of to (both classes or both properties: "lattice thermal
  conductivity" is part of "thermal conductivity"); "lowers" / "raises" / "affects" =
  from lowers / raises / changes to — to MUST be a property; from may be a class or a
  property ("grain boundary scattering" lowers "lattice thermal conductivity").
  "to" is a key, or a curie of a standard term.
- "answers": for EVERY question, exactly one entry saying how it is answered:
  mode "knowledge": op "factors" (with direction lowers / raises / affects: what lowers /
  raises / changes the target), "parts" (what the target is made of) or "kinds" (kinds of
  the target); "target" is the key of the concept the question is about.
  mode "data": op "count" (how many of a kind: give "kind"), "range" (min and max of a
  property: give "property"), "top" (the one with the largest value: give "kind" and
  "property"), "filter" (the kind whose property contains / equals a word that is WRITTEN IN
  THE QUESTION: give "kind", "property", "value" = that word copied from the question, and
  "match" contains or exact), "group" (count a kind per value of a property: give "kind" and
  "property"), "trend" (one property against another over a kind, in order of the first:
  give "kind", "property" = the x axis and "property2" = the y axis) or "join" (a kind
  followed one step through a property to another kind: give "kind" = where it starts,
  "property" = the link, "kind2" = where it ends). Choose only keys of concepts you drafted;
  never invent a value for "value" that the question does not contain.
  mode "memo": anything else (op null).
  "wanted_data": ONE line saying which table of real data would answer the question with
  values (for example: a table of grain size and thermal conductivity per sample).
- "suggested_questions": at most 3 further questions the same concepts could answer.
"""

_RULES = """\

Hard rules:
- Output ONLY the JSON object. No prose, no markdown fence.
- Do NOT write SPARQL, IRIs, URLs, code or anything between < > or { }. Names only.
- Do not use any value from a data file: you have none.
- When asked for the kinds or a list of X, give the subtypes or examples of X.
  Do not mix in a related but different classification of the same field.
  Do not invent relations; omit a relation you are not sure of.
"""

THEME_DRAFT_SYSTEM_PROMPT = (
    """\
You help a person turn the QUESTIONS they want answered into a draft KNOWLEDGE MAP.
You never see their data. You are given: (1) their questions, (2) the names of terms
that already exist, (3) the names of standard terms. Fill ONE JSON object:

"""
    + _SHAPE
    + """

Be a domain expert, not a noun extractor. Whenever a question asks about FACTORS ("what
lowers / raises / changes X?", "which factors affect X?", "what makes X small?") or about
PARTS ("what is X made of?"), draft the domain KNOWLEDGE that answers it as concepts and
relations: the parts of X (for thermal conductivity: the lattice and the electronic
contributions), the mechanisms and quantities that act on them (scattering by grain
boundaries, by point defects, carrier concentration, ...), each with the relation
(part / lowers / raises / affects) and a one-line reason. Do not stop at the nouns that
appear in the question.

"""
    + _MEANING
    + _RULES
)

THEME_EXPAND_SYSTEM_PROMPT = (
    """\
You help a person GROW a draft knowledge map around ONE concept. You never see their data.
You are given: the questions, the concepts already on the map (key | Japanese | English |
kind), the ONE concept to expand (by key), and the names of existing terms and standard
terms. Return ONLY what is NEW, as the same JSON object:

"""
    + _SHAPE
    + """

Be a domain expert: add the factors, parts, mechanisms or kinds around the chosen concept
that the map does not have yet, with relations (part / lowers / raises / affects / sub)
and a one-line reason each. At most 10 new concepts and 20 relations. "answers" and
"suggested_questions" stay empty lists; "theme_label" stays empty.

- New keys must NOT repeat any key already on the map (start them with "n": n1, n2, ...).
- A relation may point from or to a concept already on the map: use its key as given.
- Do not repeat a concept that is already on the map under another name.

"""
    + _MEANING
    + _RULES
)


def _string(max_length: int, *, nullable: bool = False) -> dict:
    t: dict[str, Any] = {"type": ["string", "null"] if nullable else "string"}
    t["maxLength"] = max_length
    return t


def theme_draft_json_schema(*, expand: bool = False) -> dict:
    """The closed shape, as a JSON Schema for guided-decoding servers. Bounded strings so
    a repetition loop cannot eat the completion; no free-form object anywhere."""
    max_concepts = _MAX_EXPAND_CONCEPTS if expand else _MAX_CONCEPTS
    max_relations = _MAX_EXPAND_RELATIONS if expand else _MAX_RELATIONS
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["theme_label", "concepts", "relations", "answers", "suggested_questions"],
        "properties": {
            "theme_label": _string(_LEN_THEME_LABEL),
            "concepts": {
                "type": "array",
                "maxItems": max_concepts,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "label_ja", "label_en", "aliases", "kind", "use", "reason"],
                    "properties": {
                        "key": _string(_LEN_KEY),
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
                "maxItems": max_relations,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["from", "rel", "to", "reason"],
                    "properties": {
                        "from": _string(_LEN_LABEL),
                        "rel": {"type": "string", "enum": list(RELATIONS)},
                        "to": _string(_LEN_LABEL),
                        "reason": _string(_LEN_REASON),
                    },
                },
            },
            "answers": {
                "type": "array",
                "maxItems": 0 if expand else _MAX_ANSWERS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "question",
                        "mode",
                        "op",
                        "target",
                        "direction",
                        "kind",
                        "property",
                        "value",
                        "match",
                        "property2",
                        "kind2",
                        "wanted_data",
                        "reason",
                    ],
                    "properties": {
                        "question": _string(_LEN_QUESTION),
                        "mode": {"type": "string", "enum": list(ANSWER_MODES)},
                        "op": {
                            "type": ["string", "null"],
                            "enum": [*KNOWLEDGE_OPS, *DATA_OPS, *_MEMO_OPS, None],
                        },
                        "target": _string(_LEN_KEY, nullable=True),
                        "direction": {"type": ["string", "null"], "enum": [*DIRECTIONS, None]},
                        "kind": _string(_LEN_KEY, nullable=True),
                        "property": _string(_LEN_KEY, nullable=True),
                        "value": _string(_LEN_VALUE, nullable=True),
                        "match": {"type": ["string", "null"], "enum": [*MATCH_MODES, None]},
                        "property2": _string(_LEN_KEY, nullable=True),
                        "kind2": _string(_LEN_KEY, nullable=True),
                        "wanted_data": _string(_LEN_REASON),
                        "reason": _string(_LEN_REASON),
                    },
                },
            },
            "suggested_questions": {
                "type": "array",
                "maxItems": 0 if expand else _MAX_SUGGESTED,
                "items": _string(_LEN_QUESTION),
            },
        },
    }


# ---------------------------------------------------------------------------
# The strict gate (every provider)
# ---------------------------------------------------------------------------


def _fail(path: str, why: str) -> ThemeDraftError:
    return ThemeDraftError(f"{path}: {why}")


def _str(
    v: Any,
    path: str,
    max_len: int,
    *,
    nullable: bool = False,
    allow_code: bool = False,
    sentence: bool = False,
):
    """A string. ``sentence=True`` = a question / theme-name sentence: only IRIs and code
    symbols are refused (``_QUESTION_LIKE``), not SQL words."""
    if v is None and nullable:
        return None
    if not isinstance(v, str):
        raise _fail(path, "must be a string")
    s = " ".join(v.split())
    if len(s) > max_len:
        raise _fail(path, f"longer than {max_len} characters")
    if not allow_code and (_QUESTION_LIKE if sentence else _CODE_LIKE).search(s):
        raise _fail(path, "looks like code or an IRI (names only)")
    return s


def _cut(v: Any, path: str, max_len: int) -> str:
    """A prose line (``reason`` / ``wanted_data``): LENGTH is the only check — folded to one
    line and cut at ``max_len``, never refused. ``None`` is an empty line."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise _fail(path, "must be a string")
    return " ".join(v.split())[:max_len]


def _opt(v: Any, path: str, max_len: int) -> str | None:
    """A reference (a key or a curie): a string, or ``None`` / empty for nothing."""
    s = _str(v, path, max_len, nullable=True)
    return s or None


def _list(v: Any, path: str, max_items: int) -> list:
    if not isinstance(v, list):
        raise _fail(path, "must be a list")
    if len(v) > max_items:
        raise _fail(path, f"more than {max_items} items")
    return v


def _concept(t: Any, p: str, seen_keys: set[str]) -> dict:
    if not isinstance(t, dict):
        raise _fail(p, "must be an object")
    key = t.get("key")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise _fail(f"{p}.key", "must be a short id like c1 (letters, digits, underscore)")
    if key in seen_keys:
        raise _fail(f"{p}.key", f"{key!r} is used twice")
    seen_keys.add(key)
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
    return {
        "key": key,
        "label_ja": label_ja,
        "label_en": _str(t.get("label_en", ""), f"{p}.label_en", _LEN_LABEL),
        "aliases": [a for a in aliases if a],
        "kind": kind,
        "use": use,
        "reason": _cut(t.get("reason", ""), f"{p}.reason", _LEN_REASON),
    }


def _relation(r: Any, p: str) -> dict:
    if not isinstance(r, dict):
        raise _fail(p, "must be an object")
    if r.get("rel") not in RELATIONS:
        raise _fail(f"{p}.rel", f"must be one of {RELATIONS}")
    frm = _str(r.get("from"), f"{p}.from", _LEN_LABEL)
    # 標準の語は curie（``prefix:name``）で書く。``://`` は禁止のまま。
    to = _str(r.get("to"), f"{p}.to", _LEN_LABEL)
    if not frm or not to:
        raise _fail(p, "from and to are required")
    return {
        "from": frm,
        "rel": r["rel"],
        "to": to,
        "reason": _cut(r.get("reason", ""), f"{p}.reason", _LEN_REASON),
    }


def _answer(a: Any, p: str) -> dict:
    if not isinstance(a, dict):
        raise _fail(p, "must be an object")
    mode = a.get("mode")
    if mode not in ANSWER_MODES:
        raise _fail(f"{p}.mode", f"must be one of {ANSWER_MODES}")
    op = a.get("op") or None
    if op is not None and op not in (*KNOWLEDGE_OPS, *DATA_OPS, *_MEMO_OPS):
        raise _fail(f"{p}.op", f"must be one of {(*KNOWLEDGE_OPS, *DATA_OPS, *_MEMO_OPS)}")
    direction = a.get("direction") or None
    if direction is not None and direction not in DIRECTIONS:
        raise _fail(f"{p}.direction", f"must be one of {DIRECTIONS}")
    match = a.get("match") or None
    if match is not None and match not in MATCH_MODES:
        raise _fail(f"{p}.match", f"must be one of {MATCH_MODES}")
    if op in _MEMO_OPS:  # 受けるが実行しない — 検証は通し、メモに落とす
        mode = "memo"
    # 値は問いの文に書かれていた語（データの値ではない）。コード・IRI・長すぎる値は、下書き全体
    # を拒まず、その問いの値を空にしてメモに落とす（ほかの欄の不足と同じ扱い）
    try:
        value = _opt(a.get("value"), f"{p}.value", _LEN_VALUE)
    except ThemeDraftError:
        value = None
        if op == "filter" and mode == "data":
            mode = "memo"
    return {
        # 問いの原文はユーザーの文（記号を含みうる）— 照合は原文との一致で行う。
        "question": _str(a.get("question"), f"{p}.question", _LEN_QUESTION, allow_code=True),
        "mode": mode,
        "op": op,
        "target": _opt(a.get("target"), f"{p}.target", _LEN_KEY),
        "direction": direction,
        "kind": _opt(a.get("kind"), f"{p}.kind", _LEN_KEY),
        "property": _opt(a.get("property"), f"{p}.property", _LEN_KEY),
        "value": value,
        "match": match,
        "property2": _opt(a.get("property2"), f"{p}.property2", _LEN_KEY),
        "kind2": _opt(a.get("kind2"), f"{p}.kind2", _LEN_KEY),
        "wanted_data": _cut(a.get("wanted_data", ""), f"{p}.wanted_data", _LEN_REASON),
        "reason": _cut(a.get("reason", ""), f"{p}.reason", _LEN_REASON),
    }


def validate_theme_draft(
    raw: Any, *, expand: bool = False, existing_keys: Iterable[str] = ()
) -> dict:
    """Check ``raw`` against the closed shape (v2) and return a normalised copy (only the
    keys of the shape; strings whitespace-folded). Raises :class:`ThemeDraftError`.

    Unknown keys are dropped, not read — the shape has no place for a query or an IRI. A
    name / alias / question that looks like code or an IRI is refused outright;
    ``reason`` and ``wanted_data`` are only cut to 200 characters. ``filter`` / ``group`` /
    ``trend`` / ``join`` are accepted and folded into ``mode="memo"`` (they cannot be run
    yet). With ``expand=True`` the limits are those of an addition (10 concepts, 20
    relations) and no answers are taken; a concept key that ``existing_keys`` (the keys
    already on the map) already uses is refused — an addition never overwrites a row."""
    if not isinstance(raw, dict):
        raise _fail("$", "must be an object")
    max_concepts = _MAX_EXPAND_CONCEPTS if expand else _MAX_CONCEPTS
    max_relations = _MAX_EXPAND_RELATIONS if expand else _MAX_RELATIONS
    seen_keys: set[str] = set(existing_keys) if expand else set()
    concepts = [
        _concept(t, f"concepts[{i}]", seen_keys)
        for i, t in enumerate(_list(raw.get("concepts", []), "concepts", max_concepts))
    ]
    relations = [
        _relation(r, f"relations[{i}]")
        for i, r in enumerate(_list(raw.get("relations", []), "relations", max_relations))
    ]
    answers = (
        []
        if expand
        else [
            _answer(a, f"answers[{i}]")
            for i, a in enumerate(_list(raw.get("answers", []), "answers", _MAX_ANSWERS))
        ]
    )
    suggested: list[str] = []
    if not expand:
        for i, q in enumerate(
            _list(raw.get("suggested_questions", []), "suggested_questions", _MAX_SUGGESTED)
        ):
            s = _str(q, f"suggested_questions[{i}]", _LEN_QUESTION, sentence=True)
            if s:
                suggested.append(s)
    label = _cut(raw.get("theme_label", ""), "theme_label", _LEN_THEME_LABEL)
    if _QUESTION_LIKE.search(label):
        raise _fail("theme_label", "looks like code or an IRI (names only)")
    return {
        "theme_label": "" if expand else label,
        "concepts": concepts,
        "relations": relations,
        "answers": answers,
        "suggested_questions": suggested,
    }


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


def _names_blocks(known_terms: list[dict], known_standards: list[dict]) -> list[str]:
    terms = [
        f"{t.get('slug')} | {t.get('label_ja') or ''} | {t.get('label_en') or ''} | {t.get('kind')}"
        for t in known_terms
    ]
    stds = [
        f"{s.get('curie')} | {s.get('label') or ''} | {s.get('kind') or ''}"
        for s in known_standards
    ]
    return [
        "# Existing terms (slug | 日本語 | English | kind)\n" + _lines(terms),
        "# Standard terms (curie | label | kind)\n" + _lines(stds),
    ]


def _language_tail(language: str | None) -> str:
    lang = language_instruction(language)
    if not lang:
        return ""
    return (
        f"\n\n{lang}\n\nIn THIS JSON the human-readable prose means the \"reason\" and"
        ' "wanted_data" values: write those in the language above. "label_ja" is Japanese and'
        ' "label_en" is English whatever the language above says; keys, slugs, curies and'
        " question texts are copied verbatim.\n"
    )


def _problem_tail(problem: str | None) -> str:
    if not problem:
        return ""
    return (
        f"\n\nYour previous answer was refused: {problem}\n"
        "Answer again with the JSON object only.\n"
    )


def theme_draft_user_message(
    questions: list[str],
    known_terms: list[dict],
    known_standards: list[dict],
    *,
    language: str | None = None,
    problem: str | None = None,
    label: str | None = None,
) -> str:
    """The user message: question sentences + term / standard NAMES only (never data).
    ``label`` is the theme name the person gave (when empty, the model names the theme in
    ``theme_label``). ``problem`` is the previous attempt's validation error, for the one
    retry."""
    named = " ".join((label or "").split())
    head = (
        f"# Theme name (given by the person; leave theme_label empty)\n{named}"
        if named
        else "# Theme name\n(none: fill theme_label with a short name, 20 characters at most)"
    )
    parts = [
        head,
        "# Questions (one per line)\n" + _lines(questions),
        *_names_blocks(known_terms, known_standards),
    ]
    return "\n\n".join(parts) + _language_tail(language) + _problem_tail(problem)


def theme_expand_user_message(
    focus_key: str,
    concepts: list[dict],
    questions: list[str],
    known_terms: list[dict],
    known_standards: list[dict],
    *,
    language: str | None = None,
    problem: str | None = None,
) -> str:
    """The user message of "grow this place": the names + keys of the concepts already on
    the map, the one to expand, the questions, term / standard names. Never data."""
    rows = [
        f"{c.get('key')} | {c.get('label_ja') or ''} | {c.get('label_en') or ''} | {c.get('kind')}"
        for c in concepts
    ]
    parts = [
        "# Concept to expand (key)\n" + focus_key,
        "# Concepts already on the map (key | 日本語 | English | kind)\n" + _lines(rows),
        "# Questions (one per line)\n" + _lines(questions),
        *_names_blocks(known_terms, known_standards),
    ]
    return "\n\n".join(parts) + _language_tail(language) + _problem_tail(problem)


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
    label: str | None = None,
) -> dict:
    """One guided call (one retry when the shape is refused) → the validated closed JSON
    (v2: ``theme_label`` / ``concepts`` / ``relations`` / ``answers`` /
    ``suggested_questions``).

    Raises :class:`ThemeDraftError` when the second answer is also off-shape. Provider
    errors propagate (the api maps them to 502)."""
    schema = theme_draft_json_schema()
    problem: str | None = None
    last: ThemeDraftError | None = None
    for _attempt in range(2):
        user = theme_draft_user_message(
            questions,
            known_terms,
            known_standards,
            language=language,
            problem=problem,
            label=label,
        )
        text = _complete_guided(llm, THEME_DRAFT_SYSTEM_PROMPT, user, schema)
        try:
            return validate_theme_draft(_extract_json_object(text))
        except ThemeDraftError as exc:
            last, problem = exc, str(exc)
    assert last is not None
    raise last


def propose_theme_expand(
    llm: LLMClient,
    *,
    focus_key: str,
    concepts: list[dict],
    questions: list[str],
    existing: list[dict],
    standards: list[dict],
    language: str | None = None,
) -> dict:
    """「ここを広げる」— the ADDITION around one concept (at most 10 concepts and 20
    relations), validated with the same gate (``expand=True``: no answers). ``concepts``
    are the rows already on the map (``key`` / ``label_ja`` / ``label_en`` / ``kind``);
    ``existing`` / ``standards`` are term / standard NAMES. A relation may point at a key
    already on the map. Never sees data. One retry, like :func:`propose_theme_draft`."""
    if not any(c.get("key") == focus_key for c in concepts):
        raise ThemeDraftError(f"focus_key {focus_key!r} is not a concept on the map")
    schema = theme_draft_json_schema(expand=True)
    problem: str | None = None
    last: ThemeDraftError | None = None
    for _attempt in range(2):
        user = theme_expand_user_message(
            focus_key,
            concepts,
            questions,
            existing,
            standards,
            language=language,
            problem=problem,
        )
        text = _complete_guided(llm, THEME_EXPAND_SYSTEM_PROMPT, user, schema)
        try:
            return validate_theme_draft(
                _extract_json_object(text),
                expand=True,
                existing_keys=[str(c.get("key")) for c in concepts],
            )
        except ThemeDraftError as exc:
            last, problem = exc, str(exc)
    assert last is not None
    raise last
