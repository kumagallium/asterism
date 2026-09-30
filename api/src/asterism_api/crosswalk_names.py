"""ハブとつながりの表示名を、読むたびに項目の表示名から引く
（契約メモ contract_b_hub_names.md）。

- R1 :func:`perspective_display_name` — つながり（perspective）の表示名。
- R2 :func:`concept_display_name` — 1 concept の表示名。
- R3 :func:`hub_class_display_name` — ハブの種類の表示名。
- :func:`hub_class_index` — ある種類の IRI がどれかのハブの concept か（
  R3 を通すべきか）を、registry の全つながりの config から 1 度だけ調べる
  （契約メモ contract_b2_hub_names.md B2-1）。

ストアの中身・保存済みの ``crosswalk.yaml``・registry meta の ``name`` は
一切書き換えない — ここは読み取り専用の計算だけ。項目の表示名を引く関数
（``asterism_api.main._crosswalk_label_resolvers`` が作る
``predicate_label_of``/``field_label_of``）は呼び出し側が渡す（循環 import
回避 — main.py の docstring 参照）。

R2 の「参加している項目の表示名 → 概念のキーの人向け直し」の順序は、
``main.py`` の ``_enrich_crosswalk_config_dict``（``GET /api/crosswalks`` が
読むたびに引いている ``concept_label``）と同じ突き合わせ順（field_label_of
→ predicate_label_of、先勝ち・重複除去）を保つ — 少なくとも 1 件でも
参加者の表示名が引ける場合は、あちらと同じ結果になる。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from asterism import crosswalk_runtime

__all__ = [
    "PLACEHOLDER_KEY_RE",
    "concept_display_name",
    "concept_labels_for_config",
    "hub_class_display_name",
    "hub_class_index",
    "load_perspective_meta",
    "perspective_display_name",
    "refresh_auto_name",
]

#: 参加している項目のどれからも表示名が引けなかった concept の表示名の型
#: ヒント。``field_label_of(dataset_id, predicate, subject_class)`` /
#: ``predicate_label_of(dataset_id, predicate)`` と同じ形
#: （``asterism_api.main._crosswalk_label_resolvers`` 参照）。
FieldLabelOf = Callable[[str, str, "str | None"], "str | None"]
PredicateLabelOf = Callable[[str, str], "str | None"]

#: discover が値の重なりだけから命名した concept キー（``shared_value_1`` 等）
#: — UI の ``crosswalkLabels.ts`` の ``PLACEHOLDER_KEY`` と同じ規則。人が
#: 認識できる語を何一つ持たないので、人向けの直し（``_`` を空白に）の元には
#: しない。
PLACEHOLDER_KEY_RE = re.compile(r"^shared[_ ]value(?:[_ ]?\d+)?$", re.IGNORECASE)

#: 古いサーバが付けた「crosswalk: xxx」のような実装の語の名前 — UI の
#: ``crosswalkLabels.ts`` の ``IMPLEMENTATION_NAME`` と同じ規則。
_IMPLEMENTATION_NAME_RE = re.compile(r"^crosswalk(\s|:)", re.IGNORECASE)

_META_FILE = "meta.json"


def load_perspective_meta(registry_root: Path | str, perspective_id: str) -> dict[str, Any]:
    """perspective の registry meta（読めなければ空 dict）。R1 の入力の一つ。"""
    try:
        registry_id = crosswalk_runtime.crosswalk_registry_id(perspective_id)
    except ValueError:
        return {}
    meta_path = Path(registry_root) / registry_id / _META_FILE
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _key_to_words(key: str) -> str:
    """概念のキーを人向けに直す（``_`` を空白に）。UI の ``conceptLabel`` と
    同じ最小限の直し — キー自体が新しい語を作るわけではない。"""
    words = key.replace("_", " ").strip()
    return words or key


def _resolved_participant_labels(
    concept: crosswalk_runtime.RuntimeConcept,
    field_label_of: FieldLabelOf,
    predicate_label_of: PredicateLabelOf,
) -> list[str]:
    """R2 その1: concept の参加者ごとに、その kind の項目名（field_label_of）
    を先に、無ければ述語だけの語（predicate_label_of）を試し、引けた語を
    重複無しで並べる（``main.py`` の ``_enrich_crosswalk_config_dict`` と
    同じ突き合わせ順）。"""
    resolved: list[str] = []
    for p in concept.participants:
        preds = p.predicates if p.predicates else (p.predicate,)
        label = next(
            (
                got
                for pred in preds
                if (
                    got := (
                        field_label_of(p.dataset_id, pred, p.subject_class)
                        or predicate_label_of(p.dataset_id, pred)
                    )
                )
            ),
            None,
        )
        if label and label not in resolved:
            resolved.append(label)
    return resolved


def concept_display_name(
    concept: crosswalk_runtime.RuntimeConcept,
    field_label_of: FieldLabelOf,
    predicate_label_of: PredicateLabelOf,
) -> str:
    """R2 — 1 concept の表示名。

    1. 参加している項目の表示名（食い違えば ``/`` でつなぐ）
    2. 引けなければ概念のキーを人向けに直したもの（ただし置き場のキーは使わ
       ない）
    3. それも無ければ空文字列（呼び出し側が R1/R3 の「名前のないつながり」
       に落とす）
    """
    resolved = _resolved_participant_labels(concept, field_label_of, predicate_label_of)
    if resolved:
        return resolved[0] if len(resolved) == 1 else " / ".join(resolved)
    if PLACEHOLDER_KEY_RE.match(concept.name):
        return ""
    return _key_to_words(concept.name)


def _is_machine_name(
    name: str,
    meta: dict[str, Any],
    config: crosswalk_runtime.RuntimeCrosswalkConfig | None,
    perspective_id: str,
) -> bool:
    """「人が付けた名前ではない」の判定（R1）。どれか 1 つでも真なら機械が
    付けた名前 — 表示に使わない。"""
    if not name:
        return True
    if meta.get("name_auto") is True:
        return True
    if config is not None:
        concept_names = {c.name for c in config.concepts}
    else:
        concept_names = {
            str(n) for n in (meta.get("crosswalk_concepts") or []) if isinstance(n, str)
        }
    if name in concept_names:
        return True
    if name == perspective_id:
        return True
    if name == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME:
        return True
    return bool(_IMPLEMENTATION_NAME_RE.match(name))


def perspective_display_name(
    meta: dict[str, Any],
    config: crosswalk_runtime.RuntimeCrosswalkConfig | None,
    perspective_id: str,
    field_label_of: FieldLabelOf,
    predicate_label_of: PredicateLabelOf,
) -> str:
    """R1 — つながりの表示名。

    1. registry meta の ``name``（前後の空白を除く）が人が付けた名前なら、
       それを返す（``crosswalk_runtime.DEFAULT_PERSPECTIVE_NAME`` はここで
       人が付けた名前として扱われる — 機械付与の判定に当たらない）
    2. そうでなければ各 concept の表示名（R2）を config の順に並べ、重複を
       除いて ``/`` でつなぐ
    3. 1 つも引けなければ「名前のないつながり」
    """
    raw_name = str(meta.get("name") or "").strip()
    if raw_name and not _is_machine_name(raw_name, meta, config, perspective_id):
        return raw_name
    if config is not None:
        labels: list[str] = []
        for concept in config.concepts:
            label = concept_display_name(concept, field_label_of, predicate_label_of)
            if label and label not in labels:
                labels.append(label)
        if labels:
            return " / ".join(labels)
    return crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME


def hub_class_index(
    registry_root: Path | str,
) -> dict[str, crosswalk_runtime.RuntimeCrosswalkConfig]:
    """registry にある全てのつながりの config を読み、ハブの種類の IRI →
    その concept を持つ config、の対応表を作る（契約メモ
    contract_b2_hub_names.md B2-1）。呼び出し元（``cards_routes``）が 1
    リクエストにつき 1 度だけ呼び、以降の :func:`hub_class_display_name`
    呼び出しに使い回す（レジストリを読み直さない）。同じ ``class_iri`` を
    複数の perspective が使うことは無い前提だが、あっても先に見つかった方を
    使う（決定論のため ``list_perspectives`` の並び順＝新しい順）。"""
    index: dict[str, crosswalk_runtime.RuntimeCrosswalkConfig] = {}
    for meta in crosswalk_runtime.list_perspectives(registry_root):
        perspective_id = meta.get("crosswalk_perspective_id")
        if not isinstance(perspective_id, str) or not perspective_id:
            continue
        config = crosswalk_runtime.load_config(registry_root, perspective_id)
        if config is None:
            continue
        for concept in config.concepts:
            index.setdefault(concept.class_iri, config)
    return index


def hub_class_display_name(
    class_iri: str,
    config: crosswalk_runtime.RuntimeCrosswalkConfig | None,
    field_label_of: FieldLabelOf,
    predicate_label_of: PredicateLabelOf,
) -> str:
    """R3 — ハブの種類の表示名。``class_iri`` がどれかの concept の
    ``class_iri`` と同じならその concept の表示名（R2）、無ければ
    「名前のないつながり」。"""
    if config is not None:
        for concept in config.concepts:
            if concept.class_iri == class_iri:
                label = concept_display_name(concept, field_label_of, predicate_label_of)
                if label:
                    return label
                break
    return crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME


def concept_labels_for_config(
    config: crosswalk_runtime.RuntimeCrosswalkConfig,
    field_label_of: FieldLabelOf,
    predicate_label_of: PredicateLabelOf,
) -> dict[str, str]:
    """``config`` の各 concept の表示名（R2）を、concept の名前 → 表示名の形で
    返す（``crosswalk_runtime.build_hub`` の ``concept_labels`` にそのまま渡せる）。
    空の結果は入れない。"""
    labels: dict[str, str] = {}
    for concept in config.concepts:
        label = concept_display_name(concept, field_label_of, predicate_label_of)
        if label:
            labels[concept.name] = label
    return labels


def _is_surely_machine_name(
    name: str,
    meta: dict[str, Any],
    config: crosswalk_runtime.RuntimeCrosswalkConfig,
    perspective_id: str,
) -> bool:
    """書き直してよい名前か — 読むときの判定（:func:`_is_machine_name`）より
    狭い。読むときは、間違えても表示が変わるだけで済む。書くときは、人の名前を
    消してしまう。だから「概念のキーや id と同じ」だけでは機械の名前と見なさ
    ず、自動でできたつながり（``auto_linked``）のときだけ、そう見なす
    （人が、わざとキーと同じ文字列を名前にすることはありうる）。"""
    if not name:
        return True
    if meta.get("name_auto") is True:
        return True
    if name == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME:
        return True
    if _IMPLEMENTATION_NAME_RE.match(name):
        return True
    if meta.get("auto_linked") is True:
        return name == perspective_id or name in {c.name for c in config.concepts}
    return False


def refresh_auto_name(
    registry_root: Path | str,
    perspective_id: str,
    field_label_of: FieldLabelOf,
    predicate_label_of: PredicateLabelOf,
) -> str | None:
    """機械が付けた名前を、いまの表示名で書き直す（ハブを作った・作り直した
    直後に呼ぶ）。書き直したらその名前、何もしなければ ``None``。

    読むときに直す（R1）だけでは、registry の ``name`` をそのまま読む場所
    （出どころ・材料表のデータセット名、カタログ、ほかの AI への一覧）に
    届かない。だから作るたびに ``name`` そのものを表示名にし、``name_auto``
    の印を残す（印があるかぎり R1 は読むたびに引き直し、次に作り直したとき
    もここで書き直す）。

    書く名前は、読むときの表示名（R1 の 2）と同じ計算にする — 2 つが食い違う
    と、画面によって同じつながりの名前が変わる。

    触らないもの: 人が付けた名前（:func:`_is_surely_machine_name` が偽）。
    参加している項目の表示名が 1 つも引けないつながり（概念のキーを直した
    だけの語で、機械の名前を上書きしても良くならない）。
    """
    meta = load_perspective_meta(registry_root, perspective_id)
    if not meta:
        return None
    config = crosswalk_runtime.load_config(registry_root, perspective_id)
    if config is None:
        return None
    raw_name = str(meta.get("name") or "").strip()
    if not _is_surely_machine_name(raw_name, meta, config, perspective_id):
        return None
    if not any(
        _resolved_participant_labels(concept, field_label_of, predicate_label_of)
        for concept in config.concepts
    ):
        return None
    labels: list[str] = []
    for concept in config.concepts:
        label = concept_display_name(concept, field_label_of, predicate_label_of)
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return None
    name = " / ".join(labels)
    if meta.get("name") == name and meta.get("name_auto") is True:
        return name
    meta["name"] = name
    meta["name_auto"] = True
    registry_id = crosswalk_runtime.crosswalk_registry_id(perspective_id)
    meta_path = Path(registry_root) / registry_id / _META_FILE
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return name
