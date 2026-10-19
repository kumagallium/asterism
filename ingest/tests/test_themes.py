"""asterism.themes — 「知りたいことから作る」(ui-redesign PR 4).

The store is a real ``rdflib.Dataset`` behind the same client shape ``test_shared_vocab``
uses, so minting, the alignment lines and the answering count run for real. The model is
never called here: an AI draft is a plain dict (what ``theme_propose`` validates).
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib
import yaml

from asterism import shared_vocab as sv
from asterism import themes
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import ALIGN_RELATIONS, ALIGNMENT_GRAPH
from asterism.shared_vocab import CQSpec
from asterism.themes import ThemeError

AT = "2026-10-19T00:00:00+00:00"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SKOS_ALT = "http://www.w3.org/2004/02/skos/core#altLabel"


class _Client:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    def triples(self, graph: str) -> set[tuple[str, str, str]]:
        g = self.ds.graph(rdflib.URIRef(graph))
        return {(str(s), str(p), str(o)) for s, p, o in g}


def _snapshot(client: _Client, root: Path) -> dict:
    """Everything a commit may write: both graphs, the CQ yaml, themes.json, wired.json."""
    vdir = root / "vocab-shared"

    def text(name: str) -> str | None:
        p = vdir / name
        return p.read_text(encoding="utf-8") if p.is_file() else None

    return {
        "shared": client.triples(sv.SHARED_VOCAB_GRAPH),
        "align": client.triples(ALIGNMENT_GRAPH),
        # an emptied file and a missing one say the same thing
        "tools": (yaml.safe_load(text("query_tools.yaml") or "tools: []") or {}).get("tools") or [],
        "themes": text("themes.json"),
        # ``at`` (a timestamp) is not state a commit has to restore
        "wired": {k: v for k, v in (json.loads(text("wired.json") or "{}")).items() if k != "at"},
    }


def _body(**over: object) -> dict:
    """A small confirmed draft: a new kind, a new item, one existing-standard kind."""
    body: dict = {
        "label": "電池材料",
        "lang": "ja",
        "ai_drafted": True,
        "questions": ["容量が最も大きいセルは？", "セルは何件？", "サイクル数ごとの推移は？"],
        "terms": [
            {
                "key": "t1",
                "label_ja": "電池セル",
                "label_en": "battery cell",
                "aliases": ["cell", "battery_cell"],
                "kind": "class",
                "use": "new",
                "slug": "battery_cell",
                "by": "ai",
            },
            {
                "key": "t2",
                "label_ja": "容量",
                "label_en": "capacity",
                "aliases": ["cap"],
                "kind": "property",
                "use": "new",
                "slug": "capacity",
                "by": "ai",
            },
            {
                "key": "t3",
                "label_ja": "正極材料",
                "label_en": "cathode material",
                "aliases": [],
                "kind": "class",
                "use": "new",
                "slug": "cathode_material",
                "by": "ai",
            },
        ],
        "relations": [{"subject": "t1", "object": "t3", "relation": "sub"}],
        "selections": [
            {"question": "容量が最も大きいセルは？", "op": "top", "kind": "t1", "property": "t2"},
            {"question": "セルは何件？", "op": "count", "kind": "t1", "property": ""},
            {"question": "サイクル数ごとの推移は？", "op": "trend", "kind": "t1", "property": "t2"},
        ],
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# slug — the same examples as ui/src/vocabQuestion.test.ts (slugFromText)
# ---------------------------------------------------------------------------


def test_slug_from_text_matches_the_ts_rule() -> None:
    assert themes.slug_from_text("Sample composition") == "sample_composition"
    assert themes.slug_from_text("hasSeebeckCoefficient") == "has_seebeck_coefficient"
    assert themes.slug_from_text("3D shape") == "x_3d_shape"
    assert themes.slug_from_text("試料") == ""
    assert themes.slug_from_text("battery cell") == "battery_cell"
    assert themes.slug_from_text("  Cycle  number!! ") == "cycle_number"
    assert themes.slug_from_text("XMLHttpRequest") == "xml_http_request"
    assert themes.slug_from_text("café") == "cafe"  # NFKD drops the accent
    assert themes.slug_from_text("") == ""


def test_new_theme_id_caps_length_and_avoids_reserved_words() -> None:
    long_id = themes.new_theme_id("Battery cathode material capacity relationship study", set())
    assert len(long_id) <= 40 and themes._ID_RE.match(long_id)
    # the cut id collides: the numbered one still fits
    again = themes.new_theme_id("Battery cathode material capacity relationship study", {long_id})
    assert again != long_id and len(again) <= 40 and again.endswith("_2")
    assert themes.new_theme_id("Term", set()) == "term_2"
    assert themes.new_theme_id("Drafts", {"drafts_2"}) == "drafts_3"
    assert themes.new_theme_id("日本語", set()) == "theme"


def test_create_draft_with_a_long_english_label(tmp_path: Path) -> None:
    d = themes.create_draft(
        tmp_path, "Battery cathode material capacity relationship study", ["q?"], at="t"
    )
    assert len(d["id"]) <= 40
    assert themes.create_draft(tmp_path, "Term", ["q?"], at="t")["id"] == "term_2"


def test_unique_slug_numbers_a_collision() -> None:
    assert themes.unique_slug("a", {"b"}) == "a"
    assert themes.unique_slug("a", {"a"}) == "a_2"
    assert themes.unique_slug("a", {"a", "a_2"}) == "a_3"


# ---------------------------------------------------------------------------
# aliases, provenance, execution sentence
# ---------------------------------------------------------------------------


async def test_mint_term_writes_aliases_and_ai_provenance(tmp_path: Path) -> None:
    c = _Client()
    out = await sv.mint_term(
        c,
        tmp_path,
        slug="battery_cell",
        kind="class",
        label="電池セル",
        label_en="battery cell",
        cqs=[CQSpec("セルは何件？", "count")],
        aliases=["cell", "Cell ", "battery_cell", "  "],
        drafted_by="ai",
        at=AT,
    )
    assert out["aliases"] == ["cell", "battery_cell"]  # trimmed, case-folded duplicates once
    g = c.triples(sv.SHARED_VOCAB_GRAPH)
    iri = sv.SV + "battery_cell"
    assert {o for s, p, o in g if s == iri and p == SKOS_ALT} == {"cell", "battery_cell"}
    assert (iri, XW + "draftedBy", "ai") in g
    assert any(s == iri and p == XW + "confirmedAt" for s, p, _ in g)
    rows = await sv.list_terms(c, tmp_path)
    assert rows[0]["aliases"] == ["battery_cell", "cell"]
    assert rows[0]["drafted_by"] == "ai" and rows[0]["confirmed_at"] == AT
    with pytest.raises(ValueError):
        await sv.mint_term(
            c, tmp_path, slug="x", kind="class", label="x", cqs=[CQSpec("q", "count")],
            drafted_by="human", at=AT,
        )  # fmt: skip


async def test_a_human_minted_term_has_no_draft_mark(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="a", kind="class", label="あ", cqs=[CQSpec("あは？", "count")], at=AT
    )
    g = c.triples(sv.SHARED_VOCAB_GRAPH)
    assert not any(p in (XW + "draftedBy", XW + "confirmedAt") for _, p, _ in g)
    row = (await sv.list_terms(c, tmp_path))[0]
    assert row["drafted_by"] is None and row["aliases"] == []


def test_fit_matches_an_alias_exactly_by_label_or_column_never_partially() -> None:
    t = sv.SharedTerm(
        sv.SV + "composition", "composition", "property", "組成", "composition", None, AT,
        aliases=("comp_str", "Formula"),
    )  # fmt: skip

    def fit(label: str, column: str) -> list[dict]:
        return sv.fit_candidates(
            label, column, shared_terms=[t], dataset_terms=[], standard_hits=[]
        )

    assert fit("", "comp_str")[0]["matched_by"] == "alias"  # the column name is an alias
    assert fit("", "comp_str")[0]["alias"] == "comp_str"
    assert fit("formula", "")[0]["matched_by"] == "alias"  # the meaning is an alias
    assert fit("formula", "")[0]["alias"] == "Formula"  # spelled as the person wrote it
    assert "alias" not in fit("組成", "x")[0]
    assert fit("組成", "x")[0]["matched_by"] == "label"  # the label still wins
    assert fit("", "comp") == []  # partial, never
    assert fit("", "comp_str_2") == []
    assert fit("", "x") == []


def test_describe_selection_is_deterministic_and_phrases_each_way() -> None:
    sel = {"op": "top", "kind": "電池セル", "property": "容量"}
    assert sv.describe_selection(sel) == (
        "電池セルのうち、容量がいちばん大きいものを出します（データセットごと）"
    )
    assert sv.describe_selection(sel, "en") == (
        "Gives the 電池セル with the largest 容量 (per dataset)."
    )
    assert sv.describe_selection({"op": "count", "kind": "電池セル"}) == (
        "電池セルの件数を数えます（データセットごと）"
    )
    assert sv.describe_selection({"op": "range", "property": "容量"}) == (
        "容量の最小と最大を出します（データセットごと）"
    )
    filt = {"op": "filter", "kind": "セル", "property": "正極", "value": "LFP"}
    assert sv.describe_selection(filt) == (
        "セルのうち、正極が「LFP」のものを出します（データセットごと）"
    )
    # not enough to phrase → empty (the screen says "pick a way of counting")
    assert sv.describe_selection({"op": "count"}) == ""
    assert sv.describe_selection({"op": "nonsense", "kind": "x"}) == ""
    assert sv.describe_selection({"op": "top", "kind": "x"}) == ""
    assert set(sv.SELECTION_OPS) >= set(themes.FITTING_OPS)


# ---------------------------------------------------------------------------
# first migration
# ---------------------------------------------------------------------------


async def test_migration_makes_one_first_theme_and_is_idempotent(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="a", kind="class", label="あ", cqs=[CQSpec("あは？", "count")], at=AT
    )
    await sv.mint_term(
        c, tmp_path, slug="b", kind="property", label="い", cqs=[CQSpec("いは？", "values")], at=AT
    )
    assert themes.load_themes_file(tmp_path) is None
    first = themes.ensure_migrated(tmp_path, at=AT)
    assert first["migrated"] is True
    assert [t["id"] for t in first["themes"]] == ["first"]
    assert first["themes"][0]["label"] == "はじめの問い"
    assert first["themes"][0]["questions"] == ["cq_a_count", "cq_b_values"]
    # a CQ that appears later does not make a second theme, and nothing is rewritten
    before = (tmp_path / "vocab-shared" / "themes.json").read_text(encoding="utf-8")
    await sv.mint_term(
        c, tmp_path, slug="c", kind="class", label="う", cqs=[CQSpec("うは？", "count")], at=AT
    )
    again = themes.ensure_migrated(tmp_path, at="2030-01-01T00:00:00+00:00")
    assert again == first
    assert (tmp_path / "vocab-shared" / "themes.json").read_text(encoding="utf-8") == before


def test_migration_with_no_cqs_writes_an_empty_themes_file(tmp_path: Path) -> None:
    out = themes.ensure_migrated(tmp_path, at=AT)
    assert out == {"themes": [], "migrated": True}
    assert json.loads((tmp_path / "vocab-shared" / "themes.json").read_text()) == out
    assert themes.ensure_migrated(tmp_path) == out


def test_migration_leaves_a_broken_themes_file_alone(tmp_path: Path) -> None:
    d = tmp_path / "vocab-shared"
    d.mkdir()
    (d / "themes.json").write_text("{not json", encoding="utf-8")
    assert themes.ensure_migrated(tmp_path)["migrated"] is False
    assert (d / "themes.json").read_text() == "{not json"


# ---------------------------------------------------------------------------
# drafts
# ---------------------------------------------------------------------------


def test_drafts_keep_the_wizard_where_it_was(tmp_path: Path) -> None:
    d = themes.create_draft(tmp_path, "電池材料", ["問い 1", "問い 1 ", "問い 2"], at=AT)
    assert d["id"] == "theme" and d["step"] == 1 and d["questions"] == ["問い 1", "問い 2"]
    d2 = themes.create_draft(tmp_path, "Battery cells", [], at=AT)
    assert d2["id"] == "battery_cells"
    d3 = themes.create_draft(tmp_path, "電池", [], at=AT)
    assert d3["id"] == "theme_2"
    working = {**d, "step": 3, "terms": [{"key": "t1"}], "updated_at": AT}
    saved = themes.save_draft(tmp_path, working)
    assert themes.load_draft(tmp_path, "theme") == saved
    assert [x["id"] for x in themes.list_drafts(tmp_path)] == ["battery_cells", "theme", "theme_2"]
    assert themes.delete_draft(tmp_path, "theme") is True
    assert themes.load_draft(tmp_path, "theme") is None
    assert themes.delete_draft(tmp_path, "theme") is False
    with pytest.raises(ThemeError):
        themes.save_draft(tmp_path, {**d, "step": 9})
    assert themes.load_draft(tmp_path, "../evil") is None  # an id is never a path


# ---------------------------------------------------------------------------
# post-process of an AI draft
# ---------------------------------------------------------------------------


def _ai(**over: object) -> dict:
    base: dict = {
        "terms": [],
        "relations": [],
        "selections": [],
        "suggested_questions": [],
    }
    base.update(over)
    return base


def _t(label_ja: str, label_en: str, kind: str, use: str = "new", aliases: list[str] | None = None):
    return {
        "label_ja": label_ja,
        "label_en": label_en,
        "aliases": aliases or [],
        "kind": kind,
        "use": use,
        "reason": "",
    }


def test_exact_match_with_an_existing_term_beats_the_models_use() -> None:
    existing = [
        sv.SharedTerm(
            sv.SV + "sample", "sample", "class", "試料", "sample", None, AT, aliases=("specimen",)
        )
    ]
    out = themes.postprocess_draft(
        _ai(
            terms=[
                _t("サンプル", "Specimen", "class", "new"),  # an alias of the existing term
                _t("試料", "something else", "class", "new"),  # its label
                _t("試料", "x", "property", "new"),  # same name, other kind → no match
            ]
        ),
        questions=[],
        existing=existing,
    )
    rows = {r["label_ja"]: r for r in out["terms"]}
    assert rows["サンプル"]["use"] == "existing:sample"
    assert rows["サンプル"]["match"] == {
        "kind": "existing", "ref": "sample", "label": "試料", "matched_by": "alias",
    }  # fmt: skip
    # the second 試料 is the same name as the first row → folded, so one row only
    assert [r["label_ja"] for r in out["terms"]].count("試料") == 1
    assert rows["サンプル"]["slug"] == "sample"


def test_exact_match_with_a_standard_term_and_a_quantity_kind() -> None:
    out = themes.postprocess_draft(
        _ai(
            terms=[
                _t("素材", "Material", "class"),
                _t("容量", "capacity", "property"),
                _t("電池セル", "battery cell", "class"),
            ]
        ),
        questions=[],
        existing=[],
    )
    rows = {r["label_ja"]: r for r in out["terms"]}
    assert rows["素材"]["use"].startswith("standard:")
    assert rows["素材"]["match"]["kind"] == "standard"
    assert rows["容量"]["use"] == "standard:quantitykind:Capacity"
    assert rows["容量"]["match"]["kind"] == "quantity"
    assert rows["電池セル"]["use"] == "new" and rows["電池セル"]["match"] is None
    assert rows["電池セル"]["slug"] == "battery_cell"
    assert set(out["standards"]) >= {"quantitykind:Capacity", rows["素材"]["use"][9:]}


def test_a_model_made_standard_or_slug_never_stands() -> None:
    existing = [sv.SharedTerm(sv.SV + "sample", "sample", "class", "試料", None, None, AT)]
    out = themes.postprocess_draft(
        _ai(
            terms=[
                _t("あ", "alpha one", "class", "standard:cmso:NoSuchThing"),
                _t("い", "beta two", "class", "existing:nothing"),
                _t("う", "gamma three", "property", "existing:sample"),  # sample is a class
            ]
        ),
        questions=[],
        existing=existing,
    )
    assert [r["use"] for r in out["terms"]] == ["new", "new", "new"]


def test_slugs_come_from_the_english_name_and_stay_unique() -> None:
    existing = [sv.SharedTerm(sv.SV + "cell", "cell", "class", "別", None, None, AT)]
    out = themes.postprocess_draft(
        _ai(
            terms=[
                _t("セル", "Cell", "class"),
                _t("セル2", "cell", "property"),
                _t("名無し", "", "class"),
                _t("別名から", "", "class", aliases=["col_name"]),
            ]
        ),
        questions=[],
        existing=existing,
    )
    assert [r["slug"] for r in out["terms"]] == ["cell_2", "cell_3", "", "col_name"]


def test_relations_are_checked_for_kind_cycle_and_quantity() -> None:
    out = themes.postprocess_draft(
        _ai(
            terms=[
                _t("甲", "alpha", "class"),
                _t("乙", "beta", "class"),
                _t("丙", "gamma", "property"),
            ],
            relations=[
                {"subject": "甲", "object": "乙", "relation": "sub"},
                {"subject": "乙", "object": "甲", "relation": "sub"},  # closes a loop
                {"subject": "甲", "object": "丙", "relation": "eq"},  # a kind and an item
                {"subject": "甲", "object": "quantitykind:Capacity", "relation": "quantity"},
                {"subject": "丙", "object": "quantitykind:Capacity", "relation": "quantity"},
                {"subject": "甲", "object": "甲", "relation": "sub"},
                {"subject": "甲", "object": "どこにもない", "relation": "sub"},  # dropped
                {"subject": "甲", "object": "乙", "relation": "sub"},  # a repeat → dropped
            ],
        ),
        questions=[],
        existing=[],
    )
    got = [(r["subject"], r["object"], r["relation"], r["problem"]) for r in out["relations"]]
    assert got == [
        ("t1", "t2", "sub", None),
        ("t2", "t1", "sub", "cycle"),
        ("t1", "t3", "eq", "mixed"),
        ("t1", "quantitykind:Capacity", "quantity", "quantity_target"),
        ("t3", "quantitykind:Capacity", "quantity", None),
        ("t1", "t1", "sub", "same"),
    ]


def test_selections_one_per_question_and_unfit_ops_become_memos() -> None:
    qs = ["最大の容量は？", "推移は？", "決められない問い", "言い換えられた問い"]
    out = themes.postprocess_draft(
        _ai(
            terms=[_t("セル", "cell", "class"), _t("容量", "cap", "property")],
            selections=[
                {"question": "最大の容量は？", "op": "top", "kind": "セル", "property": "容量",
                 "value": None, "fits": True, "reason": ""},
                {"question": "推移は？", "op": "trend", "kind": "セル", "property": "容量",
                 "value": None, "fits": True, "reason": "推移"},
                {"question": "決められない問い", "op": "count", "kind": "セル", "property": None,
                 "value": None, "fits": False, "reason": "型に合わない"},
                {"question": "勝手に書いた別の問い", "op": "count", "kind": "セル",
                 "property": None, "value": None, "fits": True, "reason": ""},
            ],
            suggested_questions=["最大の容量は？", "新しい提案"],
        ),
        questions=qs,
        existing=[],
    )  # fmt: skip
    s = out["selections"]
    assert [x["question"] for x in s] == qs
    assert (s[0]["op"], s[0]["kind"], s[0]["property"], s[0]["fits"], s[0]["memo"]) == (
        "top", "t1", "t2", True, None,
    )  # fmt: skip
    assert (s[1]["fits"], s[1]["memo"]) == (False, "op")  # no declarative tool for a trend yet
    assert (s[2]["fits"], s[2]["memo"]) == (False, "ai")
    assert (s[3]["op"], s[3]["memo"]) == ("", "none")  # the model did not answer this one
    assert out["suggested_questions"] == ["新しい提案"]


def test_two_terms_wanting_the_same_missing_parent_suggest_a_grouping_name() -> None:
    out = themes.postprocess_draft(
        _ai(
            terms=[
                _t("電池", "battery", "class"),
                _t("コンデンサ", "capacitor", "class"),
                _t("論文", "paper", "class"),
                _t("容量", "cap", "property"),
            ],
            relations=[
                {"subject": "電池", "object": "蓄電デバイス", "relation": "sub"},
                {"subject": "コンデンサ", "object": "蓄電デバイス", "relation": "sub"},
                {"subject": "論文", "object": "文書", "relation": "sub"},  # only one wants it
                {"subject": "容量", "object": "特性", "relation": "sub"},
            ],
        ),
        questions=[],
        existing=[],
    )
    assert out["relations"] == []  # nothing to point at yet
    assert out["groups"] == [{"label_ja": "蓄電デバイス", "children": ["t1", "t2"]}]


def test_an_existing_term_the_model_only_names_becomes_a_row() -> None:
    existing = [sv.SharedTerm(sv.SV + "sample", "sample", "class", "試料", "sample", None, AT)]
    out = themes.postprocess_draft(
        _ai(
            terms=[_t("電池セル", "battery cell", "class")],
            relations=[{"subject": "電池セル", "object": "試料", "relation": "sub"}],
        ),
        questions=[],
        existing=existing,
    )
    assert [r["use"] for r in out["terms"]] == ["new", "existing:sample"]
    assert out["relations"][0]["object"] == "t2"


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


async def test_commit_mints_lines_cqs_and_the_theme_in_one_operation(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _body(), at=AT)
    assert out["minted"] == ["battery_cell", "capacity", "cathode_material"]
    assert out["lines"] == 1 and out["memo"] == 1
    # the two answerable questions became CQs (the trend is a memo); the helper term got
    # a default one
    assert out["questions"] == ["cq_capacity_values", "cq_battery_cell_count"]
    theme = out["theme"]
    assert theme["id"] == "theme_2" or theme["id"] == "theme"
    assert [m["title"] for m in theme["memo_questions"]] == ["サイクル数ごとの推移は？"]
    assert theme["selections"]["cq_capacity_values"]["op"] == "top"
    assert theme["selections"]["cq_capacity_values"]["kind_slug"] == "battery_cell"

    tools = yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text())["tools"]
    titles = {t["name"]: t["title"] for t in tools}
    assert titles["cq_capacity_values"] == "容量が最も大きいセルは？"
    assert titles["cq_battery_cell_count"] == "セルは何件？"
    assert titles["cq_cathode_material_count"] == "正極材料は、どのデータセットに何件あるか"
    assert "cq_battery_cell_values" not in titles

    terms = {t["slug"]: t for t in await sv.list_terms(c, tmp_path)}
    assert terms["battery_cell"]["aliases"] == ["battery_cell", "cell"]
    assert terms["battery_cell"]["drafted_by"] == "ai"
    iri, parent = sv.SV + "battery_cell", sv.SV + "cathode_material"
    assert (iri, ALIGN_RELATIONS["subClassOf"], parent) in c.triples(ALIGNMENT_GRAPH)
    saved = themes.load_themes_file(tmp_path)
    assert saved is not None and saved["themes"][-1]["id"] == theme["id"]
    assert (tmp_path / "vocab-shared" / "wired.json").is_file()


async def test_commit_ties_a_new_term_to_its_standard_and_quantity_kind(tmp_path: Path) -> None:
    c = _Client()
    material = themes.std_exact("", "Material", [], "class")
    assert material is not None
    body = _body(
        terms=[
            {"key": "t1", "label_ja": "素材", "label_en": "material", "kind": "class",
             "use": f"standard:{material.curie}", "slug": "material"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "kind": "property",
             "use": "standard:quantitykind:Capacity", "slug": "capacity"},
        ],
        relations=[],
        selections=[],
        questions=[],
        ai_drafted=False,
    )  # fmt: skip
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["lines"] == 2
    align = c.triples(ALIGNMENT_GRAPH)
    assert (sv.SV + "material", ALIGN_RELATIONS["equivalentClass"], material.iri) in align
    assert (
        sv.SV + "capacity",
        ALIGN_RELATIONS["hasQuantityKind"],
        "http://qudt.org/vocab/quantitykind/Capacity",
    ) in align
    rows = {t["slug"]: t for t in await sv.list_terms(c, tmp_path)}
    assert rows["material"]["drafted_by"] is None  # a person's own, not an AI draft


async def test_commit_uses_an_existing_term_without_minting_it(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="sample", kind="class", label="試料",
        cqs=[CQSpec("試料は何件？", "count")], at=AT,
    )  # fmt: skip
    before_terms = c.triples(sv.SHARED_VOCAB_GRAPH)
    body = _body(
        terms=[
            {"key": "t1", "label_ja": "試料", "label_en": "", "kind": "class",
             "use": "existing:sample", "slug": "sample"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "kind": "property",
             "use": "new", "slug": "capacity"},
        ],
        relations=[{"subject": "t2", "object": "t1", "relation": "sub"}],  # item under a kind
        selections=[{"question": "試料の数は？", "op": "count", "kind": "t1", "property": ""}],
        questions=["試料の数は？"],
    )  # fmt: skip
    with pytest.raises(ThemeError, match="種類と項目"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    body["relations"] = []
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["minted"] == ["capacity"]
    assert out["questions"] == ["cq_sample_count_2"]  # appended to the existing term's CQs
    assert c.triples(sv.SHARED_VOCAB_GRAPH) >= before_terms
    tools = yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text())["tools"]
    assert {t["name"] for t in tools} >= {"cq_sample_count", "cq_sample_count_2"}


async def test_a_line_from_an_existing_term_is_refused(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="sample", kind="class", label="試料",
        cqs=[CQSpec("試料は何件？", "count")], at=AT,
    )  # fmt: skip
    body = _body(
        terms=[
            {"key": "t1", "label_ja": "試料", "label_en": "", "kind": "class",
             "use": "existing:sample", "slug": "sample"},
            {"key": "t2", "label_ja": "甲", "label_en": "alpha", "kind": "class",
             "use": "new", "slug": "alpha"},
        ],
        relations=[{"subject": "t1", "object": "t2", "relation": "sub"}],
        selections=[],
        questions=[],
    )  # fmt: skip
    with pytest.raises(ThemeError, match="既にある用語"):
        await themes.commit_theme(c, tmp_path, body, at=AT)


def _row(**over: object) -> dict:
    return {"key": "t1", "label_ja": "あ", "kind": "class", **over}


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"label": " "}, "名前が空"),
        ({"terms": [_row(use="new", slug="Bad Slug")]}, "ID"),
        ({"terms": [_row(use="standard:cmso:Nope", slug="a")]}, "標準の語"),
        ({"terms": [_row(use="existing:ghost", slug="a")]}, "既にある用語"),
        ({"relations": [{"subject": "t1", "object": "t1", "relation": "sub"}]}, "自分自身"),
        ({"relations": [{"subject": "t1", "object": "nobody", "relation": "sub"}]}, "相手"),
        ({"relations": [{"subject": "t1", "object": "t2", "relation": "sub"}]}, "種類と項目"),
        ({"relations": [{"subject": "t1", "object": "t3", "relation": "quantity"}]}, "量"),
        (
            {
                "relations": [
                    {"subject": "t1", "object": "t3", "relation": "sub"},
                    {"subject": "t3", "object": "t1", "relation": "sub"},
                ]
            },
            "循環",
        ),
    ],
)  # fmt: skip
async def test_a_refused_commit_writes_nothing(tmp_path: Path, patch: dict, match: str) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    before = _snapshot(c, tmp_path)
    with pytest.raises(ThemeError, match=match):
        await themes.commit_theme(c, tmp_path, _body(**patch), at=AT)
    assert _snapshot(c, tmp_path) == before


async def test_a_taken_slug_is_a_conflict_and_nothing_is_minted(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="capacity", kind="property", label="既にある容量",
        cqs=[CQSpec("容量は？", "values")], at=AT,
    )  # fmt: skip
    before = _snapshot(c, tmp_path)
    with pytest.raises(ThemeError) as err:
        await themes.commit_theme(c, tmp_path, _body(), at=AT)
    assert err.value.status == 409
    assert _snapshot(c, tmp_path) == before


async def test_two_new_terms_with_one_slug_are_refused(tmp_path: Path) -> None:
    c = _Client()
    body = _body()
    body["terms"][2]["slug"] = "battery_cell"
    with pytest.raises(ThemeError) as err:
        await themes.commit_theme(c, tmp_path, body, at=AT)
    assert err.value.status == 409


async def test_commit_is_atomic_a_failing_line_takes_the_mints_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    body = _body(
        terms=[
            *_body()["terms"],
            {"key": "t4", "label_ja": "電極", "label_en": "electrode", "kind": "class",
             "use": "new", "slug": "electrode"},
        ],
        relations=[
            {"subject": "t1", "object": "t3", "relation": "sub"},
            {"subject": "t3", "object": "t4", "relation": "sub"},
        ],
    )  # fmt: skip
    before = _snapshot(c, tmp_path)
    real = themes.assert_alignment
    calls = {"n": 0}

    async def flaky(*a: object, **k: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 2:  # the first line is written, the second one fails
            raise RuntimeError("store went away")
        return await real(*a, **k)

    monkeypatch.setattr(themes, "assert_alignment", flaky)
    with pytest.raises(RuntimeError, match="store went away"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    assert calls["n"] == 2
    after = _snapshot(c, tmp_path)
    assert after["shared"] == before["shared"] == set()  # no mint left without its lines
    assert after["align"] == before["align"]
    assert after["themes"] == before["themes"]
    assert after["tools"] == []
    # and the same body goes through once the store is back
    monkeypatch.setattr(themes, "assert_alignment", real)
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["lines"] == 2 and len(out["minted"]) == 4


async def test_commit_failing_at_the_theme_file_also_takes_back_the_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    before = _snapshot(c, tmp_path)

    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(themes, "_save_themes", boom)
    with pytest.raises(OSError, match="disk full"):
        await themes.commit_theme(c, tmp_path, _body(), at=AT)
    assert _snapshot(c, tmp_path) == before


async def test_commit_deletes_the_draft_it_came_from(tmp_path: Path) -> None:
    c = _Client()
    d = themes.create_draft(tmp_path, "Battery", ["セルは何件？"], at=AT)
    out = await themes.commit_theme(c, tmp_path, _body(id=d["id"], label="Battery"), at=AT)
    assert out["theme"]["id"] == d["id"]
    assert themes.load_draft(tmp_path, d["id"]) is None
    with pytest.raises(ThemeError) as err:  # the id is now a theme
        await themes.commit_theme(c, tmp_path, _body(id=d["id"], label="Battery"), at=AT)
    assert err.value.status == 409


async def test_questions_can_be_added_to_an_existing_theme_by_a_second_commit(
    tmp_path: Path,
) -> None:
    c = _Client()
    first = await themes.commit_theme(c, tmp_path, _body(id="battery"), at=AT)
    assert first["theme"]["id"] == "battery"
    body = _body(
        id="battery_more",
        extend="battery",
        questions=["容量が最小のセルは？"],
        terms=[
            {"key": "t1", "label_ja": "電池セル", "label_en": "battery cell", "kind": "class",
             "use": "existing:battery_cell", "slug": "battery_cell"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "kind": "property",
             "use": "existing:capacity", "slug": "capacity"},
        ],
        relations=[],
        selections=[
            {"question": "容量が最小のセルは？", "op": "range", "kind": "t1", "property": "t2"}
        ],
    )  # fmt: skip
    out = await themes.commit_theme(c, tmp_path, body, at="2026-10-20T00:00:00+00:00")
    assert out["minted"] == []
    saved = themes.load_themes_file(tmp_path)
    assert saved is not None
    assert [t["id"] for t in saved["themes"]] == ["battery"]  # no second theme, same place
    theme = saved["themes"][0]
    assert theme["label"] == "電池材料" and theme["created_at"] == AT
    assert theme["questions"] == [
        "cq_capacity_values",
        "cq_battery_cell_count",
        "cq_capacity_values_2",
    ]
    assert [m["title"] for m in theme["memo_questions"]] == ["サイクル数ごとの推移は？"]
    with pytest.raises(ThemeError) as err:
        await themes.commit_theme(c, tmp_path, _body(id="x", extend="ghost"), at=AT)
    assert err.value.status == 404


async def test_removing_a_theme_keeps_its_terms_lines_and_questions(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _body(), at=AT)
    before_graph = c.triples(sv.SHARED_VOCAB_GRAPH)
    before_align = c.triples(ALIGNMENT_GRAPH)
    before_tools = (tmp_path / "vocab-shared" / "query_tools.yaml").read_text()
    themes.remove_theme(tmp_path, out["theme"]["id"])
    saved = themes.load_themes_file(tmp_path)
    assert saved is not None and out["theme"]["id"] not in {t["id"] for t in saved["themes"]}
    assert c.triples(sv.SHARED_VOCAB_GRAPH) == before_graph
    assert c.triples(ALIGNMENT_GRAPH) == before_align
    assert (tmp_path / "vocab-shared" / "query_tools.yaml").read_text() == before_tools
    with pytest.raises(ThemeError) as err:
        themes.remove_theme(tmp_path, "ghost")
    assert err.value.status == 404


async def test_questions_can_be_added_to_a_theme_later(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="a", kind="class", label="あ", cqs=[CQSpec("あは？", "count")], at=AT
    )
    themes.ensure_migrated(tmp_path, at=AT)
    t = themes.add_questions_to_theme(
        tmp_path, "first", ["cq_a_count"], {"cq_a_count": {"op": "count", "kind": "あ"}}
    )
    assert t["questions"] == ["cq_a_count"]  # already there: idempotent
    with pytest.raises(ThemeError):
        themes.add_questions_to_theme(tmp_path, "first", ["nope"])
    with pytest.raises(ThemeError) as err:
        themes.add_questions_to_theme(tmp_path, "ghost", ["cq_a_count"])
    assert err.value.status == 404


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------


async def test_template_files_are_header_rows_per_kind(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _body(), at=AT)
    rows = await sv.list_terms(c, tmp_path)
    tools = sv.read_registry_raw_tools(tmp_path, sv.REGISTRY_ID) or []
    files = themes.template_files(out["theme"], rows, tools)
    assert files == {
        "battery_cell.csv": ["battery_cell_id", "capacity"],
        "cathode_material.csv": ["cathode_material_id"],  # needed by the theme (a default question)
    }
    # a theme without recorded selections: kinds get an id column, items go to other.csv
    legacy = {"questions": ["cq_battery_cell_count", "cq_capacity_values"]}
    assert themes.template_files(legacy, rows, tools) == {
        "battery_cell.csv": ["battery_cell_id"],
        "other.csv": ["id", "capacity"],
    }
