"""ID の引っ越し — 公開したあとに「ID の作り方」を変えても、前の ID を生かす。

「データの数えかた」(かんたん S4 の骨格ゲート) をやり直すと subject template が
変わり、同じ行から違う IRI が作られる。IRI は「引用できる事実」の住所そのもの
なので、黙って作り直すと配った引用が切れる —— それが ADR
``kantan-mode-two-tier-ux.md`` K21 が「直せるのは作られる前だけ」と書き、
``canBackToGate`` が公開後に閉じている理由だった。

ただし機械は旧設計と新設計を **両方** 持っている。だから「旧 IRI → 新 IRI」の
転送台帳は推測ではなく決定論で作れる。この module がその計算を持つ:

* 旧 subject template を subject に、新 subject template を object に置いた
  *引っ越し用 Mapping IR* を組み、既存の RML コンパイラ + Morph-KGC にそのまま
  流す。IRI を綴るのは常に同じエンジンなので、旧 IRI / 新 IRI の綴りが二重実装で
  ずれる余地が構造的に無い（この module は IRI 文字列を自分で組み立てない）。
* 述語は ``dcterms:isReplacedBy``。``owl:sameAs`` にしないのは、ID の作り方を
  変えると「旧 1 個 → 新 500 個」に分かれる場合が実際にあり、そのとき sameAs は
  「別々の試料が全部同一」という *嘘の事実* を公開してしまうため。
  isReplacedBy は向きを持つので 1→多（分かれた）・多→1（まとまった）の
  どちらも嘘なしで書け、推論エンジンを誘発しない。ADR
  ``id-move-after-publish.md`` §3。

「引っ越せない」も一級の結果として返す（:class:`BlockedSubject`）。前の住所が
開けなくなることは、黙って起こしてよい事ではなく、公開の直前に人へ見せる材料
だからである（K10 の公開ダイアログと同じ流儀）。

Pure: I/O も乱数も時刻も無い。同じ 2 つの設計からは常に同じ計画が出る。
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from asterism_step0.mapping_ir import (
    FunctionCatalog,
    MappingIR,
    PropertyIR,
    SubjectIR,
    TriplesMapIR,
    template_placeholders,
)

__all__ = [
    "DCTERMS_IS_REPLACED_BY",
    "BlockedSubject",
    "IdMovePlan",
    "MovedSubject",
    "PublishedSubject",
    "compile_id_move_rml",
    "expand_template",
    "plan_id_move",
    "published_subjects",
]

# 旧 ID → 新 ID を繋ぐ唯一の述語（ADR id-move-after-publish.md §3）。
DCTERMS_IS_REPLACED_BY = "http://purl.org/dc/terms/isReplacedBy"

# substrate が run-id を差し込むスロット。列ではないので「無い列」に数えない
# (mapping_ir._RUN_ID_PLACEHOLDER と同じ約束)。
_RUN_ID_PLACEHOLDER = "__run_id__"

_CURIE_HEAD = re.compile(r"^([A-Za-z][\w.\-]*):(.*)$")


def expand_template(template: str, prefixes: Mapping[str, str]) -> str:
    """Template の CURIE 頭を絶対 IRI に展開する（``sdr:sample/{id}`` →
    ``https://…/resource/sample/{id}``）。

    台帳に残すのは展開済みの形。prefix 名は設計をやり直すたびに変わりうる
    (K13 は slug から決定論で導出する) のに対し、展開後の IRI は変わらない ——
    「前の ID の作り方」の記録が prefix 定義に依存してはならない。

    展開できない（頭が CURIE でない / prefix が未宣言）ときは原文のまま返す。
    判断はしない: 不整合はコンパイラが自分の語彙で落とす。
    """
    head, sep, rest = template.partition("{")
    if head.startswith(("http://", "https://")):
        return template
    m = _CURIE_HEAD.match(head)
    if not m:
        return template
    base = prefixes.get(m.group(1))
    if base is None:
        return template
    return f"{base}{m.group(2)}{sep}{rest}"


@dataclass(frozen=True)
class PublishedSubject:
    """公開した時点の「ID の作り方」1 map 分 —— ``meta.json`` に残す最小の記録。

    設計まるごとではなく subject だけを持つ。IRI の綴りを決めるのは subject
    template と、そのプレースホルダに掛かる Tier-0 変換だけであり、他の欄
    (プロパティ・ラベル・単位) をいくら変えても住所は動かないからである。
    """

    name: str
    source: str
    template: str
    iterator: str | None = None
    transform: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {
            "name": self.name,
            "source": self.source,
            "template": self.template,
        }
        if self.iterator:
            out["iterator"] = self.iterator
        if self.transform:
            out["transform"] = dict(self.transform)
        return out

    @staticmethod
    def from_json(raw: object) -> PublishedSubject | None:
        """壊れた／古い記録は捨てて ``None``。台帳の欠落は「引っ越せない」に
        落ちるだけで、例外にはしない（読めない記録で公開を止めない）。"""
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        source = raw.get("source")
        template = raw.get("template")
        if not isinstance(name, str) or not isinstance(source, str):
            return None
        if not isinstance(template, str) or not template:
            return None
        iterator = raw.get("iterator")
        transform = raw.get("transform")
        return PublishedSubject(
            name=name,
            source=source,
            template=template,
            iterator=iterator if isinstance(iterator, str) and iterator else None,
            transform=(
                {str(k): str(v) for k, v in transform.items()}
                if isinstance(transform, dict)
                else {}
            ),
        )


def published_subjects(ir: MappingIR) -> list[PublishedSubject]:
    """設計から「ID の作り方」だけを抜き出す（公開のたびに記録する形）。

    ``subject.constant`` の map（表の行ではなく 1 個の固定エンティティ）は
    行に紐づかないので対象外 —— 引っ越しは「同じ行から作られた 2 つの IRI」を
    繋ぐ仕掛けであり、固定 IRI は設計をやり直しても綴りが変わらない限り
    そのまま生き、変わったなら対応する行が無いので機械には繋げない。
    """
    out: list[PublishedSubject] = []
    for m in ir.maps:
        if not m.subject.template:
            continue
        out.append(
            PublishedSubject(
                name=m.name,
                source=m.source,
                template=expand_template(m.subject.template, ir.prefixes),
                iterator=m.iterator,
                transform=dict(m.subject.transform),
            )
        )
    return out


@dataclass(frozen=True)
class MovedSubject:
    """住所が変わる 1 map。旧 template と新 template は同じ行に当たる。"""

    name: str  # 新しい設計での map 名（引っ越し先の呼び名）
    old_name: str
    source: str
    old_template: str
    new_template: str
    iterator: str | None = None
    old_transform: Mapping[str, str] = field(default_factory=dict)
    new_transform: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BlockedSubject:
    """住所が変わるのに、前の住所を計算できない 1 map。

    ``reason``:

    * ``no_matching_map`` —— 旧 map に対応する新 map が無い（その種類ごと
      設計から消えた）。行き先が存在しないので繋ぎようがない。
    * ``missing_columns`` —— 旧 ID を綴るのに要る列が、いまの元ファイルに無い。
      列名が変わった / その列を持たないファイルに差し替えられた場合。
    """

    name: str  # 旧 map 名
    source: str
    reason: str
    missing_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdMovePlan:
    """旧設計 → 新設計の住所の変化。UI と台帳生成の共通の材料。"""

    moved: tuple[MovedSubject, ...] = ()
    unchanged: tuple[str, ...] = ()
    blocked: tuple[BlockedSubject, ...] = ()

    @property
    def changes_ids(self) -> bool:
        """この変更で公開済みの住所が動くか。``False`` なら引っ越しは不要 ——
        種類（クラス）やラベルだけを変えたときがこれに当たる。"""
        return bool(self.moved) or bool(self.blocked)

    @property
    def fully_movable(self) -> bool:
        """動く住所すべてに転送先を書けるか。``False`` のとき、公開すると
        前の ID で開けなくなるものが残る（人に見せて決めてもらう一点）。"""
        return not self.blocked

    def to_json(self) -> dict[str, object]:
        return {
            "changes_ids": self.changes_ids,
            "fully_movable": self.fully_movable,
            "moved": [
                {
                    "name": m.name,
                    "old_name": m.old_name,
                    "source": m.source,
                    "old_template": m.old_template,
                    "new_template": m.new_template,
                }
                for m in self.moved
            ],
            "unchanged": list(self.unchanged),
            "blocked": [
                {
                    "name": b.name,
                    "source": b.source,
                    "reason": b.reason,
                    "missing_columns": list(b.missing_columns),
                }
                for b in self.blocked
            ],
        }


def _pair_key(source: str, iterator: str | None) -> tuple[str, str]:
    return (source, iterator or "")


def _needed_columns(template: str, transform: Mapping[str, str]) -> set[str]:
    cols = set(template_placeholders(template)) | set(transform)
    cols.discard(_RUN_ID_PLACEHOLDER)
    return cols


def plan_id_move(
    old: Sequence[PublishedSubject],
    new_ir: MappingIR,
    *,
    available_columns: Mapping[str, set[str]] | None = None,
) -> IdMovePlan:
    """公開中の「ID の作り方」と新しい設計を突き合わせ、住所の変化を出す。

    対応づけは **同じ元ファイル (+ XML の iterator) の中で** 行う:

    1. map 名が一致するもの同士。
    2. 残りが片側 1 つずつなら、それ同士（名前だけ変わった場合を拾う）。
    3. それでも残った旧 map は ``no_matching_map``。

    ``available_columns`` を渡すと（source 名 → いまの元ファイルの列名）、旧 ID を
    綴るのに要る列が実在するかまで見る。渡さない場合はこの検査を省く —— 省いた
    ぶんは実行時に空の台帳として現れるだけで、嘘は増えない。
    """
    new_by_key: dict[tuple[str, str], list[TriplesMapIR]] = {}
    for m in new_ir.maps:
        if not m.subject.template:
            continue
        new_by_key.setdefault(_pair_key(m.source, m.iterator), []).append(m)

    old_by_key: dict[tuple[str, str], list[PublishedSubject]] = {}
    for s in old:
        old_by_key.setdefault(_pair_key(s.source, s.iterator), []).append(s)

    moved: list[MovedSubject] = []
    unchanged: list[str] = []
    blocked: list[BlockedSubject] = []

    for key, old_maps in sorted(old_by_key.items()):
        candidates = list(new_by_key.get(key, ()))
        pairs: list[tuple[PublishedSubject, TriplesMapIR]] = []
        rest_old: list[PublishedSubject] = []
        # ① 名前一致
        by_name = {m.name: m for m in candidates}
        for o in old_maps:
            match = by_name.pop(o.name, None)
            if match is not None:
                pairs.append((o, match))
            else:
                rest_old.append(o)
        rest_new = [m for m in candidates if m.name in by_name]
        # ② 残りが片側 1 つずつなら、名前が変わっただけとみなす
        if len(rest_old) == 1 and len(rest_new) == 1:
            pairs.append((rest_old[0], rest_new[0]))
            rest_old = []
        # ③ 行き先の無い旧 map
        for o in rest_old:
            blocked.append(
                BlockedSubject(name=o.name, source=o.source, reason="no_matching_map")
            )

        for o, m in pairs:
            new_template = expand_template(m.subject.template or "", new_ir.prefixes)
            new_transform = dict(m.subject.transform)
            if o.template == new_template and dict(o.transform) == new_transform:
                unchanged.append(m.name)
                continue
            if available_columns is not None:
                have = available_columns.get(o.source)
                if have is None:
                    blocked.append(
                        BlockedSubject(
                            name=o.name, source=o.source, reason="missing_columns"
                        )
                    )
                    continue
                missing = sorted(_needed_columns(o.template, o.transform) - set(have))
                if missing:
                    blocked.append(
                        BlockedSubject(
                            name=o.name,
                            source=o.source,
                            reason="missing_columns",
                            missing_columns=tuple(missing),
                        )
                    )
                    continue
            moved.append(
                MovedSubject(
                    name=m.name,
                    old_name=o.name,
                    source=m.source,
                    old_template=o.template,
                    new_template=new_template,
                    iterator=m.iterator,
                    old_transform=dict(o.transform),
                    new_transform=new_transform,
                )
            )

    return IdMovePlan(
        moved=tuple(moved),
        unchanged=tuple(sorted(unchanged)),
        blocked=tuple(blocked),
    )


def compile_id_move_rml(
    plan: IdMovePlan,
    new_ir: MappingIR,
    catalog: FunctionCatalog | None = None,
) -> str | None:
    """転送台帳を作る RML を組む。引っ越すものが無ければ ``None``。

    出来上がる RML は「同じ行を、いまの元ファイルから、いまの方言で読み、
    旧 IRI を subject、新 IRI を object にした ``dcterms:isReplacedBy`` を
    1 本だけ書く」。型 (``rr:class``) は付けない —— 旧 IRI に新しい種類を
    主張させると、転送の記録が *データの主張* に化けてしまう。
    """
    if not plan.moved:
        return None
    from asterism_step0.rml_compile import compile_mapping_ir

    maps = tuple(
        TriplesMapIR(
            name=f"idmove_{mv.name}",
            source=mv.source,
            iterator=mv.iterator,
            subject=SubjectIR(template=mv.old_template, transform=dict(mv.old_transform)),
            properties=(
                PropertyIR(
                    predicate=DCTERMS_IS_REPLACED_BY,
                    object_template=mv.new_template,
                    transform=dict(mv.new_transform),
                    object_type="iri",
                ),
            ),
        )
        for mv in plan.moved
    )
    # prefixes は空 —— template は展開済みの絶対 IRI であり、CURIE を挟むと
    # 「どちらの設計の prefix 定義か」という解けない問いが 1 つ増える。
    # dialects は新しい設計のものをそのまま: 同じ行を同じ読み方で当てる。
    return compile_mapping_ir(
        MappingIR(prefixes={}, maps=maps, dialects=new_ir.dialects), catalog
    )
