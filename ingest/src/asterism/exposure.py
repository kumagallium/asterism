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

Config: a single env var, default **open** (backward compatible / topology A)::

    ASTERISM_EXPOSE_RAW_SPARQL=false   # typed tools only (topology B / sensitive)

Falsy values (``0`` / ``false`` / ``no`` / ``off``, case-insensitive) disable the
escape; anything else (including unset) leaves it on.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: The single env var that selects the exposure profile.
ENV_EXPOSE_RAW_SPARQL = "ASTERISM_EXPOSE_RAW_SPARQL"

_FALSY = frozenset({"0", "false", "no", "off"})


def raw_sparql_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the arbitrary read-only SPARQL escape hatch is exposed.

    Defaults to ``True`` (open) when the var is unset, so existing deployments
    and the co-located (topology A) compose keep working unchanged. Pass an
    explicit ``env`` mapping in tests; production reads ``os.environ``.
    """
    e = env if env is not None else os.environ
    raw = e.get(ENV_EXPOSE_RAW_SPARQL)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY
