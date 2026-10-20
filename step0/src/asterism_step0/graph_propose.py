"""AI diff of a knowledge graph — one turn of the "graph on the left, conversation on the
right" workspace (ui-redesign PR 7a・design.md §16.1 / §16.2).

A person says one thing ("散乱因子は格子熱伝導率に紐づくはず", "本当に同じもの？") and the model
answers with a CLOSED DIFF against the graph on screen: at most 30 operations out of seven
(``add_concept`` / ``add_relation`` / ``retarget`` / ``remove`` / ``rename`` / ``add_question`` /
``bind_column``), each with a one-line reason, plus one to three sentences of prose (``reply``)
that are shown and never stored. It is the same discipline as :mod:`asterism_step0.theme_propose`:

* the model never writes SPARQL, an IRI or code (the shape has no place for them, and a name /
  alias / question / column name that looks like one is refused);
* the model never sees data — only names: the concepts (key, labels, kind, use) and relations on
  the graph, the questions, the names of existing and standard terms, and (for a dataset) the
  column HEADINGS with a type hint;
* nothing is decided here. The diff is a proposal; a person says "yes" and a deterministic apply
  step (``asterism.graph_diff``) checks kinds, cycles and dismissals before anything is written.

The shape is enforced twice, like the theme draft: :func:`graph_diff_json_schema` for
guided-decoding servers and :func:`validate_graph_diff` for every provider (the strict gate).
``reason`` / ``wanted_data`` / ``reply`` are cut to length, never refused. A refused answer is
asked for once more with the reason named; a second refusal raises :class:`GraphDiffError`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from asterism_step0.llm import LLMClient
from asterism_step0.theme_propose import (
    _CODE_LIKE,
    _USE_RE,
    ANSWER_MODES,
    DATA_OPS,
    DIRECTIONS,
    KNOWLEDGE_OPS,
    RELATIONS,
    TERM_KINDS,
    ThemeDraftError,
    _complete_guided,
    _cut,
    _extract_json_object,
    _language_tail,
    _lines,
    _names_blocks,
    _problem_tail,
)

__all__ = [
    "BIND_RELATIONS",
    "GRAPH_DIFF_SYSTEM_PROMPT",
    "OP_NAMES",
    "SCREENS",
    "GraphDiffError",
    "filter_ops_for_screen",
    "graph_diff_json_schema",
    "graph_diff_user_message",
    "propose_graph_diff",
    "validate_graph_diff",
]

SCREENS = ("theme", "home", "dataset")
OP_NAMES = (
    "add_concept",
    "add_relation",
    "retarget",
    "remove",
    "rename",
    "add_question",
    "bind_column",
)
#: ``expand=True`` — only ops that ADD to the graph (nothing is moved or taken away).
_ADDITIVE_OPS = ("add_concept", "add_relation", "add_question")
#: a column is tied to a concept by "same" / "a kind of" / "its quantity is".
BIND_RELATIONS = ("eq", "sub", "quantity")
_QUESTION_OPS = (*KNOWLEDGE_OPS, *DATA_OPS)
_REMOVE_TARGETS = ("concept", "relation")

_MAX_OPS = 30
_MAX_EXPAND_OPS = 10
_MAX_MESSAGES = 12
_LEN_REPLY = 600
_LEN_REASON = 200
_LEN_LABEL = 80
_LEN_ALIAS = 60
_LEN_REF = 80
_LEN_KEY = 40  # a home key is a term slug (up to 40 characters)
_LEN_QUESTION = 300
_LEN_COLUMN = 120
_LEN_MESSAGE = 2000
_MAX_ALIASES = 12
_MAX_GRAPH_ROWS = 300  # concepts / relations the prompt carries (it is names only, but bounded)

_KEY_RE = re.compile(rf"^[A-Za-z][A-Za-z0-9_]{{0,{_LEN_KEY - 1}}}\Z")
_CURIE_RE = re.compile(r"^[A-Za-z][\w.\-]*:[\w.\-]+\Z")


class GraphDiffError(ValueError):
    """The model's output is not the closed diff (the caller retried once; the api maps it to
    502)."""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SHAPE = """\
{"reply": "<one to three sentences for the person; the reason, not the diff>",
 "ops": [
  {"op": "add_concept", "key": "n1", "label_ja": "...", "label_en": "...", "aliases": ["..."],
   "kind": "class|property", "use": "new|existing:<slug>|standard:<curie>", "reason": "..."},
  {"op": "add_relation", "from": "<key>", "rel": "sub|eq|quantity|part|lowers|raises|affects",
   "to": "<key or curie>", "reason": "..."},
  {"op": "retarget", "from": "<key>", "rel": "<same as the relation on the graph>",
   "to": "<its current to>", "new_to": "<key or curie>", "reason": "..."},
  {"op": "remove", "target": "concept", "key": "<key>", "reason": "..."},
  {"op": "remove", "target": "relation", "from": "<key>", "rel": "...", "to": "<key or curie>",
   "reason": "..."},
  {"op": "rename", "key": "<key>", "label_ja": "...", "label_en": "...", "aliases": ["..."],
   "reason": "..."},
  {"op": "add_question", "question": "<a sentence>", "mode": "knowledge|data",
   "op_kind": "factors|parts|kinds|count|range|top", "target": "<key> or null",
   "direction": "lowers|raises|affects or null", "kind": "<key of a class> or null",
   "property": "<key of a property> or null", "wanted_data": "...", "reason": "..."},
  {"op": "bind_column", "column": "<a column heading>", "to": "<key or curie>",
   "rel": "eq|sub|quantity", "reason": "..."}
 ]}"""

_MEANING = """\
Meaning:
- The graph is made of CONCEPTS (a CLASS = a kind of thing; a PROPERTY = something said about
  a thing) and RELATIONS between them. Every concept has a "key" (c1, c2, ... or a term slug);
  every operation refers to concepts by key. A new concept gets a NEW key (n1, n2, ...) that is
  not on the graph already, and must be added with "add_concept" BEFORE a relation uses it.
- Relations: "sub" = from is a kind of to (same kind of concept); "eq" = the same thing;
  "quantity" = the property from measures the standard quantity kind to (a curie);
  "part" = from is a part of to (same kind of concept); "lowers" / "raises" / "affects" = from
  lowers / raises / changes to; to MUST be a property, from may be a class or a property.
  "to" is a key, or a curie taken verbatim from the standard-term list.
- "use": "existing:<slug>" / "standard:<curie>" ONLY when the name below says it is that exact
  term; otherwise "new". Copy slugs and curies verbatim. NEVER invent a slug, a curie or an IRI.
- "retarget" moves an existing relation: "from", "rel" and "to" name the relation as it is on
  the graph now; "new_to" is where it should point. Use it when the person says a line is
  attached to the wrong concept; do not use remove + add_relation for that.
- "remove" takes a concept (its relations go with it) or one relation away.
- "add_question": a question the graph should answer. mode "knowledge": op "factors" (with
  direction: what lowers / raises / changes the target), "parts" or "kinds"; "target" is a key.
  mode "data": op "count" (give "kind"), "range" or "top" (give "property"). Unused fields: null.
- Every operation has a one-line "reason" a person can check.
- When the person asks "is it true?" or says "I think it is ...", answer in "reply" in one to
  three sentences with the grounds; add operations only when the graph should change. An empty
  "ops" is a good answer to a question that needs no change.
- At most 30 operations. Prefer the fewest that do what was asked.
"""

_SCREEN_NOTES = {
    "theme": (
        "The graph is a draft KNOWLEDGE MAP built from the person's questions (the factors,\n"
        "parts and kinds that answer them). All seven operations are allowed except\n"
        '"bind_column".\n'
    ),
    "home": (
        "The graph is the set of PUBLISHED terms (key = the term's slug) with the relations the\n"
        'people decided. You may use "add_relation" and "remove" (a relation) ONLY. Never add a\n'
        "concept; if a relation is wrong, remove it and add the right one. Both ends of a\n"
        "relation are keys on the graph (or, for the object, a standard curie).\n"
    ),
    "dataset": (
        "You are given the COLUMN HEADINGS of one dataset (with a type hint) and the published\n"
        'terms (key = slug). Use "bind_column" ONLY: say which term (a key from the list, or a\n'
        "standard curie) a column means: rel eq (the column IS that term), sub (a kind of it) or\n"
        "quantity (its quantity kind). You never see the values of a column. Do not add concepts.\n"
    ),
}

_RULES = """\

Hard rules:
- Output ONLY the JSON object. No prose outside it, no markdown fence.
- Do NOT write SPARQL, IRIs, URLs, code or anything between < > or { }. Names only.
- Do not use any value from a data file: you have none.
- A key that is not on the graph (and not added by "add_concept" in the same answer) is wrong.
"""

GRAPH_DIFF_SYSTEM_PROMPT = (
    """\
You are the partner of a person who is growing a knowledge graph while talking. Each turn you
return ONLY a closed DIFF against the graph on their screen, as one JSON object:

"""
    + _SHAPE
    + "\n\n"
    + _MEANING
    + _RULES
)


def _system_prompt(screen: str) -> str:
    return GRAPH_DIFF_SYSTEM_PROMPT + "\n" + _SCREEN_NOTES[screen]


def _string(max_length: int, *, nullable: bool = False) -> dict:
    return {"type": ["string", "null"] if nullable else "string", "maxLength": max_length}


def graph_diff_json_schema(*, expand: bool = False) -> dict:
    """The closed shape as a JSON Schema for guided-decoding servers. Flat on purpose (one
    object type with nullable fields; ``oneOf`` is poorly supported by guided decoding) —
    :func:`validate_graph_diff` is the per-operation gate. Bounded strings, no free-form
    object."""
    ops = [*_ADDITIVE_OPS] if expand else [*OP_NAMES]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply", "ops"],
        "properties": {
            "reply": _string(_LEN_REPLY),
            "ops": {
                "type": "array",
                "maxItems": _MAX_EXPAND_OPS if expand else _MAX_OPS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op", "reason"],
                    "properties": {
                        "op": {"type": "string", "enum": ops},
                        "reason": _string(_LEN_REASON),
                        "key": _string(_LEN_KEY, nullable=True),
                        "label_ja": _string(_LEN_LABEL, nullable=True),
                        "label_en": _string(_LEN_LABEL, nullable=True),
                        "aliases": {
                            "type": ["array", "null"],
                            "maxItems": _MAX_ALIASES,
                            "items": _string(_LEN_ALIAS),
                        },
                        "kind": _string(_LEN_KEY, nullable=True),
                        "use": _string(_LEN_REF, nullable=True),
                        "from": _string(_LEN_KEY, nullable=True),
                        "rel": {"type": ["string", "null"], "enum": [*RELATIONS, None]},
                        "to": _string(_LEN_REF, nullable=True),
                        "new_to": _string(_LEN_REF, nullable=True),
                        "target": _string(_LEN_KEY, nullable=True),
                        "question": _string(_LEN_QUESTION, nullable=True),
                        "mode": {"type": ["string", "null"], "enum": [*ANSWER_MODES[:2], None]},
                        "op_kind": {"type": ["string", "null"], "enum": [*_QUESTION_OPS, None]},
                        "direction": {"type": ["string", "null"], "enum": [*DIRECTIONS, None]},
                        "property": _string(_LEN_KEY, nullable=True),
                        "wanted_data": _string(_LEN_REASON, nullable=True),
                        "column": _string(_LEN_COLUMN, nullable=True),
                    },
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# The strict gate (every provider)
# ---------------------------------------------------------------------------


def _fail(path: str, why: str) -> GraphDiffError:
    return GraphDiffError(f"{path}: {why}")


def _name(v: Any, path: str, max_len: int, *, required: bool = True) -> str:
    """A name the person will read (label / alias / column / question): a string, whitespace
    folded, with no code or IRI in it."""
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        raise _fail(path, "must be a string")
    s = " ".join(v.split())
    if len(s) > max_len:
        raise _fail(path, f"longer than {max_len} characters")
    if _CODE_LIKE.search(s):
        raise _fail(path, "looks like code or an IRI (names only)")
    if required and not s:
        raise _fail(path, "is empty")
    return s


def _ref(v: Any, path: str, keys: set[str], *, curie_ok: bool) -> str:
    """A reference: a key on the graph (or added in this diff), or — where a standard term is
    allowed — a curie of the closed list. Anything else is refused (the retry names it)."""
    if not isinstance(v, str) or not v.strip():
        raise _fail(path, "is required")
    s = v.strip()
    if len(s) > _LEN_REF or _CODE_LIKE.search(s):
        raise _fail(path, "looks like code or an IRI (a key or a curie only)")
    if s in keys:
        return s
    if curie_ok and _CURIE_RE.match(s):
        return s
    raise _fail(path, f"{s!r} is not a key on the graph (add the concept first)")


def _opt_ref(v: Any, path: str, keys: set[str]) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return _ref(v, path, keys, curie_ok=False)


def _rel(v: Any, path: str, allowed: tuple[str, ...] = RELATIONS) -> str:
    if v not in allowed:
        raise _fail(path, f"must be one of {allowed}")
    return v


def _aliases(v: Any, path: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise _fail(path, "must be a list")
    if len(v) > _MAX_ALIASES:
        raise _fail(path, f"more than {_MAX_ALIASES} items")
    out = [_name(a, f"{path}[{j}]", _LEN_ALIAS) for j, a in enumerate(v)]
    return [a for a in out if a]


def _op(o: Any, p: str, keys: set[str], new_keys: set[str]) -> dict:
    if not isinstance(o, dict):
        raise _fail(p, "must be an object")
    name = o.get("op")
    if name not in OP_NAMES:
        raise _fail(f"{p}.op", f"must be one of {OP_NAMES}")
    reason = _cut(o.get("reason", ""), f"{p}.reason", _LEN_REASON)
    if name == "add_concept":
        kind = o.get("kind")
        if kind not in TERM_KINDS:
            raise _fail(f"{p}.kind", f"must be one of {TERM_KINDS}")
        use = " ".join(str(o.get("use") or "new").split())
        if not _USE_RE.match(use):
            raise _fail(f"{p}.use", "must be new, existing:<slug> or standard:<curie>")
        return {
            "op": name,
            "key": o["key"],  # checked in the first pass
            "label_ja": _name(o.get("label_ja"), f"{p}.label_ja", _LEN_LABEL),
            "label_en": _name(o.get("label_en"), f"{p}.label_en", _LEN_LABEL, required=False),
            "aliases": _aliases(o.get("aliases"), f"{p}.aliases"),
            "kind": kind,
            "use": use,
            "reason": reason,
        }
    if name == "add_relation":
        return {
            "op": name,
            "from": _ref(o.get("from"), f"{p}.from", keys, curie_ok=False),
            "rel": _rel(o.get("rel"), f"{p}.rel"),
            "to": _ref(o.get("to"), f"{p}.to", keys, curie_ok=True),
            "reason": reason,
        }
    if name == "retarget":
        return {
            "op": name,
            "from": _ref(o.get("from"), f"{p}.from", keys, curie_ok=False),
            "rel": _rel(o.get("rel"), f"{p}.rel"),
            "to": _ref(o.get("to"), f"{p}.to", keys, curie_ok=True),
            "new_to": _ref(o.get("new_to"), f"{p}.new_to", keys, curie_ok=True),
            "reason": reason,
        }
    if name == "remove":
        target = o.get("target")
        if target not in _REMOVE_TARGETS:
            raise _fail(f"{p}.target", f"must be one of {_REMOVE_TARGETS}")
        if target == "concept":
            return {
                "op": name,
                "target": target,
                "key": _ref(o.get("key"), f"{p}.key", keys, curie_ok=False),
                "reason": reason,
            }
        return {
            "op": name,
            "target": target,
            "from": _ref(o.get("from"), f"{p}.from", keys, curie_ok=False),
            "rel": _rel(o.get("rel"), f"{p}.rel"),
            "to": _ref(o.get("to"), f"{p}.to", keys, curie_ok=True),
            "reason": reason,
        }
    if name == "rename":
        return {
            "op": name,
            "key": _ref(o.get("key"), f"{p}.key", keys, curie_ok=False),
            "label_ja": _name(o.get("label_ja"), f"{p}.label_ja", _LEN_LABEL),
            "label_en": _name(o.get("label_en"), f"{p}.label_en", _LEN_LABEL, required=False),
            "aliases": _aliases(o.get("aliases"), f"{p}.aliases"),
            "reason": reason,
        }
    if name == "add_question":
        mode = o.get("mode")
        if mode not in ANSWER_MODES[:2]:
            raise _fail(f"{p}.mode", f"must be one of {ANSWER_MODES[:2]}")
        op_kind = o.get("op_kind")
        if op_kind not in _QUESTION_OPS:
            raise _fail(f"{p}.op_kind", f"must be one of {_QUESTION_OPS}")
        direction = o.get("direction") or None
        if direction is not None and direction not in DIRECTIONS:
            raise _fail(f"{p}.direction", f"must be one of {DIRECTIONS}")
        return {
            "op": name,
            "question": _name(o.get("question"), f"{p}.question", _LEN_QUESTION),
            "mode": mode,
            "op_kind": op_kind,
            "target": _opt_ref(o.get("target"), f"{p}.target", keys),
            "direction": direction,
            "kind": _opt_ref(o.get("kind"), f"{p}.kind", keys),
            "property": _opt_ref(o.get("property"), f"{p}.property", keys),
            "wanted_data": _cut(o.get("wanted_data", ""), f"{p}.wanted_data", _LEN_REASON),
            "reason": reason,
        }
    # bind_column
    return {
        "op": name,
        "column": _name(o.get("column"), f"{p}.column", _LEN_COLUMN),
        "to": _ref(o.get("to"), f"{p}.to", keys, curie_ok=True),
        "rel": _rel(o.get("rel"), f"{p}.rel", BIND_RELATIONS),
        "reason": reason,
    }


def validate_graph_diff(
    raw: Any,
    *,
    known_keys: Iterable[str],
    expand: bool = False,
    allow_taken_keys: bool = False,
) -> dict:
    """Check ``raw`` against the closed diff and return a normalised copy ``{reply, ops}``
    (only the fields of each operation; strings whitespace-folded). Raises
    :class:`GraphDiffError`.

    ``known_keys`` = the keys on the graph the person sees. A reference outside them (and
    outside the keys ``add_concept`` adds in this very diff) is refused — a new concept must be
    added first — and an ``add_concept`` key that is already taken is refused (an addition never
    overwrites a row). Names, aliases, questions and column headings that look like code or an
    IRI are refused outright; ``reason`` / ``wanted_data`` / ``reply`` are only cut. A ``to`` /
    ``new_to`` that is not a key may be a curie (``prefix:name``) — whether it is really in the
    closed list of standard terms is the apply step's check. With ``expand=True`` only the
    additive operations are accepted, at most 10. ``allow_taken_keys=True`` (the apply step
    only) lets an ``add_concept`` key that is already on the graph through: a resent diff is a
    no-op for the applier to report, not a refusal (a key repeated inside one diff is still
    refused)."""
    if not isinstance(raw, dict):
        raise _fail("$", "must be an object")
    known = {str(k) for k in known_keys}
    ops_raw = raw.get("ops", [])
    if not isinstance(ops_raw, list):
        raise _fail("ops", "must be a list")
    limit = _MAX_EXPAND_OPS if expand else _MAX_OPS
    if len(ops_raw) > limit:
        raise _fail("ops", f"more than {limit} operations")
    # first pass: the keys this diff adds (so an add_concept may come after its first use)
    new_keys: set[str] = set()
    for i, o in enumerate(ops_raw):
        if isinstance(o, dict) and o.get("op") == "add_concept":
            key = o.get("key")
            if not isinstance(key, str) or not _KEY_RE.match(key):
                raise _fail(f"ops[{i}].key", "must be a short id like n1 (letters, digits, _)")
            if (key in known and not allow_taken_keys) or key in new_keys:
                raise _fail(f"ops[{i}].key", f"{key!r} is already used")
            new_keys.add(key)
    keys = known | new_keys
    ops = []
    for i, o in enumerate(ops_raw):
        if expand and not (isinstance(o, dict) and o.get("op") in _ADDITIVE_OPS):
            raise _fail(f"ops[{i}].op", f"must be one of {_ADDITIVE_OPS} here")
        ops.append(_op(o, f"ops[{i}]", keys, new_keys))
    return {"reply": _cut(raw.get("reply", ""), "reply", _LEN_REPLY), "ops": ops}


def filter_ops_for_screen(ops: list[dict], screen: str) -> list[dict]:
    """Keep only the operations a screen can take. home = ``add_relation`` and ``remove`` of a
    relation; dataset = ``bind_column`` with rel ``eq`` / ``sub`` (``upper.json`` holds nothing
    else); theme = everything but ``bind_column``. The rest is not returned at all (a card the
    person could never accept is noise)."""

    def keep(op: dict) -> bool:
        name = op.get("op")
        if screen == "home":
            return name == "add_relation" or (name == "remove" and op.get("target") == "relation")
        if screen == "dataset":
            return name == "bind_column" and op.get("rel") in ("eq", "sub")
        return name != "bind_column"

    return [op for op in ops if keep(op)]


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def _concept_rows(concepts: list[dict]) -> list[str]:
    return [
        f"{c.get('key')} | {c.get('label_ja') or ''} | {c.get('label_en') or ''} | "
        f"{c.get('kind') or ''} | {c.get('use') or 'new'}"
        for c in concepts[:_MAX_GRAPH_ROWS]
    ]


def _relation_rows(relations: list[dict]) -> list[str]:
    return [
        f"{r.get('from')} -[{r.get('rel')}]-> {r.get('to')}" for r in relations[:_MAX_GRAPH_ROWS]
    ]


def _question_rows(questions: list[Any]) -> list[str]:
    rows: list[str] = []
    for q in questions[:_MAX_GRAPH_ROWS]:
        if isinstance(q, str):
            rows.append(q)
        elif isinstance(q, dict):
            text = q.get("question") or q.get("title") or ""
            how = " ".join(
                str(x)
                for x in (q.get("mode"), q.get("op"), q.get("direction"), q.get("target"))
                if x
            )
            rows.append(f"{text} [{how}]" if how else str(text))
    return rows


def graph_diff_user_message(
    *,
    screen: str,
    graph: dict,
    existing_terms: list[dict],
    standards: list[dict],
    columns: list[dict] | None,
    messages: list[dict],
    message: str,
    lang: str | None,
    problem: str | None = None,
) -> str:
    """The user message: the graph's NAMES (never data), the term / standard names, the column
    HEADINGS with a type hint (a dataset only), the recent conversation, and the person's
    latest sentence."""
    parts = [
        "# Screen\n" + screen,
        "# Concepts on the graph (key | 日本語 | English | kind | use)\n"
        + _lines(_concept_rows(graph.get("concepts") or [])),
        "# Relations on the graph (from -[rel]-> to)\n"
        + _lines(_relation_rows(graph.get("relations") or [])),
        "# Questions the graph should answer\n"
        + _lines(_question_rows(graph.get("questions") or [])),
        *_names_blocks(existing_terms, standards),
    ]
    if screen == "dataset":
        cols = [
            f"{c.get('name')} | {c.get('kind_hint') or '?'}" for c in (columns or [])[:200]
        ]
        parts.append(
            "# Column headings (heading | type hint; you never see the values)\n" + _lines(cols)
        )
    recent = [
        m
        for m in messages[-_MAX_MESSAGES:]
        if isinstance(m, dict) and str(m.get("content") or "").strip()
    ]
    if recent:
        turns = [
            f"{'Person' if m.get('role') == 'user' else 'You'}: "
            + " ".join(str(m['content']).split())[:_LEN_MESSAGE]
            for m in recent
        ]
        parts.append("# Conversation so far\n" + "\n".join(turns))
    parts.append("# The person now says\n" + " ".join(message.split())[:_LEN_MESSAGE])
    return "\n\n".join(parts) + _language_tail(lang) + _problem_tail(problem)


def propose_graph_diff(
    client: LLMClient,
    *,
    screen: str,
    graph: dict,
    existing_terms: list[dict],
    standards: list[dict],
    columns: list[dict] | None = None,
    messages: list[dict] | None = None,
    message: str,
    lang: str | None = None,
) -> dict:
    """One guided call (one retry when the shape is refused) → ``{reply, ops}`` validated, with
    the ops cut to what the screen can take (:func:`filter_ops_for_screen`).

    ``graph`` = ``{concepts[{key,label_ja,label_en,kind,use}], relations[{from,rel,to}],
    questions[…]}`` — what is on the person's screen; ``existing_terms`` = ``{slug,label_ja,
    label_en,kind}`` rows and ``standards`` = ``{curie,label,kind}`` rows (names only);
    ``columns`` = ``[{name, kind_hint}]`` (a dataset only — headings and a type hint, never a
    value); ``messages`` = the recent turns (the last 12 ride the call), ``message`` = the
    person's latest sentence. On the home and dataset screens the keys are the term slugs, so
    those count as keys on the graph.

    Raises :class:`GraphDiffError` when the second answer is also off-shape; provider errors
    propagate (the api maps them to 502)."""
    if screen not in SCREENS:
        raise GraphDiffError(f"screen must be one of {SCREENS}")
    keys = {str(c.get("key")) for c in graph.get("concepts") or []}
    if screen != "theme":
        keys |= {str(t.get("slug")) for t in existing_terms}
    schema = graph_diff_json_schema()
    system = _system_prompt(screen)
    problem: str | None = None
    last: GraphDiffError | None = None
    for _attempt in range(2):
        user = graph_diff_user_message(
            screen=screen,
            graph=graph,
            existing_terms=existing_terms,
            standards=standards,
            columns=columns,
            messages=list(messages or []),
            message=message,
            lang=lang,
            problem=problem,
        )
        text = _complete_guided(client, system, user, schema)
        try:
            diff = validate_graph_diff(_extract_json_object(text), known_keys=keys)
            return {**diff, "ops": filter_ops_for_screen(diff["ops"], screen)}
        except (GraphDiffError, ThemeDraftError) as exc:
            last = exc if isinstance(exc, GraphDiffError) else GraphDiffError(str(exc))
            problem = str(exc)
    assert last is not None
    raise last
