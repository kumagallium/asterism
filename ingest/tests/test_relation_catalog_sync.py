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


def test_ui_builtin_catalog_is_generated_from_the_catalog() -> None:
    """ui の組み込みの目録（生成物）が、今の目録から作り直したものと一字一句同じ。

    R0c で ui の直書き（``SENTENCE``・``DRAFT_SHORT``・``RELATION_LABEL``・``RELATION_ORDER``・
    ``REL_KINDS``）を消し、ui はこの生成物から導く形にした。見張りは生成物 1 か所でよい。"""
    import importlib.util

    script = UI.parents[1] / "scripts" / "gen_relation_catalog_ts.py"
    spec = importlib.util.spec_from_file_location("gen_relation_catalog_ts", script)
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    have = (UI / "relations" / "builtinCatalog.ts").read_text(encoding="utf-8")
    assert "BUILTIN_CATALOG" in have
    # 古ければ python scripts/gen_relation_catalog_ts.py で作り直す
    assert have == gen.render()


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
    # 実験の関係は述語が 2 つでも同じ文（measuresProperty・measuresMaterial）なので両方を見る
    first_rule |= {rule.name for r in rc.RELATIONS if r.experiment for rule in r.predicates}
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
