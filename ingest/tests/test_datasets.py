"""Tests for asterism.datasets — the generic dataset-descriptor loader (#20 P2).

Two jobs:
1. The loader reads ``datasets/<name>/dataset.toml`` and degrades to None safely.
2. **Contract**: starrydata's embedded fallback constants in asterism.starrydata
   stay in sync with its declared descriptor (the descriptor is the SoT). If they
   drift, this fails — catching a hand-edit of one but not the other.
"""

from __future__ import annotations

from pathlib import Path

from asterism import starrydata
from asterism.datasets import datasets_root, load_dataset

# ingest/tests/ -> ingest/ -> <repo>/ ; the content tree lives at <repo>/datasets.
REPO_DATASETS = Path(__file__).resolve().parents[2] / "datasets"


def test_load_starrydata_descriptor() -> None:
    d = load_dataset("starrydata", root=REPO_DATASETS)
    assert d is not None
    assert d.name == "starrydata"
    assert d.ontology_iri == "https://kumagallium.github.io/asterism/starrydata/ontology#"
    assert d.resource_iri == "https://kumagallium.github.io/asterism/starrydata/resource/"
    assert d.graph_base == "https://kumagallium.github.io/asterism/starrydata/graph/"


def test_load_dataset_missing_returns_none() -> None:
    assert load_dataset("no-such-dataset", root=REPO_DATASETS) is None
    assert load_dataset("starrydata", root="/nonexistent/path") is None


def test_datasets_root_finds_repo_tree() -> None:
    # With no env override, the walk-up resolution should find the repo's
    # datasets/ tree (dev / editable install).
    root = datasets_root()
    assert root is not None
    assert (root / "starrydata" / "dataset.toml").is_file()


def test_starrydata_constants_match_descriptor() -> None:
    # Contract: the engine-embedded fallback equals the declared descriptor.
    d = load_dataset("starrydata", root=REPO_DATASETS)
    assert d is not None
    assert d.ontology_iri == starrydata.DEFAULT_ONTOLOGY
    assert d.resource_iri == starrydata.DEFAULT_RESOURCE
    assert d.software_agent_iri == starrydata.SOFTWARE_AGENT_IRI
