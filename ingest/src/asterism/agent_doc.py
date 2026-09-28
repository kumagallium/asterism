"""``AGENT.md`` の決定論テンプレート（契約メモ §3・ADR object-cards-ui.md O12/O23）。

**エージェントは答えを作らず、ツールを選ぶだけ** — その規律をここに書く。LLM は
一切呼ばない（文面は全部この関数の中の固定テンプレート）。日本語 (``ja``) と
英語 (``en``) の 2 言語、それぞれ契約メモ §3 の 7 節を持つ:

1. 見出し
2. これは何か（1 件／絞り込み・種類・出どころ・生成日時・Asterism の版）
3. 答えられること（カードの一覧）
4. 答えられないこと
5. 出典の出しかた
6. 規律（答えを作らず、ツールを選ぶだけ）
7. 起動

分野固有の名詞は書かない（§0）— カードのタイトルや種類名は呼び出し側が渡す
値をそのまま埋め込むだけで、ここでは何も判断しない。
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["AgentDocCard", "AgentDocSubject", "render_agent_doc"]

#: output_kind -> 平易な日本語/英語名（契約メモ §3「答えられること」の
#: "output_kind の平易名"）。分野語ではなく契約層の語彙そのもの（O17）。
_OUTPUT_KIND_LABEL_JA: dict[str, str] = {
    "quantity": "1つの数値",
    "series": "推移(折れ線)",
    "pairs": "2つの量の関係",
    "ranked": "順位表",
    "breakdown": "内訳",
    "facts": "事実の一覧",
    "flow": "つながり(出どころの流れ)",
}
_OUTPUT_KIND_LABEL_EN: dict[str, str] = {
    "quantity": "a single number",
    "series": "a trend (line)",
    "pairs": "a relationship between two quantities",
    "ranked": "a ranked list",
    "breakdown": "a breakdown",
    "facts": "a list of facts",
    "flow": "provenance (where it came from)",
}


@dataclass(frozen=True)
class AgentDocCard:
    """§3「答えられること」1 行分。``title``/``tool`` は呼び出し側がすでに
    解決済みの表示名・ツール名（このモジュールは辞書もラベル解決もしない）。"""

    title: str
    output_kind: str
    tool: str


@dataclass(frozen=True)
class AgentDocSubject:
    """§3-2「これは何か」を埋める入力。1 件なら ``is_set=False``、絞り込み
    なら ``True``。``dataset_names`` は ``"<name> (<snapshot>)"`` のような
    表示済み文字列の一覧（順序はそのまま出す）。"""

    label: str
    is_set: bool
    class_label: str | None
    dataset_names: list[str] = field(default_factory=list)


def _kind_label(output_kind: str, lang: str) -> str:
    table = _OUTPUT_KIND_LABEL_JA if lang == "ja" else _OUTPUT_KIND_LABEL_EN
    return table.get(output_kind, output_kind)


def _render_ja(
    subject: AgentDocSubject,
    cards: list[AgentDocCard],
    *,
    generated_at: str,
    asterism_version: str,
    slug: str,
) -> str:
    kind_line = "絞り込み" if subject.is_set else "1 件/絞り込み"
    lines: list[str] = []
    lines.append(f"# {subject.label} のエージェント")
    lines.append("")
    lines.append("## これは何か")
    lines.append("")
    lines.append(f"- 種類: {kind_line}")
    if subject.class_label:
        lines.append(f"- 種類(スキーマ): {subject.class_label}")
    if subject.dataset_names:
        lines.append(f"- 出どころ: {', '.join(subject.dataset_names)}")
    lines.append(f"- 生成日時: {generated_at}")
    lines.append(f"- Asterism の版: {asterism_version}")
    lines.append("")
    lines.append("## 答えられること")
    lines.append("")
    if cards:
        for card in cards:
            kind_label = _kind_label(card.output_kind, "ja")
            lines.append(f"- {card.title}({kind_label}・ツール `{card.tool}`)")
    else:
        lines.append("- (このエージェントにはカードがありません)")
    lines.append("")
    lines.append("## 答えられないこと")
    lines.append("")
    lines.append("- この束に無い事実")
    lines.append("- 推測・機械の意見")
    lines.append("- 全データベースの検索(この束は 1 件/絞り込みの材料だけを持つ)")
    lines.append("- 最新の値(版(snapshot)が固定されているため、束の生成時点の値)")
    lines.append("")
    lines.append("## 出典の出しかた")
    lines.append("")
    lines.append("- 数値と事実には必ず IRI と版(snapshot)を添えること。")
    lines.append(
        "- IRI は Asterism が動いていれば `/describe?iri=<IRI>` に貼ると人が読める形で"
        "見られる。"
    )
    lines.append("")
    lines.append("## 規律")
    lines.append("")
    lines.append(
        "- **答えを作らず、ツールを選ぶだけ。** 上の「答えられること」にあるツール以外は"
        "呼ばない。"
    )
    lines.append("- ツールが返さないことは「分かりません」と言う。無いデータを埋めない。")
    lines.append("")
    lines.append("## 起動")
    lines.append("")
    lines.append("```")
    lines.append(f"asterism-agent serve /path/to/{slug}")
    lines.append("```")
    lines.append("")
    lines.append(
        "`mcp.json` をローカル LLM や Claude Desktop / Claude Code に貼る方法は "
        "`README.md` を見ること。"
    )
    lines.append("")
    return "\n".join(lines)


def _render_en(
    subject: AgentDocSubject,
    cards: list[AgentDocCard],
    *,
    generated_at: str,
    asterism_version: str,
    slug: str,
) -> str:
    kind_line = "a filtered set" if subject.is_set else "one thing / a filtered set"
    lines: list[str] = []
    lines.append(f"# {subject.label} Agent")
    lines.append("")
    lines.append("## What this is")
    lines.append("")
    lines.append(f"- Kind: {kind_line}")
    if subject.class_label:
        lines.append(f"- Kind (schema): {subject.class_label}")
    if subject.dataset_names:
        lines.append(f"- Source: {', '.join(subject.dataset_names)}")
    lines.append(f"- Generated at: {generated_at}")
    lines.append(f"- Asterism version: {asterism_version}")
    lines.append("")
    lines.append("## What it can answer")
    lines.append("")
    if cards:
        for card in cards:
            kind_label = _kind_label(card.output_kind, "en")
            lines.append(f"- {card.title} ({kind_label}, tool `{card.tool}`)")
    else:
        lines.append("- (this agent has no cards)")
    lines.append("")
    lines.append("## What it cannot answer")
    lines.append("")
    lines.append("- Facts not included in this bundle")
    lines.append("- Speculation or the model's own opinion")
    lines.append(
        "- A search across the whole database (this bundle only carries this page's "
        "material)"
    )
    lines.append(
        "- The latest value (the snapshot is fixed — you get the value as of when "
        "this bundle was made)"
    )
    lines.append("")
    lines.append("## How to cite")
    lines.append("")
    lines.append("- Always attach the IRI and the snapshot (version) to any number or fact.")
    lines.append(
        "- If Asterism is running, pasting the IRI into `/describe?iri=<IRI>` shows it "
        "in human-readable form."
    )
    lines.append("")
    lines.append("## Discipline")
    lines.append("")
    lines.append(
        "- **Do not make up answers — only pick a tool.** Never call a tool that is "
        'not listed under "What it can answer".'
    )
    lines.append(
        "- If a tool does not return something, say \"I don't know\" instead of "
        "filling in a guess."
    )
    lines.append("")
    lines.append("## How to start")
    lines.append("")
    lines.append("```")
    lines.append(f"asterism-agent serve /path/to/{slug}")
    lines.append("```")
    lines.append("")
    lines.append(
        "See `README.md` for how to paste `mcp.json` into a local LLM, Claude Desktop, "
        "or Claude Code."
    )
    lines.append("")
    return "\n".join(lines)


def render_agent_doc(
    subject: AgentDocSubject,
    cards: list[AgentDocCard],
    *,
    lang: str,
    generated_at: str,
    asterism_version: str,
    slug: str,
) -> str:
    """``AGENT.md`` の本文（決定論・LLM ゼロ）。``lang`` は ``"ja"``/``"en"``。"""
    if lang == "en":
        return _render_en(
            subject, cards, generated_at=generated_at, asterism_version=asterism_version, slug=slug
        )
    return _render_ja(
        subject, cards, generated_at=generated_at, asterism_version=asterism_version, slug=slug
    )
