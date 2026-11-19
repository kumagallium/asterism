"""写しと目録の同期（目録が正。写しがずれたら落ちる）。

ui の ts・json は Python からファイルを読んで突き合わせる。値を抜けた件数が 0 なら
落ちる（正規表現が外れて空振りの検査になるのを防ぐ）。R0c で ui が API の目録を使う形に
なったら、対応する写しの検査は消す。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
from pathlib import Path

from asterism import relation_catalog as rc

UI = Path(__file__).resolve().parents[2] / "ui" / "src"
LOCALES = UI / "i18n" / "locales"
LANGS = ("ja", "en")


def _read(path: Path) -> str:
    assert path.is_file(), f"写しのファイルが無い: {path}"
    return path.read_text(encoding="utf-8")


def _slice(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i : text.index(end, i)]


def _pairs(block: str) -> dict[str, str]:
    """``key: 'value'`` の並びを辞書に（値は単一引用符・中に ``'`` を含まない書式）。"""
    return dict(re.findall(r"\b(\w+):\s*'([^']*)'", block))


def _nonempty(d: dict, n: int | None = None) -> dict:
    assert d, "値を 1 つも抜けなかった（検査が空振りしている）"
    if n is not None:
        assert len(d) == n, f"抜けた件数が違う: {len(d)} != {n}"
    return d


def _norm(sentence: str) -> str:
    s = sentence.replace("{{subject}}", "{s}").replace("{{object}}", "{o}")
    s = s.replace("{{target}}", "{o}")
    return re.sub(r"[「」\"“”]", "", s)


def _pred_labels() -> dict[str, str]:
    """ui ``RELATION_LABEL`` のキー（``isA`` と述語）→ 下書きの関係名。"""
    out = {"isA": "sub", "measures": "measures"}
    for p, r in rc.pred_to_rel().items():
        if p not in ("subClassOf", "subPropertyOf", "equivalentClass", "equivalentProperty",
                     "hasQuantityKind"):  # fmt: skip
            out[p] = r
    return out


# --- ui の ts ---------------------------------------------------------------


def test_sentence_rows_SENTENCE_matches_the_catalog() -> None:
    text = _read(UI / "chat" / "sentenceRows.ts")
    body = _slice(text, "const SENTENCE = {", "} as const")
    for lang in LANGS:
        i = body.index(f"{lang}: {{")
        block = body[i : body.index("},", i)]
        got = _nonempty(_pairs(block), 12)
        assert got == {r.name: r.sentence[lang] for r in rc.RELATIONS}, lang


def test_map_model_DRAFT_SHORT_and_RELATION_LABEL_match_the_catalog() -> None:
    text = _read(UI / "chat" / "map" / "mapModel.ts")
    short = _slice(text, "const DRAFT_SHORT", "/** 下書き側の関係名の短い表示名")
    label = _slice(text, "const RELATION_LABEL", "/** 下書き側の関係名（sub・eq")
    for lang in LANGS:
        want = {r.name: r.label[lang] for r in rc.RELATIONS}
        i = short.index(f"  {lang}: {{")
        got = _nonempty(_pairs(short[i : short.index("}", i)]), 12)
        assert got == want, f"DRAFT_SHORT {lang}"
        i = label.index(f"  {lang}: {{")
        got = _nonempty(_pairs(label[i : label.index("},", i)]), 12)
        keymap = _pred_labels()
        assert set(got) == set(keymap), f"RELATION_LABEL {lang} のキー"
        for key, value in got.items():
            assert value == want[keymap[key]], f"RELATION_LABEL {lang} {key}"


def test_line_choice_RELATION_ORDER_is_the_predicate_order() -> None:
    text = _read(UI / "lineChoice.ts")
    body = _slice(text, "export const RELATION_ORDER = [", "] as const")
    got = re.findall(r"'(\w+)'", body)
    assert _nonempty({i: v for i, v in enumerate(got)}, 15)
    assert got == [p.name for p in rc.PREDICATES]


def test_theme_draft_REL_KINDS_and_RelKind_are_the_relation_names() -> None:
    text = _read(UI / "themes" / "themeDraft.ts")
    union_src = _slice(text, "export type RelKind =", "export const REL_KINDS")
    arr_src = _slice(text, "export const REL_KINDS", "export type RelationProblem")
    union = re.findall(r"'(\w+)'", union_src)
    arr = re.findall(r"'(\w+)'", arr_src)
    assert _nonempty({i: v for i, v in enumerate(union)}, 12)
    assert _nonempty({i: v for i, v in enumerate(arr)}, 12)
    assert union == arr == list(rc.relation_kinds())


# --- ui の json -------------------------------------------------------------


def _json(lang: str, name: str) -> dict:
    return json.loads(_read(LOCALES / lang / name))


def test_themes_json_rel_sentence_and_pill_title_match_the_catalog() -> None:
    for lang in LANGS:
        themes = _json(lang, "themes.json")
        sentence = _nonempty(
            {k: v for k, v in themes["rel"]["sentence"].items() if k != "other"}, 12
        )
        assert {k: _norm(v) for k, v in sentence.items()} == {
            r.name: r.sentence[lang] for r in rc.RELATIONS
        }, lang
        pill = _nonempty(themes["map"]["pillTitle"], 12)
        assert set(pill) == set(rc.relation_kinds()), lang
        for r in rc.RELATIONS:
            # pillTitle は短い表示名に「（細かい → 粗い）」などの補足がつくことがある
            assert pill[r.name].lower().startswith(r.label[lang].lower()), (lang, r.name)


#: 既に食い違っている所（R0a では直さない。どちらが正かは本体が決める）。
#: キー = (言語, 述語)。値 = decide.json の文を整えたもの。直したらこの表から消す。
KNOWN_DECIDE_DIFFERENCES = {
    # 目録・themes.json は「{s} と {o} は同じもの」
    ("ja", "equivalentClass"): "{s}は{o}と同じもの",
}


def test_decide_json_option_and_sentence_match_the_catalog() -> None:
    names = [p.name for p in rc.PREDICATES]
    first_rule = {rule.name for r in rc.RELATIONS for rule in r.predicates[:1]}
    owner = {rule.name: r for r in rc.RELATIONS for rule in r.predicates}
    seen_known: set[tuple[str, str]] = set()
    for lang in LANGS:
        decide = _json(lang, "decide.json")
        option = _nonempty(decide["option"])
        assert set(option) == set(names) | {"none"}, lang
        sentence = _nonempty(decide["sentence"], 15)
        assert set(sentence) == set(names), lang
        for name in first_rule:
            # 2 つ目の述語（subPropertyOf など）は「値は〜」と言い換えているので突き合わせない
            # 「」の有無と空白の違いは見ない（ja は「{s}」は「{o}」の一種 のように空白なし）
            got = re.sub(r"\s+", "", _norm(sentence[name]))
            if (lang, name) in KNOWN_DECIDE_DIFFERENCES:
                assert got == KNOWN_DECIDE_DIFFERENCES[(lang, name)], (lang, name)
                assert got != re.sub(r"\s+", "", owner[name].sentence[lang]), "直ったら表から消す"
                seen_known.add((lang, name))
                continue
            assert got == re.sub(r"\s+", "", owner[name].sentence[lang]), (lang, name)
    assert seen_known == set(KNOWN_DECIDE_DIFFERENCES)
