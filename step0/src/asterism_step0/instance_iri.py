"""Instance-owned IRI base for NEWLY designed datasets.

Who owns a minted IRI is part of the identity of the data. The bundled example
datasets (starrydata / papers / materials_project) are published by this
repository and correctly live under ``https://kumagallium.github.io/asterism/…``
(their ``dataset.toml`` declares it; CLAUDE.md pins those identifiers as
immutable). A **local install designing its own dataset is a different owner**:
its new namespaces must be minted neither under the upstream author's domain
nor under an LLM-invented placeholder (``example.org`` — the historic failure
mode this module closes; ``tool_propose`` has carried the matching NEVER rule
since #260, the design path was unguarded).

The base is an instance setting (env ``ASTERISM_IRI_BASE``, resolved by the
API's ``Settings`` and threaded into every design-time prompt). When the
operator has not set one we fall back to ``https://asterism.invalid`` —
``.invalid`` is reserved by RFC 2606 to never resolve, so an unconfigured
instance's IRIs are self-describing ("this identifier has no published home
yet") instead of squatting on someone else's domain. Operators mint citable
identifiers by setting the env to a namespace they control (an org domain or
``https://<user>.github.io/<repo>``).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_IRI_BASE",
    "dataset_namespace_block",
    "dataset_namespace_info",
    "derive_prefix_pair",
    "normalize_dataset_namespace",
    "normalize_iri_base",
    "placeholder_prefix_issue",
    "slugify_dataset_name",
]

DEFAULT_IRI_BASE = "https://asterism.invalid"

# Hosts that can only ever be placeholders in a minted namespace: RFC 2606
# example domains (any subdomain) and localhost. ``.invalid`` is deliberately
# NOT matched — it is our own explicit "unconfigured instance" marker and must
# survive validation.
_PLACEHOLDER_HOST = re.compile(r"(?:^|\.)example\.(?:org|com|net|edu)$|^localhost$", re.I)


def normalize_iri_base(value: str | None) -> str:
    """The effective instance base: operator value stripped of trailing '/',
    or the RFC 2606 ``.invalid`` default when unset/blank."""
    v = (value or "").strip().rstrip("/")
    return v or DEFAULT_IRI_BASE


def dataset_namespace_block(iri_base: str | None) -> str:
    """User-message block pinning where THIS design must mint its namespaces.

    Rides the user message (never the frozen system prompts) so the cacheable
    prompts stay byte-stable — the #244 language-instruction pattern.
    """
    base = normalize_iri_base(iri_base)
    return (
        "# Namespace policy for THIS dataset (fixed — not yours to choose)\n\n"
        "Mint this dataset's two NEW namespaces under this instance's IRI base:\n\n"
        f"- ontology: `{base}/datasets/<slug>/ontology#`\n"
        f"- resource: `{base}/datasets/<slug>/resource/`\n\n"
        "Replace `<slug>` with ONE short kebab-case name drawn from the data's "
        "subject (e.g. `xrd-powder`); use the SAME slug in both. Namespaces "
        "appearing in instruction examples (starrydata etc.) are examples, not "
        "this dataset's. NEVER mint under example.org / example.com or any other "
        "placeholder domain. Reused standard vocabularies (schema:, dcterms:, "
        "prov:, qudt:, …) keep their own namespaces.\n"
    )


# ---------------------------------------------------------------------------
# Deterministic dataset-namespace naming (kantan ADR K13).
#
# A CURIE prefix ("al3v:") is pure notation — it never appears in stored data,
# so neither the LLM nor the researcher should have to choose it (2026-07-23
# ZEM dogfood: the skeleton gate asked a researcher to judge a name that cannot
# matter). The ONE naming judgment that persists is the dataset slug inside the
# minted IRI; the prefix pair derives from it mechanically, and an AI-proposed
# skeleton is normalized into this canonical shape before any human sees it.
# ---------------------------------------------------------------------------

# Prefix names a derived pair must never shadow: RDF builtins plus the standard
# vocabularies the design prompts invite the model to reuse (kept in sync with
# ui/src/vocab.ts KNOWN_VOCABS) plus names the instruction examples use.
_RESERVED_PREFIX_NAMES = frozenset(
    {
        "xsd", "rdf", "rdfs", "owl", "sh", "fn",
        "schema", "prov", "dcterms", "dc", "bibo", "skos", "foaf", "dcat", "sosa",
        "qudt", "unit", "emmo", "cmso",
        "doco", "deo", "fabio", "cito", "po", "sd", "sdr", "ast",
    }
)

_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")

# The canonical minted shape, matched on ANY host so a wrong-base mint is still
# recognized (and repaired by normalize_dataset_namespace, not just flagged).
_MINT_ONTOLOGY_RE = re.compile(r"/datasets/(?P<slug>[^/#?]+)/ontology#$")
_MINT_RESOURCE_RE = re.compile(r"/datasets/(?P<slug>[^/#?]+)/resource/$")


def slugify_dataset_name(name: str | None) -> str:
    """Kebab slug for the minted-IRI dataset segment (same cleaning rule as the
    API registry's dataset-id slug, minus the uuid suffix)."""
    s = _SLUG_CLEAN_RE.sub("-", (name or "").lower()).strip("-")
    return s or "dataset"


def derive_prefix_pair(slug: str, *, taken: Collection[str] = ()) -> tuple[str, str]:
    """The (ontology, resource) CURIE prefix names derived from ``slug``.

    Deterministic so the prefix is never a judgment call: the first slug token,
    extended token-by-token when it collides with a reserved/standard name or a
    prefix already ``taken`` in this design, ``ds``/``ds2``/… as the last
    resort. The resource name is the ontology name + ``r`` (the sd:/sdr:
    convention the prompts already teach). NCName-safe: a digit-leading
    candidate gets a ``d`` head (``3v`` → ``d3v``).
    """
    tokens = [t for t in _SLUG_CLEAN_RE.split((slug or "").lower()) if t]
    candidates: list[str] = []
    if tokens:
        candidates.append(tokens[0])
        if len(tokens) > 1:
            candidates.append(tokens[0] + tokens[1])
        candidates.append("".join(tokens))
    candidates.append("ds")
    bad = _RESERVED_PREFIX_NAMES | {str(t) for t in taken}
    for cand in itertools.chain(candidates, (f"ds{n}" for n in itertools.count(2))):
        name = f"d{cand}" if cand[0].isdigit() else cand
        if name not in bad and f"{name}r" not in bad:
            return name, f"{name}r"
    raise AssertionError("unreachable")  # pragma: no cover — the ds{n} tail is infinite


def dataset_namespace_info(
    prefixes: Mapping[str, Any], iri_base: str | None
) -> dict[str, Any] | None:
    """The skeleton gate's view of this dataset's minted namespace pair, or
    ``None`` when no ``…/datasets/<slug>/…`` mint is present.

    Names which prefixes are THIS dataset's (vs reused standard vocabularies),
    under which base, and whether that base is operator-configured — so the UI
    can offer "dataset name" as the one editable judgment and route base fixes
    to Settings instead of a raw-IRI textbox."""
    onto = res = slug = None
    for name, iri in prefixes.items():
        s = str(iri)
        m = _MINT_ONTOLOGY_RE.search(s)
        if m and onto is None:
            onto = str(name)
            slug = slug or m.group("slug")
            continue
        m = _MINT_RESOURCE_RE.search(s)
        if m and res is None:
            res = str(name)
            slug = slug or m.group("slug")
    if slug is None:
        return None
    base = normalize_iri_base(iri_base)
    return {
        "slug": slug,
        "base": base,
        # Same rule as the API's /instance info: the Settings value arrives
        # already resolved, so "configured" means "not the .invalid default".
        "base_configured": base != DEFAULT_IRI_BASE,
        "ontology_prefix": onto,
        "resource_prefix": res,
    }


def normalize_dataset_namespace(
    skeleton: Mapping[str, Any],
    iri_base: str | None,
    *,
    fallback_slug: str | None = None,
) -> dict[str, Any]:
    """Return ``skeleton`` with its minted namespace pair in canonical form.

    Canonical form: ``{base}/datasets/{slug}/ontology#`` named by
    :func:`derive_prefix_pair`'s ontology name, and the matching ``resource/``
    pair — where ``base`` is this instance's IRI base and ``slug`` is the
    dataset segment the model minted (or ``fallback_slug`` when nothing
    recognizable was minted). CURIE references in every map (subject template /
    constant / classes) are renamed in lockstep.

    Recognized as minted: prefixes whose IRI matches the canonical shape on ANY
    host (a wrong-base mint is repaired), plus placeholder-domain prefixes
    (example.org & co) classified by how the maps use them (classes → ontology,
    subject templates → resource). Standard vocabularies and anything
    unrecognized pass through untouched — the placeholder gate still guards
    those. Idempotent: canonical input comes back byte-identical.
    """
    prefixes = {str(k): str(v) for k, v in (skeleton.get("prefixes") or {}).items()}
    maps = [m for m in (skeleton.get("maps") or []) if isinstance(m, Mapping)]

    old_onto = old_res = slug = None
    for name, iri in prefixes.items():
        m = _MINT_ONTOLOGY_RE.search(iri)
        if m and old_onto is None:
            old_onto = name
            slug = slug or m.group("slug")
            continue
        m = _MINT_RESOURCE_RE.search(iri)
        if m and old_res is None:
            old_res = name
            slug = slug or m.group("slug")

    if old_onto is None or old_res is None:
        # Placeholder-domain mints (example.org & co) don't match the shape;
        # classify them by USE so they're repaired instead of merely flagged.
        class_pfx = {
            c.split(":", 1)[0]
            for mp in maps
            for c in (mp.get("subject") or {}).get("classes") or []
            if isinstance(c, str) and ":" in c
        }
        tmpl_pfx = set()
        for mp in maps:
            t = (mp.get("subject") or {}).get("template")
            if isinstance(t, str) and ":" in t and not t.startswith(("http://", "https://")):
                tmpl_pfx.add(t.split(":", 1)[0])
        for name, iri in prefixes.items():
            if name in (old_onto, old_res) or placeholder_prefix_issue(name, iri) is None:
                continue
            if old_onto is None and name in class_pfx:
                old_onto = name
            elif old_res is None and name in tmpl_pfx:
                old_res = name

    if old_onto is None and old_res is None:
        return dict(skeleton)  # nothing recognizable minted — leave untouched

    slug = slugify_dataset_name(slug or fallback_slug)
    base = normalize_iri_base(iri_base)
    renamed = {n for n in (old_onto, old_res) if n is not None}
    new_onto, new_res = derive_prefix_pair(slug, taken=[n for n in prefixes if n not in renamed])
    rename = {old: new for old, new in ((old_onto, new_onto), (old_res, new_res)) if old}

    new_prefixes: dict[str, str] = {}
    placed = False
    for name, iri in prefixes.items():
        if name in renamed:
            if not placed:  # keep the pair at the first minted position
                new_prefixes[new_onto] = f"{base}/datasets/{slug}/ontology#"
                new_prefixes[new_res] = f"{base}/datasets/{slug}/resource/"
                placed = True
            continue
        new_prefixes[name] = iri

    def _ren(value: Any) -> Any:
        if isinstance(value, str):
            for old, new in rename.items():
                if value.startswith(f"{old}:"):
                    return f"{new}:{value[len(old) + 1:]}"
        return value

    new_maps = []
    for mp in maps:
        subject = dict(mp.get("subject") or {})
        for key in ("template", "constant"):
            if key in subject:
                subject[key] = _ren(subject[key])
        if isinstance(subject.get("classes"), list):
            subject["classes"] = [_ren(c) for c in subject["classes"]]
        new_maps.append({**mp, "subject": subject})

    return {**skeleton, "prefixes": new_prefixes, "maps": new_maps}


def placeholder_prefix_issue(name: str, iri: str) -> str | None:
    """An issue string when a declared prefix points at a placeholder domain,
    else None. Fed to the design self-correction loop (design_loop) like any
    other parse issue, so the model re-mints instead of shipping dead IRIs."""
    try:
        host = urlsplit(iri).hostname
    except ValueError:
        return None  # unparseable IRIs are caught by the structural checks
    if host and _PLACEHOLDER_HOST.search(host):
        return (
            f"prefix {name!r}: <{iri}> uses the placeholder domain {host!r} — such "
            "IRIs identify nothing and can never be published. Mint this dataset's "
            "namespaces under the instance IRI base from the namespace policy "
            "instead (ontology `…/datasets/<slug>/ontology#`, resource "
            "`…/datasets/<slug>/resource/`)."
        )
    return None
