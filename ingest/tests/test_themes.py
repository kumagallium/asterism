"""asterism.themes — 「知りたいことから作る」(ui-redesign PR 4・PR 6a v2).

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
from asterism.crosswalk_runtime import (
    ALIGN_RELATIONS,
    ALIGNMENT_GRAPH,
    assert_alignment,
    list_alignments,
)
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
    """Everything a commit may write: both graphs, the CQ yaml, themes.json, wired/knowledge."""
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
        "knowledge": {
            k: v for k, v in (json.loads(text("knowledge.json") or "{}")).items() if k != "at"
        },
    }


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


def _concept(key: str, ja: str, en: str, kind: str, use: str = "new", slug: str = "", **kw: object):
    return {
        "key": key, "label_ja": ja, "label_en": en, "aliases": [], "kind": kind, "use": use,
        "slug": slug, "by": "ai", **kw,
    }  # fmt: skip


Q_LOWER = "熱伝導率を下げる要素になり得るのは？"


def _rel(frm: str, rel: str, to: str, reason: str) -> dict:
    return {"from": frm, "rel": rel, "to": to, "reason": reason, "by": "ai"}


def _heat(**over: object) -> dict:
    """設計 §15 の例: 問い「熱伝導率を下げる要素は？」→ 熱伝導率・格子/電子（一部）・粒界散乱/
    点欠陥（格子を下げる）・キャリア濃度（電子を上げる）。知識の問い 1 つ・データの問いは無い。"""
    body: dict = {
        "label": "熱電材料",
        "lang": "ja",
        "ai_drafted": True,
        "batch": "batch-1",
        "questions": [Q_LOWER],
        "concepts": [
            _concept("c1", "熱伝導率", "Thermal Conductivity", "property",
                     "standard:quantitykind:ThermalConductivity", "thermal_conductivity"),
            _concept("c2", "格子熱伝導率", "lattice thermal conductivity", "property",
                     slug="lattice_tc"),
            _concept("c3", "電子熱伝導率", "electronic thermal conductivity", "property",
                     slug="electronic_tc"),
            _concept("c4", "粒界散乱", "grain boundary scattering", "class", slug="grain_boundary"),
            _concept("c5", "点欠陥散乱", "point defect scattering", "class", slug="point_defect"),
            _concept("c6", "キャリア濃度", "carrier concentration", "property",
                     slug="carrier_conc"),
        ],
        "relations": [
            _rel("c2", "part", "c1", "熱伝導率 = 格子 + 電子"),
            _rel("c3", "part", "c1", "熱伝導率 = 格子 + 電子"),
            _rel("c4", "lowers", "c2", "粒界でフォノンが散乱"),
            _rel("c5", "lowers", "c2", "質量ゆらぎ"),
            _rel("c6", "raises", "c3", "Wiedemann-Franz"),
        ],
        "answers": [
            {"question": Q_LOWER, "mode": "knowledge", "op": "factors", "target": "c1",
             "direction": "lowers", "wanted_data": "試料ごとの 粒径・熱伝導率 の表"},
        ],
    }  # fmt: skip
    body.update(over)
    return body


def _cell(**over: object) -> dict:
    """データで答える小さな例（AI を使わず手で入れた概念だけ）: 種類・項目・2 つのデータの問い。"""
    body: dict = {
        "label": "電池材料",
        "lang": "ja",
        "ai_drafted": False,
        "questions": ["容量が最も大きいセルは？", "セルは何件？", "サイクル数ごとの推移は？"],
        "concepts": [
            {"key": "t1", "label_ja": "電池セル", "label_en": "battery cell", "aliases": ["cell"],
             "kind": "class", "use": "new", "slug": "battery_cell"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "aliases": [],
             "kind": "property", "use": "new", "slug": "capacity"},
            {"key": "t3", "label_ja": "正極材料", "label_en": "cathode material", "aliases": [],
             "kind": "class", "use": "new", "slug": "cathode_material"},
        ],
        "relations": [{"from": "t1", "rel": "sub", "to": "t3", "reason": ""}],
        "answers": [
            {"question": "容量が最も大きいセルは？", "mode": "data", "op": "top",
             "kind": "t1", "property": "t2"},
            {"question": "セルは何件？", "mode": "data", "op": "count", "kind": "t1"},
            {"question": "サイクル数ごとの推移は？", "mode": "memo", "op": "trend",
             "wanted_data": "サイクルごとの容量の表"},
        ],
    }  # fmt: skip
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# drafts
# ---------------------------------------------------------------------------


def test_drafts_keep_the_wizard_where_it_was(tmp_path: Path) -> None:
    d = themes.create_draft(tmp_path, "電池材料", ["問い 1", "問い 1 ", "問い 2"], at=AT)
    assert d["id"] == "theme" and d["step"] == 1 and d["questions"] == ["問い 1", "問い 2"]
    assert d["version"] == 2 and d["concepts"] == [] and d["answers"] == []
    d2 = themes.create_draft(tmp_path, "Battery cells", [], at=AT)
    assert d2["id"] == "battery_cells"
    d3 = themes.create_draft(tmp_path, "電池", [], at=AT)
    assert d3["id"] == "theme_2"
    working = {**d, "step": 3, "concepts": [{"key": "c1"}], "updated_at": AT}
    saved = themes.save_draft(tmp_path, working)
    assert themes.load_draft(tmp_path, "theme") == saved
    assert [x["id"] for x in themes.list_drafts(tmp_path)] == ["battery_cells", "theme", "theme_2"]
    assert themes.delete_draft(tmp_path, "theme") is True
    assert themes.load_draft(tmp_path, "theme") is None
    assert themes.delete_draft(tmp_path, "theme") is False
    with pytest.raises(ThemeError):
        themes.save_draft(tmp_path, {**d, "step": 4})  # three steps now
    with pytest.raises(ThemeError):
        themes.save_draft(tmp_path, {**d, "step": 0})
    assert themes.load_draft(tmp_path, "../evil") is None  # an id is never a path


def test_an_empty_theme_name_is_the_first_20_characters_of_the_first_question(
    tmp_path: Path,
) -> None:
    q = "熱伝導率を下げる要素になり得るのは何ですか、教えてください"
    d = themes.create_draft(tmp_path, "", [q], at=AT)
    assert d["label"] == q[:20]
    with pytest.raises(ThemeError):  # neither a name nor a question
        themes.create_draft(tmp_path, " ", [], at=AT)
    assert themes.default_label(["  ", "短い問い"]) == "短い問い"


def test_the_models_raw_output_is_never_stored_in_a_draft(tmp_path: Path) -> None:
    d = themes.create_draft(tmp_path, "熱電", ["q?"], at=AT)
    saved = themes.save_draft(
        tmp_path, {**d, "ai": {"concepts": [{"label_ja": "生の出力"}]}, "terms": [1]}
    )
    assert "ai" not in saved and "terms" not in saved
    on_disk = (tmp_path / "vocab-shared" / "themes" / d["id"] / "draft.json").read_text()
    assert "生の出力" not in on_disk


def test_a_v1_draft_is_dropped_not_migrated(tmp_path: Path) -> None:
    """A six-step draft (``terms`` / ``selections`` / the raw ``ai``) reads as no draft."""
    folder = tmp_path / "vocab-shared" / "themes" / "old"
    folder.mkdir(parents=True)
    (folder / "draft.json").write_text(
        json.dumps(
            {
                "id": "old", "label": "古い", "step": 5, "questions": ["q"], "ai": {"terms": []},
                "terms": [{"key": "t1"}], "relations": [], "selections": [], "updated_at": AT,
            }
        ),
        encoding="utf-8",
    )  # fmt: skip
    assert themes.load_draft(tmp_path, "old") is None
    assert themes.list_drafts(tmp_path) == []
    # a new draft may take the id; the stale file is simply replaced
    again = themes.create_draft(tmp_path, "old", ["q"], at=AT)
    assert again["id"] == "old" and again["version"] == 2


def test_sky_is_not_an_id_a_theme_or_a_draft_can_take() -> None:
    assert themes.new_theme_id("sky", set()) == "sky_2"


# ---------------------------------------------------------------------------
# post-process of an AI draft (v2)
# ---------------------------------------------------------------------------


def _ai(**over: object) -> dict:
    base: dict = {
        "theme_label": "",
        "concepts": [],
        "relations": [],
        "answers": [],
        "suggested_questions": [],
    }
    base.update(over)
    return base


def _t(
    key: str, label_ja: str, label_en: str, kind: str, use: str = "new",
    aliases: list[str] | None = None,
):  # fmt: skip
    return {
        "key": key,
        "label_ja": label_ja,
        "label_en": label_en,
        "aliases": aliases or [],
        "kind": kind,
        "use": use,
        "reason": "",
    }


def _r(frm: str, rel: str, to: str, reason: str = "") -> dict:
    return {"from": frm, "rel": rel, "to": to, "reason": reason}


def _a(question: str, mode: str, **kw: object) -> dict:
    base = {
        "question": question, "mode": mode, "op": None, "target": None, "direction": None,
        "kind": None, "property": None, "wanted_data": "", "reason": "",
    }  # fmt: skip
    return {**base, **kw}


def test_exact_match_with_an_existing_term_beats_the_models_use() -> None:
    existing = [
        sv.SharedTerm(
            sv.SV + "sample", "sample", "class", "試料", "sample", None, AT, aliases=("specimen",)
        )
    ]
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("a", "サンプル", "Specimen", "class", "new"),  # an alias of the existing term
                _t("b", "試料", "something else", "class", "new"),  # its label
                _t("c", "試料", "x", "property", "new"),  # same name, other kind → no match
            ]
        ),
        questions=[],
        existing=existing,
    )
    rows = {r["label_ja"]: r for r in out["concepts"]}
    assert rows["サンプル"]["use"] == "existing:sample" and rows["サンプル"]["key"] == "a"
    assert rows["サンプル"]["match"] == {
        "kind": "existing", "ref": "sample", "label": "試料", "matched_by": "alias",
    }  # fmt: skip
    # the second 試料 is the same name as the first row → folded, so one row only
    assert [r["label_ja"] for r in out["concepts"]].count("試料") == 1
    assert rows["サンプル"]["slug"] == "sample"


def test_exact_match_with_a_standard_term_and_a_quantity_kind() -> None:
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("c1", "素材", "Material", "class"),
                _t("c2", "容量", "capacity", "property"),
                _t("c3", "電池セル", "battery cell", "class"),
            ]
        ),
        questions=[],
        existing=[],
    )
    rows = {r["label_ja"]: r for r in out["concepts"]}
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
            concepts=[
                _t("a", "あ", "alpha one", "class", "standard:cmso:NoSuchThing"),
                _t("b", "い", "beta two", "class", "existing:nothing"),
                _t("c", "う", "gamma three", "property", "existing:sample"),  # a class
            ]
        ),
        questions=[],
        existing=existing,
    )
    assert [r["use"] for r in out["concepts"]] == ["new", "new", "new"]


def test_slugs_come_from_the_english_name_and_stay_unique_and_unreserved() -> None:
    existing = [sv.SharedTerm(sv.SV + "cell", "cell", "class", "別", None, None, AT)]
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("a", "セル", "Cell", "class"),
                _t("b", "セル2", "cell", "property"),
                _t("c", "名無し", "", "class"),
                _t("d", "別名から", "", "class", aliases=["col_name"]),
                _t("e", "部分", "part of", "class"),  # a reserved slug is never used
                _t("f", "下げる", "Lowers", "class"),
            ]
        ),
        questions=[],
        existing=existing,
    )
    slugs = [r["slug"] for r in out["concepts"]]
    assert slugs[:2] == ["cell_2", "cell_3"]
    assert slugs[2] == "t3"  # no English name: a placeholder, never ""
    assert slugs[3] == "col_name"
    assert slugs[4] == "part_of_2" and slugs[5] == "lowers_2"
    assert not set(slugs) & sv.RESERVED_SLUGS


def test_relations_are_checked_per_predicate_for_kind_and_cycle() -> None:
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("a", "甲", "alpha", "class"),
                _t("b", "乙", "beta", "class"),
                _t("p", "丙", "gamma", "property"),
                _t("q", "丁", "delta", "property"),
            ],
            relations=[
                _r("a", "sub", "b"),
                _r("b", "sub", "a"),  # closes a sub loop
                _r("a", "eq", "p"),  # a kind and an item
                _r("a", "quantity", "quantitykind:Capacity"),  # a kind has no quantity
                _r("p", "quantity", "quantitykind:Capacity"),
                _r("a", "sub", "a"),
                _r("a", "sub", "どこにもない"),  # dropped
                _r("a", "sub", "b"),  # a repeat → dropped
                _r("a", "part", "p"),  # part joins the same kind of thing
                _r("p", "part", "q"),
                _r("q", "part", "p"),  # closes a part loop
                _r("a", "lowers", "b"),  # to must be a property
                _r("a", "lowers", "p"),  # a class may lower a property
                _r("p", "raises", "q"),
                _r("q", "raises", "p"),  # feedback is legitimate
                _r("q", "affects", "quantitykind:Capacity"),  # a quantity kind is no property
            ],
        ),
        questions=[],
        existing=[],
    )
    got = [(r["from"], r["rel"], r["to"], r["problem"]) for r in out["relations"]]
    assert got == [
        ("a", "sub", "b", None),
        ("b", "sub", "a", "cycle"),
        ("a", "eq", "p", "mixed"),
        ("a", "quantity", "quantitykind:Capacity", "quantity_target"),
        ("p", "quantity", "quantitykind:Capacity", None),
        ("a", "sub", "a", "same"),
        ("a", "part", "p", "mixed"),
        ("p", "part", "q", None),
        ("q", "part", "p", "cycle"),
        ("a", "lowers", "b", "target_not_property"),
        ("a", "lowers", "p", None),
        ("p", "raises", "q", None),
        ("q", "raises", "p", None),
        ("q", "affects", "quantitykind:Capacity", "target_not_property"),
    ]


def test_answers_one_per_question_knowledge_data_and_memo() -> None:
    qs = ["下げるものは？", "一部は？", "最大の容量は？", "推移は？", "向き無し", "言い換え"]
    qs.append("無回答")
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("c1", "熱伝導率", "tc", "property"),
                _t("c2", "セル", "cell", "class"),
                _t("c3", "容量", "cap", "property"),
            ],
            answers=[
                _a(qs[0], "knowledge", op="factors", target="c1", direction="lowers",
                   wanted_data="試料ごとの粒径の表", reason="要因"),
                _a(qs[1], "knowledge", op="parts", target="c1", direction="lowers"),
                _a(qs[2], "data", op="top", kind="c2", property="c3"),
                _a(qs[3], "memo", op="trend", kind="c2", property="c3"),
                _a(qs[4], "knowledge", op="factors", target="c1"),  # factors need a direction
                _a("勝手に書いた別の問い", "data", op="count", kind="c2"),
            ],
            suggested_questions=["下げるものは？", "新しい提案"],
        ),
        questions=qs,
        existing=[],
    )  # fmt: skip
    a = {x["question"]: x for x in out["answers"]}
    assert [x["question"] for x in out["answers"]] == qs
    f = a[qs[0]]
    assert (f["mode"], f["op"], f["target"], f["direction"], f["memo"]) == (
        "knowledge", "factors", "c1", "lowers", None,
    )  # fmt: skip
    assert f["wanted_data"] == "試料ごとの粒径の表" and f["reason"] == "要因"
    p = a[qs[1]]
    assert (p["mode"], p["op"], p["direction"]) == ("knowledge", "parts", "")  # not for parts
    t = a[qs[2]]
    assert (t["mode"], t["kind"], t["property"], t["memo"]) == ("data", "c2", "c3", None)
    assert (a[qs[3]]["mode"], a[qs[3]]["memo"]) == ("memo", "op")  # no way to run a trend yet
    assert (a[qs[4]]["mode"], a[qs[4]]["memo"]) == ("memo", "ai")
    assert (a[qs[5]]["mode"], a[qs[5]]["memo"]) == ("memo", "none")  # the model skipped it
    assert a[qs[6]]["memo"] == "none"
    assert out["suggested_questions"] == ["新しい提案"]


def test_what_the_person_dismissed_before_is_dropped() -> None:
    dismissed = [
        {"subject": sv.concept_subject("ナノ析出物"), "term": sv.CONCEPT_DISMISS_TERM, "at": AT},
        {
            "subject": sv.concept_subject("粒界散乱"),
            "term": sv.concept_subject("格子熱伝導率"),
            "relation": "lowers",
            "at": AT,
        },
    ]
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("c1", "格子熱伝導率", "lattice tc", "property"),
                _t("c2", "粒界散乱", "grain boundary", "class"),
                _t("c3", "ナノ析出物", "nano precipitate", "class"),
            ],
            relations=[
                _r("c2", "lowers", "c1"),  # dismissed as a relation
                _r("c3", "lowers", "c1"),  # its concept is dismissed → the line goes too
                _r("c2", "part", "c3"),
            ],
        ),
        questions=[],
        existing=[],
        dismissed=dismissed,
    )
    assert [r["label_ja"] for r in out["concepts"]] == ["格子熱伝導率", "粒界散乱"]
    assert out["relations"] == []
    assert out["dropped"]["concepts"] == ["ナノ析出物"]
    assert {r["rel"] for r in out["dropped"]["relations"]} == {"lowers", "part"}
    # an existing term of that name is never "dropped": the dismissal is of NEW concepts
    existing = [sv.SharedTerm(sv.SV + "nano", "nano", "class", "ナノ析出物", None, None, AT)]
    kept = themes.postprocess_draft(
        _ai(concepts=[_t("c3", "ナノ析出物", "x", "class")]),
        questions=[],
        existing=existing,
        dismissed=dismissed,
    )
    assert [r["use"] for r in kept["concepts"]] == ["existing:nano"]


def test_an_existing_term_the_model_only_names_becomes_a_row() -> None:
    existing = [sv.SharedTerm(sv.SV + "sample", "sample", "class", "試料", "sample", None, AT)]
    out = themes.postprocess_draft(
        _ai(
            concepts=[_t("c1", "電池セル", "battery cell", "class")],
            relations=[_r("c1", "sub", "試料")],  # by name, not by key
        ),
        questions=[],
        existing=existing,
    )
    assert [r["use"] for r in out["concepts"]] == ["new", "existing:sample"]
    assert out["relations"][0]["to"] == out["concepts"][1]["key"]


def test_expanding_adds_only_what_is_new_and_never_reuses_a_key() -> None:
    base = {
        "concepts": [
            {"key": "c1", "label_ja": "熱伝導率", "label_en": "tc", "kind": "property",
             "slug": "tc", "use": "new"},
            {"key": "c2", "label_ja": "格子熱伝導率", "label_en": "lattice", "kind": "property",
             "slug": "lattice", "use": "new"},
        ],
        "relations": [{"from": "c2", "rel": "part", "to": "c1", "reason": "", "problem": None}],
        "standards": {},
    }  # fmt: skip
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("c2", "ナノ析出物", "nano precipitate", "class"),  # the key clashes with base
                _t("n2", "格子熱伝導率", "dup", "property"),  # already on the map
                _t("n3", "ラットリング", "rattling", "class"),
            ],
            relations=[
                _r("c2", "lowers", "n2"),  # new concept → the duplicate, i.e. base c2
                _r("n3", "lowers", "c1"),  # a base key
                _r("c1", "part", "c2"),  # would close a loop only if c2 meant the base c2
            ],
        ),
        questions=[],
        existing=[],
        base=base,
    )
    assert [r["label_ja"] for r in out["concepts"]] == ["ナノ析出物", "ラットリング"]
    new_keys = [r["key"] for r in out["concepts"]]
    assert "c1" not in new_keys and "c2" not in new_keys and len(set(new_keys)) == 2
    assert out["concepts"][1]["key"] == "n3"  # a free key is kept
    rels = {(r["from"], r["rel"], r["to"]) for r in out["relations"]}
    assert (new_keys[0], "lowers", "c2") in rels  # the duplicate resolved to the base row
    assert ("n3", "lowers", "c1") in rels
    merged = themes.merge_expansion(base, out)
    assert len(merged["concepts"]) == 4 and len(merged["relations"]) == 1 + len(out["relations"])
    big = {"concepts": [{"key": f"k{i}"} for i in range(120)], "relations": []}
    with pytest.raises(ThemeError, match="120"):
        themes.merge_expansion(big, out)


def test_match_rows_finds_exact_existing_and_standard_terms_for_hand_written_rows() -> None:
    existing = [
        sv.SharedTerm(
            sv.SV + "record", "record", "class", "記録", "record", None, AT, aliases=("log",)
        )
    ]
    out = themes.match_rows(
        [
            {"key": "a", "label_ja": "記録", "label_en": "", "aliases": [], "kind": "class"},
            {"key": "b", "label_ja": "", "label_en": "Record", "aliases": [], "kind": "class"},
            {"key": "c", "label_ja": "x", "label_en": "y", "aliases": ["LOG"], "kind": "class"},
            {"key": "d", "label_ja": "温度", "label_en": "temperature", "aliases": [],
             "kind": "property"},
            {"key": "e", "label_ja": "記録の件数", "label_en": "", "aliases": [], "kind": "class"},
            {"key": "f", "label_ja": "記録", "label_en": "", "aliases": [], "kind": "property"},
        ],
        existing=existing,
    )  # fmt: skip
    by = {r["key"]: r for r in out}
    assert by["a"]["use"] == "existing:record" and by["a"]["slug"] == "record"
    assert by["a"]["match"] == {
        "kind": "existing", "ref": "record", "label": "記録", "matched_by": "label",
    }  # fmt: skip
    assert by["b"]["use"] == "existing:record"  # the English name
    assert by["c"]["match"]["matched_by"] == "alias"  # normalize_label: case-insensitive
    assert by["d"]["use"] == "standard:quantitykind:Temperature"
    assert by["d"]["match"]["kind"] == "quantity" and by["d"]["slug"] == "temperature"
    # a part of a name is never a match; the same name of the other kind is not one either
    assert by["e"] == {"key": "e", "match": None, "use": "new", "slug": "t5"}
    assert by["f"]["match"] is None


def test_match_rows_gives_keys_and_placeholder_ids_to_rows_without_them() -> None:
    rows = [{"label_ja": "なにか"}, {"key": "z", "label_ja": "なにか"}]
    out = themes.match_rows(rows, existing=[])
    assert [r["key"] for r in out] == ["t1", "z"]
    assert [r["slug"] for r in out] == ["t1", "t2"]
    assert all(r["match"] is None and r["use"] == "new" for r in out)


# ---------------------------------------------------------------------------
# commit — knowledge (the 熱伝導率 example of design.md §15)
# ---------------------------------------------------------------------------


def _wired(root: Path) -> dict:
    return json.loads((root / "vocab-shared" / "wired.json").read_text())["terms"]


def _knowledge(root: Path) -> dict:
    return json.loads((root / "vocab-shared" / "knowledge.json").read_text())["terms"]


async def test_a_knowledge_theme_is_placed_by_relations_alone(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _heat(), at=AT)
    assert out["minted"] == [
        "thermal_conductivity", "lattice_tc", "electronic_tc",
        "grain_boundary", "point_defect", "carrier_conc",
    ]  # fmt: skip
    assert out["lines"] == 6  # 5 relations + the quantity kind of 熱伝導率
    assert out["batch"] == "batch-1"
    theme = out["theme"]
    # the question is kept under a knowledge name; no tool is declared for any of it
    assert out["questions"] == ["kq_thermal_conductivity_factors_lowers"]
    sel = theme["selections"]["kq_thermal_conductivity_factors_lowers"]
    assert sel == {
        "mode": "knowledge", "op": "factors", "target_slug": "thermal_conductivity",
        "target": "熱伝導率", "title": Q_LOWER, "direction": "lowers",
    }  # fmt: skip
    assert theme["wanted"] == {
        "kq_thermal_conductivity_factors_lowers": "試料ごとの 粒径・熱伝導率 の表"
    }
    assert theme["support_questions"] == [] and theme["memo_questions"] == []
    assert theme["minted"] == out["minted"] and len(theme["uses"]) == 6
    assert not (tmp_path / "vocab-shared" / "query_tools.yaml").is_file() or not (
        yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text())["tools"]
    )  # no default question for any concept, no cq_ / kq_ tool
    # nobody answers it yet (no data), and nothing here counts as wiring
    assert set(_wired(tmp_path).values()) == {0}
    k = _knowledge(tmp_path)
    # 2 parts (the quantity-kind line is no knowledge)
    assert k[sv.term_iri("thermal_conductivity")] == 2
    assert k[sv.term_iri("lattice_tc")] == 3 and k[sv.term_iri("grain_boundary")] == 1
    # the run reads the lines it just wrote, one closure step, with who / when / why
    from asterism import knowledge

    ans = await knowledge.knowledge_answer(
        c,
        {"op": "factors", "target": sv.term_iri("thermal_conductivity"), "direction": "lowers"},
    )
    assert {i["label"] for i in ans["items"]} == {"粒界散乱", "点欠陥散乱"}
    by = {i["label"]: i for i in ans["items"]}
    assert by["粒界散乱"]["reason"] == "粒界でフォノンが散乱"
    assert by["粒界散乱"]["drafted_by"] == "ai" and by["粒界散乱"]["batch"] == "batch-1"
    assert by["粒界散乱"]["via_label"] == "格子熱伝導率" and by["粒界散乱"]["at"]
    aff = await knowledge.knowledge_answer(
        c, {"op": "factors", "target": sv.term_iri("thermal_conductivity"), "direction": "affects"}
    )
    assert aff["count"] == 3  # lowers ×2 + raises ×1
    parts = await knowledge.knowledge_answer(
        c, {"op": "parts", "target": sv.term_iri("thermal_conductivity")}
    )
    assert {i["label"] for i in parts["items"]} == {"格子熱伝導率", "電子熱伝導率"}


async def test_a_dataset_kind_joined_to_a_factor_does_not_change_wired(tmp_path: Path) -> None:
    """A line from a dataset's kind to a knowledge concept is a data line, not knowledge: the
    wired counts do not move (knowledge is never counted as an answering dataset)."""
    c = _Client()
    await themes.commit_theme(c, tmp_path, _heat(), at=AT)
    wired_before = dict(_wired(tmp_path))
    k_before = _knowledge(tmp_path)
    sample = "https://example.org/sample-a#Sample"
    await assert_alignment(
        c, sample, sv.term_iri("grain_boundary"), "subClassOf", at=AT
    )  # a dataset kind under the factor
    await sv.recompute_wired(c, tmp_path)
    assert _wired(tmp_path) == wired_before
    assert _knowledge(tmp_path) == k_before  # subClassOf is no knowledge relation either
    # ...and a knowledge line from that kind adds to the knowledge count only
    await assert_alignment(c, sample, sv.term_iri("lattice_tc"), "lowers", at=AT)
    await sv.recompute_wired(c, tmp_path)
    assert _wired(tmp_path) == wired_before
    lattice = sv.term_iri("lattice_tc")
    assert _knowledge(tmp_path)[lattice] == k_before[lattice] + 1


async def test_a_term_with_knowledge_keeps_its_existing_cq_tool(tmp_path: Path) -> None:
    from asterism.query_tools import parse_query_tools, prune_unwired_tools

    c = _Client()
    # 熱伝導率 exists with an ordinary question and nothing answers it
    await sv.mint_term(
        c, tmp_path, slug="thermal_conductivity", kind="property", label="熱伝導率",
        cqs=[CQSpec("熱伝導率はどこに？", "values")], at=AT,
    )  # fmt: skip
    body = _heat(
        concepts=[
            _concept("c1", "熱伝導率", "tc", "property", "existing:thermal_conductivity",
                     "thermal_conductivity"),
            *_heat()["concepts"][1:],
        ]
    )
    await themes.commit_theme(c, tmp_path, body, at=AT)
    tools = parse_query_tools({"tools": sv.read_registry_raw_tools(tmp_path, sv.REGISTRY_ID)})
    kept = prune_unwired_tools(tools, tmp_path)
    assert "cq_thermal_conductivity_values" in {t.name for t in kept}
    # without the knowledge lines the same tool is dropped (nothing answers it)
    (tmp_path / "vocab-shared" / "knowledge.json").write_text(
        json.dumps({"terms": {}, "at": AT}), encoding="utf-8"
    )
    assert "cq_thermal_conductivity_values" not in {
        t.name for t in prune_unwired_tools(tools, tmp_path)
    }


async def test_a_long_reason_is_cut_and_a_line_break_folded_not_refused(tmp_path: Path) -> None:
    c = _Client()
    body = _heat()
    body["relations"][2]["reason"] = "あ" * 150 + "\n" + "い" * 150
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["lines"] == 6
    rows = await list_alignments(c)
    reasons = [r["reason"] for r in rows if r["reason"] and r["reason"].startswith("あ")]
    assert len(reasons) == 1 and len(reasons[0]) == 200 and "\n" not in reasons[0]
    assert reasons[0].startswith("あ" * 150 + " い")
    drafter = {(r["drafted_by"], r["batch"]) for r in rows}
    assert drafter == {("ai", "batch-1")}  # the machine's own quantity-kind line too


async def test_a_human_line_is_recorded_as_human_with_a_default_batch(tmp_path: Path) -> None:
    c = _Client()
    body = _heat(ai_drafted=False)
    del body["batch"]
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["batch"] == f"{out['theme']['id']}@{AT}"
    rows = await list_alignments(c)
    assert {r["drafted_by"] for r in rows} == {"human"}
    assert {r["batch"] for r in rows} == {out["batch"]}
    with pytest.raises(ThemeError, match="batch"):
        await themes.commit_theme(c, tmp_path, _heat(batch="x" * 101, id="other"), at=AT)


async def test_a_concept_the_questions_cannot_reach_is_refused(tmp_path: Path) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    body = _heat()
    body["concepts"].append(_concept("c9", "迷子", "stray", "class", slug="stray"))
    before = _snapshot(c, tmp_path)
    with pytest.raises(ThemeError, match="迷子"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    assert _snapshot(c, tmp_path) == before
    # tied to the map by one relation, it goes through (a class may lower a property)
    body["relations"].append({"from": "c9", "rel": "lowers", "to": "c3", "reason": ""})
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert "stray" in out["minted"]
    # a theme whose questions are all memos cannot place a concept at all
    only_memo = _heat(answers=[_a(Q_LOWER, "memo")])
    with pytest.raises(ThemeError, match="つながらない"):
        await themes.commit_theme(_Client(), tmp_path / "fresh", only_memo, at=AT)


async def test_relations_are_checked_against_the_table_at_commit(tmp_path: Path) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    before = _snapshot(c, tmp_path)
    cases = [
        ({"from": "c4", "rel": "lowers", "to": "c5"}, "項目だけ"),  # to is a class
        ({"from": "c4", "rel": "part", "to": "c2"}, "種類と項目"),  # class part of property
        ({"from": "c2", "rel": "part", "to": "c4"}, "種類と項目"),
        ({"from": "c4", "rel": "sub", "to": "c2"}, "種類と項目"),
        ({"from": "c2", "rel": "part", "to": "c2"}, "自分自身"),
        ({"from": "c2", "rel": "nope", "to": "c1"}, "rel"),
        ({"from": "zz", "rel": "part", "to": "c1"}, "from"),
        ({"from": "c2", "rel": "part", "to": "nobody"}, "to"),
    ]
    for rel, match in cases:
        body = _heat()
        body["relations"].append({**rel, "reason": ""})
        with pytest.raises(ThemeError, match=match):
            await themes.commit_theme(c, tmp_path, body, at=AT)
        assert _snapshot(c, tmp_path) == before
    # lowers / raises / affects may loop (feedback); part may not
    body = _heat()
    body["relations"].append({"from": "c2", "rel": "lowers", "to": "c3", "reason": ""})
    body["relations"].append({"from": "c3", "rel": "lowers", "to": "c2", "reason": ""})
    await themes.commit_theme(c, tmp_path, body, at=AT)
    loop = _heat(id="loopy")
    loop["concepts"] = [loop["concepts"][0], *loop["concepts"][1:3]]
    loop["concepts"] = [
        _concept("c1", "甲", "alpha", "property", slug="alpha"),
        _concept("c2", "乙", "beta", "property", slug="beta"),
    ]
    loop["answers"] = [_a(Q_LOWER, "knowledge", op="parts", target="c1")]
    loop["relations"] = [
        {"from": "c1", "rel": "part", "to": "c2", "reason": ""},
        {"from": "c2", "rel": "part", "to": "c1", "reason": ""},
    ]
    with pytest.raises(ThemeError, match="循環"):
        await themes.commit_theme(c, tmp_path, loop, at=AT)


async def test_an_existing_term_may_be_the_subject_of_a_relation(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="grain_size", kind="property", label="粒径",
        cqs=[CQSpec("粒径は？", "values")], at=AT,
    )  # fmt: skip
    body = _heat(
        concepts=[
            *_heat()["concepts"],
            _concept("c7", "粒径", "grain size", "property", "existing:grain_size", "grain_size"),
        ],
    )
    body["relations"].append({"from": "c7", "rel": "affects", "to": "c4", "reason": ""})
    # ...but the existing term's kind is never changed
    wrong = _heat(
        concepts=[
            *_heat()["concepts"],
            _concept("c7", "粒径", "grain size", "class", "existing:grain_size", "grain_size"),
        ],
    )  # fmt: skip
    with pytest.raises(ThemeError, match="種類と項目"):
        await themes.commit_theme(c, tmp_path, wrong, at=AT)
    # (c4 is a class: affects needs a property to — so the line is refused)
    with pytest.raises(ThemeError, match="項目だけ"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    body["relations"][-1] = {"from": "c7", "rel": "affects", "to": "c2", "reason": "粒径"}
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert "grain_size" not in out["minted"] and "grain_size" in out["theme"]["uses"]
    lines = c.triples(ALIGNMENT_GRAPH)
    grain, lattice = sv.term_iri("grain_size"), sv.term_iri("lattice_tc")
    assert (grain, ALIGN_RELATIONS["affects"], lattice) in lines


async def test_a_reserved_slug_is_refused_at_commit(tmp_path: Path) -> None:
    c = _Client()
    body = _heat()
    body["concepts"][1]["slug"] = "part_of"
    with pytest.raises(ThemeError, match="予約語"):
        await themes.commit_theme(c, tmp_path, body, at=AT)


async def test_the_ai_less_path_commits_typed_concepts_with_placeholder_ids(tmp_path: Path) -> None:
    """No model: the concepts a person typed (no English name → no ID) still go through."""
    c = _Client()
    body = {
        "label": "",
        "lang": "ja",
        "ai_drafted": False,
        "questions": ["電池セルは何件？"],
        "concepts": [
            {"key": "t1", "label_ja": "電池セル", "kind": "class", "use": "new"},
            {"key": "t2", "label_ja": "正極", "kind": "class", "use": "new"},
        ],
        "relations": [{"from": "t2", "rel": "part", "to": "t1"}],
        "answers": [{"question": "電池セルは何件？", "mode": "data", "op": "count", "kind": "t1"}],
    }
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["minted"] == ["t1", "t2"]
    assert out["theme"]["label"] == "電池セルは何件？"  # the first question stands in
    assert out["questions"] == ["cq_t1_count"]
    rows = await list_alignments(c)
    assert {(r["drafted_by"], r["reason"]) for r in rows} == {("human", None)}


async def test_a_data_theme_places_no_default_question(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _cell(), at=AT)
    assert out["minted"] == ["battery_cell", "capacity", "cathode_material"]
    assert out["lines"] == 1 and out["memo"] == 1
    # only the two answerable questions became CQs; the helper term has none
    assert out["questions"] == ["cq_capacity_values", "cq_battery_cell_count"]
    theme = out["theme"]
    assert theme["support_questions"] == []
    assert [m["title"] for m in theme["memo_questions"]] == ["サイクル数ごとの推移は？"]
    assert theme["memo_questions"][0]["wanted_data"] == "サイクルごとの容量の表"
    assert theme["selections"]["cq_capacity_values"]["op"] == "top"
    assert theme["selections"]["cq_capacity_values"]["kind_slug"] == "battery_cell"
    tools = yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text())["tools"]
    titles = {t["name"]: t["title"] for t in tools}
    assert titles == {
        "cq_capacity_values": "容量が最も大きいセルは？",
        "cq_battery_cell_count": "セルは何件？",
    }
    terms = {t["slug"]: t for t in await sv.list_terms(c, tmp_path)}
    assert terms["battery_cell"]["aliases"] == ["cell"]
    assert terms["battery_cell"]["drafted_by"] is None
    iri, parent = sv.SV + "battery_cell", sv.SV + "cathode_material"
    assert (iri, ALIGN_RELATIONS["subClassOf"], parent) in c.triples(ALIGNMENT_GRAPH)
    saved = themes.load_themes_file(tmp_path)
    assert saved is not None and saved["themes"][-1]["id"] == theme["id"]
    assert (tmp_path / "vocab-shared" / "wired.json").is_file()


async def test_a_kind_named_only_in_a_question_is_reached_by_that_question(tmp_path: Path) -> None:
    """``top`` over a property of a kind: the kind needs no question of its own."""
    c = _Client()
    body = _cell(
        relations=[],
        questions=["容量が最も大きいセルは？"],
        answers=[_cell()["answers"][0]],
    )
    body["concepts"] = body["concepts"][:2]
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["minted"] == ["battery_cell", "capacity"]


async def test_commit_ties_a_new_term_to_its_standard_and_quantity_kind(tmp_path: Path) -> None:
    c = _Client()
    material = themes.std_exact("", "Material", [], "class")
    assert material is not None
    body = _cell(
        concepts=[
            {"key": "t1", "label_ja": "素材", "label_en": "material", "kind": "class",
             "use": f"standard:{material.curie}", "slug": "material"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "kind": "property",
             "use": "standard:quantitykind:Capacity", "slug": "capacity"},
        ],
        relations=[],
        questions=["素材は何件？"],
        answers=[{"question": "素材は何件？", "mode": "data", "op": "count", "kind": "t1"}],
    )  # fmt: skip
    body["concepts"][1]["by"] = "ai"
    body["ai_drafted"] = True
    # 容量 is tied to a question by nothing → refused; give it a question
    body["questions"].append("容量の範囲は？")
    body["answers"].append({"question": "容量の範囲は？", "mode": "data", "op": "range",
                            "property": "t2"})  # fmt: skip
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
    assert rows["capacity"]["drafted_by"] == "ai"


async def test_commit_uses_an_existing_term_without_minting_it(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="sample", kind="class", label="試料",
        cqs=[CQSpec("試料は何件？", "count")], at=AT,
    )  # fmt: skip
    before_terms = c.triples(sv.SHARED_VOCAB_GRAPH)
    body = _cell(
        concepts=[
            {"key": "t1", "label_ja": "試料", "label_en": "", "kind": "class",
             "use": "existing:sample", "slug": "sample"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "kind": "property",
             "use": "new", "slug": "capacity"},
        ],
        relations=[{"from": "t2", "rel": "sub", "to": "t1"}],  # an item under a kind
        questions=["試料の数は？"],
        answers=[{"question": "試料の数は？", "mode": "data", "op": "count", "kind": "t1"}],
    )  # fmt: skip
    with pytest.raises(ThemeError, match="種類と項目"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    body["relations"] = [{"from": "t2", "rel": "lowers", "to": "t1"}]  # to must be a property
    with pytest.raises(ThemeError, match="項目だけ"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    body["relations"] = [{"from": "t1", "rel": "affects", "to": "t2"}]  # 試料 changes 容量
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["minted"] == ["capacity"]
    assert out["questions"] == ["cq_sample_count_2"]  # appended to the existing term's CQs
    assert c.triples(sv.SHARED_VOCAB_GRAPH) >= before_terms
    tools = yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text())["tools"]
    assert {t["name"] for t in tools} >= {"cq_sample_count", "cq_sample_count_2"}


def _row(**over: object) -> dict:
    return {"key": "t1", "label_ja": "あ", "kind": "class", **over}


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"label": " ", "questions": []}, "名前が空"),
        ({"concepts": [_row(use="new", slug="Bad Slug")]}, "ID"),
        ({"concepts": [_row(use="standard:cmso:Nope", slug="a")]}, "標準の語"),
        ({"concepts": [_row(use="existing:ghost", slug="a")]}, "既にある用語"),
        ({"concepts": [_row(use="bogus", slug="a")]}, "use"),
        ({"concepts": [_row(kind="thing", slug="a")]}, "kind"),
        ({"relations": [{"from": "t1", "to": "t1", "rel": "sub"}]}, "自分自身"),
        ({"relations": [{"from": "t1", "to": "nobody", "rel": "sub"}]}, "to"),
        ({"relations": [{"from": "t1", "to": "t2", "rel": "sub"}]}, "種類と項目"),
        ({"relations": [{"from": "t1", "to": "t3", "rel": "quantity"}]}, "量"),
        (
            {
                "relations": [
                    {"from": "t1", "to": "t3", "rel": "sub"},
                    {"from": "t3", "to": "t1", "rel": "sub"},
                ]
            },
            "循環",
        ),
        ({"answers": [{"question": "セルは何件？", "mode": "knowledge", "op": "x",
                       "target": "t1"}]}, "答え方"),
        ({"answers": [{"question": "セルは何件？", "mode": "knowledge", "op": "factors",
                       "target": "t1"}]}, "向き"),
        ({"answers": [{"question": "セルは何件？", "mode": "knowledge", "op": "parts"}]}, "対象"),
        ({"answers": [{"question": "セルは何件？", "mode": "data", "op": "count",
                       "kind": "nobody"}]}, "概念"),
        ({"answers": [{"question": "セルは何件？", "mode": "data", "op": "count",
                       "kind": "t2"}]}, "種類に項目"),
    ],
)  # fmt: skip
async def test_a_refused_commit_writes_nothing(tmp_path: Path, patch: dict, match: str) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    before = _snapshot(c, tmp_path)
    with pytest.raises(ThemeError, match=match):
        await themes.commit_theme(c, tmp_path, _cell(**patch), at=AT)
    assert _snapshot(c, tmp_path) == before


async def test_a_taken_slug_is_a_conflict_and_nothing_is_minted(tmp_path: Path) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="capacity", kind="property", label="既にある容量",
        cqs=[CQSpec("容量は？", "values")], at=AT,
    )  # fmt: skip
    before = _snapshot(c, tmp_path)
    with pytest.raises(ThemeError) as err:
        await themes.commit_theme(c, tmp_path, _cell(), at=AT)
    assert err.value.status == 409
    assert _snapshot(c, tmp_path) == before


async def test_two_new_concepts_with_one_slug_are_refused(tmp_path: Path) -> None:
    c = _Client()
    body = _cell()
    body["concepts"][2]["slug"] = "battery_cell"
    with pytest.raises(ThemeError) as err:
        await themes.commit_theme(c, tmp_path, body, at=AT)
    assert err.value.status == 409


async def test_the_concept_cap_is_per_theme_and_cumulative(tmp_path: Path) -> None:
    c = _Client()
    many = [
        _concept(f"k{i}", f"概念{i}", f"concept {i}", "class", slug=f"cc{i}") for i in range(121)
    ]
    body = _cell(concepts=many, relations=[], answers=[], questions=[])
    with pytest.raises(ThemeError, match="120"):
        await themes.commit_theme(c, tmp_path, body, at=AT)


async def test_commit_is_atomic_a_failing_line_takes_the_mints_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _Client()
    themes.ensure_migrated(tmp_path, at=AT)
    body = _heat()
    before = _snapshot(c, tmp_path)
    real = themes.assert_alignment
    calls = {"n": 0}

    async def flaky(*a: object, **k: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 4:  # three lines are written, the fourth one fails
            raise RuntimeError("store went away")
        return await real(*a, **k)

    monkeypatch.setattr(themes, "assert_alignment", flaky)
    with pytest.raises(RuntimeError, match="store went away"):
        await themes.commit_theme(c, tmp_path, body, at=AT)
    assert calls["n"] == 4
    after = _snapshot(c, tmp_path)
    assert after["shared"] == before["shared"] == set()  # no mint left without its lines
    assert after["align"] == before["align"]
    assert after["themes"] == before["themes"]
    assert after["tools"] == [] and after["wired"] == before["wired"]
    assert after["knowledge"] == before["knowledge"]
    # and the same body goes through once the store is back
    monkeypatch.setattr(themes, "assert_alignment", real)
    out = await themes.commit_theme(c, tmp_path, body, at=AT)
    assert out["lines"] == 6 and len(out["minted"]) == 6


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
        await themes.commit_theme(c, tmp_path, _heat(), at=AT)
    assert _snapshot(c, tmp_path) == before


async def test_commit_deletes_the_draft_it_came_from(tmp_path: Path) -> None:
    c = _Client()
    d = themes.create_draft(tmp_path, "Battery", ["セルは何件？"], at=AT)
    out = await themes.commit_theme(c, tmp_path, _cell(id=d["id"], label="Battery"), at=AT)
    assert out["theme"]["id"] == d["id"]
    assert themes.load_draft(tmp_path, d["id"]) is None
    with pytest.raises(ThemeError) as err:  # the id is now a theme
        await themes.commit_theme(c, tmp_path, _cell(id=d["id"], label="Battery"), at=AT)
    assert err.value.status == 409
    with pytest.raises(ThemeError, match="予約"):
        await themes.commit_theme(c, tmp_path, _cell(id="sky"), at=AT)


async def test_questions_can_be_added_to_an_existing_theme_by_a_second_commit(
    tmp_path: Path,
) -> None:
    c = _Client()
    first = await themes.commit_theme(c, tmp_path, _cell(id="battery"), at=AT)
    assert first["theme"]["id"] == "battery"
    body = _cell(
        id="battery_more",
        extend="battery",
        questions=["容量が最小のセルは？"],
        concepts=[
            {"key": "t1", "label_ja": "電池セル", "label_en": "battery cell", "kind": "class",
             "use": "existing:battery_cell", "slug": "battery_cell"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "kind": "property",
             "use": "existing:capacity", "slug": "capacity"},
        ],
        relations=[],
        answers=[
            {"question": "容量が最小のセルは？", "mode": "data", "op": "range", "kind": "t1",
             "property": "t2"}
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
    assert set(theme["minted"]) == {"battery_cell", "capacity", "cathode_material"}
    with pytest.raises(ThemeError) as err:
        await themes.commit_theme(c, tmp_path, _cell(id="x", extend="ghost"), at=AT)
    assert err.value.status == 404


async def test_extending_a_knowledge_theme_reaches_the_old_question_through_the_store(
    tmp_path: Path,
) -> None:
    c = _Client()
    first = await themes.commit_theme(c, tmp_path, _heat(id="heat"), at=AT)
    more = {
        "extend": "heat",
        "id": "heat_more",
        "label": "x",
        "lang": "ja",
        "ai_drafted": True,
        "batch": "batch-2",
        "questions": [],
        "concepts": [
            _concept("c2", "格子熱伝導率", "lattice tc", "property", "existing:lattice_tc",
                     "lattice_tc"),
            _concept("n1", "ナノ析出物", "nano precipitate", "class", slug="nano"),
        ],
        "relations": [{"from": "n1", "rel": "lowers", "to": "c2", "reason": "界面散乱",
                       "by": "ai"}],
        "answers": [],
    }  # fmt: skip
    out = await themes.commit_theme(c, tmp_path, more, at="2026-10-21T00:00:00+00:00")
    assert out["minted"] == ["nano"] and out["lines"] == 1
    theme = themes.load_themes_file(tmp_path)["themes"][0]  # type: ignore[index]
    assert theme["questions"] == first["questions"]
    assert "nano" in theme["minted"] and len(theme["relations"]) == 7
    from asterism import knowledge

    ans = await knowledge.knowledge_answer(
        c, {"op": "factors", "target": sv.term_iri("thermal_conductivity"), "direction": "lowers"}
    )
    assert {i["label"] for i in ans["items"]} == {"粒界散乱", "点欠陥散乱", "ナノ析出物"}


# ---------------------------------------------------------------------------
# taking a theme away
# ---------------------------------------------------------------------------


async def test_removing_a_theme_removes_what_only_it_needed(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _heat(id="heat"), at=AT)
    res = await themes.remove_theme_cascade(c, tmp_path, "heat")
    assert {r["slug"] for r in res["removed_concepts"]} == set(out["minted"])
    assert res["kept_concepts"] == []
    assert len(res["removed_relations"]) == 6
    assert c.triples(sv.SHARED_VOCAB_GRAPH) == set()
    assert not [t for t in c.triples(ALIGNMENT_GRAPH) if ALIGN_RELATIONS["isPartOf"] in t]
    saved = themes.load_themes_file(tmp_path)
    assert saved is not None and saved["themes"] == []
    assert _knowledge(tmp_path) == {}
    with pytest.raises(ThemeError) as err:
        await themes.remove_theme_cascade(c, tmp_path, "heat")
    assert err.value.status == 404


async def test_removing_a_theme_keeps_what_another_theme_or_the_data_uses(tmp_path: Path) -> None:
    c = _Client()
    await themes.commit_theme(c, tmp_path, _heat(id="heat"), at=AT)
    # another theme reuses 格子熱伝導率 and rides on the line 粒界散乱 → 格子熱伝導率
    other = {
        "id": "other", "label": "別", "lang": "ja", "ai_drafted": False, "questions": ["q?"],
        "concepts": [
            {"key": "a", "label_ja": "格子熱伝導率", "kind": "property",
             "use": "existing:lattice_tc"},
            {"key": "g", "label_ja": "粒界散乱", "kind": "class", "use": "existing:grain_boundary"},
        ],
        "relations": [{"from": "g", "rel": "lowers", "to": "a", "reason": ""}],
        "answers": [{"question": "q?", "mode": "knowledge", "op": "factors", "target": "a",
                     "direction": "lowers"}],
    }  # fmt: skip
    await themes.commit_theme(c, tmp_path, other, at=AT)
    # a dataset's column is joined to 点欠陥散乱: that is the data's use
    await assert_alignment(
        c, "https://example.org/ds-a#PointDefect", sv.term_iri("point_defect"), "subClassOf", at=AT
    )
    res = await themes.remove_theme_cascade(c, tmp_path, "heat")
    kept = {r["slug"]: r["reason"] for r in res["kept_concepts"]}
    assert kept == {
        "lattice_tc": "other_theme",
        "grain_boundary": "other_theme",
        "point_defect": "has_lines",
    }
    assert {r["slug"] for r in res["removed_concepts"]} == {
        "thermal_conductivity", "electronic_tc", "carrier_conc",
    }  # fmt: skip
    # the line the other theme relies on stays; the others of "heat" are gone
    align = c.triples(ALIGNMENT_GRAPH)
    lowers = ALIGN_RELATIONS["lowers"]
    assert (sv.term_iri("grain_boundary"), lowers, sv.term_iri("lattice_tc")) in align
    assert (sv.term_iri("point_defect"), lowers, sv.term_iri("lattice_tc")) not in align
    assert any(r["relation"] == "lowers" for r in res["kept_relations"])


async def test_removing_a_theme_keeps_a_term_a_question_outside_it_still_uses(
    tmp_path: Path,
) -> None:
    c = _Client()
    await themes.commit_theme(c, tmp_path, _cell(id="cells"), at=AT)
    # someone adds a question of their own on 容量 (it is not one of the theme's)
    cap = sv.SharedTerm(sv.term_iri("capacity"), "capacity", "property", "容量", None, None, AT)
    sv._write_cq_tools(
        tmp_path, [sv.default_cq_tools(cap, [CQSpec("容量の別の問い", "values")] * 2)[-1]]
    )
    res = await themes.remove_theme_cascade(c, tmp_path, "cells")
    kept = {r["slug"]: r["reason"] for r in res["kept_concepts"]}
    assert kept.get("capacity") == "other_question"
    assert {"battery_cell", "cathode_material"} == {r["slug"] for r in res["removed_concepts"]}


async def test_removing_a_theme_that_was_never_a_v2_one_drops_only_the_record(
    tmp_path: Path,
) -> None:
    c = _Client()
    await sv.mint_term(
        c, tmp_path, slug="a", kind="class", label="あ", cqs=[CQSpec("あは？", "count")], at=AT
    )
    themes.ensure_migrated(tmp_path, at=AT)  # the migrated "first" theme has no minted list
    before = c.triples(sv.SHARED_VOCAB_GRAPH)
    res = await themes.remove_theme_cascade(c, tmp_path, "first")
    assert res == {
        "removed_concepts": [], "kept_concepts": [], "removed_relations": [], "kept_relations": [],
    }  # fmt: skip
    assert c.triples(sv.SHARED_VOCAB_GRAPH) == before


async def test_removing_the_record_alone_keeps_its_terms_lines_and_questions(
    tmp_path: Path,
) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _cell(), at=AT)
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
    out = await themes.commit_theme(c, tmp_path, _cell(), at=AT)
    rows = await sv.list_terms(c, tmp_path)
    tools = sv.read_registry_raw_tools(tmp_path, sv.REGISTRY_ID) or []
    files = themes.template_files(out["theme"], rows, tools)
    # 正極材料 is placed by a relation only: it gets no table of its own
    assert files == {"battery_cell.csv": ["battery_cell_id", "capacity"]}
    # a theme without recorded selections: kinds get an id column, items go to other.csv
    legacy = {"questions": ["cq_battery_cell_count", "cq_capacity_values"]}
    assert themes.template_files(legacy, rows, tools) == {
        "battery_cell.csv": ["battery_cell_id"],
        "other.csv": ["id", "capacity"],
    }


async def test_a_template_is_made_from_the_questions_targets_not_the_factors(
    tmp_path: Path,
) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _heat(), at=AT)
    rows = await sv.list_terms(c, tmp_path)
    tools = sv.read_registry_raw_tools(tmp_path, sv.REGISTRY_ID) or []
    # only 熱伝導率 (the knowledge question's target): one column, no file per factor
    assert themes.template_files(out["theme"], rows, tools) == {
        "other.csv": ["id", "Thermal Conductivity"]
    }
