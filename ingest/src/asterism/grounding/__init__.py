"""External-standard GROUNDING — propose REAL term IRIs from a curated, closed set.

When a human (or AI-assisted ``propose``) designs a class/predicate, Asterism can
suggest the matching term from a famous external standard (CMSO / QUDT / schema.org /
PROV …) so the data REUSES / ALIGNS to it instead of re-minting a private term
(external-standard-alignment.md §2/§8). The candidates come ONLY from
:mod:`asterism.grounding.catalog`'s curated SoT (``known_vocabs.yaml``) — every IRI is
real and verified against the authoritative ontology, so nothing is fabricated; the
human still confirms the choice. This is the same closed-set + human-vet safety model
as the Tier-0 function library and the crosswalk normalizers.
"""

from asterism.grounding.catalog import (
    Candidate,
    VocabTerm,
    Vocabulary,
    ground_terms,
    load_catalog,
    vocabularies,
)

__all__ = [
    "Candidate",
    "VocabTerm",
    "Vocabulary",
    "ground_terms",
    "load_catalog",
    "vocabularies",
]
