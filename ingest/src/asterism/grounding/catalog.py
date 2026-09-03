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
    """One real term in a curated vocabulary. ``iri == namespace + name`` unless the
    vocabulary mints OPAQUE identifiers, in which case the real IRI is carried
    explicitly (``explicit_iri``) and ``name`` holds the term's own ``skos:prefLabel``.

    ⭐なぜ名前と IRI を分けられるようにするか: EMMO は語を
    ``https://w3id.org/emmo#EMMO_4f2a...`` のような不透明 IRI で鋳造し、読める名前は
    ``skos:prefLabel`` にしか無い（実測 2026-09-03: emmo# の 2,631 語中 1,967 語が
    不透明）。名前＝IRI の末尾に固定したままでは、**照合できる語**（人も AI も
    「Crystal」で探す）と**実在する IRI**（引用の同一性）のどちらかを諦めることに
    なる。捏造しないという不変条件は IRI 側の話なので、そこは実ファイルの値を
    そのまま持ち、照合は prefLabel でやる。
    """

    prefix: str
    namespace: str
    name: str
    kind: str  # "class" | "property"
    label: str
    vocab_title: str
    domain: str
    #: 不透明 IRI の語彙だけが持つ。空なら ``namespace + name``（従来どおり）。
    explicit_iri: str = ""

    @property
    def iri(self) -> str:
        return self.explicit_iri or (self.namespace + self.name)

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
    #: 語彙がカタログに並んでいる順。同点のときの優先順位に使う（下記 ``ground_terms``）。
    vocab_rank: int = 0


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
                    explicit_iri=str(t.get("iri", "")),
                )
            )
    return tuple(out)


@functools.lru_cache(maxsize=1)
def _index() -> tuple[_Indexed, ...]:
    idx: list[_Indexed] = []
    # カタログに並んでいる順＝「その分野で当てにする順」。同点の決着に使う。
    rank_of = {v.prefix: i for i, v in enumerate(load_catalog())}
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
                vocab_rank=rank_of.get(term.prefix, len(rank_of)),
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
    # Deterministic ordering: score desc, then the CATALOG's own vocabulary order,
    # then shortest name, then prefix, then name.
    #
    # ⭐同点のときにアルファベット順で決めると、優先されるべき標準が押しのけられる
    # （実測 2026-09-03: EMMO を入れた直後、"quantity" が qudt:Quantity ではなく
    # emmo:Quantity を先頭にした — 'emmo' < 'qudt' というだけの理由で）。この
    # カタログは既に「その分野で当てにする順」に並べてある（材料 → 汎用）ので、
    # 並び順そのものを優先順位として使う。curation の判断を 1 か所に保てる。
    scored.sort(
        key=lambda s: (
            -s[0],
            s[2].vocab_rank,
            len(s[2].term.name),
            s[2].term.prefix,
            s[2].term.name,
        )
    )
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


def catalog_terms(*, kind: str | None = None, domain: str | None = None) -> list[VocabTerm]:
    """Every curated term, optionally filtered — the closed set itself, unranked.

    :func:`ground_terms` answers "what matches THIS query"; this answers "what
    spellings exist at all". The design stage needs the latter: a proposer that
    has never seen the catalog can only guess the standard's spelling from
    memory, and a near-miss (``Crystal`` for ``CrystalStructure``) costs the
    reviewer an identity judgement later that an exact spelling would have made
    a one-click confirmation (external-standard-alignment.md §8).
    """
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"kind must be one of {sorted(_KINDS)} or None, got {kind!r}")
    return [
        t
        for t in _all_terms()
        if (kind is None or t.kind == kind) and (domain is None or t.domain == domain)
    ]


def vocabularies() -> list[Vocabulary]:
    """The curated vocabularies (for listing the recognized standards)."""
    return list(load_catalog())
