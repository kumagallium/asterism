"""demo-agent for the ARiSE grounded-answer demo.

Consuming layer, deliberately OUTSIDE asterism core: the runtime answer
generation must not live in the Claude-free core API (see
docs/architecture/ontology-mapping-boundary-and-provenance.md §1/§5).

Two modes:

- **mock** (default): returns fixtures so the UI can build against the
  contract with zero backend deps.
- **real**: set ``CSV2RDF_OXIGRAPH_URL`` to query a live Oxigraph through the
  asterism typed MCP tools (sample_search / property_ranking / provenance_of)
  and compose a grounded answer *deterministically* — no LLM, fully
  reproducible, which is exactly the sovereignty/reproducibility story the
  demo wants. An LLM can be slotted into the ``_compose_*`` helpers later for
  free-form questions without changing the contract.

Contract (also in ../handoff_to_claude_code_arise_demo.md §3):
    POST /demo/ask        -> {answer, citations[], notes[]}
    GET  /demo/provenance -> {iri, chain[]}

Run (mock):  uvicorn app:app --port 8090 --reload
Run (real):  CSV2RDF_OXIGRAPH_URL=http://localhost:7878 uvicorn app:app --port 8090
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re

import httpx
from asterism.exposure import raw_sparql_enabled
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_OXIGRAPH_URL = os.environ.get("CSV2RDF_OXIGRAPH_URL")
_REAL = bool(_OXIGRAPH_URL)
_RES = "https://kumagallium.github.io/asterism/starrydata/resource/"

# Exposure profile (ADR store-mcp-split): when False, the raw read-only SPARQL
# escape is withheld — the Ask LLM is given ONLY the typed tools, and the
# /demo/sparql passthrough is disabled. Typed tools stay available. Default open.
_EXPOSE_RAW_SPARQL = raw_sparql_enabled()

# #18 LLM NL->SPARQL escape config. Model is overridable for cost tuning; the
# escape only fires when the deterministic typed path finds nothing (see ask()).
_ASK_MODEL = os.environ.get("ASTERISM_ASK_MODEL", "claude-sonnet-4-6")
# Default model when the request selects an OpenAI / OpenAI-compatible provider
# but pins no model id (the UI normally sends the active model's id via header).
_OPENAI_ASK_MODEL = os.environ.get("ASTERISM_OPENAI_ASK_MODEL", "gpt-4o")
_OPENAI_PROVIDERS = frozenset({"openai", "openai-compatible", "openai_compatible", "sakura"})
_ASK_MAX_STEPS = 5  # max run_sparql tool calls before we force an answer

# Where to report Ask token usage (the api owns the ledger; demo-agent runs in a
# separate process). Best-effort: if unset, usage simply isn't recorded.
_USAGE_API_URL = (os.environ.get("ASTERISM_API_URL") or "").strip().rstrip("/")
_USAGE_API_TOKEN = os.environ.get("ASTERISM_API_TOKEN")


def _server_llm(provider: str | None) -> tuple[str | None, str | None]:
    """Operator fallback ``(api_key, api_base)`` for Ask, or ``(None, None)``.

    Mirrors ``asterism_api.server_keys``: the UI/file store the api writes (the
    registry, mounted read-only here) first, then ``ASTERISM_LLM_KEY_<PROVIDER>``.
    For an openai-compatible shared key the stored ``api_base`` is pinned. Unset
    by default → ``(None, None)`` → the free typed showcase stays the no-key path."""
    p = (provider or "anthropic").strip().lower()
    root = os.environ.get("CSV2RDF_REGISTRY_ROOT")
    if root:
        try:
            with open(os.path.join(root, "_llm", "server_keys.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            entry = data.get(p) if isinstance(data, dict) else None
            if isinstance(entry, dict) and str(entry.get("api_key") or "").strip():
                return entry["api_key"].strip(), (str(entry.get("api_base") or "").strip() or None)
        except (OSError, ValueError):
            pass
    env = (os.environ.get(f"ASTERISM_LLM_KEY_{p.replace('-', '_').upper()}") or "").strip()
    return (env or None), None

app = FastAPI(title=f"asterism demo-agent ({'real' if _REAL else 'mock'})")

# CORS: only the configured UI origin(s) may read our responses cross-origin. A
# wildcard would let ANY web page the operator visits drive /demo/ask|/demo/sparql
# against the live store and read the result (drive-by exfiltration of unpublished
# data). Override with ASTERISM_DEMO_CORS_ORIGINS (comma-separated); defaults to
# the local Vite dev origins. allow_credentials stays off (no cookies cross-origin).
_CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ASTERISM_DEMO_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


class SparqlRequest(BaseModel):
    query: str
    max_rows: int = 200


# ---------------------------------------------------------------------------
# mock fixtures (used when CSV2RDF_OXIGRAPH_URL is unset)
# ---------------------------------------------------------------------------

_ASK_FIXTURE = {
    "answer": (
        "記録上の最大 ZT は SnSe の約 2.6（curve 1-2-3 / paper 1）。"
        "なお 3.5 を超える極端値が 7 件あるが、軸ラベル誤り等のデジタル化誤差の"
        "可能性が高いため除外している。"
    ),
    "citations": [
        {
            "iri": f"{_RES}curve/1-2-3",
            "kind": "curve",
            "label": "Fig.3 ZT vs T",
            "fields": {"propertyY": "ZT", "yMax": 2.6},
        },
        {
            "iri": f"{_RES}sample/1-2",
            "kind": "sample",
            "label": "SnSe",
            "fields": {"composition": "SnSe"},
        },
    ],
    "notes": ["物理的にあり得ない ZT（>3.5）を 7 件除外した（デジタル化誤差の可能性）"],
}

_PROV_FIXTURE = {
    f"{_RES}curve/1-2-3": [
        {
            "step": "curve",
            "iri": f"{_RES}curve/1-2-3",
            "label": "Fig.3 ZT vs T",
            "detail": "ZT; yMax=2.6",
        },
        {
            "step": "sample",
            "iri": f"{_RES}sample/1-2",
            "label": "SnSe",
            "detail": "composition=SnSe",
        },
        {
            "step": "paper",
            "iri": f"{_RES}paper/1",
            "label": "SnSe paper",
            "detail": "id=10.1/xyz",
        },
        {
            "step": "digitization",
            "iri": f"{_RES}digitization/xyz",
            "label": "DigitizationActivity",
            "detail": "WebPlotDigitizer; atTime=2020-01-01",
        },
        {
            "step": "ingestion",
            "iri": f"{_RES}ingestion/abc",
            "label": "IngestionActivity",
            "detail": "atTime=2026-05-01",
        },
    ]
}


# ---------------------------------------------------------------------------
# real mode: one lazily-built Oxigraph client + deterministic answer composer
# ---------------------------------------------------------------------------

_state: dict = {}


def _client():
    c = _state.get("client")
    if c is None:
        from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

        c = OxigraphClient(OxigraphConfig(base_url=_OXIGRAPH_URL))
        _state["client"] = c
    return c


# A crude formula-ish token grabber for composition questions (e.g. "Bi2Te3").
_FORMULA_RE = re.compile(r"[A-Z][A-Za-z0-9().]{1,}")

# Questions that reach BEYOND the demo-scoped typed tools (which only rank a single
# property or search by composition): crystal structure (a second, Materials-Project
# dataset) or a correlation between two things. These are exactly the cross-dataset
# questions the LLM escape exists for — it can join datasets (e.g. starrydata
# compositionString == Materials Project formula) and write the SPARQL. So even when
# a typed keyword like "ZT" is also present, defer to the escape rather than dump a
# single-property ranking that ignores half the question.
_BEYOND_TYPED = re.compile(
    r"結晶構造|結晶系|空間群|格子|crystal|structure|lattice|space\s*group|"
    r"相関|correlat|横断|cross[- ]?dataset|どんな.{0,16}どんな|どの.{0,16}どの",
    re.IGNORECASE,
)


def _route(question: str) -> tuple[str, str | None, float | None]:
    """Pick a tool + args from the question. Deterministic, demo-scoped."""
    q = question.lower()
    if "zt" in q or "figure of merit" in q or "性能指数" in q:
        return ("rank", "ZT", 3.5)
    if "seebeck" in q or "ゼーベック" in q or "熱起電力" in q or "thermopower" in q:
        return ("rank", "Seebeck coefficient", None)
    m = _FORMULA_RE.search(question)
    return ("search", m.group(0) if m else None, None)


def _compose_rank(rank: dict) -> dict:
    py = rank.get("property_y")
    results = rank.get("results", [])
    excluded = rank.get("excluded_implausible", 0)
    mp = rank.get("max_plausible")
    if not results:
        return {
            "answer": f"{py} の該当データが見つかりませんでした。",
            "citations": [],
            "notes": [],
        }
    top = results[0]
    comp = top.get("composition") or "(組成不明)"
    ans = f"記録上の最大 {py} は {comp} の約 {top.get('value')}"
    if top.get("title"):
        ans += f"（{top['title']}）"
    ans += "。"
    notes: list[str] = []
    if excluded:
        ans += (
            f" なお {mp} を超える極端値が {excluded} 件あるが、"
            "デジタル化誤差の可能性として除外している。"
        )
        notes.append(f"物理的にあり得ない {py}（>{mp}）を {excluded} 件除外した")
    citations: list[dict] = []
    if top.get("curve_iri"):
        citations.append(
            {
                "iri": top["curve_iri"],
                "kind": "curve",
                "label": top.get("title") or comp,
                "fields": {
                    "propertyY": py,
                    "yMax": top.get("value"),
                    "composition": top.get("composition"),
                },
            }
        )
    if top.get("sample_iri"):
        citations.append(
            {
                "iri": top["sample_iri"],
                "kind": "sample",
                "label": comp,
                "fields": {"composition": top.get("composition")},
            }
        )
    return {"answer": ans, "citations": citations, "notes": notes}


def _compose_search(comp: str | None, res: dict) -> dict:
    results = res.get("results", [])
    if not results:
        return {
            "answer": f"組成 '{comp}' に一致するサンプルは見つかりませんでした。",
            "citations": [],
            "notes": [],
        }
    n = res.get("count", len(results))
    head = results[0]
    ans = (
        f"組成 '{comp}' に一致するサンプルが {n} 件見つかりました"
        f"（例: {head.get('composition')}）。"
    )
    citations = [
        {
            "iri": r["sample_iri"],
            "kind": "sample",
            "label": r.get("composition") or "sample",
            "fields": {"composition": r.get("composition"), "paper": r.get("title")},
        }
        for r in results[:5]
        if r.get("sample_iri")
    ]
    return {"answer": ans, "citations": citations, "notes": []}


# ---------------------------------------------------------------------------
# #18 / P4-2b LLM tool-use agent (consuming layer only — core stays Claude-free)
# ---------------------------------------------------------------------------
# With a user-brought key, an LLM does the routing instead of a brittle keyword
# matcher: it introspects the live vocabulary via schema_summary and PICKS the
# right read-only tool per question — the DETERMINISTIC typed tools
# (property_ranking / sample_search: cited, with data-quality notes) when a
# question fits one, or raw read-only SPARQL (run_sparql) for anything else
# (cross-dataset joins, correlations, custom shapes). It composes a grounded answer
# with citations from the actual results. Still an escape per the product direction
# (the no-key path is the deterministic typed showcase), and still read-only — the
# LLM can never mutate the store.

_RUN_SPARQL_TOOL = {
    "name": "run_sparql",
    "description": (
        "Run a READ-ONLY SPARQL SELECT/ASK against the store and get back "
        "{columns, rows, count, truncated} (or {error}). Use the EXACT class/"
        "predicate IRIs from the schema in the system prompt, wrapped in <>. "
        "Update-form queries are rejected."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

_SUBMIT_ANSWER_TOOL = {
    "name": "submit_answer",
    "description": (
        "Submit the final grounded answer once you have the data you need. "
        "Cite the IRIs that actually appear in your query results — never "
        "invent values or IRIs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Concise answer in Japanese, grounded in the rows.",
            },
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "iri": {"type": "string"},
                        "kind": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["iri"],
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer"],
    },
}

# Typed tools exposed to the LLM so it can PICK the deterministic, cited path when
# a question fits one (instead of a brittle keyword router deciding for it). For
# anything the typed tools don't cover (cross-dataset joins, correlations, custom
# shapes) the LLM falls back to run_sparql. This is P4-2b: the LLM does the routing.
_PROPERTY_RANKING_TOOL = {
    "name": "property_ranking",
    "description": (
        "DETERMINISTIC, CITED ranking of measured curves by a y-axis property "
        "(e.g. 'ZT', 'Seebeck coefficient'). Returns the top items with value + "
        "composition + citable IRIs, and EXCLUDES physically-implausible outliers "
        "(reported as excluded_implausible — a data-quality signal worth mentioning). "
        "PREFER this over run_sparql for a plain 'highest / top-N by <property>' "
        "question within one dataset."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "property_y": {"type": "string", "description": "exact propertyY value, e.g. 'ZT'"},
            "top_n": {"type": "integer", "description": "default 10"},
            "max_plausible": {
                "type": "number",
                "description": "exclude values above this (e.g. 3.5 for ZT)",
            },
        },
        "required": ["property_y"],
    },
}

_SAMPLE_SEARCH_TOOL = {
    "name": "sample_search",
    "description": (
        "DETERMINISTIC, CITED search of samples by composition substring and/or by "
        "a measured property, returning citable sample IRIs + composition + paper "
        "title. PREFER this for 'samples with composition X' or 'samples that have a "
        "<property> curve' within one dataset."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "composition": {"type": "string", "description": "composition substring, e.g. 'SnSe'"},
            "property_y": {"type": "string", "description": "require a curve of this propertyY"},
        },
    },
}

_ASK_SYSTEM = """\
You answer questions over an RDF graph that may span MULTIPLE datasets. You have
deterministic typed tools AND raw read-only SPARQL — YOU choose which fits the
question. Make NO assumptions about the vocabulary beyond the schema below — it
lists the classes, predicates, and per-class predicate shapes that ACTUALLY exist
in the store, with usage counts.

Choosing a tool:
- A plain 'highest / top-N by <property>' question -> property_ranking (it is
  deterministic, cites IRIs, and reports excluded outliers). Mention the
  excluded_implausible count if it is > 0.
- 'samples with composition X' / 'samples that have a <property> curve' ->
  sample_search.
- ANYTHING ELSE — cross-dataset joins, correlations between two things, custom
  shapes — write SPARQL and run_sparql.

Cross-dataset joins (IMPORTANT): datasets can relate by shared literal VALUES, not
only by predicates. For example a starrydata `compositionString` may EQUAL a
Materials Project `formula`. To answer a question that spans datasets, JOIN on the
shared value, e.g.
  ?samp <…/compositionString> ?c . ?mat <…/formula> ?c . ?mat <…/hasCrystalStructure> ?cs .
Use the EXACT class/predicate IRIs from the schema, in <>.

Rules:
- Use ONLY IRIs that appear in the schema. SELECT enough to cite (the subject IRI).
  Add LIMIT (<=50).
- If a query errors or returns nothing, read it and try a DIFFERENT query (try a
  few times — e.g. relax a join, check the actual predicate). Do not give up after
  one failure.
- Call submit_answer with a concise Japanese answer + citations whose IRIs
  literally appear in your results. NEVER fabricate values or IRIs, and NEVER
  submit a placeholder like "ダミー" — if the data genuinely cannot answer (e.g. the
  two datasets share no join key), say so HONESTLY and explain what is missing.

Schema (classes / predicates / per-class shapes with counts):
%s
"""


def _anthropic_client(api_key: str):
    """Build an Anthropic client. Overridable via ``_state['anthropic_factory']``
    so tests can inject a fake without a network call or a real key."""
    factory = _state.get("anthropic_factory")
    if factory is not None:
        return factory(api_key)
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _openai_client(api_key: str, base_url: str | None):
    """Build an OpenAI (Chat Completions) client for any OpenAI-compatible endpoint
    — ``base_url`` selects it (Sakura AI Engine / Groq / Ollama / vLLM). Overridable
    via ``_state['openai_factory']`` so tests inject a fake without a network call."""
    factory = _state.get("openai_factory")
    if factory is not None:
        return factory(api_key, base_url)
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url or None)


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert our Anthropic-shaped tool defs to OpenAI function-calling shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _parse_json_args(raw: str | None) -> dict:
    """Parse an OpenAI tool-call ``arguments`` JSON string, tolerating junk."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _add_anthropic_usage(acc: dict, u: object) -> None:
    if u is None:
        return
    acc["input_tokens"] += getattr(u, "input_tokens", 0) or 0
    acc["output_tokens"] += getattr(u, "output_tokens", 0) or 0
    acc["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
    acc["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0


def _add_openai_usage(acc: dict, u: object) -> None:
    if u is None:
        return
    acc["input_tokens"] += getattr(u, "prompt_tokens", 0) or 0
    acc["output_tokens"] += getattr(u, "completion_tokens", 0) or 0


async def _post_usage(provider: str, model: str, usage: dict) -> None:
    """Append the Ask call's token usage to the api ledger (best-effort).

    The api owns the usage ledger; demo-agent is a separate process, so it POSTs.
    No-op if ``ASTERISM_API_URL`` is unset or the call fails — never block the answer."""
    if not _USAGE_API_URL:
        return
    if sum(int(usage.get(k, 0) or 0) for k in usage) <= 0:
        return
    body = {"feature": "ask", "provider": provider, "model_id": model, **usage}
    headers = {"Content-Type": "application/json"}
    if _USAGE_API_TOKEN:
        headers["X-Asterism-Token"] = _USAGE_API_TOKEN
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(f"{_USAGE_API_URL}/api/usage", json=body, headers=headers)
    except Exception:  # best-effort telemetry — never fail the answer on this
        pass


def _render_schema(schema: dict) -> str:
    """Compact text rendering of schema_summary for the system prompt."""
    lines: list[str] = ["Classes:"]
    for c in schema.get("classes", []):
        lines.append(f"  <{c['iri']}>  (instances: {c['count']})")
    lines.append("Per-class predicates (shape):")
    for s in schema.get("class_shapes", []):
        preds = ", ".join(f"<{p['iri']}>" for p in s.get("predicates", []))
        lines.append(f"  <{s['class']}>: {preds}")
    return "\n".join(lines)


def _blocks_to_dicts(content: list) -> list[dict]:
    """Reconstruct assistant content blocks as plain dicts to send back."""
    out: list[dict] = []
    for b in content:
        if getattr(b, "type", None) == "text":
            out.append({"type": "text", "text": b.text})
        elif getattr(b, "type", None) == "tool_use":
            out.append(
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            )
    return out


def _param_json_schema(p) -> dict:
    """Map a declared ToolParam to an Anthropic tool input_schema property."""
    if p.type == "number":
        base: dict = {"type": "number"}
    elif p.type == "integer":
        base = {"type": "integer"}
    elif p.type == "enum":
        base = {"type": "string", "enum": list(p.enum or ())}
    else:  # string, iri
        base = {"type": "string"}
    if p.description:
        base["description"] = p.description
    return base


def _content_tool_defs(exclude: set[str] | None = None) -> tuple[list[dict], dict]:
    """Anthropic tool defs for every dataset's declared query tools, EXCEPT the
    excluded datasets (starrydata keeps its richer hardcoded tools).

    Returns ``(defs, registry)`` with ``registry[name] = (dataset, QueryTool)``.
    The engine knows no vocabulary; tools come from each dataset's
    ``query_tools.yaml`` (#112/#113), so a newly onboarded dataset (e.g. Materials
    Project) becomes a VERIFIED Ask tool for free — no per-dataset code here.
    """
    from asterism.query_tools import load_all_query_tools

    exclude = exclude or set()
    defs: list[dict] = []
    registry: dict[str, tuple] = {}
    # Tools come from BOTH the repo example datasets (datasets/<name>/) AND the
    # workbench registry (registry/<id>/query_tools.yaml) — same loader, same
    # shape — so a tool a researcher saved on their own onboarded dataset routes
    # as a verified Ask tool, no repo PR (the "grow verified tools" store, P1).
    sources: dict[str, list] = dict(load_all_query_tools())
    reg_root = os.environ.get("CSV2RDF_REGISTRY_ROOT")
    if reg_root:
        with contextlib.suppress(Exception):
            sources.update(load_all_query_tools(reg_root))
    for dataset, qts in sources.items():
        if dataset in exclude:
            continue
        for qt in qts:
            name = f"{dataset}__{qt.name}"
            defs.append(
                {
                    "name": name,
                    "description": f"[verified · dataset:{dataset}] {qt.description or qt.title}",
                    "input_schema": {
                        "type": "object",
                        "properties": {p.name: _param_json_schema(p) for p in qt.params},
                        "required": [p.name for p in qt.params if p.required],
                    },
                }
            )
            registry[name] = (dataset, qt)
    return defs, registry


async def _llm_answer_via(
    question: str,
    api_key: str,
    *,
    provider: str,
    model: str | None,
    api_base: str | None,
) -> tuple[dict, dict]:
    """Schema-grounded LLM agent: it PICKS among the deterministic VERIFIED tools
    (starrydata property_ranking / sample_search + every other dataset's declared
    query tools, e.g. Materials Project's structure_by_composition) and raw
    read-only SPARQL (run_sparql, the unverified escape), grounding the answer in
    real results (P4-2b — the LLM does the routing).

    Works across providers: Anthropic uses its tool-use loop; OpenAI / any
    OpenAI-compatible endpoint (Sakura AI Engine etc.) uses the function-calling
    loop. The tool DEFINITIONS and EXECUTION are shared — only the wire format
    differs. Returns ``(answer, usage)`` where answer is the
    ``{answer, citations, notes, sparql, verified_tools, unverified_sparql}``
    contract and usage is the accumulated token counts (for the ledger). All store
    access is read-only, so the LLM cannot mutate the store.
    """
    from asterism.query_tools import run_query_tool
    from asterism_mcp.tools import (
        property_ranking,
        sample_search,
        schema_summary,
        sparql_query,
    )

    client = _client()
    schema = await schema_summary(
        client, max_classes=40, max_predicates=80, predicates_per_class=15
    )
    system = _ASK_SYSTEM % _render_schema(schema)
    # starrydata keeps its richer hardcoded tools (property_ranking carries the
    # excluded_implausible data-quality count); every OTHER dataset's declared
    # query_tools.yaml is also offered, so e.g. Materials Project's
    # structure_by_composition / thermoelectric_structure route as VERIFIED tools
    # rather than the unverified SPARQL escape. New datasets appear here for free.
    content_defs, content_registry = _content_tool_defs(exclude={"starrydata"})
    # Exposure profile: when raw SPARQL is withheld, give the LLM ONLY the typed
    # tools (hardcoded + declared content) and tell it so — it must answer from
    # them or honestly decline, never attempt run_sparql (which it does not have).
    tools = [
        _PROPERTY_RANKING_TOOL,
        _SAMPLE_SEARCH_TOOL,
        *content_defs,
    ]
    if _EXPOSE_RAW_SPARQL:
        tools.append(_RUN_SPARQL_TOOL)
    else:
        system += (
            "\n\nNOTE: raw SPARQL is DISABLED in this deployment. Use ONLY the "
            "typed tools above. If no typed tool can answer the question, say so "
            "honestly via submit_answer — do not attempt arbitrary SPARQL."
        )
    tools.append(_SUBMIT_ANSWER_TOOL)

    used_sparql: list[str] = []
    verified_used: list[dict] = []  # provenance: vetted tools the answer used
    state = {"unverified": False}  # whether the unverified SPARQL escape was used
    usage: dict = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }

    # Provider-agnostic tool execution: the loops differ only in wire format; this
    # runs the actual (read-only) tool by name and returns the JSON-able result.
    async def execute_tool(name: str, inp: dict) -> dict:
        try:
            if name == "run_sparql":
                state["unverified"] = True  # the unverified escape was used
                q = inp.get("query", "")
                result = await sparql_query(q, client, max_rows=50)
                # Disclose the query that ACTUALLY ran: sparql_query rewrites a
                # plain SELECT to read the cross-dataset canonical FROM-merge (#20),
                # so the user sees the real, reproducible query string.
                used_sparql.append(result.get("effective_query") or q)
                return result
            if name == "property_ranking":
                result = await property_ranking(
                    client,
                    property_y=inp.get("property_y", ""),
                    top_n=int(inp.get("top_n") or 10),
                    max_plausible=inp.get("max_plausible"),
                )
                verified_used.append(
                    {"dataset": "starrydata", "name": "property_ranking", "title": "property_ranking"}
                )
                return result
            if name == "sample_search":
                result = await sample_search(
                    client,
                    composition=inp.get("composition"),
                    property_y=inp.get("property_y"),
                    limit=20,
                )
                verified_used.append(
                    {"dataset": "starrydata", "name": "sample_search", "title": "sample_search"}
                )
                return result
            if name in content_registry:
                # A declared (verified) query tool from another dataset, e.g.
                # Materials Project's thermoelectric_structure — run the fixed
                # template through the canonical FROM-merge (cross-dataset).
                dataset, qt = content_registry[name]
                out = await run_query_tool(client, qt, dict(inp), max_rows=50)
                if out.get("sparql"):
                    used_sparql.append(out["sparql"])
                verified_used.append({"dataset": dataset, "name": qt.name, "title": qt.title})
                return {
                    "count": out["count"],
                    "items": out["items"],
                    "truncated": out["truncated"],
                }
            return {"error": f"unknown tool {name!r}"}
        except Exception as exc:  # never let a bad tool call kill the loop
            if name == "run_sparql":
                used_sparql.append(inp.get("query", ""))
            return {"error": str(exc)}

    def finalize(data: dict) -> dict:
        # Queries are disclosed via the dedicated ``sparql`` field (UI panel).
        return {
            "answer": data.get("answer", ""),
            "citations": data.get("citations") or [],
            "notes": list(data.get("notes") or []),
            "sparql": used_sparql,
            "verified_tools": verified_used,
            "unverified_sparql": state["unverified"],
        }

    def finalize_text(text: str) -> dict:
        return finalize({"answer": text or "回答を生成できませんでした。"})

    if provider in _OPENAI_PROVIDERS:
        answer = await _openai_agent_loop(
            _openai_client(api_key, api_base),
            model or _OPENAI_ASK_MODEL,
            system,
            _to_openai_tools(tools),
            question,
            execute_tool,
            usage,
            finalize,
            finalize_text,
        )
    else:
        answer = await _anthropic_agent_loop(
            _anthropic_client(api_key),
            model or _ASK_MODEL,
            system,
            tools,
            question,
            execute_tool,
            usage,
            finalize,
            finalize_text,
        )
    return answer, usage


async def _anthropic_agent_loop(
    anthropic, model, system, tools, question, execute_tool, usage, finalize, finalize_text
):
    messages: list[dict] = [{"role": "user", "content": question}]
    for step in range(_ASK_MAX_STEPS):
        force_final = step == _ASK_MAX_STEPS - 1  # never loop forever
        kwargs = {
            "model": model,
            "max_tokens": 2000,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        if force_final:
            kwargs["tool_choice"] = {"type": "tool", "name": "submit_answer"}
        resp = await asyncio.to_thread(anthropic.messages.create, **kwargs)
        _add_anthropic_usage(usage, getattr(resp, "usage", None))

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        submit = next((b for b in tool_uses if b.name == "submit_answer"), None)
        if submit is not None:
            return finalize(submit.input or {})
        if not tool_uses:
            text = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return finalize_text(text)

        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        tool_results: list[dict] = []
        for tu in tool_uses:
            result = await execute_tool(tu.name, tu.input or {})
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return finalize_text("回答を生成できませんでした（試行回数の上限に達しました）。")


async def _openai_agent_loop(
    openai, model, system, oai_tools, question, execute_tool, usage, finalize, finalize_text
):
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    for step in range(_ASK_MAX_STEPS):
        force_final = step == _ASK_MAX_STEPS - 1  # never loop forever
        kwargs: dict = {
            "model": model,
            "max_tokens": 2000,
            "messages": messages,
            "tools": oai_tools,
            "tool_choice": (
                {"type": "function", "function": {"name": "submit_answer"}}
                if force_final
                else "auto"
            ),
        }
        resp = await asyncio.to_thread(openai.chat.completions.create, **kwargs)
        _add_openai_usage(usage, getattr(resp, "usage", None))

        msg = resp.choices[0].message
        tool_calls = list(getattr(msg, "tool_calls", None) or [])
        submit = next((tc for tc in tool_calls if tc.function.name == "submit_answer"), None)
        if submit is not None:
            return finalize(_parse_json_args(submit.function.arguments))
        if not tool_calls:
            return finalize_text(msg.content or "")

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            result = await execute_tool(tc.function.name, _parse_json_args(tc.function.arguments))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return finalize_text("回答を生成できませんでした（試行回数の上限に達しました）。")


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


async def _typed_answer(question: str) -> dict:
    """The deterministic starrydata-shaped path (no LLM). Returns NO citations when
    the question does not fit the typed tools — that is the signal for ``ask`` to
    fall through to the LLM escape.

    Crucially, the typed path only "answers" when the router extracted a concrete
    argument (a property to rank, or a composition to search). A bare
    ``sample_search(composition=None)`` matches *every* sample, so it would always
    return citations and thus permanently block the LLM fallback — any question
    without a ZT/Seebeck keyword or a formula token would get the same arbitrary
    "all samples" list. So when nothing concrete was extracted we return empty and
    let the escape (or the no-key hint) handle it.
    """
    from asterism_mcp.tools import property_ranking, sample_search

    # Cross-dataset / crystal-structure / correlation questions are beyond the typed
    # tools (single-property ranking, composition search). Defer to the LLM escape —
    # which can join across datasets — instead of short-circuiting on a "ZT" keyword.
    if _BEYOND_TYPED.search(question):
        return {"answer": "", "citations": [], "notes": []}

    kind, arg, mp = _route(question)
    if kind == "rank" and arg:
        rank = await property_ranking(
            _client(), property_y=arg, top_n=10, max_plausible=mp
        )
        out = _compose_rank(rank)
        if out.get("citations"):
            out["verified_tools"] = [
                {"dataset": "starrydata", "name": "property_ranking", "title": "property_ranking"}
            ]
        return out
    if kind == "search" and arg:
        res = await sample_search(_client(), composition=arg, limit=20)
        out = _compose_search(arg, res)
        if out.get("citations"):
            out["verified_tools"] = [
                {"dataset": "starrydata", "name": "sample_search", "title": "sample_search"}
            ]
        return out
    # Nothing concrete to query on — a generic / cross-dataset question the
    # demo-scoped typed tools cannot answer. Empty -> fall through to the LLM escape.
    return {"answer": "", "citations": [], "notes": []}


@app.post("/demo/ask")
async def ask(
    req: AskRequest,
    x_api_key: str | None = Header(default=None),
    x_llm_provider: str | None = Header(default=None),
    x_llm_model: str | None = Header(default=None),
    x_llm_api_base: str | None = Header(default=None),
) -> dict:
    if not _REAL:
        # MOCK: same grounded fixture regardless of question.
        return _ASK_FIXTURE

    # P4-2b: with a key, the LLM does the routing — it PICKS among the deterministic
    # typed tools (property_ranking / sample_search, which it can call for a clean
    # fit) and raw SPARQL (cross-dataset / anything else). This replaces the brittle
    # keyword router for exploration: a "ZT by crystal structure" question is no
    # longer short-circuited to a single-property ranking. The provider/model come
    # from the active model selected in Settings (absent → Anthropic default).
    provider = (x_llm_provider or "anthropic").strip().lower() or "anthropic"
    # No browser key → fall back to the operator's server-side key (opt-in). This
    # is what lets a login-gated instance answer general questions without each
    # user pasting a key. For an openai-compatible shared key its stored base is
    # pinned (don't send the shared key to a per-request endpoint).
    key = x_api_key
    api_base = x_llm_api_base
    if not key:
        key, pinned = _server_llm(provider)
        if pinned:
            api_base = pinned
    if key:
        model = x_llm_model or (
            _OPENAI_ASK_MODEL if provider in _OPENAI_PROVIDERS else _ASK_MODEL
        )
        answer, usage = await _llm_answer_via(
            req.question,
            key,
            provider=provider,
            model=model,
            api_base=api_base,
        )
        await _post_usage(provider, model, usage)
        return answer

    # No key: the free, deterministic typed showcase (the example chips). A question
    # the typed tools cannot answer gets an honest hint that the general path needs
    # a key (we never fabricate a generic "all samples" answer — see _typed_answer).
    typed = await _typed_answer(req.question)
    if typed.get("citations"):
        return typed
    typed.setdefault("notes", []).append(
        "型付きツールで該当が見つかりませんでした。一般的な質問・横断的な質問には "
        "API キーが必要です（スキーマを内省して型付きツール/SPARQL を選びます）。"
    )
    return typed


@app.get("/demo/provenance")
async def provenance(iri: str) -> dict:
    if not _REAL:
        chain = _PROV_FIXTURE.get(iri) or _PROV_FIXTURE[f"{_RES}curve/1-2-3"]
        return {"iri": iri, "chain": chain}
    from asterism_mcp.tools import provenance_of

    return await provenance_of(iri, _client())


# ---------------------------------------------------------------------------
# #18 generic Ask layer — schema-agnostic foundation (LLM-free)
# ---------------------------------------------------------------------------
# /demo/ask above routes deterministically into the starrydata-shaped tools, so
# it cannot answer over a user-designed schema. The two endpoints below expose
# the schema-AGNOSTIC core tools (asterism_mcp.tools.schema_summary /
# sparql_query) straight through — no LLM, no starrydata assumptions. They are
# the foundation the NL->SPARQL escape will sit on next: a future /demo/ask can
# call schema_summary to learn the vocabulary, have an LLM draft a SELECT, then
# run it via the same sparql_query path — without changing this contract.

_SCHEMA_FIXTURE = {
    "graph": None,
    "classes": [
        {"iri": f"{_RES.rsplit('/resource/', 1)[0]}/Curve", "count": 3},
        {"iri": f"{_RES.rsplit('/resource/', 1)[0]}/Sample", "count": 2},
    ],
    "predicates": [
        {"iri": "https://kumagallium.github.io/asterism/starrydata/propertyY", "count": 3},
        {"iri": "https://kumagallium.github.io/asterism/starrydata/yMax", "count": 3},
    ],
    "class_shapes": [],
}


@app.get("/demo/schema")
async def schema(graph: str | None = None) -> dict:
    """Schema-agnostic vocabulary introspection (classes/predicates/shapes)."""
    if not _REAL:
        return _SCHEMA_FIXTURE
    if graph is not None and not _EXPOSE_RAW_SPARQL:
        # Introspecting an ARBITRARY named graph (e.g. an unreviewed draft) is part
        # of the raw escape — in the typed-only profile only the canonical summary
        # (graph=None) is exposed, so a draft's vocabulary cannot be probed.
        raise HTTPException(
            403,
            "この配備では特定グラフのスキーマ参照は無効です（型付きツールのみ公開）。"
            "ASTERISM_EXPOSE_RAW_SPARQL=1 で有効化できます。",
        )
    from asterism_mcp.tools import schema_summary

    return await schema_summary(_client(), graph=graph)


@app.post("/demo/sparql")
async def sparql(req: SparqlRequest) -> dict:
    """Read-only SPARQL SELECT/ASK passthrough (schema-agnostic escape hatch)."""
    if not _EXPOSE_RAW_SPARQL:
        raise HTTPException(
            403,
            "この配備では生 SPARQL は無効です（型付きツールのみ公開）。"
            "ASTERISM_EXPOSE_RAW_SPARQL=1 で有効化できます。",
        )
    if not _REAL:
        return {"columns": [], "rows": [], "count": 0, "truncated": False, "mode": "mock"}
    from asterism_mcp.tools import SparqlNotReadOnlyError, sparql_query

    try:
        return await sparql_query(req.query, _client(), max_rows=req.max_rows)
    except (ValueError, SparqlNotReadOnlyError) as exc:
        return {"error": str(exc), "columns": [], "rows": [], "count": 0}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mode": "real" if _REAL else "mock"}
