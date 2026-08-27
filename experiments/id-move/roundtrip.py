"""ID の引っ越しの実機一周検証（ADR id-move-after-publish.md）。

シナリオは実際に起きる数えかたの誤り:
  ① 論文単位でしか数えていない設計で公開し、その ID を「引用として配る」
  ② 1 行 = 1 試料だったと気づき、データの数えかたをやり直して再公開
  ③ 配ってしまった ID を開くと、どうなるか
"""
from __future__ import annotations

import json
import sys
import time

import httpx

API = "http://127.0.0.1:8137"
CSV = (
    "paper_id,sample_id,composition,seebeck\n"
    "P1,S1,Bi2Te3,210\n"
    "P1,S2,Bi2Te2.7Se0.3,185\n"
    "P2,S1,PbTe,240\n"
)
NS = "https://lab.example.jp/datasets/thermo/"
OLD_IRI = f"{NS}resource/sample/P1"  # ← 配ってしまう引用（誤った数えかた）
NEW_IRI = f"{NS}resource/sample/P1-S1"


def spec(subject_template: str) -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        f'  th: "{NS}ontology#"\n'
        f'  thr: "{NS}resource/"\n'
        "maps:\n"
        "  - name: sample\n"
        "    source: thermo.csv\n"
        "    subject:\n"
        f'      template: "{subject_template}"\n'
        "      classes: [th:Sample]\n"
        "    properties:\n"
        "      - predicate: th:composition\n"
        "        column: composition\n"
        "      - predicate: th:seebeck\n"
        "        column: seebeck\n"
        "        datatype: xsd:double\n"
        "```\n"
    )


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def wait_ingested(c: httpx.Client, dsid: str, *, want: bool = True) -> dict:
    for _ in range(120):
        meta = c.get(f"{API}/api/datasets/{dsid}").json()["meta"]
        if bool(meta.get("ingested")) is want:
            return meta
        time.sleep(0.5)
    raise SystemExit(f"ingest did not settle for {dsid}")


def main() -> int:
    c = httpx.Client(timeout=120.0, headers={"X-Asterism-Token": "verify-token"})

    step("① 論文単位で数える設計で公開する（これが後で誤りと分かる）")
    r = c.post(
        f"{API}/api/materialize",
        json={"proposal_md": spec("thr:sample/{paper_id}"), "dataset_name": "熱電測定"},
    )
    r.raise_for_status()
    dsid = r.json()["dataset"]["id"]
    print("dataset:", dsid)

    r = c.post(
        f"{API}/api/datasets/{dsid}/ingest",
        files={"files": ("thermo.csv", CSV.encode(), "text/csv")},
    )
    print("ingest:", r.status_code, r.text[:200])
    r.raise_for_status()
    wait_ingested(c, dsid)
    r = c.post(f"{API}/api/datasets/{dsid}/promote")
    r.raise_for_status()
    print("promote:", r.json()["triples_promoted"], "triples")
    subs = c.get(f"{API}/api/datasets/{dsid}").json()["meta"].get("published_subjects")
    print("公開時に記録した ID の作り方:", json.dumps(subs, ensure_ascii=False))

    step("② 配った引用が開けることを確かめる")
    r = c.get(f"{API}/describe", params={"iri": OLD_IRI})
    print(f"GET /describe?iri={OLD_IRI} → {r.status_code}")
    assert r.status_code == 200, "公開直後の ID が開けない"
    assert "Bi2Te3" in r.text, "中身が出ていない"
    print("  中身が見える（Bi2Te3）✓")

    step("③ 数えかたをやり直す（1 行 = 1 試料）— 再設計 → 再取り込み → 再公開")
    r = c.post(
        f"{API}/api/materialize",
        json={
            "proposal_md": spec("thr:sample/{paper_id}-{sample_id}"),
            "dataset_name": "熱電測定",
            "dataset_id": dsid,
        },
    )
    r.raise_for_status()
    r = c.post(f"{API}/api/datasets/{dsid}/ingest")
    r.raise_for_status()
    wait_ingested(c, dsid)
    move = c.get(f"{API}/api/datasets/{dsid}/id-move").json()
    print("公開の直前に人へ見せる材料:", json.dumps(move, ensure_ascii=False, indent=2)[:600])
    r = c.post(f"{API}/api/datasets/{dsid}/promote")
    r.raise_for_status()
    print("再公開:", r.json()["triples_promoted"], "triples")

    step("④ 配ってしまった古い ID を、いま開くとどうなるか")
    r = c.get(f"{API}/describe", params={"iri": OLD_IRI})
    print(f"GET /describe?iri={OLD_IRI} → {r.status_code}")
    ok_html = r.status_code == 200 and "引っ越し" in r.text
    print("  引っ越しの表示:", "あり ✓" if ok_html else "なし ✗")
    for probe in ("分かれました", OLD_IRI, NEW_IRI):
        print(f"  「{probe[:40]}」を含む:", probe in r.text)

    r = c.get(f"{API}/describe", params={"iri": OLD_IRI}, headers={"Accept": "text/turtle"})
    print(f"\n機械向け (Accept: text/turtle) → {r.status_code}")
    print(r.text.strip() or "(空)")

    step("⑤ 新しい ID もそのまま開けることを確かめる")
    r = c.get(f"{API}/describe", params={"iri": NEW_IRI})
    print(f"GET /describe?iri={NEW_IRI} → {r.status_code}, Bi2Te3:", "Bi2Te3" in r.text)

    step("⑥ 知らない ID は、これまで通り 404 のまま")
    r = c.get(f"{API}/describe", params={"iri": f"{NS}resource/sample/NOPE"})
    print("→", r.status_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
