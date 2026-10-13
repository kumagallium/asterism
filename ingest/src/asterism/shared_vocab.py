"""Shared vocabulary runtime (ADR upper-structure-shared-terms.md §2.1〜§2.4).

A *shared term* (``sv:<slug>``) is a TBox-only word a human mints — a class or a
property with no individuals of its own. It lives in its own named graph
(:data:`SHARED_VOCAB_GRAPH`), is hung under by dataset terms through alignment lines
(the existing alignment graph), and is justified by at least one competency question
(CQ) that is stored with it as a declarative query tool.

Everything here is deterministic — no LLM, no reasoner, no generated code. The store
is read and written through the same client the crosswalk runtime uses (anything with
``sparql_select`` / ``sparql_update``). Nothing here puts :data:`SHARED_VOCAB_GRAPH`
into the canonical set: it is readable on the raw-SPARQL escape (substrate allowlist)
and nowhere else.

Invariants (ADR §2.1〜§2.3, handoff §7):

* ``sv:`` holds slugs only; provenance predicates are ``xw:``.
* The upward-path expression lives in exactly ONE definition
  (:data:`UPPER_PATH_CLASS` / :data:`UPPER_PATH_PROPERTY`); the CQ templates, the
  answering-dataset count and :func:`upper_map` all reuse it.
* A term is *wired* when at least one published dataset answers its CQ.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism import substrate
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import ALIGN_RELATIONS, ALIGNMENT_GRAPH, _sparql_str
from asterism.query_tools import (
    lint_query_tool,
    parse_query_tools,
    read_registry_raw_tools,
    remove_registry_query_tools_by_name,
    upsert_registry_query_tools_by_name,
    write_registry_raw_tools,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The ``sv:`` namespace — slugs only (provenance predicates are ``xw:``).
SV = "https://kumagallium.github.io/asterism/vocab/shared#"
SHARED_VOCAB_GRAPH = substrate.SHARED_VOCAB_GRAPH
SLUG_RE = r"^[a-z][a-z0-9_]*$"
_SLUG_PATTERN = r"^[a-z][a-z0-9_]*\Z"
#: The registry entry that carries the shared terms' CQs + ``wired.json``. It is a
#: registry item, not a dataset (meta ``is_shared_vocab``).
REGISTRY_ID = "vocab-shared"

_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_OWL = "http://www.w3.org/2002/07/owl#"
_PROV = "http://www.w3.org/ns/prov#"
_XSD = "http://www.w3.org/2001/XMLSchema#"
_QUDT_QK = "http://qudt.org/vocab/quantitykind/"

#: The upward path (ADR §2.2). ``≡`` is stored as ONE directed triple, so the walk
#: also goes backwards over it (``^``). Length 0 is included, so a term used directly
#: by a dataset counts as answering. The only definition in the code base.
UPPER_PATH_CLASS = f"(<{_RDFS}subClassOf>|<{_OWL}equivalentClass>|^<{_OWL}equivalentClass>)*"
UPPER_PATH_PROPERTY = (
    f"(<{_RDFS}subPropertyOf>|<{_OWL}equivalentProperty>|^<{_OWL}equivalentProperty>)*"
)

#: Labels too generic to suggest a fit (already :func:`normalize_label`-ed).
GENERIC_LABELS = frozenset(
    {"name", "id", "type", "date", "value", "no", "名前", "番号", "種類", "日付", "値"}
)

_KINDS = ("class", "property")
_OPS = {"class": "count", "property": "values"}
_SLUG = re.compile(_SLUG_PATTERN)
_ISO_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})?\Z")
_LANGTAG_OK = re.compile(r"^[a-z]{2,3}$")
_IRI = re.compile(r'^[a-z][a-z0-9+.\-]*://[^\s<>"{}|\\^`]+\Z', re.IGNORECASE)

_META_FILE = "meta.json"
_TOOLS_FILE = "query_tools.yaml"
_WIRED_FILE = "wired.json"
_SHARED_NAME = "ことば"

_CLASS_RELS = ("subClassOf", "equivalentClass")
_PROP_RELS = ("subPropertyOf", "equivalentProperty")


# ---------------------------------------------------------------------------
# Errors + value types
# ---------------------------------------------------------------------------


class TermError(ValueError):
    """A shared-term operation was refused (the caller maps it to a 4xx)."""


class TermExists(TermError):
    """That slug is already minted (409)."""


class TermHasLines(TermError):
    """The term still has alignment lines; withdraw them first (409)."""


class TermNotFound(TermError):
    """No such minted term (404)."""


@dataclass(frozen=True)
class CQSpec:
    """One competency question: ``title`` is human text, ``op`` is a closed choice
    (``"count"`` for a class, ``"values"`` for a property)."""

    title: str
    op: str


@dataclass(frozen=True)
class SharedTerm:
    iri: str
    slug: str
    kind: str  # "class" | "property"
    label: str
    label_en: str | None
    comment: str | None
    created_at: str
    declined_standard: str | None = None
    declined_reason: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_label(s: str) -> str:
    """NFKC → casefold → whitespace folded. The SAME rule as step0's
    ``skeleton_annotate._meaning_key`` (a test pins the two together). Symbols are
    not dropped."""
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def is_system_entry(meta: dict) -> bool:
    """True for a registry entry that is NOT a dataset (the crosswalk hub or the
    shared vocabulary) — dataset lists, discovery and autolink skip these."""
    return bool(meta.get("is_crosswalk") or meta.get("is_shared_vocab"))


def term_iri(slug: str) -> str:
    if not _SLUG.match(slug or ""):
        raise ValueError(f"slug must match {SLUG_RE}: {slug!r}")
    return SV + slug


def slug_of(iri: str) -> str | None:
    """The slug of an ``sv:`` IRI, or ``None`` when ``iri`` is not one."""
    if isinstance(iri, str) and iri.startswith(SV):
        slug = iri[len(SV) :]
        if _SLUG.match(slug):
            return slug
    return None


def is_shared_iri(iri: str) -> bool:
    return slug_of(iri) is not None


def is_wired(n: int) -> bool:
    """A term is wired (not isolated) when at least one dataset answers its CQ."""
    return n >= 1


def known_namespaces() -> list[str]:
    """Namespaces of the curated standards (``known_vocabs.yaml``) plus QUDT's
    ``quantitykind/`` — the ``standard`` kind of :func:`classify_endpoint`."""
    from asterism.grounding.catalog import vocabularies

    out = [v.namespace for v in vocabularies()]
    if _QUDT_QK not in out:
        out.append(_QUDT_QK)
    return out


def classify_endpoint(
    iri: str,
    *,
    ontology_terms: dict[str, str],
    shared_iris: set[str],
    known_namespaces: Iterable[str],
) -> tuple[str, str | None]:
    """Which kind of word ``iri`` is, computed at read time (ADR §2.2).

    The NAMESPACE decides first (so a dataset that reuses ``sv:`` or a standard term
    in its own ontology graph is still ``shared`` / ``standard``):

    ``sv:`` → ``shared`` (or ``shared_unminted`` when not in ``shared_iris``) →
    ``xw:`` → ``perspective`` → a known standard namespace → ``standard`` → listed in
    a dataset's ontology graph → ``dataset`` (with its dataset id) → ``unknown``.
    """
    if iri.startswith(SV):
        return ("shared", None) if iri in shared_iris else ("shared_unminted", None)
    if iri.startswith(XW):
        return ("perspective", None)
    if any(iri.startswith(ns) for ns in known_namespaces):
        return ("standard", None)
    if iri in ontology_terms:
        return ("dataset", ontology_terms[iri])
    return ("unknown", None)


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------


async def _bindings(client: Any, query: str) -> list[dict]:
    data = await client.sparql_select(query)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


async def _ask(client: Any, query: str) -> bool:
    data = await client.sparql_select(query)
    return bool(data.get("boolean")) if isinstance(data, dict) else False


def _check_iri(iri: str) -> None:
    if not _IRI.match(iri):
        raise ValueError(f"not an absolute IRI: {iri!r}")


def _local(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/#"))[-1] or iri


def _is_hub_or_alignment(graph_iri: str) -> bool:
    """A graph that is NOT a dataset's data: any crosswalk hub (the legacy
    ``…/canonical/crosswalk`` included) or the alignment graph."""
    return graph_iri == ALIGNMENT_GRAPH or substrate.hub_perspective_name(graph_iri) is not None


def _hub_filter(var: str) -> str:
    """SPARQL FILTER body excluding every ``…/canonical/crosswalk[/…]`` graph."""
    base = f"{substrate.CANONICAL_GRAPH_BASE}crosswalk"
    return f'STR({var}) != "{base}" && !STRSTARTS(STR({var}), "{base}/")'


# ---------------------------------------------------------------------------
# Registry files (meta.json / query_tools.yaml / wired.json)
# ---------------------------------------------------------------------------


def _dir(root: Path | str) -> Path:
    return Path(root) / REGISTRY_ID


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _scaffold(root: Path | str) -> None:
    """Create ``registry/vocab-shared/meta.json`` if absent (never clobbers)."""
    meta_path = _dir(root) / _META_FILE
    if meta_path.is_file():
        return
    meta = {"id": REGISTRY_ID, "is_shared_vocab": True, "promoted": True, "name": _SHARED_NAME}
    _atomic_write(meta_path, json.dumps(meta, indent=2, ensure_ascii=False))


def _read_cq_tools(root: Path | str) -> list[dict]:
    """The raw tool declarations in ``query_tools.yaml`` ([] when absent). An
    unreadable file aborts rather than being overwritten."""
    tools = read_registry_raw_tools(root, REGISTRY_ID)
    if tools is None:
        raise TermError(f"{_TOOLS_FILE} が読めないため書き換えを中止しました")
    return tools


def _save_cq_tools(root: Path | str, tools: list[dict]) -> None:
    """Replace ``query_tools.yaml`` with ``tools`` through the one YAML writer in
    :mod:`asterism.query_tools` (tmp → fsync → replace)."""
    if write_registry_raw_tools(root, REGISTRY_ID, tools) is None:
        raise TermError(f"{_TOOLS_FILE} を書けません (登録簿の項目がありません)")


def _write_cq_tools(root: Path | str, tools: list[dict]) -> None:
    """Upsert ``tools`` into ``query_tools.yaml`` BY NAME via the registry-wide
    :func:`asterism.query_tools.upsert_registry_query_tools_by_name` (tmp → replace; an
    unreadable file or a declaration that fails lint aborts with :class:`TermError`)."""
    rejected = upsert_registry_query_tools_by_name(root, REGISTRY_ID, tools)
    if rejected is None:
        raise TermError(f"{_TOOLS_FILE} が読めないため書き換えを中止しました")
    if rejected:
        raise TermError(f"問いのツールが検査を通りません: {', '.join(rejected)}")


def load_wired(root: Path | str) -> dict[str, int]:
    """term IRI → number of answering datasets from ``wired.json`` ({} when absent or
    unreadable). Reads both ``{"terms": {...}, "at": …}`` and a flat ``{iri: n}``."""
    path = _dir(root) / _WIRED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    terms = data.get("terms") if isinstance(data.get("terms"), dict) else data
    return {
        str(k): int(v)
        for k, v in terms.items()
        if isinstance(v, int) and not isinstance(v, bool) and k != "at"
    }


# ---------------------------------------------------------------------------
# CQ templates (ADR §2.3)
# ---------------------------------------------------------------------------


def default_cq_tools(term: SharedTerm, cqs: list[CQSpec]) -> list[dict]:
    """The declarative query tools for ``term``'s CQs (raw ``query_tools.yaml``
    entries, ``for_terms: [<iri>]``). A fixed template filled from a closed choice —
    nothing is generated. Names are ``cq_<slug>_<op>`` (a repeated op gets ``_2``…).

    The query reads the dataset graphs ``?dataset_graph`` (the hub graphs, old and new
    form, and the alignment graph are filtered out) and walks the upward path to the
    term, so an alignment line is what makes a dataset answer."""
    if term.kind not in _KINDS:
        raise TermError(f"kind must be one of {_KINDS}: {term.kind!r}")
    op = _OPS[term.kind]
    path = UPPER_PATH_CLASS if term.kind == "class" else UPPER_PATH_PROPERTY
    iri = term.iri
    _check_iri(iri)
    flt = _hub_filter("?dataset_graph")
    if term.kind == "class":
        pattern = "GRAPH ?dataset_graph { ?e a ?k }"
        select = "(COUNT(DISTINCT ?e) AS ?entities)"
        out_var = "entities"
        describe = f"「{term.label}」に当たる種類を、どのデータセットが何件持っているかを数えます。"
    else:
        pattern = "GRAPH ?dataset_graph { ?e ?k ?v }"
        select = "(COUNT(*) AS ?value_count)"
        out_var = "value_count"
        describe = (
            f"「{term.label}」に当たる項目の値を、どのデータセットがいくつ持っているかを数えます。"
        )
    query = (
        f"SELECT ?dataset_graph {select}\n"
        "WHERE {\n"
        f"  ?k {path} <{iri}> .\n"
        f"  {pattern}\n"
        f"  FILTER({flt})\n"
        "}\n"
        "GROUP BY ?dataset_graph\n"
        f"ORDER BY DESC(?{out_var})\n"
    )
    tools: list[dict] = []
    seen: dict[str, int] = {}
    for cq in cqs:
        if cq.op != op:
            raise TermError(f"{term.kind} の問いの op は {op!r} です ({cq.op!r} は使えません)")
        if not cq.title.strip():
            raise TermError("問いの題が空です")
        n = seen.get(cq.op, 0) + 1
        seen[cq.op] = n
        name = f"cq_{term.slug}_{cq.op}" + ("" if n == 1 else f"_{n}")
        tools.append(
            {
                "name": name,
                "title": cq.title.strip(),
                "description": describe,
                "for_terms": [iri],
                "parameters": [],
                "query": query,
                "result": {
                    "item": {
                        "dataset_graph": "dataset_graph",
                        out_var: {"var": out_var, "number": True},
                    }
                },
            }
        )
    return tools


def _lint_or_raise(tools: list[dict]) -> None:
    """A CQ that does not lint is never saved (handoff §7)."""
    try:
        parsed = parse_query_tools({"tools": tools})
    except Exception as exc:  # QueryToolError — message is the useful part
        raise TermError(f"問いの形が不正です: {exc}") from exc
    for qt in parsed:
        lint = lint_query_tool(qt)
        if lint.errors:
            raise TermError(f"問い {qt.name!r} が検査に通りません: {'; '.join(lint.errors)}")


# ---------------------------------------------------------------------------
# Terms: mint / list / remove
# ---------------------------------------------------------------------------


def _lit(s: str, lang: str | None = None) -> str:
    body = f'"{_sparql_str(s)}"'
    return f"{body}@{lang}" if lang else body


async def minted_iris(client: Any) -> list[str]:
    rows = await _bindings(
        client,
        f"SELECT DISTINCT ?s WHERE {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?s a ?t . "
        f"VALUES ?t {{ <{_RDFS}Class> <{_RDF}Property> }} }} }} ORDER BY ?s",
    )
    return [b["s"]["value"] for b in rows if b.get("s", {}).get("type") == "uri"]


async def mint_term(
    client: Any,
    root: Path | str,
    *,
    slug: str,
    kind: str,
    label: str,
    cqs: list[CQSpec],
    at: str,
    label_en: str | None = None,
    comment: str | None = None,
    declined_standard: str | None = None,
    declined_reason: str | None = None,
) -> dict:
    """Mint ``sv:<slug>`` together with its first CQ(s) in ONE operation (ADR §2.1):

    * ``ValueError`` — bad slug / kind / label / ``at`` / IRI;
    * :class:`TermError` — no ``cqs`` (422), a CQ that does not lint, or an unreadable
      ``query_tools.yaml``; :class:`TermExists` — the slug is already minted (409).

    Everything that can be refused is checked before the first write. Returns the
    :class:`SharedTerm` as a dict."""
    iri = term_iri(slug)
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}: {kind!r}")
    if not (label or "").strip():
        raise ValueError("label (日本語) は必須です")
    if not _ISO_AT.match(at or ""):
        raise ValueError(f"at must be an ISO datetime: {at!r}")
    if declined_standard:
        _check_iri(declined_standard)
    if not cqs:
        raise TermError("共有のことばは、問いを 1 つ以上添えて作ります")
    term = SharedTerm(
        iri=iri,
        slug=slug,
        kind=kind,
        label=label.strip(),
        label_en=(label_en or "").strip() or None,
        comment=(comment or "").strip() or None,
        created_at=at,
        declined_standard=declined_standard or None,
        declined_reason=(declined_reason or "").strip() or None,
    )
    tools = default_cq_tools(term, cqs)
    _lint_or_raise(tools)
    _read_cq_tools(root)  # abort now if the yaml is unreadable (nothing written yet)
    if await _ask(client, f"ASK {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ <{iri}> ?p ?o }} }}"):
        raise TermExists(f"{slug!r} はすでにあります。既存のことばを使ってください")

    rdf_type = f"{_RDFS}Class" if kind == "class" else f"{_RDF}Property"
    triples = [
        f"<{iri}> a <{rdf_type}>",
        f"<{iri}> <{_RDFS}label> {_lit(term.label, 'ja')}",
    ]
    if term.label_en:
        triples.append(f"<{iri}> <{_RDFS}label> {_lit(term.label_en, 'en')}")
    if term.comment:
        triples.append(f"<{iri}> <{_RDFS}comment> {_lit(term.comment, 'ja')}")
    if term.declined_standard:
        triples.append(f"<{iri}> <{XW}declinedStandard> <{term.declined_standard}>")
    if term.declined_reason:
        triples.append(f"<{iri}> <{XW}declinedReason> {_lit(term.declined_reason)}")
    triples.append(f'<{iri}> <{_PROV}generatedAtTime> "{at}"^^<{_XSD}dateTime>')
    # Order: files first (scaffold + CQ upsert), then the store INSERT. If the INSERT
    # fails the CQs just written are taken back out by name, so a retry is not a
    # TermExists and no CQ outlives a term that was never minted.
    _scaffold(root)
    _write_cq_tools(root, tools)
    try:
        await client.sparql_update(
            f"INSERT DATA {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ {' . '.join(triples)} }} }}"
        )
    except BaseException:
        remove_registry_query_tools_by_name(root, REGISTRY_ID, [t["name"] for t in tools])
        raise
    await recompute_wired(client, root)
    return asdict(term)


async def _read_terms(client: Any) -> list[SharedTerm]:
    rows = await _bindings(
        client,
        f"SELECT ?s ?p ?o WHERE {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?s a ?t . ?s ?p ?o . "
        f"VALUES ?t {{ <{_RDFS}Class> <{_RDF}Property> }} }} }}",
    )
    acc: dict[str, dict[str, Any]] = {}
    for b in rows:
        s = b["s"]["value"]
        slug = slug_of(s)
        if slug is None:
            continue
        d = acc.setdefault(
            s, {"slug": slug, "kind": None, "ja": [], "en": [], "plain": [], "comment": []}
        )
        p, o = b["p"]["value"], b["o"]
        v = o["value"]
        lang = (o.get("xml:lang") or "").lower()
        if p == f"{_RDF}type":
            if v == f"{_RDFS}Class":
                d["kind"] = "class"
            elif v == f"{_RDF}Property" and d["kind"] is None:
                d["kind"] = "property"
        elif p == f"{_RDFS}label":
            d["en" if lang.startswith("en") else "ja" if lang.startswith("ja") else "plain"].append(
                v
            )
        elif p == f"{_RDFS}comment":
            d["comment"].append(v)
        elif p == f"{XW}declinedStandard":
            d["ds"] = v
        elif p == f"{XW}declinedReason":
            d["dr"] = v
        elif p == f"{_PROV}generatedAtTime":
            d["at"] = v
    out: list[SharedTerm] = []
    for iri, d in acc.items():
        labels = sorted(d["ja"]) or sorted(d["plain"]) or sorted(d["en"])
        out.append(
            SharedTerm(
                iri=iri,
                slug=d["slug"],
                kind=d["kind"] or "class",
                label=labels[0] if labels else d["slug"],
                label_en=sorted(d["en"])[0] if d["en"] else None,
                comment=sorted(d["comment"])[0] if d["comment"] else None,
                created_at=d.get("at", ""),
                declined_standard=d.get("ds"),
                declined_reason=d.get("dr"),
            )
        )
    return sorted(out, key=lambda t: t.slug)


async def ontology_terms(client: Any) -> dict[str, str]:
    """Every class / property in a dataset's ontology graph → that dataset's id (the
    last segment of ``…/graph/ontology/{id}``). On an IRI that two datasets declare,
    the id that sorts first wins (deterministic)."""
    rows = await _bindings(
        client,
        "SELECT DISTINCT ?s ?g WHERE { GRAPH ?g { ?s a ?t } "
        f"VALUES ?t {{ <{_RDFS}Class> <{_RDF}Property> }} "
        f'FILTER(isIRI(?s) && STRSTARTS(STR(?g), "{substrate.ONTOLOGY_GRAPH_BASE}")) }} '
        "ORDER BY ?g ?s",
    )
    out: dict[str, str] = {}
    for b in rows:
        g = b["g"]["value"]
        out.setdefault(b["s"]["value"], g[len(substrate.ONTOLOGY_GRAPH_BASE) :])
    return out


async def _display_labels(client: Any, iris: list[str]) -> dict[str, str]:
    """rdfs:label (ja first) of ``iris`` from the dataset ontology graphs + the shared
    graph; falls back to the IRI's local name."""
    out: dict[str, str] = {}
    safe = sorted({i for i in iris if _IRI.match(i)})
    if safe:
        values = " ".join(f"<{i}>" for i in safe)
        rows = await _bindings(
            client,
            "SELECT ?s ?l WHERE { GRAPH ?g { ?s "
            f"<{_RDFS}label> ?l }} VALUES ?s {{ {values} }} "
            f'FILTER(STRSTARTS(STR(?g), "{substrate.ONTOLOGY_GRAPH_BASE}") '
            f"|| ?g = <{SHARED_VOCAB_GRAPH}>) }}",
        )
        ranked: dict[str, tuple[int, str]] = {}
        for b in rows:
            lang = (b["l"].get("xml:lang") or "").lower()
            rank = 0 if lang.startswith("ja") else 1 if not lang else 2
            cand = (rank, b["l"]["value"])
            cur = ranked.get(b["s"]["value"])
            if cur is None or cand < cur:
                ranked[b["s"]["value"]] = cand
        out = {k: v[1] for k, v in ranked.items()}
    for i in iris:
        out.setdefault(i, _local(i))
    return out


_REL_BY_IRI = {v: k for k, v in ALIGN_RELATIONS.items()}


async def _alignment_lines(client: Any) -> list[tuple[str, str, str]]:
    """(source, relation key, target) for every semantic line in the alignment graph."""
    values = " ".join(f"<{v}>" for v in ALIGN_RELATIONS.values())
    rows = await _bindings(
        client,
        f"SELECT ?s ?r ?t WHERE {{ GRAPH <{ALIGNMENT_GRAPH}> {{ ?s ?r ?t }} "
        f"VALUES ?r {{ {values} }} FILTER(isIRI(?s) && isIRI(?t)) }} ORDER BY ?s ?r ?t",
    )
    return [
        (b["s"]["value"], _REL_BY_IRI[b["r"]["value"]], b["t"]["value"])
        for b in rows
        if b["r"]["value"] in _REL_BY_IRI
    ]


async def list_terms(client: Any, root: Path | str) -> list[dict]:
    """Every minted term with what hangs under it (``narrower``), the standards it is
    tied to (``standards``), its CQs (``cqs``) and whether anything answers
    (``answering_datasets`` / ``wired``, read from ``wired.json``). Sorted by slug."""
    terms = await _read_terms(client)
    if not terms:
        return []
    shared = {t.iri for t in terms}
    onto = await ontology_terms(client)
    ns = known_namespaces()
    lines = await _alignment_lines(client)
    kinds: dict[str, tuple[str, str | None]] = {}

    def kind_of(iri: str) -> tuple[str, str | None]:
        if iri not in kinds:
            kinds[iri] = classify_endpoint(
                iri, ontology_terms=onto, shared_iris=shared, known_namespaces=ns
            )
        return kinds[iri]

    narrower: dict[str, dict[str, dict]] = {t.iri: {} for t in terms}
    standards: dict[str, list[dict]] = {t.iri: [] for t in terms}
    for src, rel, tgt in lines:
        sub = rel in ("subClassOf", "subPropertyOf")
        eq = rel in ("equivalentClass", "equivalentProperty")
        if sub and tgt in narrower and kind_of(src)[0] in ("dataset", "perspective"):
            narrower[tgt][src] = {
                "iri": src,
                "kind": kind_of(src)[0],
                "dataset_id": kind_of(src)[1],
            }
        if eq:
            for me, other in ((tgt, src), (src, tgt)):
                if me in narrower and kind_of(other)[0] in ("dataset", "perspective"):
                    narrower[me][other] = {
                        "iri": other,
                        "kind": kind_of(other)[0],
                        "dataset_id": kind_of(other)[1],
                    }
        if src in standards and kind_of(tgt)[0] == "standard":
            standards[src].append({"iri": tgt, "relation": rel})

    need = sorted({n for per in narrower.values() for n in per})
    labels = await _display_labels(client, need) if need else {}
    wired = load_wired(root)
    raw_tools = _read_cq_tools(root)
    out: list[dict] = []
    for t in terms:
        n = wired.get(t.iri, 0)
        cqs = []
        for tool in raw_tools:
            for_terms = [str(x) for x in (tool.get("for_terms") or [])]
            if t.iri in for_terms:
                cqs.append(
                    {
                        "tool_name": str(tool.get("name")),
                        "title": str(tool.get("title", tool.get("name"))),
                        "answering_datasets": max((wired.get(x, 0) for x in for_terms), default=0),
                    }
                )
        nar = [
            {**e, "label": labels.get(e["iri"], _local(e["iri"]))}
            for e in sorted(narrower[t.iri].values(), key=lambda e: (e["kind"], e["iri"]))
        ]
        d = asdict(t)
        d.update(
            narrower=nar,
            standards=sorted(standards[t.iri], key=lambda e: (e["iri"], e["relation"])),
            cqs=cqs,
            answering_datasets=n,
            wired=is_wired(n),
        )
        out.append(d)
    return out


async def remove_term(client: Any, root: Path | str, slug: str) -> None:
    """Withdraw a minted term. Refused (:class:`TermHasLines`) while ANY alignment
    line touches it. Its CQs go with it: a tool whose only term this is is deleted, a
    tool over several terms just loses this one from ``for_terms``."""
    iri = term_iri(slug)
    if not await _ask(client, f"ASK {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ <{iri}> ?p ?o }} }}"):
        raise TermNotFound(f"{slug!r} ということばはありません")
    if await _ask(
        client,
        f"ASK {{ GRAPH <{ALIGNMENT_GRAPH}> {{ {{ <{iri}> ?p ?o }} UNION {{ ?s ?p <{iri}> }} }} }}",
    ):
        raise TermHasLines("線が残っています。先に線を外してください")
    existing = _read_cq_tools(root)  # abort before writing when unreadable
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ <{iri}> ?p ?o }} }}"
    )
    kept: list[dict] = []
    for tool in existing:
        for_terms = [str(x) for x in (tool.get("for_terms") or [])]
        if iri not in for_terms:
            kept.append(tool)
        elif for_terms != [iri]:
            kept.append({**tool, "for_terms": [x for x in for_terms if x != iri]})
    if kept != existing:
        _save_cq_tools(root, kept)
    await recompute_wired(client, root)


# ---------------------------------------------------------------------------
# Answering datasets / wired
# ---------------------------------------------------------------------------


async def answering_datasets(client: Any, term_iris: list[str]) -> dict[str, int]:
    """term IRI → number of published DATASETS that answer it, in ONE aggregate query.

    A dataset answers a term when one of its terms reaches it by the upward path
    (length 0 included, so a direct use counts) and has individuals (class) / values
    (property) in its data graph. Only dataset data graphs are read: the hub graphs
    (old ``…/canonical/crosswalk`` and new) and the alignment graph are left out, and
    version graphs fold onto their dataset id so one dataset counts once. With no
    published dataset graph nothing is sent to the store and every count is 0."""
    iris = sorted(set(term_iris))
    out = dict.fromkeys(iris, 0)
    for i in iris:
        _check_iri(i)
    if not iris:
        return out
    graphs = [g for g in await substrate.canonical_graphs(client) if not _is_hub_or_alignment(g)]
    graphs = [g for g in graphs if substrate.dataset_id_of_canonical_graph(g)]
    if not graphs:
        return out
    t_values = " ".join(f"<{i}>" for i in iris)
    g_values = " ".join(f"<{g}>" for g in sorted(set(graphs)))
    a = f"GRAPH <{ALIGNMENT_GRAPH}>"
    # Four branches (class / property × direct / via a line). The direct branch binds
    # ?k = ?t itself, so a term no line mentions still counts (a zero-length path over a
    # variable subject only visits nodes of the active graph, so it is not relied on).
    branches = [
        f"{{ VALUES ?t {{ {t_values} }} BIND(?t AS ?k) GRAPH ?g {{ ?e a ?k }} }}",
        f"{{ VALUES ?t {{ {t_values} }} {a} {{ ?k {UPPER_PATH_CLASS} ?t }} "
        "GRAPH ?g { ?e a ?k } }",
        f"{{ VALUES ?t {{ {t_values} }} BIND(?t AS ?k) GRAPH ?g {{ ?e ?k ?v }} }}",
        f"{{ VALUES ?t {{ {t_values} }} {a} {{ ?k {UPPER_PATH_PROPERTY} ?t }} "
        "GRAPH ?g { ?e ?k ?v } }",
    ]
    query = (
        "SELECT DISTINCT ?t ?g WHERE { "
        f"VALUES ?g {{ {g_values} }} " + " UNION ".join(branches) + " }"
    )
    seen: dict[str, set[str]] = {i: set() for i in iris}
    for b in await _bindings(client, query):
        t, g = b["t"]["value"], b["g"]["value"]
        did = substrate.dataset_id_of_canonical_graph(g)
        if t in seen and did:
            seen[t].add(did)
    return {i: len(seen[i]) for i in iris}


async def recompute_wired(
    client: Any, root: Path | str, *, at: str | None = None
) -> dict[str, int]:
    """Recount every minted term's answering datasets and write
    ``registry/vocab-shared/wired.json`` (``{"terms": {iri: n}, "at": …}``, tmp →
    replace). Returns ``{iri: n}``."""
    counts = await answering_datasets(client, await minted_iris(client))
    stamp = at or datetime.now(UTC).isoformat()
    _atomic_write(
        _dir(root) / _WIRED_FILE,
        json.dumps({"terms": counts, "at": stamp}, indent=2, ensure_ascii=False, sort_keys=True),
    )
    return counts


# ---------------------------------------------------------------------------
# Upper map (ADR §2.6) — collapses dataset terms onto the top shared term
# ---------------------------------------------------------------------------


async def _upper_for(client: Any, rels: tuple[str, ...], path: str) -> dict[str, str]:
    rel_values = " ".join(f"<{ALIGN_RELATIONS[r]}>" for r in rels)
    keys = {
        b["x"]["value"]
        for b in await _bindings(
            client,
            f"SELECT DISTINCT ?x WHERE {{ GRAPH <{ALIGNMENT_GRAPH}> {{ "
            f"{{ ?x ?r ?y }} UNION {{ ?y ?r ?x }} }} VALUES ?r {{ {rel_values} }} "
            "FILTER(isIRI(?x) && isIRI(?y)) }",
        )
    }
    reach: dict[str, set[str]] = {k: set() for k in keys}
    rows = await _bindings(
        client,
        f"SELECT DISTINCT ?x ?up WHERE {{ GRAPH <{ALIGNMENT_GRAPH}> {{ ?x {path} ?up }} "
        f'FILTER(isIRI(?x) && STRSTARTS(STR(?up), "{SV}")) }}',
    )
    for b in rows:
        x, up = b["x"]["value"], b["up"]["value"]
        if x in reach and slug_of(up):
            reach[x].add(up)
    out: dict[str, str] = {}

    def is_top(u: str) -> bool:
        # A "top" term reaches nothing above itself: everything it reaches reaches it
        # back (≡ makes mutual reachability, which is not "above").
        return all(v == u or u in reach.get(v, ()) for v in reach.get(u, ()))

    for x in sorted(keys):
        tops = [u for u in reach[x] if is_top(u)]
        out[x] = min(tops, key=lambda u: slug_of(u) or u) if tops else x
    return out


async def upper_map(client: Any) -> dict[str, dict[str, str]]:
    """``{"classes": {iri: top}, "properties": {iri: top}}`` from the alignment lines
    ALONE. ``top`` is the topmost ``sv:`` term :data:`UPPER_PATH_CLASS` /
    :data:`UPPER_PATH_PROPERTY` reaches from the IRI (no further ``sv:`` above it); with
    several, the first by slug. An IRI that reaches no ``sv:`` term maps to itself.
    Every IRI that appears in a line is a key."""
    return {
        "classes": await _upper_for(client, _CLASS_RELS, UPPER_PATH_CLASS),
        "properties": await _upper_for(client, _PROP_RELS, UPPER_PATH_PROPERTY),
    }


# ---------------------------------------------------------------------------
# Fit candidates (ADR §2.5 ③) — pure
# ---------------------------------------------------------------------------


def fit_candidates(
    label: str,
    column: str,
    *,
    shared_terms: list[SharedTerm],
    dataset_terms: list[dict],
    standard_hits: list[dict],
) -> list[dict]:
    """Words a column could be fitted to, by EXACT match only (after
    :func:`normalize_label`) — never fuzzy. ``standard_hits`` (``{iri, label}``, the
    exact-grade result of ``ground_terms`` on the column name) come first as kind
    ``standard``, then shared terms (``label`` / ``label_en``), then other datasets'
    item labels (``dataset_terms``: ``{iri, label, dataset_id, kind}``) — these two
    are matched against the column's MEANING (``label``) only, never the column name.
    Every candidate carries ``term_kind`` (``"class"`` | ``"property"``) so the caller
    can tell a kind from an item; ``standard_hits`` pass theirs as ``kind``. A label or
    column that is generic (:data:`GENERIC_LABELS`) yields nothing at all."""
    nl, nc = normalize_label(label or ""), normalize_label(column or "")
    if nl in GENERIC_LABELS or nc in GENERIC_LABELS:
        return []
    # Shared terms and other datasets' items are matched on the MEANING only (the
    # label a person settled in ③). A bare column name (``source``, ``category``)
    # matched another dataset's item of the same spelling regardless of what either
    # column meant — noise the screen showed on the first real run (2026-10-09).
    # The column name still reaches standard terms, via ``standard_hits`` (the
    # caller grounds the ASCII column, since ``ground_terms`` cannot read Japanese).
    keys = [(nl, "label")]

    def matched_by(*texts: str | None) -> str | None:
        norms = {normalize_label(t) for t in texts if t}
        for key, by in keys:
            if key and key in norms:
                return by
        return None

    out: list[dict] = []
    seen: set[str] = set()
    for h in standard_hits:
        iri = h.get("iri")
        if iri and iri not in seen:
            seen.add(iri)
            out.append(
                {
                    "term": iri,
                    "kind": "standard",
                    "term_kind": h.get("kind") or "",
                    "label": h.get("label") or _local(iri),
                    "matched_by": h.get("matched_by") or "column",
                }
            )
    for t in sorted(shared_terms, key=lambda t: t.iri):
        by = matched_by(t.label, t.label_en)
        if by and t.iri not in seen:
            seen.add(t.iri)
            out.append(
                {
                    "term": t.iri,
                    "kind": "shared",
                    "term_kind": t.kind,
                    "label": t.label,
                    "matched_by": by,
                }
            )
    for d in sorted(dataset_terms, key=lambda d: (str(d.get("dataset_id")), str(d.get("iri")))):
        by = matched_by(d.get("label"))
        iri = d.get("iri")
        if by and iri and iri not in seen:
            seen.add(iri)
            out.append(
                {
                    "term": iri,
                    "kind": "dataset",
                    "term_kind": d.get("kind") or "",
                    "label": d["label"],
                    "matched_by": by,
                }
            )
    return out


__all__ = [
    "GENERIC_LABELS",
    "REGISTRY_ID",
    "SHARED_VOCAB_GRAPH",
    "SLUG_RE",
    "SV",
    "UPPER_PATH_CLASS",
    "UPPER_PATH_PROPERTY",
    "CQSpec",
    "SharedTerm",
    "TermError",
    "TermExists",
    "TermHasLines",
    "TermNotFound",
    "answering_datasets",
    "classify_endpoint",
    "default_cq_tools",
    "fit_candidates",
    "is_shared_iri",
    "is_system_entry",
    "is_wired",
    "known_namespaces",
    "list_terms",
    "load_wired",
    "mint_term",
    "minted_iris",
    "normalize_label",
    "ontology_terms",
    "recompute_wired",
    "remove_term",
    "slug_of",
    "term_iri",
    "upper_map",
]
