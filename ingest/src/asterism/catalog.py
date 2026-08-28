"""Dataset **discovery** for the serving surfaces (the `find_databases` analogue).

A consumer that mounts the asterism MCP server next to other knowledge-graph MCP
servers (dbcls/togomcp & co) faces the same first question everywhere: *which
dataset holds the thing I am asking about, and what may I call on it?* togomcp
answers it with ``find_databases`` / ``get_MIE_file``; asterism had no
equivalent, so an agent could only guess from the flat tool list — every
dataset's typed tools arrive as sibling names with no indication of which
dataset they belong to, whether that dataset is citable yet, or what vocabulary
it speaks.

This module is that answer, and it is **LLM-free** (product direction
"決定論・型付きが主役"): it reads what is already on disk — the workbench
registry's ``meta.json`` (identity, lifecycle, the classes the design declares)
and each dataset's declared ``query_tools.yaml`` — and returns a compact record
per dataset. Nothing is inferred; a field is present because a human-vetted
artifact says so.

Two invariants the callers depend on:

* **Only citable datasets are listed by default.** A draft has no promoted
  graph, so a tool call against it answers from nothing (or from a staged
  version graph that may still be withdrawn). ``include_drafts=True`` opts in.
* **The reported tool name is the name the caller must actually send.** The MCP
  server prefixes a declared tool with ``{dataset}_`` when its name is already
  taken; discovery that reported the *declared* name would hand the agent a
  name that does not exist. :func:`resolve_tool_names` is therefore the single
  source of that mapping, shared by the server's registration path and by
  :func:`find_datasets`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from asterism.datasets import datasets_root, load_dataset
from asterism.query_tools import (
    QueryTool,
    bundled_tools_enabled,
    load_all_query_tools,
)

logger = logging.getLogger(__name__)

#: Tool names the MCP server registers itself. A dataset-declared tool that
#: collides with one of these is served under a ``{dataset}_`` prefix — keep this
#: in sync with the hardcoded ``@mcp.tool`` names in ``asterism_mcp.server``.
RESERVED_TOOL_NAMES: tuple[str, ...] = (
    "template_curve_fetch",
    "provenance_of",
    "schema_summary",
    "sparql_query",
    "find_datasets",
)

#: Same env var the api / demo-agent / MCP server read for the workbench registry.
ENV_REGISTRY_ROOT = "CSV2RDF_REGISTRY_ROOT"

_META_FILE = "meta.json"
_MIE_FILE = "mie.yaml"
_SAFE_ID = re.compile(r"[a-z0-9-]{1,128}")
#: A MIE is authored content, not a data file; anything larger is not a MIE and
#: is not worth parsing on a discovery call.
_MAX_MIE_BYTES = 512 * 1024
_DEFAULT_LIMIT = 50


def registry_root(root: Path | str | None = None) -> Path | None:
    """Resolve the workbench registry root: explicit argument, else the env var."""
    if root is not None:
        p = Path(root)
        return p if p.is_dir() else None
    raw = os.environ.get(ENV_REGISTRY_ROOT)
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def tool_sources(root: Path | str | None = None) -> dict[str, list[QueryTool]]:
    """Every dataset's declared query tools, in the order the server registers them.

    Bundled repo examples (``datasets/<name>/``) come first and only under the
    ``ASTERISM_BUNDLED_TOOLS=1`` opt-in; the workbench registry always follows.
    Kept here — not inline in the server — so registration and discovery cannot
    drift apart on ordering (which decides who wins a name collision).
    """
    sources: dict[str, list[QueryTool]] = (
        dict(load_all_query_tools()) if bundled_tools_enabled() else {}
    )
    reg = registry_root(root)
    if reg is not None:
        with contextlib.suppress(Exception):
            sources.update(load_all_query_tools(reg))
    return sources


def resolve_tool_names(
    sources: dict[str, list[QueryTool]],
    reserved: tuple[str, ...] = RESERVED_TOOL_NAMES,
) -> dict[str, dict[str, str]]:
    """Map ``{dataset: {declared_name: served_name}}`` for a tool-source mapping.

    Mirrors the server's collision rule exactly: first declaration keeps the bare
    name, later collisions (and anything colliding with a reserved name) are
    prefixed with ``{dataset}_``.
    """
    taken = set(reserved)
    out: dict[str, dict[str, str]] = {}
    for dataset, tools in sources.items():
        mapping: dict[str, str] = {}
        for tool in tools:
            served = tool.name if tool.name not in taken else f"{dataset}_{tool.name}"
            taken.add(served)
            mapping[tool.name] = served
        out[dataset] = mapping
    return out


def _mie_description(dataset_dir: Path) -> str:
    """``schema_info.description`` from the dataset's vetted MIE, or ``""``.

    Best-effort by contract: a missing / oversized / malformed MIE degrades to no
    description rather than failing the whole discovery call.
    """
    path = dataset_dir / _MIE_FILE
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MIE_BYTES:
            return ""
        import yaml  # local import: discovery must not cost a yaml import when unused

        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        info = doc.get("schema_info") if isinstance(doc, dict) else None
        if isinstance(info, dict):
            return str(info.get("description", "") or "").strip()
    except Exception:  # never let one bad artifact hide every dataset
        logger.debug("unreadable MIE at %s", path, exc_info=True)
    return ""


def _registry_entries(reg: Path) -> list[dict[str, Any]]:
    """One record per registry dataset, built from its ``meta.json``."""
    entries: list[dict[str, Any]] = []
    for child in sorted(reg.iterdir()):
        if not child.is_dir() or not _SAFE_ID.fullmatch(child.name):
            continue
        meta_path = child / _META_FILE
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append(
            {
                "id": str(meta.get("id") or child.name),
                "name": str(meta.get("name") or child.name),
                "description": _mie_description(child),
                "source": "registry",
                "promoted": bool(meta.get("promoted")),
                "status": str(meta.get("status") or "active"),
                "classes": [str(c) for c in (meta.get("classes") or [])],
                "graph": meta.get("live_graph") or meta.get("canonical_graph"),
                "triples": meta.get("triples_promoted"),
                "promoted_at": meta.get("promoted_at"),
            }
        )
    return entries


def _bundled_entries() -> list[dict[str, Any]]:
    """Repo-bundled example datasets (``datasets/<name>/dataset.toml``).

    Only reachable under the ``ASTERISM_BUNDLED_TOOLS=1`` opt-in — the same gate
    that decides whether their tools are served at all, so discovery never lists
    a dataset whose tools the caller cannot call.
    """
    base = datasets_root()
    if base is None:
        return []
    entries: list[dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        desc = load_dataset(child.name)
        if desc is None:
            continue
        entries.append(
            {
                "id": desc.name,
                "name": desc.name,
                "description": desc.description,
                "source": "bundled",
                # Bundled content ships with the image; it is citable as soon as
                # the store holds it, and carries no registry lifecycle.
                "promoted": True,
                "status": "active",
                "classes": [],
                "graph": desc.graph_base,
                "triples": None,
                "promoted_at": None,
            }
        )
    return entries


def _haystack(entry: dict[str, Any]) -> str:
    parts = [
        entry.get("id", ""),
        entry.get("name", ""),
        entry.get("description", ""),
        " ".join(entry.get("classes") or []),
    ]
    for tool in entry.get("tools") or []:
        parts.append(tool.get("name", ""))
        parts.append(tool.get("description", ""))
    return " ".join(str(p) for p in parts).lower()


def find_datasets(
    keywords: list[str] | tuple[str, ...] | str | None = None,
    *,
    root: Path | str | None = None,
    include_drafts: bool = False,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Discover the datasets this deployment serves, with the tools each carries.

    ``keywords`` narrows by case-insensitive substring over the dataset's id,
    name, MIE description, declared classes, and its tools' names/descriptions.
    **All** keywords must match (AND) — an OR over several terms floods the
    result with near-misses, which is the failure mode a discovery call exists to
    avoid. No keywords lists everything (bounded by ``limit``).

    Returns ``{"datasets": [...], "count": n, "truncated": bool}``; each dataset
    carries ``tools[].name`` as the name the caller must actually send.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    terms = [k.strip().lower() for k in (keywords or []) if str(k).strip()]

    reg = registry_root(root)
    entries: list[dict[str, Any]] = []
    if bundled_tools_enabled():
        entries.extend(_bundled_entries())
    if reg is not None:
        entries.extend(_registry_entries(reg))

    sources = tool_sources(root)
    served = resolve_tool_names(sources)
    by_id = {e["id"]: e for e in entries}
    for dataset, tools in sources.items():
        entry = by_id.get(dataset)
        if entry is None:
            # A dataset dir that declares tools but has no meta.json / descriptor:
            # serve it anyway (its tools ARE registered) rather than hiding a
            # callable surface from discovery.
            entry = {
                "id": dataset,
                "name": dataset,
                "description": "",
                "source": "registry" if reg is not None else "bundled",
                "promoted": True,
                "status": "active",
                "classes": [],
                "graph": None,
                "triples": None,
                "promoted_at": None,
            }
            by_id[dataset] = entry
            entries.append(entry)
        entry["tools"] = [
            {
                "name": served[dataset][t.name],
                "title": t.title,
                "description": t.description,
                "params": [p.name for p in t.params],
            }
            for t in tools
        ]

    out: list[dict[str, Any]] = []
    for entry in entries:
        entry.setdefault("tools", [])
        if not include_drafts and not (
            entry["promoted"] and entry["status"] != "retracted"
        ):
            continue
        if terms and not all(t in _haystack(entry) for t in terms):
            continue
        out.append(entry)

    out.sort(key=lambda e: (e["source"] != "registry", e["name"].lower()))
    limit = max(1, int(limit))
    return {
        "datasets": out[:limit],
        "count": len(out[:limit]),
        "truncated": len(out) > limit,
    }
