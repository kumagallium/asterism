"""``SubjectKey`` / ``SetSpec``: the shared vocabulary for "which thing(s) is
this card about" (object-cards-ui.md §1, handoff §5.5).

One canonical place normalizes a ``SetSpec`` and computes its ``set_id`` —
api and ui must never compute two different hashes for the same filter (§1:
"ui は set_id を api から受け取る。ui で計算しない"). Both other PR C modules
(``subject_tools.py``, ``cards_routes.py``) and PR C2's ui import this module
for the SAME normalization/validation, so a malformed ``where`` clause (or
the Phase-1-unsupported ``at`` escape hatch) is rejected in exactly one place.

No store access, no vocabulary of any single domain — this module is pure
data validation, plus (:data:`LABEL_PREDICATES` / :func:`label_union_clause` /
:func:`pick_label`) the ONE shared "which literal is THE display label"
priority rule every read path in this codebase composes into its own query
(§2: resolve/search/set_members/subject_facts/place all used to carry their
own ad-hoc predicate list, which is how a resource with only
``<http://schema.org/name>`` ended up displaying its IRI's tail instead).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import rdflib

from asterism import substrate

__all__ = [
    "ALLOWED_OPS",
    "ALLOWED_SOURCE_SCOPES",
    "DATASET_ID_RE",
    "LABEL_PREDICATES",
    "MAX_LIMIT",
    "SetSpecError",
    "SubjectKeyError",
    "class_type_clause",
    "label_union_clause",
    "normalize_set_spec",
    "pick_label",
    "resolve_dataset_label",
    "safe_http_iri",
    "safe_iri",
    "set_id_of",
    "subject_key_string",
    "valid_dataset_id",
    "validate_subject_key",
]

#: The ONE priority order every "which literal is THE display label" decision
#: in this codebase uses (契約メモ §2 の実機所見: ``subjects/resolve`` /
#: ``subjects/search`` / ``set_members`` / ``subject_facts`` の IRI 値ラベル /
#: ``place/subjects`` の候補ラベルが、それぞれ別の場当たり的な predicate リストを
#: 持っていたために ``<http://schema.org/name> "Hydrogen"`` があるのに resolve の
#: label が IRI 末尾になる、といった食い違いが起きていた — 1 か所にする)。
#: index が SPARQL 側の優先順位 ``?rank``（小さいほど優先）と一致する。
LABEL_PREDICATES: tuple[str, ...] = (
    "http://www.w3.org/2000/01/rdf-schema#label",
    "http://schema.org/name",
    "https://schema.org/name",
    "http://purl.org/dc/terms/title",
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "http://xmlns.com/foaf/0.1/name",
)


def label_union_clause(
    subject_term: str,
    *,
    label_var: str = "?label",
    predicate_var: str = "?__lp",
    rank_var: str = "?__rank",
) -> str:
    """One ``OPTIONAL`` SPARQL block binding every :data:`LABEL_PREDICATES`
    literal found on ``subject_term`` (an already-embeddable term — a
    ``<iri>``/``?var``), tagged with the predicate's priority rank (0 =
    ``rdfs:label``, highest priority) so :func:`pick_label` can choose among
    the rows it returns. The caller supplies the ``FROM``/``GRAPH`` scoping
    and ``SELECT``s ``label_var``/``rank_var`` (and, for language selection,
    ``?lang`` via ``(lang(?label) AS ?lang)`` — this module builds no
    ``BIND``/``SELECT`` clause itself, only the ``OPTIONAL`` pattern, so it
    composes into any caller's existing query shape."""
    pairs = " ".join(f"(<{p}> {i})" for i, p in enumerate(LABEL_PREDICATES))
    return (
        f"OPTIONAL {{ VALUES ({predicate_var} {rank_var}) {{ {pairs} }} "
        f"{subject_term} {predicate_var} {label_var} . FILTER(isLiteral({label_var})) }}"
    )


def class_type_clause(class_iri: Any, *, subject_term: str = "?s") -> str | None:
    """``"{subject_term} a <class_iri> . "``, or ``None`` when ``class_iri``
    fails :func:`safe_iri` — the ONE way a caller-supplied class IRI is
    embedded into a ``rdf:type`` scoping pattern (契約メモ contract_pr_f9.md
    §2.2: ``subjects/search``'s ``class_iri`` — ``?s a <class>`` を組む唯一の
    場所)。文字安全性のみを見る（``safe_iri`` と同じ — スキーム要件は課さ
    ない）。"""
    safe = safe_iri(class_iri)
    if safe is None:
        return None
    return f"{subject_term} a <{safe}> . "


def pick_label(candidates: list[tuple[str | None, int | None, str | None]]) -> str | None:
    """Choose ONE display label among ``(value, rank, lang)`` candidates —
    :func:`label_union_clause`'s ``rank`` (predicate priority; lower wins)
    decides first, then, among the tied-for-best-rank values, language
    ``"ja"`` → ``"en"`` → untagged → the lexicographically first value
    (deterministic — never store-iteration-order dependent). ``None`` when
    every candidate is empty/``None`` (a missing ``OPTIONAL`` row)."""
    filtered = [(v, r, lang or "") for v, r, lang in candidates if v]
    if not filtered:
        return None
    best_rank = min(r if r is not None else len(LABEL_PREDICATES) for _v, r, _lang in filtered)
    at_best = [
        (v, lang)
        for v, r, lang in filtered
        if (r if r is not None else len(LABEL_PREDICATES)) == best_rank
    ]
    by_lang: dict[str, str] = {}
    for v, lang in sorted(at_best, key=lambda t: (t[1], t[0])):
        by_lang.setdefault(lang, v)
    for lang in ("ja", "en", ""):
        if lang in by_lang:
            return by_lang[lang]
    return sorted(v for v, _lang in at_best)[0]


#: SPARQL 1.1 の IRIREF 文法 (``<...>``) が一切許さない文字 — ``<``/``>``/``"``/
#: ``{``/``}``/``|``/``^``/`` ` ``/``\`` に加えて空白・制御文字（0x00-0x20）。この
#: どれか 1 文字でも生きたまま ``<...>`` に文字列連結で埋めれば、IRIREF を早期に
#: 閉じて任意の SPARQL 節を注入できる（例: ``}`` で三つ組パターンを閉じる・``#``
#: 以外の制御文字・空白でトークンを分割する）。この regex は
#: ``asterism.shape_match._UNSAFE_IRI_CHARS`` と同一の文字集合（shape_match.py は
#: 別担当のファイルなので独立にこの regex を持つ — 逸脱として notes に明記）。
_UNSAFE_IRI_CHARS = re.compile(r'[<>"{}|^`\\\x00-\x20]')

#: The where/order_by clause operators Phase 1 accepts (§3.2 — ``at`` is a
#: separate, explicitly-rejected escape hatch, not a 6th op).
ALLOWED_OPS: tuple[str, ...] = ("gt", "lt", "eq", "between", "in")
ALLOWED_SOURCE_SCOPES: tuple[str, ...] = ("all", "own", "open")

_DEFAULT_LIMIT = 20
MAX_LIMIT = 200

#: The dataset-id shape every registry writer already enforces (``registry.py``'s
#: ``_ID_RE``, ``class_schema.py``'s ``_ID_RE``, ``licenses.py``'s
#: ``_DATASET_ID_RE`` — each module keeps its own copy rather than importing this
#: one, same reasoning as :data:`_UNSAFE_IRI_CHARS` above: those are api/ingest
#: modules this PR does not own). This copy is the ONE new call sites this PR adds
#: (``subjects/search``'s ``dataset_id`` query param, ``dataset_summary``) share,
#: so a caller-supplied dataset_id is checked the same way everywhere new code in
#: this PR touches it.
DATASET_ID_RE: re.Pattern[str] = re.compile(r"[a-z0-9-]{1,128}")


def valid_dataset_id(value: Any) -> str | None:
    """``value`` if it is a non-empty string matching :data:`DATASET_ID_RE`
    (a bare slug — no path separators, no SPARQL-unsafe characters), else
    ``None``. A caller-supplied ``dataset_id`` (a query param, a path
    segment) must pass this before being embedded in a filesystem path or a
    SPARQL graph filter."""
    if not isinstance(value, str) or not value:
        return None
    return value if DATASET_ID_RE.fullmatch(value) else None


#: ``dcterms:title`` — the predicate :mod:`asterism.metadata` projects a
#: dataset's ``mie.yaml``/``schema_info.title`` into (§3.1's "dataset の
#: rdfs:label" tier is this triple in practice: a dataset has no
#: ``rdfs:label`` of its own, ``dcterms:title`` is the one human-facing name
#: the description graph actually carries).
_DCTERMS_TITLE = rdflib.URIRef("http://purl.org/dc/terms/title")


def _title_from_metadata_ttl(registry_root: Path, dataset_id: str) -> str | None:
    """The dataset's own ``dcterms:title`` literal(s), read straight out of
    the registry's projected ``metadata.ttl`` (no store round trip — the same
    file :func:`asterism.licenses._license_from_store` already reads for
    ``dcterms:license``). ``ja`` wins over ``en`` wins over untagged wins over
    the lexicographically-first tagged value (deterministic, never
    store/file-iteration-order dependent — the same tie-break shape as
    :func:`pick_label`). ``None`` if the file is absent/empty/unparsable or
    carries no title."""
    path = registry_root / dataset_id / "metadata.ttl"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    try:
        graph = rdflib.Graph()
        graph.parse(data=text, format="turtle")
        subject = rdflib.URIRef(substrate.dataset_iri(dataset_id))
    except Exception:  # best-effort: a malformed metadata.ttl must not break
        # label resolution (mirrors asterism.metadata's own best-effort style).
        return None
    by_lang: dict[str, str] = {}
    for obj in graph.objects(subject, _DCTERMS_TITLE):
        if not isinstance(obj, rdflib.Literal):
            continue
        lang = str(obj.language) if obj.language else ""
        value = str(obj)
        if not value:
            continue
        if lang not in by_lang or value < by_lang[lang]:
            by_lang[lang] = value
    for lang in ("ja", "en", ""):
        if lang in by_lang:
            return by_lang[lang]
    return sorted(by_lang.values())[0] if by_lang else None


def _name_from_meta_json(registry_root: Path, dataset_id: str) -> str | None:
    """``meta.json``'s ``name`` field — the same rule
    :func:`asterism.subject_tools.dataset_labels` and
    :func:`asterism.materials._dataset_label` each keep their own copy of
    (§0 of this codebase: duplicating this tiny file read avoids a circular
    import — ``subject_tools`` already imports THIS module)."""
    meta_path = registry_root / dataset_id / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    name = meta.get("name")
    return name if isinstance(name, str) and name else None


def resolve_dataset_label(registry_root: Path | str | None, dataset_id: str) -> str:
    """The ONE dataset-display-name resolution every read path that names a
    dataset shares (契約メモ §3.1): ``metadata.ttl``'s ``dcterms:title``
    (``ja`` → ``en`` → untagged) → the registry's ``meta.json`` ``name`` →
    ``dataset_id`` itself (K4: never a bare id when a real name exists, but
    never nothing either). Pure filesystem reads, no store access — callers
    that already have a store client (``dataset_summary``) still call this
    for the parts a store query cannot answer (the registry's own metadata),
    same layering as :mod:`asterism.licenses`.
    """
    safe_id = valid_dataset_id(dataset_id)
    if safe_id is None or registry_root is None:
        return dataset_id
    root = Path(registry_root)
    title = _title_from_metadata_ttl(root, safe_id)
    if title:
        return title
    name = _name_from_meta_json(root, safe_id)
    if name:
        return name
    return dataset_id


class SetSpecError(ValueError):
    """A SetSpec is malformed, or uses a Phase-1-unsupported feature (``at``).

    A plain ``ValueError`` subclass so a caller that only catches
    ``ValueError`` (the convention every other read path in this codebase
    uses — see ``prov_graph._validate_iri``) still catches this; the api
    layer maps it to 400.
    """


class SubjectKeyError(ValueError):
    """A SubjectKey dict is malformed (→ 400 at the api boundary)."""


def safe_iri(value: Any) -> str | None:
    """``value`` if it contains no character the SPARQL IRIREF grammar
    forbids, else ``None`` — a **character-safety** check only (no scheme
    requirement), for embedding an IRI that is not itself the caller-supplied
    identifier (e.g. a predicate/object IRI the store already returned).
    Defense in depth for :mod:`asterism.subject_tools` / callers that read
    IRIs out of query results — a well-behaved store never returns one of
    these characters, but nothing should trust that blindly."""
    if not isinstance(value, str) or not value:
        return None
    return None if _UNSAFE_IRI_CHARS.search(value) else value


def safe_http_iri(value: Any) -> str | None:
    """``value`` if it is a non-empty ``http://``/``https://`` IRI that is
    also :func:`safe_iri`-safe, else ``None``. The boundary check every
    caller-supplied IRI (a ``SetSpec`` field, a ``class_iri`` query param,
    ...) must pass before either being accepted or embedded in SPARQL."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not text.startswith(("http://", "https://")):
        return None
    return safe_iri(text)


def _require_iri(value: Any, what: str) -> str:
    text = safe_http_iri(value)
    if text is None:
        raise SetSpecError(f"{what} must be a well-formed http(s) IRI, got {value!r}")
    return text


def _normalize_clause(raw: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SetSpecError(f"where[{index}] must be an object")
    if "at" in raw and raw["at"] is not None:
        # Phase 1 explicitly does not support the "at a fixed condition"
        # escape hatch (§3.2) — reject rather than silently ignore it, so a
        # caller that relies on it fails loudly instead of getting a
        # differently-scoped answer.
        raise SetSpecError(f"where[{index}].at is not supported yet (Phase 1)")
    if "iri" in raw and raw["iri"] is not None:
        # link 形（PR F4 契約メモ §1-3 / ADR O46）: 「この 1 件を指す種類」の
        # where 条件 — ``?s <property> <iri>`` の存在チェックのみで、op/value
        # を持つ値条件とは別物（混ぜて送るのは呼び出し側のバグなので拒否）。
        if "op" in raw or "value" in raw:
            raise SetSpecError(f"where[{index}] cannot mix a link clause (iri) with op/value")
        prop = _require_iri(raw.get("property"), f"where[{index}].property")
        target = _require_iri(raw.get("iri"), f"where[{index}].iri")
        return {"property": prop, "iri": target}
    prop = _require_iri(raw.get("property"), f"where[{index}].property")
    op = raw.get("op")
    if op not in ALLOWED_OPS:
        raise SetSpecError(f"where[{index}].op must be one of {ALLOWED_OPS}, got {op!r}")
    if "value" not in raw:
        raise SetSpecError(f"where[{index}].value is required")
    value = raw["value"]
    if op == "between":
        if not (isinstance(value, list) and len(value) == 2):
            raise SetSpecError(f"where[{index}].value must be [low, high] for op 'between'")
    elif op == "in":
        if not isinstance(value, list) or not value:
            raise SetSpecError(f"where[{index}].value must be a non-empty list for op 'in'")
    elif isinstance(value, (list, dict)):
        raise SetSpecError(f"where[{index}].value must be a scalar for op {op!r}")
    return {"property": prop, "op": op, "value": value}


def _normalize_order_by(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SetSpecError("order_by must be an object")
    if "at" in raw and raw["at"] is not None:
        raise SetSpecError("order_by.at is not supported yet (Phase 1)")
    prop = _require_iri(raw.get("property"), "order_by.property")
    direction = raw.get("dir", "desc")
    if direction not in ("asc", "desc"):
        raise SetSpecError(f"order_by.dir must be 'asc' or 'desc', got {direction!r}")
    return {"property": prop, "dir": direction}


def normalize_set_spec(raw: Any) -> dict[str, Any]:
    """Validate + normalize a caller-supplied SetSpec (handoff §5.5).

    Raises :class:`SetSpecError` for anything malformed, including a Phase-1
    ``at`` clause (the api boundary maps this to 400). Returns a NEW dict
    holding only the recognized fields, in a fixed shape — ``class`` (str),
    ``where`` (list of ``{property, op, value}`` value clauses, or
    ``{property, iri}`` link clauses — PR F4 §1-3: 「この 1 件を指す種類」の
    where 条件, no ``op``/``value``), ``order_by``
    (``{property, dir}`` or None), ``limit`` (int, 1..:data:`MAX_LIMIT`,
    default 20), ``source_scope`` (one of :data:`ALLOWED_SOURCE_SCOPES`,
    default ``"all"``) — so :func:`set_id_of` always hashes the same shape
    regardless of what order or extra keys the caller's JSON carried.
    """
    if not isinstance(raw, dict):
        raise SetSpecError("spec must be an object")
    class_iri = _require_iri(raw.get("class"), "class")
    where_raw = raw.get("where") or []
    if not isinstance(where_raw, list):
        raise SetSpecError("where must be a list")
    where = [_normalize_clause(c, index=i) for i, c in enumerate(where_raw)]
    order_by = _normalize_order_by(raw.get("order_by"))
    limit_raw = raw.get("limit")
    try:
        limit = _DEFAULT_LIMIT if limit_raw is None else int(limit_raw)
    except (TypeError, ValueError) as exc:
        raise SetSpecError(f"limit must be an integer, got {limit_raw!r}") from exc
    if limit < 1:
        raise SetSpecError("limit must be >= 1")
    limit = min(limit, MAX_LIMIT)
    scope = raw.get("source_scope") or "all"
    if scope not in ALLOWED_SOURCE_SCOPES:
        raise SetSpecError(f"source_scope must be one of {ALLOWED_SOURCE_SCOPES}, got {scope!r}")
    return {
        "class": class_iri,
        "where": where,
        "order_by": order_by,
        "limit": limit,
        "source_scope": scope,
    }


def set_id_of(spec: dict[str, Any]) -> str:
    """Deterministic id for a normalized SetSpec (§1): ``"set-"`` + the first
    12 hex chars of the sha256 of the spec's canonical JSON (sorted keys, no
    whitespace, ASCII-escaped so the digest is byte-stable across platforms)
    — over exactly the 5 identity fields ``class``/``where``/``order_by``/
    ``limit``/``source_scope`` (never a dataset id, a label, or anything
    else a caller might have attached to the dict).

    Call :func:`normalize_set_spec` first; this function trusts its input is
    already that normalized shape and hashes it verbatim — two callers
    (api, ui) that both normalize-then-hash the same filter always agree.
    """
    identity = {
        "class": spec["class"],
        "where": spec["where"],
        "order_by": spec["order_by"],
        "limit": spec["limit"],
        "source_scope": spec["source_scope"],
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"set-{digest[:12]}"


def validate_subject_key(raw: Any) -> dict[str, Any]:
    """Validate + normalize a SubjectKey (§1).

    Accepts ``{"kind": "individual", "iri": "..."}`` or
    ``{"kind": "set", "spec": {...}}`` and returns the normalized form —
    ``{"kind": "individual", "iri": ...}`` or
    ``{"kind": "set", "set_id": ..., "spec": <normalized>}``. ``set_id`` is
    ALWAYS derived here via :func:`set_id_of`, never trusted from the
    caller (a client-supplied ``set_id`` could otherwise disagree with its
    own ``spec``). Raises :class:`SubjectKeyError` / :class:`SetSpecError`
    (both ``ValueError``) for anything malformed.
    """
    if not isinstance(raw, dict):
        raise SubjectKeyError("subject must be an object")
    kind = raw.get("kind")
    if kind == "individual":
        iri = _require_iri(raw.get("iri"), "subject.iri")
        return {"kind": "individual", "iri": iri}
    if kind == "set":
        spec = normalize_set_spec(raw.get("spec"))
        return {"kind": "set", "set_id": set_id_of(spec), "spec": spec}
    raise SubjectKeyError(f"subject.kind must be 'individual' or 'set', got {kind!r}")


def subject_key_string(subject: dict[str, Any]) -> str:
    """The ``"i:<iri>"`` / ``"s:<set_id>"`` string form (§1) — used for URLs
    (the caller ``encodeURIComponent``s the individual form) and as the
    hash input for ``card_id``. Call :func:`validate_subject_key` first."""
    kind = subject.get("kind")
    if kind == "individual":
        return f"i:{subject['iri']}"
    if kind == "set":
        return f"s:{subject['set_id']}"
    raise SubjectKeyError(f"subject.kind must be 'individual' or 'set', got {kind!r}")
