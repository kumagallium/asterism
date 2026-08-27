"""引っ越しの残り 2 ケースを実機で確かめる。

  (A) チェーン: 数えかたを 2 回やり直しても、最初に配った ID が辿れるか
      （途中の版の ID は、もうどのグラフにも存在しない）
  (B) 引っ越せない: 旧 ID を綴る列がいまのファイルに無いとき、
      黙って公開されず、公開の直前に理由が出るか
"""
from __future__ import annotations

import json
import sys
import time

import httpx

API = "http://127.0.0.1:8137"
NS = "https://lab.example.jp/datasets/thermo/"
CSV = (
    "paper_id,sample_id,composition,seebeck\n"
    "P1,S1,Bi2Te3,210\n"
    "P1,S2,Bi2Te2.7Se0.3,185\n"
    "P2,S1,PbTe,240\n"
)
# 列名が変わったファイル（装置の設定変更・出力形式の変更で実際に起きる）
CSV_RENAMED = (
    "doc,specimen,composition,seebeck\n"
    "P1,S1,Bi2Te3,210\n"
    "P1,S2,Bi2Te2.7Se0.3,185\n"
)


def spec(template: str, source: str = "thermo.csv", cols: tuple[str, str] = ("composition", "seebeck")) -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        f'  th: "{NS}ontology#"\n'
        f'  thr: "{NS}resource/"\n'
        "maps:\n"
        "  - name: sample\n"
        f"    source: {source}\n"
        "    subject:\n"
        f'      template: "{template}"\n'
        "      classes: [th:Sample]\n"
        "    properties:\n"
        "      - predicate: th:composition\n"
        f"        column: {cols[0]}\n"
        "      - predicate: th:seebeck\n"
        f"        column: {cols[1]}\n"
        "```\n"
    )


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def wait_ingested(c: httpx.Client, dsid: str) -> None:
    for _ in range(120):
        if c.get(f"{API}/api/datasets/{dsid}").json()["meta"].get("ingested"):
            return
        time.sleep(0.5)
    raise SystemExit(f"ingest did not settle for {dsid}")


def main() -> int:
    c = httpx.Client(timeout=120.0, headers={"X-Asterism-Token": "verify-token"})
    dsid = sys.argv[1]

    step("(A) もう一度 数えかたをやり直す（v2 → v3）")
    c.post(
        f"{API}/api/materialize",
        json={
            "proposal_md": spec("thr:sample/{paper_id}-{sample_id}-{composition}"),
            "dataset_name": "熱電測定",
            "dataset_id": dsid,
        },
    ).raise_for_status()
    c.post(f"{API}/api/datasets/{dsid}/ingest").raise_for_status()
    wait_ingested(c, dsid)
    move = c.get(f"{API}/api/datasets/{dsid}/id-move").json()
    print("v2→v3 の引っ越し:", move["changes_ids"], "/ forwarded:", move.get("forwarded"))
    c.post(f"{API}/api/datasets/{dsid}/promote").raise_for_status()

    first = f"{NS}resource/sample/P1"  # v1 で配った ID
    mid = f"{NS}resource/sample/P1-S1"  # v2 の ID（もうデータは無い）
    last = f"{NS}resource/sample/P1-S1-Bi2Te3"
    for label, iri in (("v1 で配った ID", first), ("v2 の ID", mid), ("v3 の ID", last)):
        r = c.get(f"{API}/describe", params={"iri": iri}, headers={"Accept": "text/turtle"})
        body = r.text.strip().replace(NS + "resource/", "…/")
        print(f"\n{label} ({iri.replace(NS + 'resource/', '…/')}) → {r.status_code}")
        print("  " + (body.replace("\n", "\n  ") if body else "(空)"))

    step("(B) 前の ID を綴る列が、いまのファイルに無い場合")
    r = c.post(
        f"{API}/api/materialize",
        json={"proposal_md": spec("thr:s2/{paper_id}"), "dataset_name": "列が変わる例"},
    )
    r.raise_for_status()
    ds2 = r.json()["dataset"]["id"]
    c.post(
        f"{API}/api/datasets/{ds2}/ingest",
        files={"files": ("thermo.csv", CSV.encode(), "text/csv")},
    ).raise_for_status()
    wait_ingested(c, ds2)
    c.post(f"{API}/api/datasets/{ds2}/promote").raise_for_status()
    print("公開:", ds2)

    # 元ファイルを列名の違うものに差し替え、その列で設計し直す
    c.post(
        f"{API}/api/materialize",
        json={
            "proposal_md": spec("thr:s2/{doc}-{specimen}", cols=("composition", "seebeck")),
            "dataset_name": "列が変わる例",
            "dataset_id": ds2,
        },
    ).raise_for_status()
    c.post(
        f"{API}/api/datasets/{ds2}/ingest",
        files={"files": ("thermo.csv", CSV_RENAMED.encode(), "text/csv")},
    ).raise_for_status()
    wait_ingested(c, ds2)
    move2 = c.get(f"{API}/api/datasets/{ds2}/id-move").json()
    print("公開の直前に人へ見せる材料:")
    print(json.dumps(move2, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
