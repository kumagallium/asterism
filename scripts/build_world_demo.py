#!/usr/bin/env python3
"""Build the **world** example dataset (object-cards-ui PR E, 契約メモ
contract_pr_e.md §1) — content-authoring tool, NOT runtime ingestion code
(same family as ``datasets/materials_project/seed/build_seed.py`` and
``datasets/papers/seed/build_paper_graph.py``). Deterministic: re-running it
on the same ``source/gapminder.json`` produces byte-identical output.

What it does, in order — the SAME declarative path any onboarded Asterism
dataset goes through, never a hand-written shortcut:

1. ``source/gapminder.json`` (the unmodified vega-datasets extract; fetch
   instructions in README.md) -> ``source/world.csv`` (a deterministic,
   sorted CSV — the "meaning before identity" tabular source).
2. ``mapping.yaml`` (the reviewed Mapping IR) is compiled to
   ``mapping.rml.ttl`` with the SAME deterministic compiler the api's
   materialize step uses (``asterism_step0.rml_compile.compile_mapping_ir``).
   No hand-written RML.
3. ``mapping.rml.ttl`` + ``source/world.csv`` are run through **Morph-KGC**
   (``asterism.substrate.materialize_to_graph``) — the real declarative
   ingest engine, not a hand-written Turtle fixture.
4. The resulting graph is packaged as ``snapshot.tar``, byte-for-byte in the
   ``asterism_api.exchange`` snapshot format, so
   ``asterism_api.exchange.import_snapshot`` can load it exactly as it would
   any other instance's export. ``--verify`` (default on) proves this by
   actually calling ``import_snapshot`` against a throwaway registry dir and
   an in-memory (rdflib-backed) SPARQL store — no server, no Oxigraph
   process needed for a normal build.

Run from the ``api`` package's venv (the one editable-installed venv in this
repo that already has ``asterism-step0``, ``asterism-ingest[substrate]`` for
Morph-KGC, and ``asterism-api`` together)::

    cd api && uv run python ../scripts/build_world_demo.py

``--oxigraph-bin`` additionally round-trips the graph through a real,
temporary Oxigraph process (``POST /store`` + ``SELECT COUNT``) before
packaging, to double-check byte-for-byte that a real store loads it
unchanged. Not required for a normal rebuild — see README.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DATASET_DIR = REPO_ROOT / "datasets" / "world"
SOURCE_DIR = DATASET_DIR / "source"
GAPMINDER_JSON = SOURCE_DIR / "gapminder.json"
WORLD_CSV = SOURCE_DIR / "world.csv"
ACTIVITY_CSV = SOURCE_DIR / "activity.csv"
MAPPING_YAML = DATASET_DIR / "mapping.yaml"
MAPPING_RML = DATASET_DIR / "mapping.rml.ttl"
MODEL_YAML = DATASET_DIR / "model.yaml"
MIE_YAML = DATASET_DIR / "mie.yaml"
QUERY_TOOLS_YAML = DATASET_DIR / "query_tools.yaml"
SNAPSHOT_TAR = DATASET_DIR / "snapshot.tar"

DATASET_ID = "world"
DATASET_NAME = "世界の国（Gapminder）"  # noqa: RUF001 — Japanese full-width parens, correct typography

CSV_COLUMNS = [
    "country",
    "country_ja",
    "region",
    "region_ja",
    "year",
    "population",
    "life_expectancy",
    "fertility",
]

# vega-datasets' own six `cluster` values (0-5), named per the dataset's
# documented grouping (contract memo §1). Fixed, closed set — no invented
# clusters.
CLUSTER_REGION_EN: dict[int, str] = {
    0: "South Asia",
    1: "Europe & Central Asia",
    2: "Sub-Saharan Africa",
    3: "America",
    4: "East Asia & Pacific",
    5: "Middle East & North Africa",
}
REGION_JA: dict[str, str] = {
    "South Asia": "南アジア",
    "Europe & Central Asia": "ヨーロッパ・中央アジア",
    "Sub-Saharan Africa": "サブサハラアフリカ",
    "America": "アメリカ大陸",
    "East Asia & Pacific": "東アジア・太平洋",
    "Middle East & North Africa": "中東・北アフリカ",
}

# Deterministic (country, English) -> 日本語の一般的な表記. Closed set matched
# against the exact 62 country strings vega-datasets@2's gapminder.json
# carries; a country not in this table keeps its English name (contract memo
# §1: "分からない国は英名のまま" — never guessed).
COUNTRY_JA: dict[str, str] = {
    "Afghanistan": "アフガニスタン",
    "Argentina": "アルゼンチン",
    "Australia": "オーストラリア",
    "Austria": "オーストリア",
    "Bahamas": "バハマ",
    "Bangladesh": "バングラデシュ",
    "Barbados": "バルバドス",
    "Belgium": "ベルギー",
    "Bolivia": "ボリビア",
    "Brazil": "ブラジル",
    "Canada": "カナダ",
    "Chile": "チリ",
    "China": "中国",
    "Colombia": "コロンビア",
    "Costa Rica": "コスタリカ",
    "Croatia": "クロアチア",
    "Cuba": "キューバ",
    "Dominican Republic": "ドミニカ共和国",
    "Ecuador": "エクアドル",
    "Egypt": "エジプト",
    "El Salvador": "エルサルバドル",
    "Finland": "フィンランド",
    "France": "フランス",
    "Georgia": "ジョージア",
    "Germany": "ドイツ",
    "Greece": "ギリシャ",
    "Grenada": "グレナダ",
    "Haiti": "ハイチ",
    "Hong Kong, China": "香港",
    "Iceland": "アイスランド",
    "India": "インド",
    "Indonesia": "インドネシア",
    "Iran": "イラン",
    "Iraq": "イラク",
    "Ireland": "アイルランド",
    "Israel": "イスラエル",
    "Italy": "イタリア",
    "Jamaica": "ジャマイカ",
    "Japan": "日本",
    "Kenya": "ケニア",
    "Lebanon": "レバノン",
    "Mexico": "メキシコ",
    "Netherlands": "オランダ",
    "New Zealand": "ニュージーランド",
    "Nigeria": "ナイジェリア",
    "North Korea": "北朝鮮",
    "Norway": "ノルウェー",
    "Pakistan": "パキスタン",
    "Peru": "ペルー",
    "Philippines": "フィリピン",
    "Poland": "ポーランド",
    "Portugal": "ポルトガル",
    "Rwanda": "ルワンダ",
    "Saudi Arabia": "サウジアラビア",
    "South Africa": "南アフリカ",
    "South Korea": "韓国",
    "Spain": "スペイン",
    "Switzerland": "スイス",
    "Turkey": "トルコ",
    "United Kingdom": "イギリス",
    "United States": "アメリカ合衆国",
    "Venezuela": "ベネズエラ",
}

SNAPSHOT_FORMAT = "asterism-snapshot"
SNAPSHOT_FORMAT_VERSION = 1
ORIGIN_IRI_BASE = "https://asterism.invalid"  # asterism_step0.instance_iri.DEFAULT_IRI_BASE

# The take-in's own PROV-O activity (checker blocker fix: mapping.yaml's
# `activity` map, source/activity.csv — see that file's docstring). A single
# row, one fixed activity id, matching mapping.yaml's constant
# `wr:activity/{ACTIVITY_ID}` object used by `country`/`observation`'s
# `prov:wasGeneratedBy`. Values here MUST match README.md's "来歴" section.
ACTIVITY_ID = "ingest-world-v1"
ACTIVITY_SOURCE_URL = "https://cdn.jsdelivr.net/npm/vega-datasets@2/data/gapminder.json"
ACTIVITY_RETRIEVED_AT = "2026-09-24T00:00:00Z"  # README.md "Retrieved on: 2026-09-24"
ACTIVITY_TOOL = "asterism build_world_demo.py"
ACTIVITY_CSV_COLUMNS = ["activity_id", "source_file", "source_url", "retrieved_at", "tool"]


# ---------------------------------------------------------------------------
# Step 1: source/gapminder.json -> source/world.csv
# ---------------------------------------------------------------------------


def build_world_csv(gapminder_path: Path, out_path: Path) -> list[dict[str, object]]:
    """Deterministic CSV projection of the raw vega-datasets extract.

    Sorted by (country, year) so the file diffs cleanly and world.csv's row
    order never depends on the source JSON's own order.
    """
    records = json.loads(gapminder_path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for r in records:
        country = str(r["country"])
        cluster = int(r["cluster"])
        region_en = CLUSTER_REGION_EN[cluster]
        rows.append(
            {
                "country": country,
                "country_ja": COUNTRY_JA.get(country, country),
                "region": region_en,
                "region_ja": REGION_JA[region_en],
                "year": int(r["year"]),
                "population": int(r["pop"]),
                "life_expectancy": float(r["life_expect"]),
                "fertility": float(r["fertility"]),
            }
        )
    rows.sort(key=lambda row: (row["country"], row["year"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def build_activity_csv(out_path: Path) -> dict[str, str]:
    """Deterministic 1-row CSV: the take-in's own PROV-O activity (checker
    blocker fix — see ``mapping.yaml``'s ``activity`` map docstring). Fixed
    values, not derived from ``source/gapminder.json`` — re-running the build
    against the same fetch always writes the same row."""
    row = {
        "activity_id": ACTIVITY_ID,
        "source_file": "gapminder.json",
        "source_url": ACTIVITY_SOURCE_URL,
        "retrieved_at": ACTIVITY_RETRIEVED_AT,
        "tool": ACTIVITY_TOOL,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ACTIVITY_CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
    return row


# ---------------------------------------------------------------------------
# Step 2: mapping.yaml -> mapping.rml.ttl (asterism_step0, deterministic)
# ---------------------------------------------------------------------------


def compile_rml(mapping_yaml_text: str):
    from asterism_step0.mapping_ir import parse_mapping_ir
    from asterism_step0.rml_compile import compile_mapping_ir

    ir = parse_mapping_ir(mapping_yaml_text)
    return compile_mapping_ir(ir)


# ---------------------------------------------------------------------------
# Step 3: mapping.rml.ttl + source/world.csv -> real RDF (Morph-KGC)
# ---------------------------------------------------------------------------


def materialize(rml_ttl: str, csv_dir: Path):
    from asterism.substrate import materialize_to_graph

    return materialize_to_graph(rml_ttl, csv_dir)


# ---------------------------------------------------------------------------
# Step 4: package snapshot.tar (asterism_api.exchange.SNAPSHOT_FORMAT, byte
# for byte — see asterism_api/exchange.py::build_snapshot)
# ---------------------------------------------------------------------------


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    tar.addfile(info, io.BytesIO(payload))


def _add_text(tar: tarfile.TarFile, name: str, text: str) -> None:
    _add_bytes(tar, name, text.encode("utf-8"))


def _registry_meta(*, world_csv_rows: int) -> dict[str, object]:
    """The dataset's ``meta.json`` shape as ``asterism_api.registry.save_dataset``
    would have written it for a hand-authored (non-LLM-proposed) bundle — the
    same convention ``datasets/materials_project`` / ``datasets/papers`` follow
    (``has_proposal: false``: there is no propose/refine Markdown to reopen).
    Only the fields ``asterism_api.exchange._META_KEEP`` actually carries
    through an import matter for round-tripping; the rest are realistic
    filler so the dataset dir looks like any other onboarded one.
    """
    now = datetime.now(UTC).isoformat()
    return {
        "id": DATASET_ID,
        "name": DATASET_NAME,
        "created_at": now,
        "complete": True,
        "warnings": [],
        "exit_code": 0,
        "traps": [],
        "classes": ["Country", "Observation"],
        "class_count": 2,
        "has_mie": True,
        "has_rml": True,
        "has_mapping_ir": True,
        "has_proposal": False,
        "advisories": [],
        "has_source": True,
        "source_files": ["activity.csv", "world.csv"],
        "source_kind": "csv",
        "ingested": False,
    }


def _diagram_md() -> str:
    return (
        "```mermaid\n"
        "classDiagram\n"
        "    class Country { schema:name; world:nameJa; world:region; world:regionJa }\n"
        "    class Observation { world:year; world:population; world:lifeExpectancy; "
        "world:fertility }\n"
        "    Observation --> Country : world:ofCountry\n"
        "```\n"
    )


def build_snapshot_tar(
    *,
    canonical_ttl: bytes,
    triple_count: int,
    world_csv_rows: int,
    mapping_yaml_text: str,
    mapping_rml_text: str,
    model_yaml_text: str,
    mie_yaml_text: str,
    query_tools_yaml_text: str,
    world_csv_text: str,
    activity_csv_text: str,
) -> bytes:
    manifest = {
        "format": SNAPSHOT_FORMAT,
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "dataset_id": DATASET_ID,
        "name": DATASET_NAME,
        "origin_iri_base": ORIGIN_IRI_BASE,
        "exported_at": datetime.now(UTC).isoformat(),
        "live_graph": f"https://kumagallium.github.io/asterism/graph/canonical/{DATASET_ID}/v1",
        "canonical_triples": triple_count,
        "canonical_sha256": hashlib.sha256(canonical_ttl).hexdigest(),
        "ontology_included": False,
        "meta_included": False,
    }
    meta = _registry_meta(world_csv_rows=world_csv_rows)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add_text(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        _add_bytes(tar, "graphs/canonical.ttl", canonical_ttl)
        _add_text(tar, "registry/meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        _add_text(tar, "registry/model.yaml", model_yaml_text)
        _add_text(tar, "registry/mie.yaml", mie_yaml_text)
        _add_text(tar, "registry/mapping.yaml", mapping_yaml_text)
        _add_text(tar, "registry/mapping.rml.ttl", mapping_rml_text)
        _add_text(tar, "registry/metadata.ttl", "")
        _add_text(tar, "registry/diagram.md", _diagram_md())
        _add_text(tar, "registry/proposal.md", "")
        _add_text(tar, "registry/query_tools.yaml", query_tools_yaml_text)
        _add_text(tar, "registry/source/world.csv", world_csv_text)
        _add_text(tar, "registry/source/activity.csv", activity_csv_text)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# --verify: actually call asterism_api.exchange.import_snapshot (contract
# memo §1: "自分で import_snapshot をテストで呼んで確認"). Backed by a real
# rdflib SPARQL 1.1 engine (same pattern as ingest/tests/test_substrate.py's
# _RWClient) rather than a live Oxigraph process — no store/server needed for
# a normal rebuild. Pass --oxigraph-bin to additionally verify against a real
# Oxigraph binary.
# ---------------------------------------------------------------------------


class _RdflibStoreClient:
    """Minimal SupportsSparql + SupportsTurtlePost backed by ``rdflib.Dataset``
    (a real SPARQL 1.1 engine) — enough surface for
    ``asterism_api.exchange.import_snapshot`` /
    ``asterism.substrate.set_staged_graph``."""

    def __init__(self) -> None:
        import rdflib

        self.ds = rdflib.Dataset()

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        import rdflib

        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_context
        g.parse(data=payload, format="turtle")
        return len(payload)

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def graph_triple_count(self, graph_iri: str) -> int:
        data = await self.sparql_select(
            f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}"
        )
        bindings = data.get("results", {}).get("bindings", [])
        return int(bindings[0]["c"]["value"]) if bindings else 0


async def _verify_import(tar_bytes: bytes) -> dict[str, object]:
    from asterism_api import exchange

    with tempfile.TemporaryDirectory(prefix="world-demo-verify-") as tmp:
        cfg = SimpleNamespace(registry_root=Path(tmp), iri_base=ORIGIN_IRI_BASE)
        client = _RdflibStoreClient()
        result = await exchange.import_snapshot(
            cfg, client, tar_bytes, max_extracted_bytes=64 * 1024 * 1024
        )
        # _safe_members is exercised by import_snapshot itself; re-open here
        # too so a future manifest/tar-shape regression is caught even if
        # import_snapshot's own checks ever loosen.
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            names = tar.getnames()
        result["tar_members"] = len(names)
        return result


# ---------------------------------------------------------------------------
# Optional: round-trip through a real, temporary Oxigraph process
# ---------------------------------------------------------------------------


def _verify_with_real_oxigraph(oxigraph_bin: str, canonical_ttl: bytes) -> int:
    """Load ``canonical_ttl`` into a throwaway Oxigraph store and read the
    triple count back via SPARQL — proves a real store round-trips the
    materialized graph unchanged. Not part of the normal build path."""
    import httpx

    with tempfile.TemporaryDirectory(prefix="world-demo-oxigraph-") as tmp:
        port = 17878
        proc = subprocess.Popen(
            [oxigraph_bin, "serve", "--location", tmp, "--bind", f"127.0.0.1:{port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(50):
                try:
                    httpx.get(base + "/", timeout=0.2)
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("oxigraph did not come up in time")
            graph_iri = "https://kumagallium.github.io/asterism/graph/canonical/world/v1"
            r = httpx.post(
                base + "/store",
                params={"graph": graph_iri},
                content=canonical_ttl,
                headers={"Content-Type": "text/turtle; charset=utf-8"},
                timeout=30.0,
            )
            r.raise_for_status()
            q = f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}"
            r = httpx.post(
                base + "/query",
                content=q,
                headers={
                    "Content-Type": "application/sparql-query",
                    "Accept": "application/sparql-results+json",
                },
                timeout=30.0,
            )
            r.raise_for_status()
            bindings = r.json()["results"]["bindings"]
            return int(bindings[0]["c"]["value"]) if bindings else 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the import_snapshot self-check (contract memo §1)",
    )
    parser.add_argument(
        "--oxigraph-bin",
        default=None,
        help="path to a real oxigraph binary; additionally round-trips canonical.ttl "
        "through it (ASTERISM_OXIGRAPH_BIN convention)",
    )
    args = parser.parse_args(argv)

    if not GAPMINDER_JSON.is_file():
        print(
            f"missing {GAPMINDER_JSON} — fetch it first:\n"
            '  curl -fsSL "https://cdn.jsdelivr.net/npm/vega-datasets@2/data/gapminder.json" '
            f"-o {GAPMINDER_JSON}",
            file=sys.stderr,
        )
        return 1

    rows = build_world_csv(GAPMINDER_JSON, WORLD_CSV)
    countries = {r["country"] for r in rows}
    years = {r["year"] for r in rows}
    print(f"wrote {WORLD_CSV} ({len(rows)} rows, {len(countries)} countries, {len(years)} years)")

    build_activity_csv(ACTIVITY_CSV)
    print(f"wrote {ACTIVITY_CSV} (1 row, activity_id={ACTIVITY_ID!r})")

    mapping_yaml_text = MAPPING_YAML.read_text(encoding="utf-8")
    rml_ttl = compile_rml(mapping_yaml_text)
    MAPPING_RML.write_text(rml_ttl, encoding="utf-8")
    print(f"wrote {MAPPING_RML} ({len(rml_ttl)} bytes)")

    import rdflib

    graph = materialize(rml_ttl, SOURCE_DIR)
    world_ns = "https://kumagallium.github.io/asterism/world/ontology#"
    country_type = (None, rdflib.RDF.type, rdflib.URIRef(world_ns + "Country"))
    observation_type = (None, rdflib.RDF.type, rdflib.URIRef(world_ns + "Observation"))
    n_country = sum(1 for _ in graph.triples(country_type))
    n_observation = sum(1 for _ in graph.triples(observation_type))
    print(
        f"materialized {len(graph)} triples via Morph-KGC "
        f"({n_country} world:Country, {n_observation} world:Observation)"
    )
    if n_country != len(countries):
        print(
            f"WARNING: expected {len(countries)} world:Country instances, got {n_country}",
            file=sys.stderr,
        )
    if n_observation != len(rows):
        print(
            f"WARNING: expected {len(rows)} world:Observation instances, got {n_observation}",
            file=sys.stderr,
        )
    prov_ns = "http://www.w3.org/ns/prov#"
    n_activity = sum(
        1 for _ in graph.triples((None, rdflib.RDF.type, rdflib.URIRef(prov_ns + "Activity")))
    )
    n_generated = sum(
        1 for _ in graph.triples((None, rdflib.URIRef(prov_ns + "wasGeneratedBy"), None))
    )
    print(f"provenance: {n_activity} prov:Activity, {n_generated} prov:wasGeneratedBy")
    if n_activity != 1:
        print(f"WARNING: expected exactly 1 prov:Activity, got {n_activity}", file=sys.stderr)
    if n_generated != n_country + n_observation:
        print(
            f"WARNING: expected {n_country + n_observation} prov:wasGeneratedBy, "
            f"got {n_generated}",
            file=sys.stderr,
        )

    canonical_ttl = graph.serialize(format="turtle")
    if isinstance(canonical_ttl, str):
        canonical_ttl = canonical_ttl.encode("utf-8")

    tar_bytes = build_snapshot_tar(
        canonical_ttl=canonical_ttl,
        triple_count=len(graph),
        world_csv_rows=len(rows),
        mapping_yaml_text=mapping_yaml_text,
        mapping_rml_text=rml_ttl,
        model_yaml_text=MODEL_YAML.read_text(encoding="utf-8"),
        mie_yaml_text=MIE_YAML.read_text(encoding="utf-8"),
        query_tools_yaml_text=QUERY_TOOLS_YAML.read_text(encoding="utf-8"),
        world_csv_text=WORLD_CSV.read_text(encoding="utf-8"),
        activity_csv_text=ACTIVITY_CSV.read_text(encoding="utf-8"),
    )
    SNAPSHOT_TAR.write_bytes(tar_bytes)
    print(f"wrote {SNAPSHOT_TAR} ({len(tar_bytes)} bytes, gzip'd tar)")

    if not args.no_verify:
        import asyncio

        result = asyncio.run(_verify_import(tar_bytes))
        print(f"import_snapshot self-check: {json.dumps(result, ensure_ascii=False)}")

    if args.oxigraph_bin:
        n = _verify_with_real_oxigraph(args.oxigraph_bin, canonical_ttl)
        print(f"real oxigraph round-trip: {n} triples read back")
        if n != len(graph):
            print(
                f"WARNING: oxigraph read back {n} triples, materialize produced {len(graph)}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
