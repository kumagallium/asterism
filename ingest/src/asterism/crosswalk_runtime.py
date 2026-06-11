"""Crosswalk HUB runtime — read the live store, build the hub, write it back.

The Turtle is built by the PURE, tested library :mod:`asterism.crosswalk`; this
module is the I/O half (ADR ``docs/architecture/crosswalk-hub.md`` productize ②):

- a **persisted participation config** (``crosswalk-bridge/crosswalk.yaml`` in the
  registry) replaces the spike's hardcoded rules — adding a dataset = adding a
  participant, authored once and re-read on every rebuild;
- :func:`build_hub` reads each participating dataset's composition values from its
  **exact promoted canonical graph** (no draft leakage, no substring matching),
  normalizes the join key 100% in Python (single source of truth =
  ``asterism.crosswalk.NORMALIZERS``), bounds the read to **shared** values, delegates
  the Turtle to ``build_turtle``, and writes the hub graph + control ``promoted``
  flag so the FROM-merge unions it — **engine unchanged**;
- registry helpers persist the config + the ``crosswalk-bridge`` dataset scaffold so
  the catalog lists the hub and the auto-rebuild hook can re-read the participants.

The trust model is the Tier-0 one: the normalization (the join key) is a vetted,
named function; nothing is generated at runtime. The hub is a *derived dated claim*.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from asterism import substrate
from asterism.crosswalk import (
    NORMALIZERS,
    XW,
    Concept,
    CrosswalkConfig,
    Rule,
    build_turtle,
    normalize_identity,
)

# The hub lives in a per-dataset canonical graph keyed ``crosswalk`` (the registry
# id is ``crosswalk-bridge``; the graph name stays the short ``crosswalk``). One
# canonical named graph the FROM-merge already unions (engine unchanged).
DATASET_ID = "crosswalk-bridge"
HUB_GRAPH = substrate.canonical_graph_iri("crosswalk")
ACTIVITY_IRI = "https://kumagallium.github.io/asterism/crosswalk/resource/build/latest"

# Default composition concept (the proven one). The config is multi-concept-ready;
# the authoring UI starts with composition.
DEFAULT_CONCEPT_NAME = "composition"
DEFAULT_CLASS_IRI = f"{XW}Composition"
DEFAULT_LINK_PREDICATE = f"{XW}hasComposition"
DEFAULT_NORMALIZER = "composition"

_CONFIG_FILE = "crosswalk.yaml"
_META_FILE = "meta.json"


# ---------------------------------------------------------------------------
# Config (yaml <-> dataclass). The persisted participation rules.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeParticipant:
    """One dataset's participation in a concept: its registry id (resolves the exact
    promoted graph), a human label (used in provenance + link stats), and the
    predicate that carries the concept's value (the human-vetted mapping claim)."""

    dataset_id: str
    label: str
    predicate: str


@dataclass(frozen=True)
class RuntimeConcept:
    """A shared hub concept: a class + link predicate + named normalizer (the join
    key) + the per-dataset participants that map into it."""

    name: str
    class_iri: str
    link_predicate: str
    normalizer: str
    participants: tuple[RuntimeParticipant, ...]


@dataclass(frozen=True)
class RuntimeCrosswalkConfig:
    """The whole growing hub as persisted config: a set of shared concepts."""

    concepts: tuple[RuntimeConcept, ...]
    min_datasets: int = 2

    def dataset_ids(self) -> set[str]:
        """Every registry id that participates in any concept (for the auto-rebuild
        hook: is THIS just-promoted/appended dataset part of the crosswalk?)."""
        return {p.dataset_id for c in self.concepts for p in c.participants}


def parse_config(data: dict) -> RuntimeCrosswalkConfig:
    """Build a :class:`RuntimeCrosswalkConfig` from a plain dict (yaml / request body).

    A bare ``{"name": ..., "participants": [...]}`` (single concept) is accepted as a
    convenience and wrapped. Raises ``ValueError`` on a structurally invalid config.
    """
    if not isinstance(data, dict):
        raise ValueError("crosswalk config must be a mapping")
    raw_concepts = data.get("concepts")
    if raw_concepts is None and "participants" in data:
        raw_concepts = [data]  # single-concept shorthand
    if not isinstance(raw_concepts, list) or not raw_concepts:
        raise ValueError("crosswalk config needs a non-empty 'concepts' list")
    concepts: list[RuntimeConcept] = []
    for c in raw_concepts:
        if not isinstance(c, dict):
            raise ValueError("each concept must be a mapping")
        name = str(c.get("name") or DEFAULT_CONCEPT_NAME)
        parts_raw = c.get("participants") or []
        if not isinstance(parts_raw, list) or not parts_raw:
            raise ValueError(f"concept {name!r} needs a non-empty 'participants' list")
        participants: list[RuntimeParticipant] = []
        for p in parts_raw:
            if not isinstance(p, dict):
                raise ValueError("each participant must be a mapping")
            dsid = str(p.get("dataset_id") or "").strip()
            pred = str(p.get("predicate") or "").strip()
            if not dsid or not pred:
                raise ValueError("each participant needs dataset_id and predicate")
            participants.append(
                RuntimeParticipant(
                    dataset_id=dsid,
                    label=str(p.get("label") or dsid),
                    predicate=pred,
                )
            )
        concepts.append(
            RuntimeConcept(
                name=name,
                class_iri=str(c.get("class_iri") or DEFAULT_CLASS_IRI),
                link_predicate=str(c.get("link_predicate") or DEFAULT_LINK_PREDICATE),
                normalizer=str(c.get("normalizer") or DEFAULT_NORMALIZER),
                participants=tuple(participants),
            )
        )
    min_datasets = int(data.get("min_datasets", 2) or 2)
    return RuntimeCrosswalkConfig(concepts=tuple(concepts), min_datasets=max(2, min_datasets))


def config_to_dict(config: RuntimeCrosswalkConfig) -> dict:
    """Serialize a config back to a plain dict (for yaml persistence / API response)."""
    return {
        "min_datasets": config.min_datasets,
        "concepts": [
            {
                "name": c.name,
                "class_iri": c.class_iri,
                "link_predicate": c.link_predicate,
                "normalizer": c.normalizer,
                "participants": [
                    {"dataset_id": p.dataset_id, "label": p.label, "predicate": p.predicate}
                    for p in c.participants
                ],
            }
            for c in config.concepts
        ],
    }


# ---------------------------------------------------------------------------
# Build outcome
# ---------------------------------------------------------------------------


@dataclass
class BuildOutcome:
    """Result of a hub rebuild: per-concept shared keys + link counts, and which
    participants were used vs skipped (skipped = not promoted -> not in the FROM
    scope, so excluded — never silently)."""

    built_at: str
    hub_graph: str
    triple_count: int
    shared: dict[str, list[str]]
    links: dict[str, dict[str, int]]
    participants_used: list[dict]
    participants_skipped: list[dict]

    @property
    def shared_total(self) -> int:
        return sum(len(v) for v in self.shared.values())


# ---------------------------------------------------------------------------
# SPARQL helpers
# ---------------------------------------------------------------------------


def _sparql_str(s: str) -> str:
    """A SPARQL double-quoted string literal body (escape \\, ", and controls)."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _literal_values(rows: list[dict], var: str) -> list[str]:
    out: list[str] = []
    for b in rows:
        v = b.get(var, {})
        if "value" in v:
            out.append(v["value"])
    return out


async def _select_bindings(client, query: str) -> list[dict]:
    data = await client.sparql_select(query)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


async def _distinct_values(client, graph: str, predicate: str) -> list[str]:
    """Pass 1: every distinct raw value of ``?e <predicate> ?v`` in ``graph`` (bounded
    by the number of distinct values, not entities)."""
    rows = await _select_bindings(
        client,
        f"SELECT DISTINCT ?v WHERE {{ GRAPH <{graph}> {{ ?e <{predicate}> ?v }} }}",
    )
    return _literal_values(rows, "v")


async def _entities_for_values(
    client, graph: str, predicate: str, values: list[str]
) -> list[tuple[str, str]]:
    """Pass 2: ``(entity, raw)`` pairs whose raw value is in ``values`` — bounded to
    the SHARED set, so the read is O(#shared-entities), not O(#entities)."""
    if not values:
        return []
    vals = " ".join(f'"{_sparql_str(v)}"' for v in values)
    rows = await _select_bindings(
        client,
        f"SELECT ?e ?v WHERE {{ GRAPH <{graph}> {{ ?e <{predicate}> ?v }} "
        f"VALUES ?v {{ {vals} }} }}",
    )
    out: list[tuple[str, str]] = []
    for b in rows:
        e = b.get("e", {})
        v = b.get("v", {})
        if e.get("type") == "uri" and "value" in v:
            out.append((e["value"], v["value"]))
    return out


# ---------------------------------------------------------------------------
# Build / remove
# ---------------------------------------------------------------------------


async def build_hub(client, config: RuntimeCrosswalkConfig, *, built_at: str) -> BuildOutcome:
    """Rebuild the crosswalk hub from the live store (read FROM the promoted canonical
    graphs, write the hub graph + control flag). Idempotent (drop + replace).

    ``client`` is an :class:`asterism.oxigraph_client.OxigraphClient` (satisfies the
    SPARQL select/update + Turtle-post slices). The read is bounded to shared values;
    normalization happens in Python via the concept's named normalizer.
    """
    promoted = set(await substrate.canonical_graphs(client))
    observations: dict[tuple[str, str], list[tuple[str, str]]] = {}
    used_rules: dict[str, list[Rule]] = {}
    used: list[dict] = []
    skipped: list[dict] = []
    seen_used: set[str] = set()

    for concept in config.concepts:
        normalize = NORMALIZERS.get(concept.normalizer, normalize_identity)
        # Resolve each participant to its EXACT promoted live graph (skip if not
        # citable — draft / retracted / absent never enters the hub).
        live: dict[str, str] = {}
        for p in concept.participants:
            key_graph = substrate.canonical_graph_iri(p.dataset_id)
            live_graph = await substrate.live_graph_of(client, key_graph) or key_graph
            if live_graph not in promoted:
                skipped.append(
                    {
                        "dataset_id": p.dataset_id,
                        "label": p.label,
                        "concept": concept.name,
                        "reason": "not promoted (excluded from the citable FROM-merge)",
                    }
                )
                continue
            live[p.label] = live_graph
            used_rules.setdefault(concept.name, []).append(Rule(p.label, p.predicate))
            if p.dataset_id not in seen_used:
                seen_used.add(p.dataset_id)
                used.append({"dataset_id": p.dataset_id, "label": p.label})

        active = [p for p in concept.participants if p.label in live]
        if len(active) < config.min_datasets:
            continue  # nothing can be shared by >= min_datasets participants

        # Pass 1: distinct raw values per participant -> the shared normalized keys.
        per_label_raws: dict[str, list[str]] = {}
        norm_counts: dict[str, int] = {}
        for p in active:
            raws = await _distinct_values(client, live[p.label], p.predicate)
            per_label_raws[p.label] = raws
            for k in {normalize(r) for r in raws}:
                norm_counts[k] = norm_counts.get(k, 0) + 1
        shared = {k for k, n in norm_counts.items() if n >= config.min_datasets}
        if not shared:
            continue

        # Pass 2: bounded read of (entity, raw) for raws whose key is shared.
        for p in active:
            shared_raws = [r for r in per_label_raws[p.label] if normalize(r) in shared]
            observations[(concept.name, p.label)] = await _entities_for_values(
                client, live[p.label], p.predicate, shared_raws
            )

    # Delegate Turtle construction to the tested pure library (multi-concept,
    # per-link provenance). Only concepts with >= min_datasets used participants
    # are passed (so build provenance reflects what actually entered the hub).
    lib_concepts = tuple(
        Concept(
            name=c.name,
            class_iri=c.class_iri,
            link_predicate=c.link_predicate,
            normalizer=c.normalizer,
            rules=tuple(used_rules.get(c.name, ())),
        )
        for c in config.concepts
        if used_rules.get(c.name)
    )
    result = build_turtle(
        CrosswalkConfig(lib_concepts, min_datasets=config.min_datasets),
        observations,
        activity_iri=ACTIVITY_IRI,
        built_at=built_at,
    )
    triple_count = _count_triples(result.turtle)

    # Write: replace the hub graph (idempotent), flag it promoted so the FROM-merge
    # unions it. drop_graph is safe here — the hub is small (bounded to shared).
    await substrate.drop_graph(client, HUB_GRAPH)
    await client.post_turtle_bytes(result.turtle.encode("utf-8"), graph_iri=HUB_GRAPH)
    await substrate.mark_graph_promoted(client, HUB_GRAPH)

    return BuildOutcome(
        built_at=built_at,
        hub_graph=HUB_GRAPH,
        triple_count=triple_count,
        shared=result.shared,
        links=result.links,
        participants_used=used,
        participants_skipped=skipped,
    )


async def remove_hub(client) -> None:
    """Tear down the hub: drop the graph and clear its control ``promoted`` flag."""
    await substrate.drop_graph(client, HUB_GRAPH)
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f"<{HUB_GRAPH}> <{substrate.STATUS_PREDICATE}> ?o }} }}"
    )


def _count_triples(turtle: str) -> int:
    """Count the asserted triples in the hub Turtle: every line ending in ``.`` that
    is not a ``@prefix`` directive or a comment (the builder emits one statement per
    line, so this is exact for hub output)."""
    n = 0
    for line in turtle.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("@prefix") and s.endswith("."):
            n += 1
    return n


# ---------------------------------------------------------------------------
# Registry persistence (filesystem; no asterism_api dependency)
# ---------------------------------------------------------------------------


def _dataset_dir(registry_root: Path | str) -> Path:
    return Path(registry_root) / DATASET_ID


def load_config(registry_root: Path | str) -> RuntimeCrosswalkConfig | None:
    """Read the persisted crosswalk config, or ``None`` if there is no hub yet."""
    path = _dataset_dir(registry_root) / _CONFIG_FILE
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_config(data)


def save_config(registry_root: Path | str, config: RuntimeCrosswalkConfig) -> Path:
    """Persist the crosswalk config to ``crosswalk-bridge/crosswalk.yaml``."""
    d = _dataset_dir(registry_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / _CONFIG_FILE
    path.write_text(
        yaml.safe_dump(config_to_dict(config), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def write_registry_scaffold(
    registry_root: Path | str, config: RuntimeCrosswalkConfig, outcome: BuildOutcome
) -> dict:
    """Create/refresh the ``crosswalk-bridge`` registry dataset so the catalog lists
    the hub. Updates meta stats every build; seeds model.yaml / diagram.md and the
    generic ``datasets_for_composition`` tool **only if absent** (never clobbers
    human-authored tools like ``zt_by_crystal_structure``)."""
    d = _dataset_dir(registry_root)
    d.mkdir(parents=True, exist_ok=True)
    meta_path = d / _META_FILE
    meta: dict = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}

    classes = sorted({_local_name(c.class_iri) for c in config.concepts})
    participants = sorted({p.label for c in config.concepts for p in c.participants})
    meta.update(
        {
            "id": DATASET_ID,
            "name": meta.get("name") or "crosswalk hub (shared concepts across datasets)",
            "created_at": meta.get("created_at") or outcome.built_at,
            "complete": True,
            "exit_code": 0,
            "classes": classes,
            "class_count": len(classes),
            "has_ingester": False,
            "has_mie": False,
            "has_rml": False,
            "ingested": True,
            "promoted": True,
            "status": "active",
            "triple_count": outcome.triple_count,
            "triples_promoted": outcome.triple_count,
            "canonical_graph": HUB_GRAPH,
            "warnings": [],
            "traps": [],
            # crosswalk-specific facets (the UI + auto-rebuild hook read these).
            "is_crosswalk": True,
            "crosswalk_participants": participants,
            "crosswalk_shared_compositions": outcome.shared_total,
            "crosswalk_built_at": outcome.built_at,
            "crosswalk_concepts": [c.name for c in config.concepts],
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    model = d / "model.yaml"
    if not model.is_file():
        model.write_text(
            "".join(f"- {_local_name(c.class_iri)}:\n" for c in config.concepts),
            encoding="utf-8",
        )
    diagram = d / "diagram.md"
    if not diagram.is_file():
        diagram.write_text(_default_diagram(config), encoding="utf-8")
    tools = d / "query_tools.yaml"
    if not tools.is_file():
        tools.write_text(GENERIC_TOOLS, encoding="utf-8")
    return meta


def _local_name(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or iri


def _default_diagram(config: RuntimeCrosswalkConfig) -> str:
    lines = ["```mermaid", "classDiagram"]
    for c in config.concepts:
        cls = _local_name(c.class_iri)
        lines.append(f"  class {cls}")
        lines.append(f"  class Entity_{cls}")
        lines.append(f"  Entity_{cls} --> {cls} : {_local_name(c.link_predicate)}")
    lines.append("```")
    return "\n".join(lines) + "\n"


# Generic, hub-resident cross-dataset tool. Deterministic, citable, KEY-FREE — it
# belongs to the JOIN (lives with the hub, not either source dataset). Seeded only
# when no query_tools.yaml exists, so domain-specific tools authored later survive.
GENERIC_TOOLS = r"""# Cross-dataset CROSSWALK tools — they live with the HUB, not with either source
# dataset, because they belong to the JOIN. The crosswalk shared entity is the
# deterministic join key, so these are reproducible, citable, key-free.
tools:
  - name: datasets_for_composition
    title: "Which datasets report a given composition (via the crosswalk hub)"
    description: >
      List the named graphs (datasets) that have an entity linked to the crosswalk
      composition matching the given label — shows how many sources the hub joins
      for one composition.
    parameters:
      - name: composition
        type: string
        required: true
        description: 'normalized composition label, e.g. "Bi2Te3" or "Ba8Ga16Ge30"'
    query: |
      PREFIX xw: <https://kumagallium.github.io/asterism/crosswalk/ontology#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT ?dataset_graph (COUNT(DISTINCT ?e) AS ?entities)
      WHERE {
        ?comp a xw:Composition ; rdfs:label {{composition}} .
        ?e xw:hasComposition ?comp .
        GRAPH ?dataset_graph { ?e a ?cls }
      }
      GROUP BY ?dataset_graph
      ORDER BY DESC(?entities)
    result:
      item:
        dataset_graph: dataset_graph
        entities: { var: entities, number: true }
"""
