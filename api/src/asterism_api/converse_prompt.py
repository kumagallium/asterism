"""ページの会話（契約メモ contract_pr_f12.md §1-2/§1-3、契約メモ
contract_pr_f13.md §1-2/§1-6）の純関数群 — 系統プロンプトの組み立て・
``<proposal>`` タグの取り出し・提案の検証。

store にもレジストリにも触れない（I/O なし）。``asterism.measure_spec`` の
``validate_measure``/``output_kind_for``/``title_parts`` に検証を委ねる
（O44: この module は自分では新しい妥当性表を持たない）。同様に
``kind: "view"`` の提案（AI が Vega-Lite/表仕様/Mermaid を書く方の経路、契約
F13 §1-1）は ``asterism.view_spec_check`` の許可リストに検証を委ねる——この
module はどちらの検証器にも独自ルールを足さない、単なる呼び出しと
「どちらの kind か」の振り分け役。

題名テンプレート（``_TITLE_TEMPLATES``/``_AGG_LABELS``）は
``ui/src/i18n/locales/{ja,en}/cards.json`` の ``newcard.title_*``/
``newcard.agg_*`` と同じ文言を Python 側に複製したもの（この api モジュール
は ui の i18n ファイルを読めない・触れない — §2 分担で唯一の担当は
ui-page）。ここで返す ``title`` はドロワーのプレビュー用の文字列で、実際に
「足す」を押した後の正本は ui-drawer が ``measureCardFields.titleFor`` で
改めて計算する（契約 §1-2「F4 の toCardSpec と同じ CardSpec・同じ題名規則」）
ので、2 箇所の文言がドリフトしても実害は無い（プレビューの言い回しが一瞬
ずれるだけ）——完全一致を保証する仕組みは持たない。
"""

# このモジュールは日本語の系統プロンプト/文言を組み立てる（全角の括弧・句読点
# は正しい表記であって ASCII の見た目似ではない — ``describe.py`` と同じ理由）。
# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
from typing import Any

from asterism import view_spec_check
from asterism.measure_spec import (
    AGGS,
    SHAPES,
    MeasureSpecError,
    output_kind_for,
    title_parts,
    validate_measure,
)

__all__ = [
    "build_system_prompt",
    "extract_proposal",
    "render_retry_message",
    "render_user_prompt",
    "top_linking_kind_classes",
    "validate_proposal",
    "with_cannot_build_note",
]

_PROPOSAL_TAG_RE = re.compile(r"<proposal>(.*?)</proposal>", re.DOTALL)

#: プレビューの見せ方の切替（presentation.mark）が受け付ける値。この worktree
#: の時点では F3（切替コンポーネント）がまだ無いので、既存の
#: `ui/src/cards/defaultView.ts` が実際に使っている Vega-Lite の mark 語彙
#: （line/bar/point）をそのまま閉じた集合として使う — deviations 参照。
_VALID_MARKS: tuple[str, ...] = ("line", "bar", "point")

#: 「書く」（契約 F13 §1-1）で AI が選べる言語。``ui/src/cards/viewSpec.ts``
#: の ``ViewLang`` とは語彙が違う点に注意（``mermaid`` は AI がテキストとして
#: 書く入力形式であって、UI が保存する ``ViewSpec.lang`` は解析後の
#: ``"graph"`` — その変換は ui-drawer 側、契約 F13 §2 の担当外）。
_VIEW_LANGS: tuple[str, ...] = ("vega-lite", "table", "mermaid")

_TITLE_TEMPLATES: dict[str, dict[str, str]] = {
    "ja": {
        "series": "{y}の推移",
        "ranked": "{item}が多い順",
        "breakdown": "{category}ごとの件数",
        "pairs": "{x}と{y}",
        "quantity": "{item}の{agg}",
        "facts": "{items}",
    },
    "en": {
        "series": "{y} over time",
        "ranked": "{item}, highest first",
        "breakdown": "Count by {category}",
        "pairs": "{x} vs {y}",
        "quantity": "{agg} of {item}",
        "facts": "{items}",
    },
}

_AGG_LABELS: dict[str, dict[str, str]] = {
    "ja": {"avg": "平均", "max": "最大", "min": "最小", "sum": "合計", "count": "件数"},
    "en": {"avg": "Average", "max": "Maximum", "min": "Minimum", "sum": "Sum", "count": "Count"},
}

_ITEMS_JOIN = {"ja": "・", "en": ", "}

#: 候補の種類の「道の説明」テンプレート（契約 F14 §1.3・ui-form の
#: ``ui/src/i18n/locales/{ja,en}/cards.json`` の ``newcard.path_*`` と同じ文の
#: サーバ側複製 — この api モジュールは ui の i18n ファイルを読めない・触れない
#: ので、系統プロンプトに載せる分だけここに持つ。``{anchor}``/``{via}`` は
#: ``linking_kinds`` の行の ``anchor_label``（無ければ ``anchor_class_label``）／
#: ``via.class_label`` を差し込む。``path_kind`` が無い行（ingest 側の新
#: フィールドがまだ無い移行期）は ``"direct"`` として扱う。
_PATH_DESC: dict[str, dict[str, str]] = {
    "ja": {
        "direct": "この 1 件を指す記録",
        "child_child": "この 1 件を指す{via}を、さらに指す記録",
        "sibling": "同じ「{anchor}」を持つ記録",
        "sibling_child": "同じ「{anchor}」を持つ{via}を指す記録",
    },
    "en": {
        "direct": "records that point at this one",
        "child_child": "records that point at a {via} that points at this one",
        "sibling": "records that share the same {anchor}",
        "sibling_child": "records that point at a {via} that shares the same {anchor}",
    },
}

#: 観点を作れる候補として ``build_system_prompt`` に渡す ``schema_properties``
#: に採る ``linking_kinds`` の種類の上限（契約 F14 §1.4「hops 昇順で先頭 8
#: 種類（同じ class は 1 回）」）。
_MAX_LINKING_CANDIDATE_CLASSES = 8

#: 契約メモ contract_pr_f16.md §1.4: ``via_member`` を持つ行（ハブのページの
#: 候補＝メンバー起点）の道の説明を、従来の道の説明の外側から包むテンプレート。
#: ``{dataset}``/``{member}``/``{path}`` を差し込む。
_VIA_MEMBER_DESC: dict[str, str] = {
    "ja": "{dataset}の「{member}」から: {path}",
    "en": "from {member} in {dataset}: {path}",
}


def _path_description(kind: dict[str, Any], lang: str) -> str:
    lk = lang if lang in _PATH_DESC else "ja"
    path_kind = kind.get("path_kind")
    templates = _PATH_DESC[lk]
    template = (
        templates.get(path_kind, templates["direct"])
        if isinstance(path_kind, str)
        else (templates["direct"])
    )
    anchor = kind.get("anchor_label") or kind.get("anchor_class_label") or ""
    via = kind.get("via")
    via_label = via.get("class_label", "") if isinstance(via, dict) else ""
    base = template.format(anchor=anchor, via=via_label)
    via_member = kind.get("via_member")
    if isinstance(via_member, dict):
        member_label = via_member.get("label") or ""
        dataset_label = via_member.get("dataset_label") or ""
        wrapper = _VIA_MEMBER_DESC.get(lk, _VIA_MEMBER_DESC["ja"])
        return wrapper.format(dataset=dataset_label, member=member_label, path=base)
    return base


def top_linking_kind_classes(
    linking_kinds: list[dict[str, Any]], *, limit: int = _MAX_LINKING_CANDIDATE_CLASSES
) -> list[str]:
    """``linking_kinds`` を ``hops`` 昇順に並べ替え、重複を除いた先頭 ``limit``
    件の ``class_iri`` を返す（契約 F14 §1.4）。``hops`` を持たない行（移行期の
    行）は ``1`` として扱う。並べ替えは安定ソート——同じ ``hops`` の中では
    ``linking_kinds`` が既に決定論の順（ingest 側の並び）で来ている前提を保つ。
    """

    def hops_of(k: dict[str, Any]) -> int:
        hops = k.get("hops")
        return hops if isinstance(hops, int) else 1

    ordered = sorted(
        (k for k in linking_kinds if isinstance(k, dict)),
        key=hops_of,
    )
    out: list[str] = []
    for k in ordered:
        cls = k.get("class_iri")
        if isinstance(cls, str) and cls not in out:
            out.append(cls)
        if len(out) >= limit:
            break
    return out


#: 検証を 2 回とも通らなかったときに ``reply`` へ添える定型文（K4: 生の
#: MeasureSpecError の文面には property IRI が入るので、人向けの reply には
#: 絶対に出さない — AI への言い直し指示（``render_retry_message``）にだけ
#: 生の理由を渡す）。
_CANNOT_BUILD_NOTE: dict[str, str] = {
    "ja": "うまく観点を作れませんでした。もう少し具体的に教えてください。",
    "en": "I couldn't put together a working view from that — could you be a bit more specific?",
}


def _lang_key(lang: str | None) -> str:
    return lang if lang in _TITLE_TEMPLATES else "ja"


# ----------------------------------------------------------------------------
# 系統プロンプト
# ----------------------------------------------------------------------------


def _shape_fields_line(lang: str) -> list[str]:
    """SHAPES/AGGS（``measure_spec.py``）そのものから妥当性表の要約を組む —
    契約メモ §1-3 の「SHAPES/AGGS と妥当性表の要約」。この module は独自の
    表を持たず、``asterism.measure_spec`` の定数をそのまま読むだけ。"""
    fields_of = {
        "series": "x, y",
        "ranked": "item",
        "breakdown": "category",
        "pairs": "x, y",
        "quantity": "item, agg",
        "facts": "items",
    }
    if lang == "ja":
        lines = ["見せ方（shape）ごとに必要な項目:"]
        lines += [f"- {shape}: {fields_of[shape]}" for shape in SHAPES]
        lines.append(f"agg（数字 1 つの集計）は次のどれか: {', '.join(AGGS)}")
        return lines
    lines = ["Required fields per shape:"]
    lines += [f"- {shape}: {fields_of[shape]}" for shape in SHAPES]
    lines.append(f"agg (for shape 'quantity') must be one of: {', '.join(AGGS)}")
    return lines


def _property_line(prop: dict[str, Any]) -> str:
    iri = prop.get("iri")
    label = prop.get("label") or iri
    kind = prop.get("kind")
    unit = prop.get("unit")
    unit_part = f", unit={unit}" if unit else ""
    return f'  - "{iri}": label="{label}", kind={kind}{unit_part}'


def build_system_prompt(
    lang: str,
    schema_properties: dict[str, list[dict[str, Any]]],
    linking_kinds: list[dict[str, Any]],
    existing_titles: list[str],
    draft: dict[str, Any] | None,
) -> str:
    """このページで観点を作る/直す/聞くための系統プロンプト（契約メモ §1-2/
    §1-3）。

    ``schema_properties`` はこの会話で観点を作れる「候補の種類」ごとの
    class_schema の properties（``{class_iri: [property, ...]}``）——
    個体のページでは linking_kinds の候補ぶん複数になり得る（1 件のページで
    候補が複数あるとき、AI にはどちらの種類で作るか選ばせる）。空 dict なら
    「このページでは観点を新しく作れない」と明示し、聞くことだけを促す。
    """
    lk = _lang_key(lang)
    lines: list[str]
    if lk == "ja":
        lines = [
            "あなたはデータの可視化づくりを手伝う相談役です。",
            "このページに表示されている値や、並んでいるグラフ（カード）の結果をもとに、"
            "ユーザーの質問に答えたり、新しい観点（グラフ）を提案したりします。",
            "答えの最後には必ず根拠を書いてください"
            "（例:「『国の人口の推移』の2005年の値」のように、どのカードのどの値を見て"
            "答えたかを示す）。根拠にはカードの題名を書き、id（[id: …] の中身）は"
            "人には見せないので書かないでください。",
            "文章は Markdown を使わず平文で書いてください（** や # で飾らない）。",
            "プログラムのコードや SPARQL クエリは書かないでください。",
            "",
            "質問の答えが、このページの値にも並んでいるカードの結果にも無く、下に挙げる"
            "候補の種類の項目で答えられそうなときは、推測で答えず、その観点"
            "（例: 強度の高い順）を提案してください。文章には「足すと答えられます」の"
            'ように書き、<proposal> の JSON に "answers": true を付け加えてください。'
            "答えが本当にどこにも無いときだけ、「このデータには無い」と伝えてください。",
            "",
            "観点（グラフ）を提案したいときだけ、文章の後に <proposal> タグで囲んだ JSON を"
            "1 つだけ書いてください（他の場所に JSON を書かない）。",
            "JSON の形: "
            '{"params": {"class": "...", "shape": "...", "x"?, "y"?, "category"?, '
            '"items"?, "agg"?}, "presentation": {"mark": "line"|"bar"|"point"} または '
            'null, "title": "..."}',
            "params.where は書かなくてよい（どの記録を対象にするかはサーバー側が自動的に補う）。",
            "",
            "まず、この観点の指定（params）だけで表せないか考えてください。表せるなら"
            "それを使ってください。",
            "指定では表せない特殊な見せ方（例: 複数系列を重ねて描く・注釈を添える・軸を"
            "作り込む）のときだけ、代わりに次の形の JSON を書いてください:",
            '{"kind": "view", "view": {"lang": "vega-lite"|"table"|"mermaid", '
            '"spec": {...}（vega-lite/table のとき）または "text": "..."（mermaidのとき）, '
            '"source_card_id": "..."}}',
            "view.spec/view.text の中に、データそのもの（Vega-Lite の data など）・URL・"
            "コードとして評価される式は絶対に書かないでください"
            "（データはページに並んでいるカードの結果から画面側が差し込みます）。",
            "source_card_id には、[並んでいるカード] の各行の先頭にある [id: …] の id を"
            "そのまま写してください（id が分からなければ、そのカードの題名を"
            "そのまま書いてもかまいません）。",
            "例1（指定で表せる — view は書かない）:",
            '<proposal>{"params": {"class": "' + "<class IRI>" + '", "shape": "series", '
            '"x": "<property IRI>", "y": "<property IRI>"}, "presentation": null, '
            '"title": "推移"}</proposal>',
            "例2（指定では表せない — 2 つの量を 1 つの図に重ねて描きたいので view を書く）:",
            '<proposal>{"kind": "view", "view": {"lang": "vega-lite", "spec": '
            '{"layer": ['
            '{"mark": "line", "encoding": {'
            '"x": {"field": "x", "type": "quantitative"}, '
            '"y": {"field": "y1", "type": "quantitative"}}}, '
            '{"mark": "line", "encoding": {'
            '"x": {"field": "x", "type": "quantitative"}, '
            '"y": {"field": "y2", "type": "quantitative"}}}'
            ']}, "source_card_id": "<既存のカードの id>"}}</proposal>',
        ]
        lines += _shape_fields_line(lk)
        if schema_properties:
            lines.append('観点を作れる種類と、それぞれの項目（"property IRI": 情報）:')
            for class_iri, props in schema_properties.items():
                lines.append(f'- class "{class_iri}":')
                lines.extend(_property_line(p) for p in props)
        else:
            lines.append(
                "このページでは、まだ新しい観点を作れる種類が見つかっていません。"
                "質問に答えるだけにしてください（<proposal> は書かない）。"
            )
        if linking_kinds:
            lines.append("この 1 件から届く候補の種類（参考情報・道の説明つき）:")
            for k in linking_kinds:
                lines.append(f"- {k.get('class_label')}（{_path_description(k, lk)}）")
        if existing_titles:
            lines.append("すでにこのページにある観点: " + "、".join(existing_titles))
        if draft:
            lines.append(
                "いまの下書き（「直す」で更新してほしい前回の提案）: "
                + json.dumps(draft, ensure_ascii=False)
            )
    else:
        lines = [
            "You are a chat assistant that helps build data visualizations.",
            "Answer the user's questions using the values shown on this page and the results "
            "of the cards already on it, and — only when asked — propose a new view (chart).",
            'Always end your answer with your evidence (e.g. "the 2005 value from '
            "'Population over time'\"), naming which card and which value you read it from. "
            "Name cards by their title; never write a card id ([id: ...]) — ids are not "
            "shown to people.",
            "Write plain text without Markdown (no ** or # decoration).",
            "Never write program code or a SPARQL query.",
            "",
            "If the answer isn't in this page's values or in the cards' results, but it could "
            "be answered from one of the candidate kinds listed below, don't guess — propose "
            "that view instead (e.g. highest intensity first). Say in your text that adding it "
            'will answer the question, and add "answers": true to the <proposal> JSON. Only '
            "say the data doesn't have the answer when it truly cannot be found anywhere.",
            "",
            "Only when you want to propose a view, write exactly one JSON object wrapped in a "
            "<proposal> tag after your text (never place JSON anywhere else).",
            "JSON shape: "
            '{"params": {"class": "...", "shape": "...", "x"?, "y"?, "category"?, '
            '"items"?, "agg"?}, "presentation": {"mark": "line"|"bar"|"point"} or null, '
            '"title": "..."}',
            "You do not need to set params.where — the server fills in which records to use "
            "automatically.",
            "",
            "First consider whether this view spec (params) alone can express what is "
            "wanted, and use it if it can.",
            "Only when it cannot (e.g. overlaying two series in one chart, an annotation, a "
            "custom axis), write this JSON shape instead:",
            '{"kind": "view", "view": {"lang": "vega-lite"|"table"|"mermaid", '
            '"spec": {...} (for vega-lite/table) or "text": "..." (for mermaid), '
            '"source_card_id": "..."}}',
            "Never put the data itself (e.g. Vega-Lite's data), a URL, or code that gets "
            "evaluated as an expression inside view.spec/view.text — the data is spliced in "
            "by the screen from the results of a card already on this page.",
            "For source_card_id, copy the id shown as [id: …] at the start of a card line "
            "in the page summary (if unsure, the card's exact title is also accepted).",
            "Example 1 (the spec suffices — no view):",
            '<proposal>{"params": {"class": "<class IRI>", "shape": "series", '
            '"x": "<property IRI>", "y": "<property IRI>"}, "presentation": null, '
            '"title": "Trend"}</proposal>',
            "Example 2 (the spec cannot express it — overlaying two quantities in one "
            "chart, so a view is written):",
            '<proposal>{"kind": "view", "view": {"lang": "vega-lite", "spec": '
            '{"layer": ['
            '{"mark": "line", "encoding": {'
            '"x": {"field": "x", "type": "quantitative"}, '
            '"y": {"field": "y1", "type": "quantitative"}}}, '
            '{"mark": "line", "encoding": {'
            '"x": {"field": "x", "type": "quantitative"}, '
            '"y": {"field": "y2", "type": "quantitative"}}}'
            ']}, "source_card_id": "<id of an existing card>"}}</proposal>',
        ]
        lines += _shape_fields_line(lk)
        if schema_properties:
            lines.append('Kinds you can build a view from, and their fields ("property IRI"):')
            for class_iri, props in schema_properties.items():
                lines.append(f'- class "{class_iri}":')
                lines.extend(_property_line(p) for p in props)
        else:
            lines.append(
                "No kind of record is available to build a new view from on this page yet. "
                "Only answer questions (do not write a <proposal>)."
            )
        if linking_kinds:
            lines.append("Candidate kinds reachable from this one (for context, with the path):")
            for k in linking_kinds:
                lines.append(f"- {k.get('class_label')} ({_path_description(k, lk)})")
        if existing_titles:
            lines.append("Views already on this page: " + ", ".join(existing_titles))
        if draft:
            lines.append(
                'Current draft (the previous proposal to refine on "fix" requests): '
                + json.dumps(draft, ensure_ascii=False)
            )
    return "\n".join(lines)


def render_user_prompt(messages: list[dict[str, str]], page: dict[str, Any] | None) -> str:
    """このページの要約（``page.facts``/``page.cards`` — UI が既に先頭 20 行・
    series は先頭と末尾に絞っている、契約メモ §1-2）とここまでの会話をつなげた
    1 通のユーザーメッセージ（tool calling を使わない・プロバイダ非依存の
    1 ラウンド生成という consult ルートと同じ流儀）。"""
    lines: list[str] = []
    if isinstance(page, dict):
        facts = page.get("facts")
        if isinstance(facts, list) and facts:
            lines.append("[このページの値]")
            for f in facts:
                if isinstance(f, dict):
                    lines.append(f"- {f.get('label')}: {f.get('value')}")
        cards = page.get("cards")
        if isinstance(cards, list) and cards:
            lines.append("[並んでいるカード]")
            for c in cards:
                if not isinstance(c, dict):
                    continue
                rows = c.get("rows")
                rows_json = json.dumps(rows, ensure_ascii=False) if rows is not None else "[]"
                # card_id は AI が view の source_card_id に写すための手がかり
                # （実機で「AI に id を一切見せていない」穴が見つかった — 人向けの
                # 文ではなく AI 向けの材料なので生の id を出してよい）。
                card_id = c.get("card_id")
                head = f"[id: {card_id}] " if isinstance(card_id, str) and card_id else ""
                lines.append(f"- {head}{c.get('title')} ({c.get('output_kind')}): {rows_json}")
    lines.append("[会話]")
    for m in messages:
        lines.append(f"{m.get('role')}: {m.get('content')}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# <proposal> の取り出し
# ----------------------------------------------------------------------------


def extract_proposal(reply_text: str) -> tuple[str, dict[str, Any] | None]:
    """返事から ``<proposal>...</proposal>`` を取り出す。タグが無い／中身が
    JSON として読めない／オブジェクトでない、のいずれも「提案なし」（``None``）
    として扱う（AI がタグの書式を間違えても壊れず、文章だけの返事に倒れる）。
    返す文字列はタグを取り除いた地の文（前後の空白は詰める）。``kind: "view"``
    （契約 F13 §1-2）かどうかはここでは見ない——ただの dict として返し、
    :func:`validate_proposal` が ``kind`` で振り分ける。"""
    match = _PROPOSAL_TAG_RE.search(reply_text)
    if match is None:
        return reply_text.strip(), None
    text = (reply_text[: match.start()] + reply_text[match.end() :]).strip()
    raw = match.group(1).strip()
    try:
        proposal = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return text, None
    if not isinstance(proposal, dict):
        return text, None
    return text, proposal


# ----------------------------------------------------------------------------
# 提案の検証
# ----------------------------------------------------------------------------


def _validate_presentation(raw: Any) -> dict[str, str] | None:
    """``presentation`` は ``{"mark": ...}`` の形だけを受け付ける — それ以外
    （余計なキー・不正な mark・オブジェクトでない）は無視して ``None`` にする
    （契約メモ §4「余計なキーがあっても無視される」— 提案そのものを落とす
    理由にはしない）。"""
    if not isinstance(raw, dict):
        return None
    mark = raw.get("mark")
    if mark not in _VALID_MARKS:
        return None
    return {"mark": mark}


def _render_title(shape: str, parts: dict[str, Any], lang: str) -> str:
    lk = _lang_key(lang)
    template = _TITLE_TEMPLATES[lk][shape]
    data = dict(parts)
    if shape == "quantity":
        agg = data.get("agg")
        data["agg"] = _AGG_LABELS[lk].get(agg, agg or "")
    if shape == "facts":
        data["items"] = _ITEMS_JOIN[lk].join(data.get("items") or [])
    return template.format(**{k: (v if v is not None else "") for k, v in data.items()})


def _page_card_ids(page: dict[str, Any] | None) -> set[str]:
    """``page.cards`` に載っている ``card_id`` の集合（契約 F13 §1-2
    「source_card_id が page.cards の card_id にあることを確かめる」）。
    ``card_id`` を持たないエントリ（F12 時点の ``{title, output_kind,
    rows}`` — ui 側がまだ ``card_id`` を積んでいない場合を含む）は無視する
    ので、ui がこのフィールドを送るまでは view 提案は「見つからない」側に
    安全に倒れる（fail-closed）。"""
    if not isinstance(page, dict):
        return set()
    cards = page.get("cards")
    if not isinstance(cards, list):
        return set()
    out: set[str] = set()
    for c in cards:
        if isinstance(c, dict):
            card_id = c.get("card_id")
            if isinstance(card_id, str) and card_id:
                out.add(card_id)
    return out


def _resolve_source_card_id(page: dict[str, Any] | None, ref: str) -> str | None:
    """``source_card_id`` を ``page.cards`` の ``card_id`` で引き、無ければ題名の
    **完全一致** で引く（弱い LLM が id を写し損ねて題名を書いたときの救済。
    プロンプトでも「id が分からなければ題名でもよい」と言っている）。題名が
    2 枚以上に一致するときは曖昧なので引かない（fail-closed）。"""
    if ref in _page_card_ids(page):
        return ref
    cards = page.get("cards") if isinstance(page, dict) else None
    if not isinstance(cards, list):
        return None
    matches = [
        c.get("card_id")
        for c in cards
        if isinstance(c, dict)
        and c.get("title") == ref
        and isinstance(c.get("card_id"), str)
        and c.get("card_id")
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _extract_answers(proposal: dict[str, Any]) -> bool:
    """``proposal.answers`` は ``bool`` のときだけ通す（既定 ``False`` —
    契約 F14 §1.4「answers は bool のときだけ通す」）。"""
    value = proposal.get("answers")
    return value if isinstance(value, bool) else False


def _linking_kind_where(subject: dict[str, Any], class_iri: str) -> list[dict[str, Any]]:
    """``class_iri`` に一致する ``subject["linking_kinds"]`` の行から ``where``
    を決める（契約 F14 §1.4）。同じ class の行が複数あれば **hops が最小 →
    count が最大** の行を選ぶ（決定論・同点は ``linking_kinds`` の元の順を
    保つ安定ソート）。行が ``where`` を持っていれば（ingest の新フィールド）
    それをそのまま使い、まだ持たない移行期の行は従来どおり
    ``[{"property": ..., "iri": individual_iri}]`` を組み立てる。"""
    matches = [k for k in (subject.get("linking_kinds") or []) if k.get("class_iri") == class_iri]
    individual_iri = subject.get("individual_iri")
    if not matches or not individual_iri:
        raise MeasureSpecError(
            "proposal.params.class must be this page's own kind or one of the linking kinds"
        )

    def sort_key(k: dict[str, Any]) -> tuple[int, int]:
        hops = k.get("hops")
        hops = hops if isinstance(hops, int) else 1
        count = k.get("count")
        count = count if isinstance(count, int) else 0
        return (hops, -count)

    chosen = sorted(matches, key=sort_key)[0]
    where = chosen.get("where")
    if isinstance(where, list) and where:
        return where
    return [{"property": chosen["property"], "iri": individual_iri}]


def _validate_view_proposal(
    proposal: dict[str, Any], page: dict[str, Any] | None
) -> dict[str, Any]:
    """``kind: "view"`` の提案（契約 F13 §1-2）を検証する。``view.lang`` ごと
    に ``asterism.view_spec_check`` の該当する許可リストへ委ね、
    ``source_card_id`` は ``page.cards`` に実在するものだけを通す。返り値は
    ``{"kind": "view", "view": {"lang", "spec"|"text", "source_card_id"},
    "answers"}``——``data`` は AI にも呼び出し側にも書かせず／持たせない。"""
    view_in = proposal.get("view")
    if not isinstance(view_in, dict):
        raise MeasureSpecError("proposal.view must be an object")
    lang_in = view_in.get("lang")
    if lang_in not in _VIEW_LANGS:
        raise MeasureSpecError(f"proposal.view.lang must be one of {_VIEW_LANGS}")
    source_card_id = view_in.get("source_card_id")
    if not isinstance(source_card_id, str) or not source_card_id:
        raise MeasureSpecError("proposal.view.source_card_id must be a non-empty string")
    resolved = _resolve_source_card_id(page, source_card_id)
    if resolved is None:
        raise MeasureSpecError("proposal.view.source_card_id must be a card already on this page")
    source_card_id = resolved
    answers = _extract_answers(proposal)
    if lang_in == "mermaid":
        text = view_in.get("text")
        ok, reason = view_spec_check.check_mermaid(text)
        if not ok:
            raise MeasureSpecError(f"proposal.view.text: {reason}")
        return {
            "kind": "view",
            "view": {"lang": lang_in, "text": text, "source_card_id": source_card_id},
            "answers": answers,
        }
    spec = view_in.get("spec")
    checker = (
        view_spec_check.check_vega_lite if lang_in == "vega-lite" else view_spec_check.check_table
    )
    ok, reason = checker(spec)
    if not ok:
        raise MeasureSpecError(f"proposal.view.spec: {reason}")
    return {
        "kind": "view",
        "view": {"lang": lang_in, "spec": spec, "source_card_id": source_card_id},
        "answers": answers,
    }


def validate_proposal(
    proposal: Any,
    schema_properties: dict[str, list[dict[str, Any]]],
    subject: dict[str, Any],
    *,
    lang: str = "ja",
    page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """AI の ``<proposal>`` を検証する。``proposal.kind == "view"``（契約
    F13 §1-2）なら :func:`_validate_view_proposal` に委ね、それ以外（従来の
    観点の指定・``kind`` を書かない F12 の形も含む）は §1-2/§1-3 の妥当性表
    に照らして検証し、正規化した ``{"kind": "measure", "params",
    "presentation", "output_kind", "title", "answers"}`` を返す（表の外は
    ``MeasureSpecError``）。

    - ``class`` は ``subject`` の種類（``own_class`` — set のページなら
      ``spec.class``、種類のページなら ``class_iri``）か、``linking_kinds``
      に載っている種類のどれかでなければならない。
    - ``where`` は AI の出力を一切信用せず、常にこの関数が補う: ``class`` が
      ``own_class`` と一致すれば ``subject["own_where"]``（絞り込みページの
      いまの条件・種類のページなら全件）、そうでなければ
      :func:`_linking_kind_where` が ``linking_kinds`` の該当行の ``where``
      をそのまま使う（同じ class が複数行あれば hops 最小 → count 最大の行
      ——契約 F14 §1.4）。
    - ``title`` はここで（Python 側のテンプレートで）計算する — AI が
      ``proposal.title`` に何を書いても採用しない（モジュール docstring
      参照）。
    - ``answers`` は ``proposal.answers`` が ``bool`` のときだけ通す（既定
      ``False``——契約 F14 §1.4「提案してから答える」）。返り値の
      ``proposal`` に必ず含める（``kind: "view"`` も同様）。
    """
    if not isinstance(proposal, dict):
        raise MeasureSpecError("proposal must be an object")
    if proposal.get("kind") == "view":
        return _validate_view_proposal(proposal, page)
    params_in = proposal.get("params")
    if not isinstance(params_in, dict):
        raise MeasureSpecError("proposal.params must be an object")
    class_iri = params_in.get("class")
    if not isinstance(class_iri, str) or class_iri not in schema_properties:
        raise MeasureSpecError(
            "proposal.params.class must be this page's own kind or one of the linking kinds"
        )
    own_class = subject.get("own_class")
    own_where = subject.get("own_where")
    if class_iri == own_class and own_where is not None:
        where = own_where
    else:
        where = _linking_kind_where(subject, class_iri)

    measure_params = {k: v for k, v in params_in.items() if k not in ("class", "where")}
    properties = schema_properties[class_iri]
    normalized = validate_measure(measure_params, properties)
    full_params: dict[str, Any] = {"class": class_iri, "where": where, **normalized}
    output_kind = output_kind_for(normalized["shape"])
    presentation = _validate_presentation(proposal.get("presentation"))
    parts = title_parts(normalized, properties)
    title = _render_title(normalized["shape"], parts, lang)
    return {
        "kind": "measure",
        "params": full_params,
        "presentation": presentation,
        "output_kind": output_kind,
        "title": title,
        "answers": _extract_answers(proposal),
    }


# ----------------------------------------------------------------------------
# 1 回だけの言い直し
# ----------------------------------------------------------------------------


def render_retry_message(reason: str, lang: str) -> str:
    """検証で落ちたときに AI に返す「この理由で通らない」の 1 文（AI 向けの
    内部メッセージ — ``reason`` は :class:`MeasureSpecError` の生の文面
    （property IRI を含み得る）で、これは会話履歴として AI にだけ渡す。人が
    読む ``reply`` には絶対に混ぜない（K4）。"""
    lk = _lang_key(lang)
    if lk == "ja":
        return f"その指定は通りませんでした（理由: {reason}）。直して、もう一度答えてください。"
    return f"That did not validate ({reason}). Please fix it and answer again."


def with_cannot_build_note(text: str, lang: str) -> str:
    """2 回とも検証を通らなかったときに ``reply`` へ添える定型文（K4: 生の
    理由は出さない）。"""
    note = _CANNOT_BUILD_NOTE[_lang_key(lang)]
    text = text.strip()
    return f"{text}\n\n{note}" if text else note
