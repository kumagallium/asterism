"""Generic dataset-descriptor loader (#20 P2).

A dataset declares its *identity* (namespace IRIs) as content under
``<datasets_root>/<name>/dataset.toml``. This lets starrydata — and any future,
non-starrydata dataset — be **configured data rather than hardcoded core
constants** (ADR ``ontology-canonical-lifecycle.md`` §4). The engine stays
schema-agnostic; each dataset brings its own IRIs.

The loader is best-effort: it returns ``None`` (never raises) when the datasets
tree or a descriptor is absent, so callers can fall back to an embedded default.
That keeps the package importable when installed as a wheel without the repo's
``datasets/`` content (e.g. a production image that only ships the engine).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetDescriptor:
    """The declared identity of one dataset (read from its ``dataset.toml``)."""

    name: str
    ontology_iri: str
    resource_iri: str
    graph_base: str | None = None
    software_agent_iri: str | None = None
    description: str = ""


def datasets_root() -> Path | None:
    """Resolve the ``datasets/`` content root, or None if not found.

    Order: ``ASTERISM_DATASETS_ROOT`` env override, then a walk up from this
    module to find a sibling ``datasets/`` directory (the dev / editable-install
    case where the repo tree is present).
    """
    env = os.environ.get("ASTERISM_DATASETS_ROOT")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    for parent in Path(__file__).resolve().parents:
        cand = parent / "datasets"
        if cand.is_dir():
            return cand
    return None


def load_dataset(name: str, root: Path | str | None = None) -> DatasetDescriptor | None:
    """Load the descriptor for dataset ``name``, or None if unavailable.

    ``root`` overrides the search (used by tests); otherwise :func:`datasets_root`
    resolves it. Missing tree / missing file / malformed TOML / missing required
    key all yield ``None`` rather than raising.
    """
    base = Path(root) if root is not None else datasets_root()
    if base is None:
        return None
    path = base / name / "dataset.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return DatasetDescriptor(
            name=str(data.get("name", name)),
            ontology_iri=str(data["ontology_iri"]),
            resource_iri=str(data["resource_iri"]),
            graph_base=data.get("graph_base"),
            software_agent_iri=data.get("software_agent_iri"),
            description=str(data.get("description", "")),
        )
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return None
