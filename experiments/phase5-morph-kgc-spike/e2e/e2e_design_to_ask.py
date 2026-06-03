#!/usr/bin/env python3
"""設計 → Ask の連結を end-to-end で実証するスパイク(Phase 5)。

一周を「コード生成ゼロ」で通す:

    設計(宣言的マッピング mappings.rml.ttl + 検証済み関数 udfs.py)
      └→ substrate(Morph-KGC が実行。生成コードなし= RCE 面なし)
          └→ RDF(papers / samples / curves。curve の yMax も検証済み関数で)
              └→ Ask(typed tool property_ranking + demo-agent の回答整形)
                  └→ 根拠(引用 IRI)+ 来歴(curve → sample → paper)付きの回答

ポイント: ここで Morph-KGC が作る RDF は、手続き型 ingester が作るものと
同じ形(同じ IRI・述語)なので、Ask 側は一切変えずにそのまま答えられる。
つまり「ワークベンチで設計したものが Ask で使える」流れが、安全な宣言経路でも成立する。

実行(リポジトリ直下から):
    python scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40
    python experiments/phase5-morph-kgc-spike/e2e/e2e_design_to_ask.py
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

# asterism は 3.11 の datetime.UTC を使う。3.10 環境向けの無害なシム。
if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017  shim は 3.10 用

SELF = Path(__file__).resolve().parent
REPO = SELF.parents[2]  # experiments/phase5-morph-kgc-spike/e2e -> repo root
SEED = REPO / "demo-agent" / "seed" / "csv"

for p in ("ingest/src", "mcp/src", "demo-agent"):
    sys.path.insert(0, str(REPO / p))

import app as demo  # noqa: E402  demo-agent/app.py(_route / _compose_rank)
import morph_kgc  # noqa: E402
import rdflib  # noqa: E402
from asterism_mcp.tools import property_ranking, provenance_of  # noqa: E402


class LocalClient:
    """rdflib グラフに対して SPARQL を実行する最小クライアント(Oxigraph の代用)。"""

    def __init__(self, g: rdflib.Graph) -> None:
        self.g = g

    async def sparql_select(self, query: str) -> dict:
        raw = self.g.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def build_rdf() -> rdflib.Graph:
    """設計(RML + 関数)→ substrate(Morph-KGC)→ RDF。コード生成なし。"""
    if not (SEED / "curves.csv").exists():
        raise SystemExit(
            f"seed CSV が無い: {SEED}\n"
            "先に scripts/make_demo_subset.py を実行してください。"
        )
    for name in ("papers.csv", "samples.csv", "curves.csv"):
        shutil.copyfile(SEED / name, SELF / name)  # rml:source は cwd 相対
    cfg = (
        "[CONFIGURATION]\n"
        f"udfs: {SELF / 'udfs.py'}\n"
        "[DataSource1]\n"
        f"mappings: {SELF / 'mappings.rml.ttl'}\n"
    )
    cwd = Path.cwd()
    try:
        import os

        os.chdir(SELF)
        return morph_kgc.materialize(cfg)
    finally:
        os.chdir(cwd)


async def ask(g: rdflib.Graph, question: str) -> None:
    client = LocalClient(g)
    _kind, arg, max_plausible = demo._route(question)
    rank = await property_ranking(
        client, property_y=arg, top_n=3, max_plausible=max_plausible
    )
    answer = demo._compose_rank(rank)
    print(f"\nQ: {question}")
    print(f"A: {answer['answer']}")
    print("引用:", [(c["kind"], c["iri"].rsplit("/resource/", 1)[-1]) for c in answer["citations"]])
    print("品質: 非現実値として除外した曲線数 =", rank["excluded_implausible"])
    if answer["citations"]:
        prov = await provenance_of(answer["citations"][0]["iri"], client)
        print("来歴:", " -> ".join(s["step"] for s in prov["chain"]))
    assert rank["results"], "substrate 由来データから ZT が引けない(連結が壊れている)"


def main() -> None:
    g = build_rdf()
    print(f"[設計=RML+関数] → [substrate=Morph-KGC] → RDF: {len(g)} triples(生成コード無し)")
    asyncio.run(ask(g, "ZTが最も高い熱電材料は?"))
    print("\nOK: 宣言マッピングだけで作った RDF に、Ask が根拠付き+来歴付きで答えた。")


if __name__ == "__main__":
    main()
