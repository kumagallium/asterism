"""AI の適性検査（PR 11q）— Asterism が AI に求める作業を、固定の台本で測る。

* 形: 提案（新規）5 問＋対象つきの修正 5 問を、**初回の 1 回だけ**（聞き直し無し）で読めるか
* 振り分け: ラベル付きの 30 文を 11m の振り分け prompt にそのまま通して、期待どおりか
* 対象の遵守: 対象つきの修正 5 問の返りが ``filter_ops_to_scope`` で 1 件も捨てられないか

読むだけ（ストアも下書きも話も触らない）・乱数なし・既存の prompt と engine をそのまま使う。
判定の式は :func:`judge`。
"""

# ruff: noqa: RUF001
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from asterism import themes
from asterism_step0.graph_propose import GraphDiffError, propose_graph_diff
from asterism_step0.theme_propose import ThemeDraftError, propose_theme_draft

from asterism_api import chat_route, chat_turns

__all__ = [
    "GRAPH",
    "ROUTING",
    "SHAPE_CREATE",
    "SHAPE_DIFF",
    "TOTAL",
    "AptitudeCancelled",
    "AptitudeResult",
    "judge",
    "run_aptitude",
    "settings_key",
]

#: 提案（新規）の問い 5（価格表の例）
SHAPE_CREATE: list[str] = [
    "どの食材をどの店で買うのが安い？",
    "賞味期限が近い食材を先に使うには？",
    "まとめ買いすると 1 つあたりの値段は下がる？",
    "特売の日に値段はどれくらい下がる？",
    "店によって同じ食材の値段はどれくらい違う？",
]

#: 修正の固定の図（価格表の 5 用語・4 関係）。鍵は c1〜c5
GRAPH: dict = {
    "concepts": [
        {"key": "c1", "label_ja": "食材", "label_en": "ingredient", "kind": "class", "use": "new"},
        {"key": "c2", "label_ja": "店", "label_en": "shop", "kind": "class", "use": "new"},
        {"key": "c3", "label_ja": "値段", "label_en": "price", "kind": "property", "use": "new"},
        {"key": "c4", "label_ja": "1 つあたりの値段", "label_en": "unit price",
         "kind": "property", "use": "new"},
        {"key": "c5", "label_ja": "賞味期限", "label_en": "best-before date",
         "kind": "property", "use": "new"},
    ],
    "relations": [
        {"from": "c4", "rel": "part", "to": "c3"},
        {"from": "c1", "rel": "affects", "to": "c3"},
        {"from": "c2", "rel": "affects", "to": "c3"},
        {"from": "c5", "rel": "lowers", "to": "c3"},
    ],
    "questions": [],
}  # fmt: skip

#: 対象つきの修正 5 問 = (対象, 指示)。対象は ``{"keys": […], "name": …, "line": …}``
SHAPE_DIFF: list[tuple[dict, str]] = [
    ({"keys": ["c4", "c3"], "name": "1 つあたりの値段 - 値段", "line": ["c4", "part", "c3"]},
     "一部ではなく一種にして"),
    ({"keys": ["c1", "c3"], "name": "食材 - 値段", "line": ["c1", "affects", "c3"]},
     "影響するのではなく、値段を上げるにして"),
    ({"keys": ["c3"], "name": "値段", "line": None},
     "値段 を下げるものとして 特売 を足して"),
    ({"keys": ["c1"], "name": "食材", "line": None},
     "食材 の名前を 野菜 に直して"),
    ({"keys": ["c5"], "name": "賞味期限", "line": None},
     "賞味期限 は 値段 を下げるのではなく上げる、にして"),
]  # fmt: skip

#: 振り分け 30 = (文, 期待)。決定論に当たる文（図の言葉・指摘の言葉）は入れない
ROUTING: list[tuple[str, str]] = [
    ("ありがとう", "chat"),
    ("このアプリは何ができる？", "chat"),
    ("こんにちは", "chat"),
    ("助かりました", "chat"),
    ("どうやって使い始めればいいですか", "chat"),
    ("今日は寒いですね", "chat"),
    ("なるほど、わかりました", "chat"),
    ("Thanks, that helps", "chat"),
    ("Hello there", "chat"),
    ("What can this app do for me?", "chat"),
    ("じゃがいもが一番安い店は？ 教えて", "ask"),
    ("値段の記録は何件ある？", "ask"),
    ("先週いちばん高かった食材は？", "ask"),
    ("牛乳の平均の値段を教えて", "ask"),
    ("どの店の記録が一番多い？", "ask"),
    ("卵の値段が 200 円を超えた日はある？", "ask"),
    ("登録した食材は全部で何種類？", "ask"),
    ("Which shop sells potatoes cheapest?", "ask"),
    ("How many price records are there?", "ask"),
    ("What was the highest price of milk?", "ask"),
    ("食材ごとに値段を比べたい", "propose"),
    ("店の営業時間も知りたい", "propose"),
    ("賞味期限が近いものを先に使う方法を整理したい", "propose"),
    ("産地によって値段がどう変わるかを調べたい", "propose"),
    ("特売の日は値段が下がるという知識を入れたい", "propose"),
    ("まとめ買いと 1 つあたりの値段の関係を扱いたい", "propose"),
    ("食材の種類と栄養の関係も考えたい", "propose"),
    ("I want to compare prices by shop", "propose"),
    ("I also want to track the best-before date", "propose"),
    ("Bulk buying should lower the unit price", "propose"),
]  # fmt: skip

TOTAL = len(SHAPE_CREATE) + len(SHAPE_DIFF) + len(ROUTING)

#: 判定の閾値
_SHAPE_GOOD = 0.9
_ROUTING_GOOD = 0.9
_SCOPE_GOOD = 0.8
_ROUTING_LIMITED = 0.7


class AptitudeCancelled(Exception):
    """取り消しに応えて検査を止めた。"""


@dataclass
class AptitudeResult:
    verdict: str
    shape: float  # 形（0〜1）
    routing: float  # 振り分け（0〜1）
    scope: float  # 対象の遵守（0〜1）
    # [{"kind": "shape"|"routing"|"scope", "index": 0 始まり, "reason": …}]
    failed: list[dict] = field(default_factory=list)
    at: str = ""

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict, "shape": self.shape, "routing": self.routing,
            "scope": self.scope, "failed": self.failed, "at": self.at,
        }  # fmt: skip


def settings_key(provider: str, model: str | None) -> str:
    """settings.json の ``aptitude`` の鍵。鍵（秘密）は入れない。"""
    return f"{provider}::{model or ''}"


def judge(shape: float, routing: float, scope: float) -> str:
    """率（0〜1）→ ``good``（推奨）／``limited``（条件つき）／``poor``（非推奨）。

    推奨 = 形 ≥ 90% かつ 振り分け ≥ 90% かつ 対象 ≥ 80%。推奨でなく振り分け ≥ 70% なら
    条件つき（対象を絞って使うと通りやすい）。それ以外は非推奨。"""
    if shape >= _SHAPE_GOOD and routing >= _ROUTING_GOOD and scope >= _SCOPE_GOOD:
        return "good"
    if routing >= _ROUTING_LIMITED:
        return "limited"
    return "poor"


def _rate(ok: int, total: int) -> float:
    return round(ok / total, 3) if total else 0.0


def run_aptitude(
    llm: Any,
    *,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AptitudeResult:
    """台本を順に流して判定する（ブロッキング。``asyncio.to_thread`` で呼ぶ）。

    形・振り分けの読めない返りは不合格として数える。LLM に届かない・認証などの例外は
    検査そのものの失敗なので、そのまま投げる。"""
    done = 0

    def step() -> None:
        nonlocal done
        if should_cancel is not None and should_cancel():
            raise AptitudeCancelled()
        done += 1
        if progress is not None:
            progress(done, TOTAL)

    failed: list[dict] = []
    shape_ok = 0
    for i, q in enumerate(SHAPE_CREATE, 1):
        step()
        try:
            propose_theme_draft(
                llm, questions=[q], known_terms=[],
                known_standards=themes.default_standards([q]),
                language="ja", label="", attempts=1,
            )  # fmt: skip
            shape_ok += 1
        except ThemeDraftError:
            failed.append({"kind": "shape", "index": i - 1, "reason": "create"})

    scope_ok = 0
    for i, (scope, instruction) in enumerate(SHAPE_DIFF, 1):
        step()
        try:
            diff = propose_graph_diff(
                llm, screen="theme", graph=GRAPH, existing_terms=[], standards=[],
                columns=None, messages=[], message=instruction, lang="ja",
                scope=chat_turns.scope_prompt(scope, GRAPH), attempts=1,
            )  # fmt: skip
        except (GraphDiffError, ThemeDraftError):
            failed.append({"kind": "shape", "index": len(SHAPE_CREATE) + i - 1, "reason": "diff"})
            failed.append({"kind": "scope", "index": i - 1, "reason": "unreadable"})
            continue
        shape_ok += 1
        ops = diff["ops"]
        alias = chat_turns.scope_aliases(ops, GRAPH["concepts"])
        kept, dropped = chat_turns.filter_ops_to_scope(ops, scope, alias)
        if not dropped and kept:
            scope_ok += 1
        else:
            failed.append({"kind": "scope", "index": i - 1, "reason": "out_of_scope"})

    routing_ok = 0
    for i, (text, expected) in enumerate(ROUTING, 1):
        step()
        try:
            routed = chat_route.route_message(
                llm, text=text, history=[], lang="ja", theme_label="食材の値段",
                has_draft=False, n_terms=5, n_datasets=1,
            )  # fmt: skip
        except chat_route.RouteError:
            failed.append({"kind": "routing", "index": i - 1, "reason": "unreadable"})
            continue
        if routed.mode == expected:
            routing_ok += 1
        else:
            failed.append(
                {"kind": "routing", "index": i - 1, "reason": f"{expected}->{routed.mode}"}
            )

    n_shape = len(SHAPE_CREATE) + len(SHAPE_DIFF)
    verdict = judge(
        shape_ok / n_shape, routing_ok / len(ROUTING), scope_ok / len(SHAPE_DIFF)
    )
    return AptitudeResult(
        verdict=verdict,
        shape=_rate(shape_ok, n_shape),
        routing=_rate(routing_ok, len(ROUTING)),
        scope=_rate(scope_ok, len(SHAPE_DIFF)),
        failed=failed,
        at=datetime.now(UTC).isoformat(),
    )
