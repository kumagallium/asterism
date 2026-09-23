"""``GraphSpec`` -> Mermaid flowchart テキスト（契約メモ §3・ADR object-cards-ui.md
O15/O24）。

``ui/src/cards/mermaidFlow.ts`` の ``toMermaidFlowchart`` の**逐語移植**
（往路のみ — Mermaid をこの言語で解析し直すことはしない。契約メモ §3 が要る
のは ``cards/<card_id>.mmd`` を書き出す片道だけ）。同じ ``GraphSpec`` から
同じ文字列になることは共有フィクスチャ ``ui/src/cards/fixtures/mermaid_cases.json``
（``ingest/tests/test_mermaid_flow.py`` と ui 側テストの両方が読む）で固定する。

**ノード ID の前提**: この関数は ``graph["nodes"][i]["id"]`` を Mermaid の
node id としてそのまま書き出す（ts 版と同じ振る舞い）— Mermaid の id 文法
（英数字・``_``・``-``）を満たす前提で、呼び出し側の責任。IRI のような id
（``asterism.prov_graph`` が返す節の id）を渡す前には、呼び出し側
（``agent_bundle.py``）が先に安全な id へ置き換える（IRI をそのまま書くと
壊れた Mermaid になるため）— この関数自身は ts 版と同じ「そのまま書く」だけ
に留め、フィクスチャとの逐語一致を保つ。

LLM 呼び出し・生成コード実行は無い（§0）。
"""
from __future__ import annotations

from typing import Any

__all__ = ["to_mermaid"]


def to_mermaid(graph: dict[str, Any]) -> str:
    """``GraphSpec`` -> Mermaid flowchart テキスト。``toMermaidFlowchart``
    と同じ書式: ``graph LR``/``id["label"]:::kind``/``A -->|label| B``。
    サブグラフ等の ``props`` は書き出さない（読み手はそれで十分・O24）。"""
    lines: list[str] = [f"graph {graph.get('direction') or 'LR'}"]
    for node in graph.get("nodes") or []:
        label = str(node["label"]).replace('"', "#quot;")
        lines.append(f'  {node["id"]}["{label}"]:::{node["kind"]}')
    for edge in graph.get("edges") or []:
        edge_label = edge.get("label")
        if edge_label:
            label = str(edge_label).replace('"', "#quot;")
            lines.append(f'  {edge["from"]} -->|{label}| {edge["to"]}')
        else:
            lines.append(f'  {edge["from"]} --> {edge["to"]}')
    return "\n".join(lines)
