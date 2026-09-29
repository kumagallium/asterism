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
