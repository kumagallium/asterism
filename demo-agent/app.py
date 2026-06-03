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

import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_OXIGRAPH_URL = os.environ.get("CSV2RDF_OXIGRAPH_URL")
_REAL = bool(_OXIGRAPH_URL)
_RES = "https://kumagallium.github.io/asterism/starrydata/resource/"

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
# endpoints
# ---------------------------------------------------------------------------


@app.post("/demo/ask")
async def ask(req: AskRequest) -> dict:
    if not _REAL:
        # MOCK: same grounded fixture regardless of question.
        return _ASK_FIXTURE
    from asterism_mcp.tools import property_ranking, sample_search

    kind, arg, mp = _route(req.question)
    if kind == "rank" and arg:
        rank = await property_ranking(
            _client(), property_y=arg, top_n=10, max_plausible=mp
        )
        return _compose_rank(rank)
    res = await sample_search(_client(), composition=arg, limit=20)
    return _compose_search(arg, res)


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
