"""Deployment **exposure profile** — which query surface a deployment exposes.

Asterism's product direction (決定論・型付きを主役、LLM/生 SPARQL は escape) means a
deployment can choose to expose *only* the vetted, typed query tools
(``query_tools.yaml``) and withhold the arbitrary read-only SPARQL escape. This
is the *controlled exposure* axis of the store/MCP split (ADR
``store-mcp-split.md``): a sensitive store (topology B) caps consumers to
pre-vetted typed questions so the graph cannot be root-extracted; an open store
(topology A) leaves the escape on for free exploration.

The escape hatch is exposed in **several** places — keep them gated by this one
switch so a half-closed deployment can't leak:

* the MCP ``sparql_query`` tool (``asterism_mcp.server``),
* the demo-agent Ask ``run_sparql`` tool + ``POST /demo/sparql``,
* the api ``POST /api/sparql`` relay.

Typed tools are **always** available regardless of this switch — they run only
human-vetted templates with type-safe argument binding (``query_tools.py``), so
they expose nothing the operator did not deliberately publish.

Config: a single env var, default **closed** (safe-by-default for a sensitive
store). The arbitrary read-only SPARQL escape is withheld unless the operator
explicitly opts in::

    ASTERISM_EXPOSE_RAW_SPARQL=1   # open the raw escape (topology A / co-located demo)

When **unset** the escape is OFF (typed tools only). An explicit value enables it
unless it is falsy (``0`` / ``false`` / ``no`` / ``off``, case-insensitive), which
keeps it OFF. This was deliberately flipped from the original backward-compatible
"open by default": a fresh deployment that ingests confidential data must not
publish a graph-wide root-extraction escape merely by omission.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: The single env var that selects the exposure profile.
ENV_EXPOSE_RAW_SPARQL = "ASTERISM_EXPOSE_RAW_SPARQL"

_FALSY = frozenset({"0", "false", "no", "off"})


def raw_sparql_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the arbitrary read-only SPARQL escape hatch is exposed.

    Defaults to ``False`` (closed) when the var is unset — safe-by-default for a
    sensitive store, so a fresh deployment does not publish a graph-wide
    root-extraction escape merely by omission. The operator opts in with an
    explicit non-falsy value. Pass an explicit ``env`` mapping in tests;
    production reads ``os.environ``.
    """
    e = env if env is not None else os.environ
    raw = e.get(ENV_EXPOSE_RAW_SPARQL)
    if raw is None:
        return False
    return raw.strip().lower() not in _FALSY
