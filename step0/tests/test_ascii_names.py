"""``ascii_names``: 英字にできない名前から一意な識別子を作る道具（契約メモ a・R1）。

同じ入力からは、いつも同じ符号が出る（乱数・時刻・列の順番を使わない）。
見本の期待値は次の段（api・ui）が同じ値をテストに固定するための正本 — ここで
値を変えたら interfaces も直すこと。
"""

from __future__ import annotations

from asterism_step0.ascii_names import loses_words, name_tag

# 契約メモ a・R4 が指定する5つの見本。期待値はこの実装で計算した値（正本）。
SAMPLES_NAME_TAG = {
    "食材の名前": "389a00",
    "店の名前": "98874d",
    "温度 (K)": "174149",
    "価格表": "165085",
    "Shelf Item": "3a8921",
}


def test_name_tag_five_samples_are_pinned() -> None:
    for text, expected in SAMPLES_NAME_TAG.items():
        assert name_tag(text) == expected


def test_name_tag_is_deterministic_across_calls() -> None:
    assert name_tag("食材の名前") == name_tag("食材の名前")


def test_name_tag_differs_for_different_japanese_names() -> None:
    assert name_tag("食材の名前") != name_tag("店の名前")


def test_name_tag_is_six_hex_digits() -> None:
    tag = name_tag("何でもいい文字列")
    assert len(tag) == 6
    int(tag, 16)  # 16進として読めること


def test_loses_words_true_for_kanji_and_kana() -> None:
    assert loses_words("食材の名前") is True
    assert loses_words("温度 (K)") is True  # 温度 が Lo


def test_loses_words_false_for_ascii_and_greek_letters() -> None:
    # μ (Ll) / Ω (Lu) は大文字小文字の区別を持つ文字体系 — 対象外。
    assert loses_words("Resistivity (μΩ·cm)") is False
    assert loses_words("Shelf Item") is False


def test_ascii_only_names_never_get_a_tag() -> None:
    """英字・数字・記号だけの名前は、英字を含まなくても今までの識別子のまま。"""
    from asterism_step0.skeleton_annotate import _ascii_map_name
    from asterism_step0.staged_propose import _class_name, _identifier

    for text in ("2024", "1", "0.5", "(1)", "", "  ", "---", "(", "_"):
        assert len(_identifier(text)) <= 6 and not _identifier(text).endswith(name_tag(text))
        assert not _class_name(text).endswith("_" + name_tag(text))
        assert _ascii_map_name(text, set(), "value") == "value"


def test_name_tag_strip_set_is_explicit_and_matches_typescript() -> None:
    """BOM は除く・NEL と制御文字は除かない（TS 版 asciiNames.test.ts と同じ見本）。"""
    assert name_tag("﻿温度") == name_tag("温度") == "703a58"
    assert name_tag("温度\x85") == "ad4161"
    assert name_tag("x\x1c") == "f562e4"
