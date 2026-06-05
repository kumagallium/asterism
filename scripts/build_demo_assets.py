#!/usr/bin/env python3
"""Build the static, server-less demo assets under ``docs/demo/data/``.

Takes the seed Turtle produced by ``make_demo_subset.py`` and emits:

- ``starrydata-demo.ttl`` — the three seed graphs merged into one file, with the
  large ``sd:xValuesJSON`` / ``sd:yValuesJSON`` point arrays **stripped from all
  curves except a small "featured" set** (the top ZT curves, which the demo can
  plot). Ranking / search / provenance only read scalars, so this keeps the
  shipped bundle small without changing any answer.
- ``answers.json`` — precomputed results + the exact SPARQL used, produced by
  calling the **real** typed MCP tools (``asterism_mcp.tools``) against the
  shipped Turtle through an in-process pyoxigraph adapter. The browser re-runs
  the same SPARQL via oxigraph-wasm; this file is the graceful fallback and the
  source of provenance chains / curve plots. Demo == production tool logic by
  construction (no hand-written answers).
- ``PROVENANCE.md`` — source / attribution / license / regeneration commands.

Design: docs/architecture/static-citable-facts-demo.md

Usage (run after make_demo_subset.py):
    python scripts/build_demo_assets.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

import pyoxigraph as ox
from rdflib import Graph, URIRef

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "ingest" / "src"))
sys.path.insert(0, str(_REPO / "mcp" / "src"))

from asterism_mcp.tools import (  # noqa: E402
    property_ranking,
    provenance_of,
    sample_search,
    template_curve_fetch,
)

SD = "https://kumagallium.github.io/asterism/starrydata/ontology#"
X_VALUES = URIRef(SD + "xValuesJSON")
Y_VALUES = URIRef(SD + "yValuesJSON")

SEED = _REPO / "datasets" / "starrydata" / "seed"
OUT = _REPO / "docs" / "demo" / "data"

MAX_PLAUSIBLE = 3.5  # physically-plausible ZT ceiling (digitization-error guard)
TOP_N = 10  # ranking rows shown in the demo
N_FEATURED = TOP_N  # keep point arrays for exactly the plottable ranking curves
# Composition substrings to try for the default search card (first with hits wins).
# Ordered to favour formulae actually present in the subset, with common
# thermoelectric families as fallbacks for other subsets.
COMPOSITION_CANDIDATES = [
    "Ba8Ga16",
    "Ba8",
    "Bi2Te3",
    "PbTe",
    "SnSe",
    "SiGe",
    "CoSb3",
    "Mg2Si",
]

# --- cross-dataset (Materials Project) ---
PROV_NS = "http://www.w3.org/ns/prov#"
# starrydata's per-dataset graph namespace; the MP structure links live in their
# own named graph so the demo can show a real 2-source join (FROM/GRAPH).
MP_GRAPH = "https://kumagallium.github.io/asterism/starrydata/graph/mp-links"
MP_OUT = _REPO / "experiments" / "mp-linking-poc" / "out"
MP_TBOX = _REPO / "experiments" / "mp-linking-poc" / "mp_link_tbox.ttl"
CROSS_TOP_N = 12
_PFX = f"PREFIX sd: <{SD}>\nPREFIX prov: <{PROV_NS}>\n"

# The headline cross-dataset query (disclosed in the UI): literature ZT
# (starrydata, default graph) joined to the host crystal structure (Materials
# Project, named graph) on the SAME sample IRI — no ETL.
CROSS_SPARQL = _PFX + (
    "# 2 ソースを 1 クエリで結合（ETL 不要・結合キー = 同じ sample IRI）\n"
    "#   既定グラフ = starrydata（文献の測定値 ZT）\n"
    "#   名前付きグラフ = Materials Project（計算された母相の結晶構造）\n"
    "# 母相結晶構造ごとに 1 行（その相の最大 ZT・該当サンプル数）。\n"
    "SELECT ?formula (MAX(?zt) AS ?ztmax) (COUNT(DISTINCT ?sample) AS ?nsamples)\n"
    "       ?sg ?csys ?proto ?mp (SAMPLE(?sample) AS ?example)\n"
    "       (SAMPLE(?comp) AS ?examplecomp) WHERE {\n"
    '  ?curve a sd:Curve ; sd:propertyY "ZT" ; sd:yMax ?zt ; sd:ofSample ?sample .\n'
    "  ?sample sd:compositionString ?comp .\n"
    f"  GRAPH <{MP_GRAPH}> {{\n"
    "    ?sample sd:hasHostStructure ?st .\n"
    "    ?st sd:reducedHostFormula ?formula .\n"
    "    OPTIONAL { ?st sd:spaceGroupSymbol ?sg }\n"
    "    OPTIONAL { ?st sd:crystalSystem ?csys }\n"
    "    OPTIONAL { ?st sd:structurePrototype ?proto }\n"
    "    OPTIONAL { ?st sd:idealizedFrom ?mp }\n"
    "  }\n"
    "} GROUP BY ?formula ?sg ?csys ?proto ?mp\n"
    "ORDER BY DESC(?ztmax) LIMIT " + str(CROSS_TOP_N)
)


# --------------------------------------------------------------------------
# pyoxigraph -> SPARQL-JSON adapter so we can call the real async MCP tools
# --------------------------------------------------------------------------


def _term_cell(term: Any) -> dict[str, Any]:
    name = type(term).__name__
    if name == "NamedNode":
        return {"type": "uri", "value": term.value}
    if name == "BlankNode":
        return {"type": "bnode", "value": term.value}
    cell: dict[str, Any] = {"type": "literal", "value": term.value}
    dt = getattr(term, "datatype", None)
    if dt is not None:
        cell["datatype"] = dt.value
    lang = getattr(term, "language", None)
    if lang:
        cell["xml:lang"] = lang
    return cell


class StoreClient:
    """Minimal OxigraphClient stand-in over an in-process pyoxigraph Store.

    Implements the only method the tools use (``sparql_select``) and records the
    SPARQL strings issued, so we can ship the exact queries the tools ran.
    """

    def __init__(self, store: ox.Store) -> None:
        self.store = store
        self.queries: list[str] = []

    async def sparql_select(self, query: str) -> dict[str, Any]:
        self.queries.append(query)
        res = self.store.query(query)
        if isinstance(res, bool):
            return {"head": {}, "boolean": res}
        variables = [str(v).lstrip("?") for v in res.variables]
        bindings: list[dict[str, Any]] = []
        for sol in res:
            row: dict[str, Any] = {}
            for var in variables:
                term = sol[var]
                if term is not None:
                    row[var] = _term_cell(term)
            bindings.append(row)
        return {"head": {"vars": variables}, "results": {"bindings": bindings}}


def _load_store(ttl_path: Path) -> ox.Store:
    store = ox.Store()
    store.load(io.BytesIO(ttl_path.read_bytes()), mime_type="text/turtle")
    return store


# --------------------------------------------------------------------------
# Build steps
# --------------------------------------------------------------------------


def _featured_iris(merged_ttl: Path) -> list[str]:
    """Top-N ZT curve IRIs by yMax (<= MAX_PLAUSIBLE) — the plottable set."""
    client = StoreClient(_load_store(merged_ttl))
    ranking = asyncio.run(
        property_ranking(
            client, property_y="ZT", top_n=N_FEATURED, max_plausible=MAX_PLAUSIBLE
        )
    )
    return [r["curve_iri"] for r in ranking["results"] if r.get("curve_iri")]


def _merge_and_trim() -> tuple[Path, dict[str, int]]:
    g = Graph()
    for name in ("papers.ttl", "samples.ttl", "curves.ttl"):
        g.parse(SEED / name, format="turtle")

    OUT.mkdir(parents=True, exist_ok=True)
    full = OUT / "_full.ttl"
    g.serialize(destination=str(full), format="turtle")

    featured = set(_featured_iris(full))

    stripped = 0
    for subj in {s for s, _, _ in g.triples((None, X_VALUES, None))}:
        if str(subj) in featured:
            continue
        g.remove((subj, X_VALUES, None))
        g.remove((subj, Y_VALUES, None))
        stripped += 1

    merged = OUT / "starrydata-demo.ttl"
    g.serialize(destination=str(merged), format="turtle")
    full.unlink(missing_ok=True)

    return merged, {"stripped_curve_arrays": stripped, "featured": len(featured)}


def _count(store: ox.Store, cls: str) -> int:
    res = store.query(f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ ?s a <{SD}{cls}> }}")
    for sol in res:
        return int(sol["n"].value)
    return 0


def _pick_composition(store: ox.Store) -> str:
    client = StoreClient(store)
    for cand in COMPOSITION_CANDIDATES:
        out = asyncio.run(sample_search(client, composition=cand, limit=20))
        if out["count"] > 0:
            return cand
    return ""


def _template(query: str, value: str, token: str) -> str:
    return query.replace(value, token, 1)


# --------------------------------------------------------------------------
# Cross-dataset (Materials Project) — second graph + 2-source join
# --------------------------------------------------------------------------


def _v(sol: Any, var: str) -> str | None:
    term = sol[var]
    return term.value if term is not None else None


def _prepare_mp_links() -> tuple[Path, str] | None:
    """Merge MP-link ABox (live preferred) + TBox into docs/demo/data/mp-links.ttl.

    The ABox is produced by ``experiments/mp-linking-poc/link_mp.py`` (run after
    make_demo_subset, on the seed samples). Returns (path, source) or None if no
    MP-link TTL has been generated yet (then the demo simply omits the card).
    """
    live = MP_OUT / "sample_mp_links.live.ttl"
    demo = MP_OUT / "sample_mp_links.demo.ttl"
    src_ttl = live if live.exists() else (demo if demo.exists() else None)
    if src_ttl is None:
        return None
    g = Graph()
    g.parse(src_ttl, format="turtle")
    if MP_TBOX.exists():
        g.parse(MP_TBOX, format="turtle")  # bundle TBox so the graph is self-describing
    out = OUT / "mp-links.ttl"
    g.serialize(destination=str(out), format="turtle")
    placeholder = any(
        str(o).startswith("https://next-gen.materialsproject.org/materials/mp-DEMO")
        for o in g.objects(None, URIRef(SD + "idealizedFrom"))
    )
    source = (
        "demo (placeholder mp-id)" if placeholder else "live (Materials Project REST)"
    )
    return out, source


def _bridge_details(store: ox.Store, sample_iri: str) -> dict[str, Any]:
    """How the link was made (the 'ontology bridge'): match activity + dopants."""
    scal_q = (
        _PFX
        + f"SELECT ?st ?method ?conf ?time ?mp WHERE {{ GRAPH <{MP_GRAPH}> {{ "
        + f"<{sample_iri}> sd:hasHostStructure ?st . "
        + f"OPTIONAL {{ ?act a sd:StructureMatchActivity ; prov:used <{sample_iri}> . "
        + "OPTIONAL { ?act sd:matchMethod ?method } "
        + "OPTIONAL { ?act sd:matchConfidence ?conf } "
        + "OPTIONAL { ?act prov:endedAtTime ?time } } "
        + "OPTIONAL { ?st sd:idealizedFrom ?mp } } } LIMIT 1"
    )
    out: dict[str, Any] = {"dopants": []}
    for sol in store.query(scal_q):
        out.update(
            structure_iri=_v(sol, "st"),
            match_method=_v(sol, "method"),
            match_confidence=_v(sol, "conf"),
            match_time=_v(sol, "time"),
            mp_iri=_v(sol, "mp"),
        )
        break
    dq = (
        _PFX
        + f"SELECT ?el ?amt WHERE {{ GRAPH <{MP_GRAPH}> {{ "
        + f"<{sample_iri}> sd:hasPointDefect ?d . ?d sd:dopantElement ?el ; sd:siteAmount ?amt "
        + "} }"
    )
    for sol in store.query(dq):
        out["dopants"].append({"element": _v(sol, "el"), "amount": _v(sol, "amt")})
    return out


def _cross_dataset(store: ox.Store) -> dict[str, Any]:
    """Run the 2-source join and attach per-row provenance of the link itself."""
    rows: list[dict[str, Any]] = []
    for sol in store.query(CROSS_SPARQL):
        mp_iri = _v(sol, "mp")
        rows.append(
            {
                "host_formula": _v(sol, "formula"),
                "zt": float(_v(sol, "ztmax")) if _v(sol, "ztmax") else None,
                "n_samples": int(_v(sol, "nsamples")) if _v(sol, "nsamples") else 0,
                "space_group": _v(sol, "sg"),
                "crystal_system": _v(sol, "csys"),
                "prototype": _v(sol, "proto"),
                "mp_id": mp_iri.rsplit("/", 1)[-1] if mp_iri else None,
                "mp_iri": mp_iri,
                "example_sample_iri": _v(sol, "example"),
                "example_composition": _v(sol, "examplecomp"),
            }
        )
    for r in rows:
        r["bridge"] = _bridge_details(store, r["example_sample_iri"])
    return {"sparql": CROSS_SPARQL, "graph": MP_GRAPH, "rows": rows}


def main() -> int:
    if not (SEED / "curves.ttl").exists():
        print("seed not found — run scripts/make_demo_subset.py first", file=sys.stderr)
        return 1

    merged, trim_stats = _merge_and_trim()
    store = _load_store(merged)

    n_papers = _count(store, "Paper")
    n_samples = _count(store, "Sample")
    n_curves = _count(store, "Curve")

    # --- ZT ranking (property_ranking) ---
    rc = StoreClient(store)
    ranking = asyncio.run(
        property_ranking(rc, property_y="ZT", top_n=TOP_N, max_plausible=MAX_PLAUSIBLE)
    )
    ranking_sparql = rc.queries[0]

    # --- composition search (sample_search) ---
    comp_value = _pick_composition(store)
    sc = StoreClient(store)
    search = asyncio.run(sample_search(sc, composition=comp_value, limit=20))
    search_sparql = sc.queries[0]
    # token the lowercased+escaped value the tool embedded, for live re-templating
    search_template = _template(search_sparql, comp_value.lower(), "%Q%")

    # --- provenance for each ranking curve (provenance_of) ---
    chains: dict[str, Any] = {}
    prov_template = ""
    for row in ranking["results"]:
        iri = row["curve_iri"]
        if not iri:
            continue
        pc = StoreClient(store)
        chain = asyncio.run(provenance_of(iri, pc))
        chains[iri] = chain
        if not prov_template:
            prov_template = _template(pc.queries[0], iri, "%IRI%")

    # --- featured curve point arrays (template_curve_fetch) ---
    featured_curves: dict[str, Any] = {}
    for iri in chains:
        tc = StoreClient(store)
        try:
            curve = asyncio.run(template_curve_fetch(iri, tc))
        except Exception:  # noqa: BLE001 — a stripped/absent curve just skips the plot
            continue
        if curve.get("x") and curve.get("y"):
            featured_curves[iri] = {
                k: curve.get(k)
                for k in (
                    "property_x",
                    "property_y",
                    "unit_x",
                    "unit_y",
                    "figure_name",
                    "point_count",
                    "x",
                    "y",
                )
            }

    # --- cross-dataset: load MP links into a named graph + run the 2-source join ---
    cross: dict[str, Any] | None = None
    mp_meta: dict[str, Any] = {}
    mp = _prepare_mp_links()
    if mp is not None:
        mp_path, mp_source = mp
        store.load(
            io.BytesIO(mp_path.read_bytes()),
            mime_type="text/turtle",
            to_graph=ox.NamedNode(MP_GRAPH),
        )
        cross = _cross_dataset(store)
        cross["label"] = "横断結合 — Starrydata × Materials Project"
        cross["source"] = mp_source
        mp_meta = {"mp_source": mp_source, "mp_linked_rows": len(cross["rows"])}

    answers = {
        "meta": {
            "generated_note": (
                "Precomputed by scripts/build_demo_assets.py via the real "
                "asterism_mcp.tools against the shipped Turtle. The browser "
                "re-runs the same SPARQL with oxigraph-wasm; this is the fallback."
            ),
            "dataset": "starrydata (demo subset)",
            "papers": n_papers,
            "samples": n_samples,
            "curves": n_curves,
            "max_plausible": MAX_PLAUSIBLE,
            **trim_stats,
            **mp_meta,
        },
        "ranking": {
            "label": "ZT ランキング (property_ranking)",
            "sparql": ranking_sparql,
            "result": ranking,
        },
        "composition": {
            "label": "組成検索 (sample_search)",
            "sparql_template": search_template,
            "default_value": comp_value,
            "result": search,
        },
        "provenance": {
            "label": "来歴トレース (provenance_of)",
            "sparql_template": prov_template,
            "chains": chains,
        },
        "featured_curves": featured_curves,
        "cross": cross,
    }

    (OUT / "answers.json").write_text(
        json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _write_provenance_md(n_papers, n_samples, n_curves, trim_stats, comp_value, mp_meta)

    print(
        f"built: papers={n_papers} samples={n_samples} curves={n_curves} "
        f"featured={trim_stats['featured']} stripped={trim_stats['stripped_curve_arrays']} "
        f"cross_rows={mp_meta.get('mp_linked_rows', 0)} ({mp_meta.get('mp_source', 'no MP links')})",
        file=sys.stderr,
    )
    print(f"  -> {merged.relative_to(_REPO)}", file=sys.stderr)
    print(f"  -> {(OUT / 'answers.json').relative_to(_REPO)}", file=sys.stderr)
    return 0


def _write_provenance_md(
    papers: int,
    samples: int,
    curves: int,
    trim: dict[str, int],
    comp: str,
    mp_meta: dict[str, Any],
) -> None:
    mp_section = ""
    if mp_meta:
        mp_section = f"""
## 横断結合データ (Materials Project)

`mp-links.ttl` は、各 starrydata サンプルの**母相結晶構造**を
[Materials Project](https://next-gen.materialsproject.org/) から解決したリンク
（同じ `sd:sample/...` IRI に追加した別グラフ `{MP_GRAPH}`）です。生成は
`experiments/mp-linking-poc/link_mp.py`（母相正規化 → MP 照合 → PROV 付きリンク）。

- 取得元: Materials Project（mode = {mp_meta.get("mp_source", "?")}・解決 {mp_meta.get("mp_linked_rows", 0)} 行）
- ライセンス/帰属: Materials Project のデータは **CC-BY 4.0**。本デモは最小の事実
  （mp-id・空間群・結晶系・prototype・還元式）のみを帰属付きで同梱します。
  引用: A. Jain et al., "Commentary: The Materials Project", *APL Materials* 1, 011002 (2013).
  各 mp-id は `https://next-gen.materialsproject.org/materials/<mp-id>` で解決できます。
- 設計: 実サンプル(ドープ)と MP の計算相は別物なので `owl:sameAs` を使わず、
  `sd:idealizedFrom`（`prov:wasDerivedFrom` のサブプロパティ）で「母相参照」に留め、
  ドープは `sd:PointDefect`、リンク自体は `sd:StructureMatchActivity`（方法・一致度）で由来づけ。
"""
    text = f"""# デモ同梱データの出典・帰属・ライセンス

このディレクトリの `starrydata-demo.ttl` / `answers.json` は、
[Starrydata](https://www.starrydata2.org/) の熱電材料データから生成した
**最小限の公開サブセット**です（決定論・サーバ不要・AI 不要の静的デモ用）。

- 出典: Starrydata（熱電材料の論文・サンプル・曲線データ）
- 帰属: 各事実は IRI 経由で原典論文（DOI）まで辿れます（来歴トレース参照）。
- このサブセットは `datasets/starrydata/seed/`（gitignore 済の作業用 seed）とは別に、
  **デモ配信のため意図的にコミットする** curated データです。

## 規模

- papers: {papers}
- samples: {samples}
- curves: {curves}
- 点列（x/y JSON 配列）を保持した featured 曲線: {trim["featured"]}
  （プロット表示用。他 {trim["stripped_curve_arrays"]} 曲線は点列を除去しスカラのみ保持）
- 既定の組成検索クエリ: `{comp}`
{mp_section}
## 再生成

```bash
# 1) seed サブセット生成（ローカルの ../starrydata_dataset が必要）
python scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40 \\
  --include-sids 3,9,20,120,869
# 2) MP 構造リンク（横断結合用・要 MP_API_KEY で実 mp-id）
MP_API_KEY=... python experiments/mp-linking-poc/link_mp.py \\
  --csv datasets/starrydata/seed/csv/samples.csv \\
  --out experiments/mp-linking-poc/out/sample_mp_links.live.ttl --limit 100000 --mode live
# 3) 静的デモアセット生成（このディレクトリを再生成）
python scripts/build_demo_assets.py
```

`answers.json` は本番の typed ツール（`asterism_mcp.tools` の
`property_ranking` / `sample_search` / `provenance_of` / `template_curve_fetch`）を
同梱 Turtle に対して実行した結果です。ブラウザは同じ SPARQL を oxigraph-wasm で
再実行します（このファイルはフォールバック）。
"""
    (OUT / "PROVENANCE.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
