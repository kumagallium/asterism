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
import json
import os
import re

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_OXIGRAPH_URL = os.environ.get("CSV2RDF_OXIGRAPH_URL")
_REAL = bool(_OXIGRAPH_URL)
_RES = "https://kumagallium.github.io/asterism/starrydata/resource/"

# #18 LLM NL->SPARQL escape config. Model is overridable for cost tuning; the
# escape only fires when the deterministic typed path finds nothing (see ask()).
_ASK_MODEL = os.environ.get("ASTERISM_ASK_MODEL", "claude-sonnet-4-6")
_ASK_MAX_STEPS = 5  # max run_sparql tool calls before we force an answer

app = FastAPI(title=f"asterism demo-agent ({'real' if _REAL else 'mock'})")

# Dev-only CORS so the Vite UI (different port) can call us. Tighten before any
# non-local deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
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
    for dataset, qts in load_all_query_tools().items():
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


async def _llm_answer(question: str, api_key: str) -> dict:
    """Schema-grounded LLM agent: it PICKS among the deterministic VERIFIED tools
    (starrydata property_ranking / sample_search + every other dataset's declared
    query tools, e.g. Materials Project's structure_by_composition) and raw
    read-only SPARQL (run_sparql, the unverified escape), grounding the answer in
    real results (P4-2b — the LLM does the routing).

    Returns the same ``{answer, citations, notes}`` contract as the typed path,
    plus a ``sparql`` list of the queries actually run (disclosure). All store
    access is read-only (typed tools + the ``sparql_query`` guard), so the LLM
    cannot mutate the store.
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
    tools = [
        _PROPERTY_RANKING_TOOL,
        _SAMPLE_SEARCH_TOOL,
        *content_defs,
        _RUN_SPARQL_TOOL,
        _SUBMIT_ANSWER_TOOL,
    ]
    messages: list[dict] = [{"role": "user", "content": question}]
    used_sparql: list[str] = []
    verified_used: list[dict] = []  # provenance: vetted tools the answer used
    unverified = False  # provenance: whether the unverified SPARQL escape was used
    anthropic = _anthropic_client(api_key)

    for step in range(_ASK_MAX_STEPS):
        # Force the final answer on the last step so we never loop forever.
        force_final = step == _ASK_MAX_STEPS - 1
        kwargs = {
            "model": _ASK_MODEL,
            "max_tokens": 2000,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        if force_final:
            kwargs["tool_choice"] = {"type": "tool", "name": "submit_answer"}
        resp = await asyncio.to_thread(anthropic.messages.create, **kwargs)

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        submit = next((b for b in tool_uses if b.name == "submit_answer"), None)
        if submit is not None:
            data = submit.input or {}
            # The queries are disclosed via the dedicated ``sparql`` field (the UI
            # renders them in a panel); we no longer stuff them into ``notes``.
            return {
                "answer": data.get("answer", ""),
                "citations": data.get("citations") or [],
                "notes": list(data.get("notes") or []),
                "sparql": used_sparql,
                "verified_tools": verified_used,
                "unverified_sparql": unverified,
            }

        if not tool_uses:
            # The model replied with text but no tool call — surface that text.
            text = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return {
                "answer": text or "回答を生成できませんでした。",
                "citations": [],
                "notes": [],
                "sparql": used_sparql,
                "verified_tools": verified_used,
                "unverified_sparql": unverified,
            }

        # Execute every tool call and feed the results back.
        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        tool_results: list[dict] = []
        for tu in tool_uses:
            inp = tu.input or {}
            try:
                if tu.name == "run_sparql":
                    unverified = True  # the unverified escape was used
                    q = inp.get("query", "")
                    result = await sparql_query(q, client, max_rows=50)
                    # Disclose the query that ACTUALLY ran: sparql_query rewrites a
                    # plain SELECT to read the cross-dataset canonical FROM-merge
                    # (#20), so the user sees the real, reproducible query string.
                    used_sparql.append(result.get("effective_query") or q)
                elif tu.name == "property_ranking":
                    result = await property_ranking(
                        client,
                        property_y=inp.get("property_y", ""),
                        top_n=int(inp.get("top_n") or 10),
                        max_plausible=inp.get("max_plausible"),
                    )
                    verified_used.append(
                        {"dataset": "starrydata", "name": "property_ranking", "title": "property_ranking"}
                    )
                elif tu.name == "sample_search":
                    result = await sample_search(
                        client,
                        composition=inp.get("composition"),
                        property_y=inp.get("property_y"),
                        limit=20,
                    )
                    verified_used.append(
                        {"dataset": "starrydata", "name": "sample_search", "title": "sample_search"}
                    )
                elif tu.name in content_registry:
                    # A declared (verified) query tool from another dataset, e.g.
                    # Materials Project's thermoelectric_structure — run the fixed
                    # template through the canonical FROM-merge (cross-dataset).
                    dataset, qt = content_registry[tu.name]
                    out = await run_query_tool(client, qt, dict(inp), max_rows=50)
                    if out.get("sparql"):
                        used_sparql.append(out["sparql"])
                    verified_used.append(
                        {"dataset": dataset, "name": qt.name, "title": qt.title}
                    )
                    result = {
                        "count": out["count"],
                        "items": out["items"],
                        "truncated": out["truncated"],
                    }
                else:
                    result = {"error": f"unknown tool {tu.name!r}"}
            except Exception as exc:  # never let a bad tool call kill the loop
                if tu.name == "run_sparql":
                    used_sparql.append(inp.get("query", ""))
                result = {"error": str(exc)}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    # Exhausted steps without a submit_answer (should be rare given force_final).
    return {
        "answer": "回答を生成できませんでした（試行回数の上限に達しました）。",
        "citations": [],
        "notes": [],
        "sparql": used_sparql,
        "verified_tools": verified_used,
        "unverified_sparql": unverified,
    }


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
) -> dict:
    if not _REAL:
        # MOCK: same grounded fixture regardless of question.
        return _ASK_FIXTURE

    # P4-2b: with a key, the LLM does the routing — it PICKS among the deterministic
    # typed tools (property_ranking / sample_search, which it can call for a clean
    # fit) and raw SPARQL (cross-dataset / anything else). This replaces the brittle
    # keyword router for exploration: a "ZT by crystal structure" question is no
    # longer short-circuited to a single-property ranking.
    if x_api_key:
        return await _llm_answer(req.question, x_api_key)

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
    from asterism_mcp.tools import schema_summary

    return await schema_summary(_client(), graph=graph)


@app.post("/demo/sparql")
async def sparql(req: SparqlRequest) -> dict:
    """Read-only SPARQL SELECT/ASK passthrough (schema-agnostic escape hatch)."""
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
