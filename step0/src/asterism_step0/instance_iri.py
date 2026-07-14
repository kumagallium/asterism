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

import re
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_IRI_BASE",
    "dataset_namespace_block",
    "normalize_iri_base",
    "placeholder_prefix_issue",
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
