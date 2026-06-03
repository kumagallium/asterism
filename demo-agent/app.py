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

_ASK_SYSTEM = """\
You answer questions over an RDF graph by writing READ-ONLY SPARQL and grounding \
your answer in the actual results. Make NO assumptions about the vocabulary \
beyond the schema below — it lists the classes, predicates, and per-class \
predicate shapes that ACTUALLY exist in the store, with usage counts.

Rules:
- Use ONLY IRIs that appear in the schema, written as full IRIs in <>.
- Always SELECT enough to cite (include the subject IRI). Add LIMIT (<=50).
- Call run_sparql to execute. If it returns an error, read it and try again \
(at most a few times); do not give up after one failure.
- When you have the data, call submit_answer with a concise Japanese answer and \
citations whose IRIs literally appear in your results. Never fabricate values.
- If the data genuinely cannot answer the question, say so honestly.

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


async def _llm_sparql_answer(question: str, api_key: str) -> dict:
    """Schema-grounded LLM agent: introspect → write SPARQL → run → answer.

    Returns the same ``{answer, citations, notes}`` contract as the typed path,
    plus a ``sparql`` list of the queries actually run (disclosure: the user can
    see and verify how the answer was derived). All queries go through the
    read-only ``sparql_query`` guard, so the LLM cannot mutate the store.
    """
    from asterism_mcp.tools import schema_summary, sparql_query

    client = _client()
    schema = await schema_summary(
        client, max_classes=40, max_predicates=80, predicates_per_class=15
    )
    system = _ASK_SYSTEM % _render_schema(schema)
    tools = [_RUN_SPARQL_TOOL, _SUBMIT_ANSWER_TOOL]
    messages: list[dict] = [{"role": "user", "content": question}]
    used_sparql: list[str] = []
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
            }

        if not tool_uses:
            # The model replied with text but no tool call — surface that text.
            text = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return {
                "answer": text or "回答を生成できませんでした。",
                "citations": [],
                "notes": [],
                "sparql": used_sparql,
            }

        # Execute every run_sparql call and feed the results back.
        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        tool_results: list[dict] = []
        for tu in tool_uses:
            if tu.name == "run_sparql":
                q = (tu.input or {}).get("query", "")
                used_sparql.append(q)
                try:
                    result = await sparql_query(q, client, max_rows=50)
                except Exception as exc:  # never let a bad query kill the loop
                    result = {"error": str(exc)}
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            else:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": "unknown tool",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})

    # Exhausted steps without a submit_answer (should be rare given force_final).
    return {
        "answer": "回答を生成できませんでした（試行回数の上限に達しました）。",
        "citations": [],
        "notes": [],
        "sparql": used_sparql,
    }


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


async def _typed_answer(question: str) -> dict:
    """The deterministic starrydata-shaped path (no LLM). May return no citations
    when the question/schema does not fit the typed tools — that is the signal to
    fall through to the LLM escape."""
    from asterism_mcp.tools import property_ranking, sample_search

    kind, arg, mp = _route(question)
    if kind == "rank" and arg:
        rank = await property_ranking(
            _client(), property_y=arg, top_n=10, max_plausible=mp
        )
        return _compose_rank(rank)
    res = await sample_search(_client(), composition=arg, limit=20)
    return _compose_search(arg, res)


@app.post("/demo/ask")
async def ask(
    req: AskRequest,
    x_api_key: str | None = Header(default=None),
) -> dict:
    if not _REAL:
        # MOCK: same grounded fixture regardless of question.
        return _ASK_FIXTURE

    # 1. Deterministic typed path first (LLM-free, fast, reproducible). This is
    #    the main act per product direction; it only fits the starrydata schema.
    typed = await _typed_answer(req.question)
    if typed.get("citations"):
        return typed

    # 2. Escape: the typed tools found nothing — the data may use a schema they
    #    were never specialized for. If the caller brought an API key, let an LLM
    #    introspect the schema and write a read-only SPARQL query to answer.
    if x_api_key:
        return await _llm_sparql_answer(req.question, x_api_key)

    # 3. No key, nothing typed: hand back the empty typed answer with a hint so
    #    the user knows the general (LLM) path needs a key.
    typed.setdefault("notes", []).append(
        "型付きツールで該当が見つかりませんでした。任意スキーマへの一般的な質問には "
        "API キーが必要です（スキーマを内省して SPARQL を生成します）。"
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
