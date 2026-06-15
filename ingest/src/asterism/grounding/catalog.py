"""Deterministic grounding search over the curated known-vocabulary catalog.

Given a class/predicate NAME (or a free-text label), return the best-matching REAL
term IRIs from the curated SoT (``known_vocabs.yaml``). The search is:

- **closed-set** — it can only ever return IRIs that are in the catalog, so it cannot
  fabricate an IRI (the invariant; external-standard-alignment.md §8). The human picks
  from the candidates and confirms.
- **deterministic** — pure string scoring, no LLM, no network, no randomness; the same
  query always yields the same ranking. Safe to call from the API / MCP / propose.

The matcher is intentionally simple (tokenize, then tier by exact / token-subset /
substring / overlap). It does not try to be clever — it surfaces a short, ranked list
of plausible standard terms for a human to vet, which is exactly the grounding gate.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_CATALOG_FILE = Path(__file__).with_name("known_vocabs.yaml")

# Leading tokens to also try DROPPING when matching a property name, so a query like
# "structure" matches the property "hasStructure" and "space group" matches
# "hasSpaceGroup". Never removes information — the un-stripped tokens are matched too.
_PROP_LEAD = {"has", "is", "was", "had", "in", "of"}

_KINDS = {"class", "property"}


@dataclass(frozen=True)
class Vocabulary:
    """One curated external vocabulary (metadata only — no terms)."""

    prefix: str
    title: str
    namespace: str
    domain: str
    homepage: str
    source: str
    retrieved: str
    version: str
    term_count: int

    def to_dict(self) -> dict:
        return {
            "prefix": self.prefix,
            "title": self.title,
            "namespace": self.namespace,
            "domain": self.domain,
            "homepage": self.homepage,
            "source": self.source,
            "retrieved": self.retrieved,
            "version": self.version,
            "term_count": self.term_count,
        }


@dataclass(frozen=True)
class VocabTerm:
    """One real term in a curated vocabulary. ``iri == namespace + name``."""

    prefix: str
    namespace: str
    name: str
    kind: str  # "class" | "property"
    label: str
    vocab_title: str
    domain: str

    @property
    def iri(self) -> str:
        return self.namespace + self.name

    @property
    def curie(self) -> str:
        return f"{self.prefix}:{self.name}"


@dataclass(frozen=True)
class Candidate:
    """A grounding result: a real term + why/how strongly it matched the query."""

    iri: str
    curie: str
    prefix: str
    name: str
    kind: str
    label: str
    vocab_title: str
    domain: str
    score: int
    match: str  # "exact" | "exact_tokens" | "tokens_subset" | "substring" | "overlap"

    def to_dict(self) -> dict:
        return {
            "iri": self.iri,
            "curie": self.curie,
            "prefix": self.prefix,
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "vocab_title": self.vocab_title,
            "domain": self.domain,
            "score": self.score,
            "match": self.match,
        }


def _split(text: str) -> list[str]:
    """Lowercased word tokens from camelCase / snake / kebab / spaced text."""
    # Split camelCase / acronym boundaries, then on any non-alphanumeric run.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _norm(text: str) -> str:
    """Separator-free normalized form, e.g. "Crystal Structure" -> "crystalstructure"."""
    return "".join(_split(text))


@dataclass(frozen=True)
class _Indexed:
    term: VocabTerm
    name_norm: str
    label_norm: str
    # token sets used for matching: the term's own tokens, plus property tokens with a
    # leading has/is/was dropped (so "structure" can match "hasStructure").
    tokens: frozenset[str]
    core_norm: str  # name_norm with a leading has/is/was prefix removed


@functools.lru_cache(maxsize=1)
def _load_raw() -> dict:
    if not _CATALOG_FILE.is_file():  # defensive — the file ships with the package
        return {"vocabularies": []}
    with _CATALOG_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"vocabularies": []}


@functools.lru_cache(maxsize=1)
def load_catalog() -> tuple[Vocabulary, ...]:
    """Curated vocabularies (metadata + term counts), in file order."""
    out: list[Vocabulary] = []
    for v in _load_raw().get("vocabularies", []):
        terms = v.get("terms") or []
        out.append(
            Vocabulary(
                prefix=str(v["prefix"]),
                title=str(v.get("title", v["prefix"])),
                namespace=str(v["namespace"]),
                domain=str(v.get("domain", "")),
                homepage=str(v.get("homepage", "")),
                source=str(v.get("source", "")),
                retrieved=str(v.get("retrieved", "")),
                version=str(v.get("version", "")),
                term_count=len(terms),
            )
        )
    return tuple(out)


@functools.lru_cache(maxsize=1)
def _all_terms() -> tuple[VocabTerm, ...]:
    out: list[VocabTerm] = []
    for v in _load_raw().get("vocabularies", []):
        prefix, namespace = str(v["prefix"]), str(v["namespace"])
        title, domain = str(v.get("title", prefix)), str(v.get("domain", ""))
        for t in v.get("terms") or []:
            kind = str(t.get("kind", "")).strip()
            if kind not in _KINDS:  # skip a malformed entry rather than mis-ground
                continue
            out.append(
                VocabTerm(
                    prefix=prefix,
                    namespace=namespace,
                    name=str(t["name"]),
                    kind=kind,
                    label=str(t.get("label", t["name"])),
                    vocab_title=title,
                    domain=domain,
                )
            )
    return tuple(out)


@functools.lru_cache(maxsize=1)
def _index() -> tuple[_Indexed, ...]:
    idx: list[_Indexed] = []
    for term in _all_terms():
        name_tokens = _split(term.name)
        label_tokens = _split(term.label)
        tokens = set(name_tokens) | set(label_tokens)
        core_tokens = name_tokens
        if term.kind == "property" and name_tokens and name_tokens[0] in _PROP_LEAD:
            core_tokens = name_tokens[1:]
            tokens |= set(core_tokens)
        idx.append(
            _Indexed(
                term=term,
                name_norm=_norm(term.name),
                label_norm=_norm(term.label),
                tokens=frozenset(tokens),
                core_norm="".join(core_tokens),
            )
        )
    return tuple(idx)


def _score(q_norm: str, q_tokens: frozenset[str], ix: _Indexed) -> tuple[int, str]:
    """Deterministic match score + tier name for one indexed term (0 = no match)."""
    if not q_norm:
        return 0, ""
    if q_norm in (ix.name_norm, ix.label_norm, ix.core_norm):
        return 100, "exact"
    if q_tokens and (q_tokens == ix.tokens):
        return 90, "exact_tokens"
    if q_tokens and q_tokens <= ix.tokens:
        # all query words appear in the term; tighter (fewer extra words) ranks higher
        return 70 + max(0, 10 - (len(ix.tokens) - len(q_tokens))), "tokens_subset"
    if q_norm in ix.name_norm or ix.name_norm in q_norm or q_norm in ix.label_norm:
        return 50, "substring"
    overlap = q_tokens & ix.tokens
    if overlap:
        return 20 + len(overlap), "overlap"
    return 0, ""


def ground_terms(
    query: str,
    *,
    kind: str | None = None,
    domain: str | None = None,
    limit: int = 8,
) -> list[Candidate]:
    """Rank curated external terms matching ``query`` (a class/predicate name or label).

    ``kind`` filters to "class" or "property"; ``domain`` (e.g. "materials") filters the
    vocabulary domain. Returns at most ``limit`` candidates, best first. Closed-set:
    every result is a real catalog IRI — the caller/human then confirms the choice.
    """
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"kind must be one of {sorted(_KINDS)} or None, got {kind!r}")
    q_norm = _norm(query)
    q_tokens = frozenset(_split(query))
    scored: list[tuple[int, str, _Indexed]] = []
    for ix in _index():
        if kind is not None and ix.term.kind != kind:
            continue
        if domain is not None and ix.term.domain != domain:
            continue
        score, match = _score(q_norm, q_tokens, ix)
        if score > 0:
            scored.append((score, match, ix))
    # Deterministic ordering: score desc, then shortest name, then prefix, then name.
    scored.sort(key=lambda s: (-s[0], len(s[2].term.name), s[2].term.prefix, s[2].term.name))
    return [
        Candidate(
            iri=ix.term.iri,
            curie=ix.term.curie,
            prefix=ix.term.prefix,
            name=ix.term.name,
            kind=ix.term.kind,
            label=ix.term.label,
            vocab_title=ix.term.vocab_title,
            domain=ix.term.domain,
            score=score,
            match=match,
        )
        for score, match, ix in scored[: max(0, limit)]
    ]


def vocabularies() -> list[Vocabulary]:
    """The curated vocabularies (for listing the recognized standards)."""
    return list(load_catalog())
