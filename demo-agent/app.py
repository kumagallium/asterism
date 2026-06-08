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
# #18 LLM NL->SPARQL escape (consuming layer only — core stays Claude-free)
# ---------------------------------------------------------------------------
# When the typed path finds nothing (e.g. a user-designed schema the typed tools
# were never specialized for), an LLM introspects the live vocabulary via
# schema_summary, writes a READ-ONLY SPARQL query, runs it through sparql_query
# (which enforces read-only), inspects the rows, and composes a grounded answer
# with citations. This is the "exploration is an escape hatch" path from the
# product direction: deterministic-and-typed is primary, the LLM only enters
# when typed answers run out, and it can only call the same read-only tools.

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


# ---------------------------------------------------------------------------
# C: LLM-as-router over the verified typed tools (P4-2b)
# ---------------------------------------------------------------------------
# Single free-text box, but the LLM only *routes*: it picks a human-vetted typed
# tool (from any dataset's query_tools.yaml) and the deterministic tool produces
# the cited answer (fixed query, data-quality gate baked in, reproducible
# citation). Only when no verified tool fits does it fall back to run_sparql
# (escape, unverified). So the answerable range = everything (escape covers the
# tail), while curated question-families stay deterministic + citable. New tools
# appear automatically (load_all_query_tools spans every dataset) — no per-dataset
# code: "every ingested dataset is askable" without touching this router.

_ROUTER_SYSTEM = """\
You answer questions over an RDF graph. You have two kinds of tools:

1. VERIFIED TOOLS (names containing "__"): human-vetted, parameterized,
   deterministic, reproducible, citable operations over specific datasets.
   PREFER these — if the question fits one, call it with the right arguments.
2. run_sparql: a fallback to write your OWN read-only SPARQL, used only when NO
   verified tool fits. Its results are unverified.

The schema below lists the classes/predicates that ACTUALLY exist (with counts);
use only IRIs from it, written as full IRIs in <>.

Rules:
- Try a VERIFIED tool FIRST. Use run_sparql only if none fits the question.
- A verified tool may span datasets (e.g. join a thermoelectric property with a
  crystal structure) — prefer it for cross-dataset questions when one fits.
- Cite the IRIs that appear in your tool results (include subject IRIs). Add
  LIMIT (<=50) to any run_sparql. Never fabricate values or IRIs.
- When you have the data, call submit_answer with a concise Japanese answer.
- If the data genuinely cannot answer the question, say so honestly.

Schema (classes / predicates / per-class shapes with counts):
%s
"""

_VERIFIED_SEP = "__"


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


def _verified_tool_defs() -> tuple[list[dict], dict]:
    """Anthropic tool defs for every declared query tool, across ALL datasets.

    Returns ``(defs, registry)`` with ``registry[name] = (dataset, QueryTool)``.
    The engine knows no vocabulary; tools come from each dataset's
    ``query_tools.yaml`` (#112/#113), so a newly onboarded dataset's tools appear
    here for free — the generality requirement ("all ingested datasets askable").
    """
    from asterism.query_tools import load_all_query_tools

    defs: list[dict] = []
    registry: dict[str, tuple] = {}
    for dataset, qts in load_all_query_tools().items():
        for qt in qts:
            name = f"{dataset}{_VERIFIED_SEP}{qt.name}"
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


async def _llm_router_answer(question: str, api_key: str) -> dict:
    """C: an LLM routes a free-text question to a VERIFIED typed tool
    (deterministic, citable) or, only if none fits, to a read-only SPARQL escape
    (unverified).

    Returns ``{answer, citations, notes, sparql}`` plus provenance for the UI
    badge: ``verified_tools`` (the vetted tools actually used) and
    ``unverified_sparql`` (whether the escape was used). Execution stays
    read-only: verified tools run fixed templates through the FROM-merge, and
    run_sparql is guarded.
    """
    from asterism.query_tools import run_query_tool
    from asterism_mcp.tools import schema_summary, sparql_query

    client = _client()
    schema = await schema_summary(
        client, max_classes=40, max_predicates=80, predicates_per_class=15
    )
    verified_defs, registry = _verified_tool_defs()
    system = _ROUTER_SYSTEM % _render_schema(schema)
    tools = [*verified_defs, _RUN_SPARQL_TOOL, _SUBMIT_ANSWER_TOOL]
    messages: list[dict] = [{"role": "user", "content": question}]
    used_sparql: list[str] = []
    verified_used: list[dict] = []
    unverified = False
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

        # Execute every tool call (verified tool or run_sparql) and feed results back.
        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        tool_results: list[dict] = []
        for tu in tool_uses:
            result: dict
            if tu.name == "run_sparql":
                unverified = True
                q = (tu.input or {}).get("query", "")
                try:
                    result = await sparql_query(q, client, max_rows=50)
                    # Disclose the query that ACTUALLY ran (FROM-merge rewrite, #20).
                    used_sparql.append(result.get("effective_query") or q)
                except Exception as exc:  # never let a bad query kill the loop
                    used_sparql.append(q)
                    result = {"error": str(exc)}
            elif tu.name in registry:
                dataset, qt = registry[tu.name]
                try:
                    out = await run_query_tool(
                        client, qt, dict(tu.input or {}), max_rows=50
                    )
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
                except Exception as exc:
                    result = {"error": str(exc)}
            else:
                result = {"error": "unknown tool"}
            tr: dict = {
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
            if isinstance(result, dict) and result.get("error"):
                tr["is_error"] = True
            tool_results.append(tr)
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
    # Nothing concrete to query on (#142): a bare sample_search(None) matches every
    # sample and would block the fallthrough — return empty so the no-key path
    # appends a hint instead of a canned "all samples" list.
    return {"answer": "", "citations": [], "notes": []}


@app.post("/demo/ask")
async def ask(
    req: AskRequest,
    x_api_key: str | None = Header(default=None),
) -> dict:
    if not _REAL:
        # MOCK: same grounded fixture regardless of question.
        return _ASK_FIXTURE

    # 1. With a key: the C router. The LLM picks a VERIFIED typed tool (any
    #    dataset — deterministic, citable) and only falls back to a read-only
    #    SPARQL escape when none fits. Covers every ingested dataset + the long
    #    tail; the response carries provenance so the UI can badge each answer.
    if x_api_key:
        return await _llm_router_answer(req.question, x_api_key)

    # 2. No key: the LLM-free keyword floor (starrydata typed tools — key-free,
    #    deterministic). It only fits the starrydata schema; if nothing matches,
    #    hand back the empty answer with a hint that the general router needs a key.
    typed = await _typed_answer(req.question)
    if typed.get("citations"):
        return typed
    typed.setdefault("notes", []).append(
        "キー無しの型付き検索（starrydata）で該当がありませんでした。MP など他データセットや "
        "自由な質問には API キーが必要です（検証済ツールへ自動振り分け＋未整備な問いは SPARQL 生成）。"
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
