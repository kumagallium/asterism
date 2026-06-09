"""AI-assisted draft of ONE query tool (P2 of "grow verified tools").

Given a natural-language intent + the dataset's vocabulary (rdf-config
``model.yaml``) and example queries (the MIE ``sparql_query_examples``), an LLM
drafts a parameterized, read-only SPARQL ``query_tool`` (the same shape the
engine binds type-safely and the Ask layer routes to).

The draft is a PROPOSAL, not a verified tool: a human reviews/edits it and saves
it through the api (``POST /api/datasets/{id}/tools``, which validates it again
with ``parse_query_tools``) — that human review is the vet gate. This module only
lowers the authoring barrier; nothing is auto-activated and nothing is executed.
"""
from __future__ import annotations

import json
import re

from asterism_step0.propose import LLMClient

_SYSTEM = """\
You design ONE read-only SPARQL "query tool" for a dataset: a named,
parameterized SELECT that an AI agent calls instead of writing raw SPARQL.

Output ONLY a single JSON object (no prose, no markdown fence) with these keys:
  name         snake_case id (a-z, 0-9, _), short and unique.
  title        one-line human title.
  description  what it returns + when to use it (one or two sentences).
  parameters   array of {name, type, required, description} — and optionally
               default / minimum / maximum / enum. type is one of:
               string, number, integer, iri, enum.
  query        a SPARQL SELECT (or ASK). READ-ONLY — never INSERT/DELETE/CLEAR/
               DROP/etc. Reference parameter VALUES with {{name}} placeholders;
               they are bound type-safely by the engine (a string -> escaped
               literal, a number -> numeric, an enum -> whitelisted literal), so
               never quote or concatenate them yourself. A bare {{p}} that is NOT
               inside a {{#p}}...{{/p}} block MUST be a required or defaulted
               parameter; put an OPTIONAL filter inside a {{#p}}...{{/p}} block.
               SELECT the subject IRI(s) so the answer can be cited; add a LIMIT.
  result       {"item": {output_key: sparql_var, ...}} mapping result columns to
               friendly keys. Use {"var": x, "number": true} for numeric columns.

Hard rules:
- Use ONLY classes/predicates that appear in the dataset vocabulary below. Do not
  invent IRIs. Declare PREFIXes in the query (or use full <IRI>s).
- The query MUST be read-only and self-contained.
- Output the JSON object and NOTHING else.
"""


def _user_message(intent: str, model_yaml: str, mie_yaml: str) -> str:
    parts = [f"Intent (what the tool should do):\n{intent.strip()}\n"]
    parts.append(
        "Dataset vocabulary (rdf-config model.yaml — classes & predicates):\n"
        + (model_yaml.strip() or "(none provided)")
        + "\n"
    )
    parts.append(
        "Example queries for this dataset (MIE sparql_query_examples — follow "
        "these patterns/prefixes):\n" + (mie_yaml.strip() or "(none provided)")
    )
    return "\n".join(parts)


_FENCE = re.compile(r"```(?:json|yaml)?\s*\n(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> dict:
    """Parse the single JSON object the model returns, tolerating a code fence or
    surrounding prose."""
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i == -1 or j <= i:
            raise ValueError("the model did not return a JSON object") from None
        obj = json.loads(t[i : j + 1])
    if not isinstance(obj, dict):
        raise ValueError("the model did not return a JSON object")
    return obj


def propose_query_tool(
    llm: LLMClient, *, intent: str, model_yaml: str = "", mie_yaml: str = ""
) -> dict:
    """Draft one query_tool dict from ``intent`` + the dataset's vocabulary.

    Raises ``ValueError`` if the model's output cannot be parsed into a tool with
    at least ``name`` and ``query``. The caller validates the draft with
    ``asterism.query_tools.parse_query_tools`` before it is offered for saving.
    """
    if not intent.strip():
        raise ValueError("intent is required")
    text = llm.complete(_SYSTEM, _user_message(intent, model_yaml, mie_yaml))
    tool = _extract_json_object(text)
    if "name" not in tool or "query" not in tool:
        raise ValueError("draft is missing required keys (name, query)")
    return tool
