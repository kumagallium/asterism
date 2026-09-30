"""同梱の見本を、すでにある環境へ届ける（ADR kantan K62）。

契約メモ contract_sample_refresh.md のテスト節に 1 対 1 で対応する。

* 実物の検査（台帳・単位の表・件数）— 直しを外すと落ちる
* ``plan_refresh``（純関数）
* ツールの合流
* 実行（tmp の registry に A の版のファイルを置いて refresh）
* 失敗（投影の例外・原子的な書き込み・dataset の外へのパス）
* 起動の順（``run_startup_sample``・``main()``）
* 実 rdflib の Dataset を注入したストアで ontology graph が入れ替わる
* データが変わる版（PR2・契約メモ contract_sample_refresh_pr2_data.md）: 群の判定・入れ替えの
  手順・途中で落ちたときの収束・保留・番号・つながりのハブ

A の版のファイル（mapping.yaml・model.yaml・diagram.md）は、git の A（2ae2f064）の
snapshot.tar から取り出して ``fixtures/world_sample_a/`` に置いてある（浅い clone の CI
でも動くように）。台帳の最初のエントリの sha と一致することを、下のテストで確かめる。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import os
import tarfile
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import rdflib
from asterism import substrate
from fastapi.testclient import TestClient

from asterism_api import demo_sample, local, registry
from asterism_api import main as main_mod
from asterism_api.demo_sample import (
    LEDGER_MEMBER,
    UNIT_TABLE,
    Bundled,
    entry_from_members,
    format_ledger,
    plan_refresh,
    read_bundled,
    read_members,
    revision_of_members,
    unit_of_member,
)
from asterism_api.main import Settings

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "datasets" / "world" / "snapshot.tar"
LEDGER_FILE = REPO / "datasets" / "world" / "sample_revisions.json"
A_DIR = Path(__file__).parent / "fixtures" / "world_sample_a"

# 配った版の指紋。消す・変えると、過去の環境が「触った」と誤判定される（台帳は追記専用）。
# A=v0.46.0/v0.47.0（2ae2f064）・B=main の中間版（91a5279b）・C=v0.47.1（52f87ab2）。
PAST_REVISIONS = {
    "A": "864d89a0e72662b7",
    "B": "e15f9bbdc18c2e16",
    "C": "08c3557051b4b61b",
}
DATASET_ID = "world"


# ---------------------------------------------------------------------------
# 部品


@cache
def _real_bytes() -> bytes:
    return SNAPSHOT.read_bytes()


@cache
def _real_members() -> dict[str, bytes]:
    return read_members(_real_bytes())


@cache
def _real() -> Bundled:
    return read_bundled(_real_bytes())


def _a_files() -> dict[str, bytes]:
    return {
        "registry/mapping.yaml": (A_DIR / "mapping.yaml").read_bytes(),
        "registry/model.yaml": (A_DIR / "model.yaml").read_bytes(),
        "registry/diagram.md": (A_DIR / "diagram.md").read_bytes(),
    }


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


@cache
def _triples_of(ttl: bytes) -> int:
    graph = rdflib.Graph()
    graph.parse(data=ttl.decode("utf-8"), format="turtle")
    return len(graph)


def _make_bundle(mutate: dict[str, bytes] | None = None, note: str = "テスト") -> Bundled:
    """実物の tar の中身を変えた「新しい版」の同梱。台帳には実物の 3 エントリ＋今回の
    エントリ（seq 4）を持たせる。"""
    return read_bundled(_bundle_tar(mutate, note))


def _bundle_tar(mutate: dict[str, bytes] | None = None, note: str = "テスト") -> bytes:
    """:func:`_make_bundle` の tar のバイト（``refresh_bundled_sample`` に渡す用）。"""
    members = dict(_real_members())
    members.update(mutate or {})
    if "graphs/canonical.ttl" in members and "graphs/canonical.ttl" in (mutate or {}):
        manifest = json.loads(members["manifest.json"])
        manifest["canonical_sha256"] = hashlib.sha256(members["graphs/canonical.ttl"]).hexdigest()
        manifest["canonical_triples"] = _triples_of(members["graphs/canonical.ttl"])
        members["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    old = demo_sample.parse_ledger(members[LEDGER_MEMBER].decode("utf-8"))
    entry = entry_from_members(members, seq=len(old) + 1, note={"ja": note, "en": note})
    members[LEDGER_MEMBER] = format_ledger([*old, entry]).encode("utf-8")
    return _tar_bytes(members)


def _subjects(artifacts: dict[str, str]) -> list[dict]:
    result = main_mod._subjects_of_design(artifacts)
    assert result is not None
    return result


def _design_artifacts(files: dict[str, bytes]) -> dict[str, str]:
    return {
        "mapping.yaml": files["registry/mapping.yaml"].decode("utf-8"),
    }


def _env_files(bundle: Bundled, *, a_design: bool = False) -> dict[str, bytes]:
    """環境の design・description・tools の member（C の版、a_design なら設計 3 つが A）。"""
    files: dict[str, bytes] = {}
    for unit in (demo_sample.UNIT_DESIGN, demo_sample.UNIT_DESCRIPTION, demo_sample.UNIT_TOOLS):
        for member in UNIT_TABLE[unit]["members"]:
            if member in bundle.members:
                files[member] = bundle.members[member]
    if a_design:
        files.update(_a_files())
    return files


def _meta(bundle: Bundled, **over: Any) -> dict[str, Any]:
    """seed 済みの見本の meta.json の形（stamp 無し）。"""
    meta: dict[str, Any] = {
        "id": DATASET_ID,
        "name": bundle.name,
        "created_at": "2026-09-24T00:51:18.933815+00:00",
        "complete": True,
        "warnings": [],
        "exit_code": 0,
        "traps": [],
        "classes": ["Country", "Observation"],
        "class_count": 2,
        "has_mie": True,
        "has_rml": True,
        "has_mapping_ir": True,
        "has_proposal": False,
        "advisories": [],
        "has_source": True,
        "source_files": ["activity.csv", "world.csv"],
        "source_kind": "csv",
        "origin": "open",
        "imported": {
            "origin_iri_base": "https://asterism.invalid",
            "exported_at": "2026-09-24T00:51:18.933397+00:00",
            "imported_at": "2026-09-25T10:00:00+00:00",
            "rebased": False,
            "canonical_sha256": bundle.canonical_sha256,
        },
        "data_seq": 1,
        "triple_count": 5958,
        "ingested": False,
        "promoted": True,
        "status": "active",
        "graph_iri": None,
        "canonical_graph": substrate.canonical_graph_iri(DATASET_ID),
        "live_graph": substrate.versioned_graph_iri(DATASET_ID, 1),
        "triples_promoted": 5958,
        "alignment": {},
        "promoted_at": "2026-09-25T10:00:05+00:00",
        "version": 1,
        "versions": [{"version": 1, "promoted_at": "2026-09-25T10:00:05+00:00"}],
        "published_subjects": _subjects(_a_design_artifacts()),
    }
    meta.update(over)
    return meta


def _a_design_artifacts() -> dict[str, str]:
    return _design_artifacts(_a_files())


def _plan(bundle: Bundled | None = None, **kw: Any) -> demo_sample.RefreshPlan:
    b = bundle or _real()
    params: dict[str, Any] = {
        "meta": _meta(b),
        "files": _env_files(b, a_design=True),
        "decision_files": [],
        "bundled_subjects": _subjects(b.design_artifacts()),
    }
    params.update(kw)
    return plan_refresh(b, **params)


def _reasons(plan: demo_sample.RefreshPlan) -> set[tuple[str, str]]:
    return {(h["unit"], h["reason"]) for h in plan.held}


# ---------------------------------------------------------------------------
# 実物の検査


def test_ledger_latest_revision_matches_the_real_tar_contents() -> None:
    ledger = demo_sample.load_ledger_file(LEDGER_FILE)
    assert ledger[-1]["revision"] == revision_of_members(_real_members())


def test_ledger_inside_the_tar_equals_the_canonical_file() -> None:
    assert _real_members()[LEDGER_MEMBER] == LEDGER_FILE.read_bytes()


def test_a_b_c_revisions_stay_in_the_ledger() -> None:
    ledger = demo_sample.load_ledger_file(LEDGER_FILE)
    revisions = [e["revision"] for e in ledger]
    assert revisions[:3] == [PAST_REVISIONS["A"], PAST_REVISIONS["B"], PAST_REVISIONS["C"]]
    # 印の仕組みより前に配った版は seq 1〜3（ツールを消したかの判定の上限に使う）
    assert demo_sample.PRE_STAMP_LAST_SEQ == 3
    # 台帳の各エントリの revision は、そのエントリの中身から計算した値
    for entry in ledger:
        assert entry["revision"] == demo_sample.entry_revision(entry)


def test_a_fixture_files_are_the_first_entry_of_the_ledger() -> None:
    entry = demo_sample.load_ledger_file(LEDGER_FILE)[0]
    for member, blob in _a_files().items():
        assert hashlib.sha256(blob).hexdigest() == entry["files"][member]


def test_every_tar_member_belongs_to_exactly_one_unit_or_ignored() -> None:
    for name in _real_members():
        owners = [
            unit
            for unit, spec in UNIT_TABLE.items()
            if name in spec["members"] or any(name.startswith(p) for p in spec["prefixes"])
        ]
        owners += [demo_sample.IGNORED] if name in demo_sample.IGNORED_MEMBERS else []
        assert len(owners) == 1, f"{name}: {owners}"
        assert unit_of_member(name) == owners[0]
    assert unit_of_member("registry/never-heard-of.txt") is None


def test_unit_table_members_are_disjoint() -> None:
    seen: dict[str, str] = {}
    for unit, spec in UNIT_TABLE.items():
        for member in spec["members"]:
            assert member not in seen, f"{member} is in {seen[member]} and {unit}"
            seen[member] = unit


def test_real_manifest_triple_count_matches_the_bundled_canonical() -> None:
    """データの入れ替えは、載せた graph の件数を ``manifest.canonical_triples`` と突き合わせる。
    実物の tar でその突き合わせが通ること（通らないと、データが変わる版が永久に届かない）。"""
    manifest = json.loads(_real_members()["manifest.json"])
    assert _triples_of(_real_members()["graphs/canonical.ttl"]) == manifest["canonical_triples"]


def test_real_bundle_reads_and_units_are_consistent() -> None:
    b = _real()
    assert b.dataset_id == DATASET_ID
    assert b.seq == len(b.ledger)
    assert set(b.units) == {"design", "description", "tools", "name", "data"}
    # 単位の revision は、その単位の member の sha だけで決まる
    only_name_changed = _make_bundle({"registry/meta.json": _renamed_meta("別の名前")})
    assert only_name_changed.units["name"] != b.units["name"]
    assert only_name_changed.units["design"] == b.units["design"]
    assert only_name_changed.units["tools"] == b.units["tools"]


def test_a_tar_whose_ledger_does_not_match_its_contents_is_refused() -> None:
    members = dict(_real_members())
    members["registry/model.yaml"] = members["registry/model.yaml"] + b"# edited\n"
    with pytest.raises(demo_sample.BundleError):
        read_bundled(_tar_bytes(members))
    del members[LEDGER_MEMBER]
    with pytest.raises(demo_sample.BundleError):
        read_bundled(_tar_bytes(members))
    with pytest.raises(demo_sample.BundleError):
        read_bundled(b"not a tar")


def _renamed_meta(name: str) -> bytes:
    meta = json.loads(_real_members()["registry/meta.json"])
    meta["name"] = name
    return json.dumps(meta, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# ビルドスクリプト（台帳の更新）


def _load_build_script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "build_world_demo_under_test", REPO / "scripts" / "build_world_demo.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_ledger(members: dict[str, bytes]) -> bytes:
    return _tar_bytes({k: v for k, v in members.items() if k != LEDGER_MEMBER})


def test_build_script_adds_no_entry_when_contents_are_unchanged() -> None:
    script = _load_build_script()
    ledger, added = script.update_ledger(
        _without_ledger(_real_members()), note_ja=None, note_en=None
    )
    assert added is None
    assert ledger == demo_sample.load_ledger_file(LEDGER_FILE)


def test_build_script_needs_both_notes_when_contents_changed() -> None:
    script = _load_build_script()
    members = dict(_real_members())
    members["registry/model.yaml"] = members["registry/model.yaml"] + "# 直した\n".encode()
    tar = _without_ledger(members)
    with pytest.raises(ValueError, match="--note-ja"):
        script.update_ledger(tar, note_ja=None, note_en=None)
    with pytest.raises(ValueError, match="--note-ja"):
        script.update_ledger(tar, note_ja="日本語だけ", note_en="")
    ledger, added = script.update_ledger(tar, note_ja="直した", note_en="Fixed")
    assert added is not None
    assert [e["seq"] for e in ledger] == [1, 2, 3, 4]
    assert ledger[-1]["note"] == {"ja": "直した", "en": "Fixed"}
    assert ledger[:3] == demo_sample.load_ledger_file(LEDGER_FILE)[:3]  # 追記専用


def test_build_script_verify_ledger_passes_on_the_real_tar() -> None:
    script = _load_build_script()
    result = script.verify_ledger(_real_bytes())
    assert result["revision"] == demo_sample.load_ledger_file(LEDGER_FILE)[-1]["revision"]


def test_ledger_is_written_deterministically() -> None:
    ledger = demo_sample.load_ledger_file(LEDGER_FILE)
    assert LEDGER_FILE.read_text(encoding="utf-8") == format_ledger(ledger)
    assert LEDGER_FILE.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# plan_refresh（純関数）


def test_plan_from_a_replaces_the_design_and_reaches_the_rest() -> None:
    plan = _plan()
    assert plan.skip is None
    assert plan.held == []
    assert set(plan.replace) == {"mapping.yaml", "model.yaml", "diagram.md"}
    assert plan.replace["diagram.md"] == _real().members["registry/diagram.md"]
    assert plan.derive_design is True
    # 印の無い環境は、説明のファイルが同じでも meta graph を 1 回投影し直す（届いたと数えるのは
    # 投影が済んでから）
    assert plan.reproject_description is True
    assert plan.new_name is None
    assert plan.reached == {"tools", "name"}
    assert plan.tools_seq == _real().seq


def test_plan_from_c_without_a_stamp_writes_no_file_but_derives_once() -> None:
    plan = _plan(files=_env_files(_real()))
    assert plan.skip is None
    assert plan.replace == {}
    assert plan.derive_design is True
    assert "design" not in plan.reached
    # 派生が済んで印が書かれた後は、速い道
    stamped = _meta(
        _real(),
        sample=demo_sample.build_stamp(
            _real(), {"design", "description", "tools", "name"}, [], "2026-09-30T00:00:00+00:00"
        ),
    )
    assert _plan(meta=stamped, files=_env_files(_real())).skip == "up-to-date"


def test_plan_one_byte_edit_of_mapping_holds_only_design() -> None:
    files = _env_files(_real(), a_design=True)
    files["registry/mapping.yaml"] += b" "
    plan = _plan(files=files)
    assert ("design", "edited") in _reasons(plan)
    assert plan.replace == {}  # 全部か何もしないか
    assert plan.derive_design is False
    assert plan.reached == {"tools", "name"}
    assert plan.reproject_description is True


def test_plan_a_and_c_mixed_environment_converges() -> None:
    """途中で落ちた環境（設計のうち一部だけ A、残りは C）→ 全部が「配った版」なので入れ替える。"""
    files = _env_files(_real())  # C
    files["registry/mapping.yaml"] = _a_files()["registry/mapping.yaml"]
    plan = _plan(files=files)
    assert plan.held == []
    assert set(plan.replace) == {"mapping.yaml"}
    assert plan.derive_design is True


def test_plan_a_missing_design_file_counts_as_touched() -> None:
    files = _env_files(_real(), a_design=True)
    del files["registry/model.yaml"]
    assert ("design", "edited") in _reasons(_plan(files=files))


def test_plan_downgraded_app_does_nothing() -> None:
    b = _real()
    stamp = demo_sample.build_stamp(b, {"design"}, [], "2026-09-30T00:00:00+00:00")
    stamp["seq"] = b.seq + 1
    assert _plan(meta=_meta(b, sample=stamp)).skip == "newer-than-bundle"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"status": "retracted"}, "retracted"),
        ({"promoted": False}, "not-promoted"),
        ({"imported": None}, "not-imported"),
        ({"origin": "own"}, "not-open"),
        ({"origin": None}, "not-open"),
    ],
)
def test_plan_skips_environments_that_are_not_the_sample(over: dict, reason: str) -> None:
    b = _real()
    meta = _meta(b, **over)
    if over.get("imported", 1) is None:
        del meta["imported"]
    assert _plan(meta=meta).skip == reason


def test_plan_skips_when_canonical_is_not_in_the_ledger() -> None:
    """利用者が自分で作った同じ id を、見本と取り違えない。"""
    b = _real()
    meta = _meta(b)
    meta["imported"]["canonical_sha256"] = "0" * 64
    assert _plan(meta=meta).skip == "unknown-canonical"


def test_plan_held_when_the_bundled_data_differs() -> None:
    """同梱のデータが環境と違う → design と tools を保留。description・name は進める。"""
    newer = _make_bundle({"graphs/canonical.ttl": _real_members()["graphs/canonical.ttl"] + b"\n"})
    assert newer.canonical_sha256 != _real().canonical_sha256
    plan = plan_refresh(
        newer,
        meta=_meta(newer, imported=_meta(_real())["imported"]),
        files=_env_files(_real(), a_design=True),
        decision_files=[],
        bundled_subjects=_subjects(newer.design_artifacts()),
    )
    assert {("design", "data"), ("tools", "data")} <= _reasons(plan)
    assert plan.replace == {}
    assert plan.reached == {"name"}
    assert plan.reproject_description is True
    # data 保留の間は、ツールを新しい版まで受け取ったことにしない
    assert plan.tools_seq == demo_sample.PRE_STAMP_LAST_SEQ


def test_plan_held_when_ids_would_move() -> None:
    moved = [{**s, "template": s["template"] + "/x"} for s in _subjects(_real().design_artifacts())]
    plan = _plan(bundled_subjects=moved)
    assert ("design", "ids_move") in _reasons(plan)
    assert plan.replace == {}
    assert plan.reached == {"tools", "name"}


def test_plan_ids_unknown_is_fail_closed() -> None:
    b = _real()
    meta = _meta(b)
    del meta["published_subjects"]
    assert ("design", "ids_unknown") in _reasons(_plan(meta=meta))
    assert ("design", "ids_unknown") in _reasons(_plan(bundled_subjects=None))


@pytest.mark.parametrize(
    "filename",
    [
        "display-meta.json",
        "column-decisions.json",
        "column-meanings.json",
        "handles.json",
        "reshape.json",
    ],
)
def test_plan_held_when_a_decision_file_exists(filename: str) -> None:
    plan = _plan(decision_files=[filename])
    assert ("design", "decisions") in _reasons(plan)
    assert plan.replace == {}


def test_plan_renamed_environment_keeps_its_name() -> None:
    b = _real()
    plan = _plan(meta=_meta(b, name="わたしの世界の国"))
    assert ("name", "edited") in _reasons(plan)
    assert plan.new_name is None
    assert "name" not in plan.reached
    assert "design" not in {h["unit"] for h in plan.held}  # 他の単位は進む
    assert plan.derive_design is True


def test_plan_replaces_a_name_that_an_earlier_release_gave() -> None:
    newer = _make_bundle({"registry/meta.json": _renamed_meta("新しい見本の名前")})
    plan = plan_refresh(
        newer,
        meta=_meta(newer, name=_real().name),
        files=_env_files(newer),
        decision_files=[],
        bundled_subjects=_subjects(newer.design_artifacts()),
    )
    assert plan.new_name == "新しい見本の名前"
    assert "name" in plan.reached


def test_plan_edited_description_holds_only_description() -> None:
    files = _env_files(_real(), a_design=True)
    files["registry/mie.yaml"] += b"# license\n"
    plan = _plan(files=files)
    assert ("description", "edited") in _reasons(plan)
    assert plan.reproject_description is False
    assert "description" not in plan.reached
    assert plan.derive_design is True


# ---------------------------------------------------------------------------
# ツールの合流


def _tools_yaml(tools: list[dict]) -> bytes:
    return demo_sample.dump_tools(tools)


def _real_tools() -> list[dict]:
    return demo_sample.parse_tools(_real().members["registry/query_tools.yaml"].decode("utf-8"))


def _tools_plan(env_tools_bytes: bytes | None, bundle: Bundled | None = None):
    b = bundle or _real()
    files = _env_files(b)
    if env_tools_bytes is None:
        files.pop("registry/query_tools.yaml", None)
    else:
        files["registry/query_tools.yaml"] = env_tools_bytes
    return plan_refresh(
        b,
        meta=_meta(b, sample=None),
        files=files,
        decision_files=[],
        bundled_subjects=_subjects(b.design_artifacts()),
    )


def _custom_tool(name: str = "my_tool") -> dict[str, Any]:
    tool = dict(_real_tools()[0])
    tool["name"] = name
    return tool


def test_tools_user_added_tool_stays() -> None:
    env = [*_real_tools(), _custom_tool()]
    plan = _tools_plan(_tools_yaml(env))
    assert not [h for h in plan.held if h["unit"] == "tools"]
    assert "tools" in plan.reached
    assert "query_tools.yaml" not in plan.replace  # 合流の結果が環境と同じ → 書かない


def test_tools_user_deleted_bundled_tool_is_not_restored() -> None:
    env = _real_tools()[1:]
    plan = _tools_plan(_tools_yaml(env))
    assert "query_tools.yaml" not in plan.replace
    assert not [h for h in plan.held if h["unit"] == "tools"]
    assert "tools" in plan.reached


def test_tools_fixed_bundled_tool_is_held() -> None:
    env = _real_tools()
    fixed = dict(env[0])
    fixed["description"] = "利用者が直した説明"
    env[0] = fixed
    plan = _tools_plan(_tools_yaml(env))
    assert {(h["unit"], h["reason"], h.get("detail")) for h in plan.held} == {
        ("tools", "edited", str(fixed["name"]))
    }
    assert "tools" not in plan.reached


def test_tools_new_bundled_tool_is_added_and_a_users_same_name_is_not_overwritten() -> None:
    added = _custom_tool("brand_new_tool")
    newer = _make_bundle(
        {
            "registry/query_tools.yaml": demo_sample.dump_tools([*_real_tools(), added]),
        }
    )
    # 環境に無い → 台帳のどこにも名前が無い → 足す（他のツールはそのまま）
    plan = _tools_plan(_tools_yaml(_real_tools()), newer)
    written = demo_sample.parse_tools(plan.replace["query_tools.yaml"].decode("utf-8"))
    assert [t["name"] for t in written][-1] == "brand_new_tool"
    assert len(written) == len(_real_tools()) + 1
    assert "tools" in plan.reached
    # 環境に同じ名前の利用者の別の定義が先にある → 上書きしない・保留
    users = {**added, "description": "利用者の別の定義"}
    plan = _tools_plan(_tools_yaml([*_real_tools(), users]), newer)
    assert ("tools", "edited") in _reasons(plan)
    assert plan.held[0]["detail"] == "brand_new_tool"
    assert "query_tools.yaml" not in plan.replace
    assert "tools" not in plan.reached


def test_tools_a_tool_only_in_the_latest_release_is_not_re_added_once_delivered() -> None:
    """最新の版だけにあるツールは、まだ受け取っていない環境には足す。印が最新の版を
    示す環境（受け取り済み）で利用者が消したものは、再判定でも足し直さない。"""
    added = _custom_tool("brand_new_tool")
    newer = _make_bundle(
        {"registry/query_tools.yaml": demo_sample.dump_tools([*_real_tools(), added])}
    )
    env_bytes = _tools_yaml(_real_tools())  # 丸ごとは配った版のバイトではない → 合流の道
    files = _env_files(newer)
    files["registry/query_tools.yaml"] = env_bytes

    def plan_with(stamp: dict | None) -> demo_sample.RefreshPlan:
        return plan_refresh(
            newer,
            meta=_meta(newer, sample=stamp),
            files=files,
            decision_files=[],
            bundled_subjects=_subjects(newer.design_artifacts()),
        )

    assert "query_tools.yaml" in plan_with(None).replace  # 印なし: 印より前の版しか受け取っていない
    delivered = demo_sample.build_stamp(
        newer,
        {"design", "tools"},
        [{"unit": "name", "reason": "edited"}],
        "2026-10-01T00:00:00+00:00",
    )
    assert "query_tools.yaml" not in plan_with(delivered).replace  # 受け取り済み → 消したのは利用者


def test_tools_untouched_file_is_replaced_by_bytes_with_comments() -> None:
    """丸ごと触っていないファイル（過去に配ったどれかの版のバイト）は、バイトで入れ替わる。"""
    real_bytes = _real().members["registry/query_tools.yaml"]
    newer_bytes = "# 新しい版のコメント\n".encode() + real_bytes
    newer = _make_bundle({"registry/query_tools.yaml": newer_bytes})
    plan = _tools_plan(real_bytes, newer)
    assert plan.replace["query_tools.yaml"] == newer_bytes
    assert plan.replace["query_tools.yaml"].startswith("# 新しい版のコメント".encode())
    assert "tools" in plan.reached


def test_tools_a_changed_bundled_tool_replaces_the_known_old_definition() -> None:
    changed = _real_tools()
    changed[0] = {**changed[0], "description": "新しい説明"}
    newer = _make_bundle({"registry/query_tools.yaml": demo_sample.dump_tools(changed)})
    # 環境: 古い版のファイルに利用者が別のツールを足した（丸ごとは触ったことになる）
    env = [*_real_tools(), _custom_tool()]
    plan = _tools_plan(_tools_yaml(env), newer)
    written = demo_sample.parse_tools(plan.replace["query_tools.yaml"].decode("utf-8"))
    assert written[0]["description"] == "新しい説明"
    assert written[-1]["name"] == "my_tool"
    assert not [h for h in plan.held if h["unit"] == "tools"]


def test_tools_unreadable_file_is_held_not_overwritten() -> None:
    plan = _tools_plan(b"tools: [unclosed\n")
    assert ("tools", "edited") in _reasons(plan)
    assert "query_tools.yaml" not in plan.replace


# ---------------------------------------------------------------------------
# 実行（tmp の registry に A の版のファイルを置く）


class _Projections:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, onto: int = 1, meta: int = 1) -> None:
        self.onto_calls: list[dict[str, str]] = []
        self.meta_calls: list[dict[str, str]] = []
        self.onto_result: Any = onto
        self.meta_result: Any = meta

        async def onto_fn(client: Any, dataset_id: str, artifacts: dict[str, str]) -> int:
            self.onto_calls.append(artifacts)
            if isinstance(self.onto_result, Exception):
                raise self.onto_result
            return int(self.onto_result)

        async def meta_fn(
            client: Any, dataset_id: str, artifacts: dict[str, str], **_kw: Any
        ) -> int:
            self.meta_calls.append(artifacts)
            if isinstance(self.meta_result, Exception):
                raise self.meta_result
            return int(self.meta_result)

        monkeypatch.setattr(main_mod, "_project_ontology_graph", onto_fn)
        monkeypatch.setattr(main_mod, "_project_meta_graph", meta_fn)


def _cfg(
    tmp_path: Path, *, single_user: bool = True, env_extra: dict[str, str] | None = None
) -> Settings:
    env = {
        "CSV2RDF_REGISTRY_ROOT": str(tmp_path / "registry"),
        "ASTERISM_APPDATA_ROOT": str(tmp_path / "appdata"),
        **(env_extra or {}),
    }
    if single_user:
        env["ASTERISM_SINGLE_USER"] = "1"
    return Settings(env)


def _write_env(
    tmp_path: Path,
    *,
    a_design: bool = True,
    meta_over: dict[str, Any] | None = None,
    extra: dict[str, bytes] | None = None,
) -> Path:
    """dataset ディレクトリ（``registry/world``）に A（または C）の版のファイルと meta を置く。"""
    b = _real()
    root = tmp_path / "registry"
    dest = root / DATASET_ID
    files = _env_files(b, a_design=a_design)
    files["registry/proposal.md"] = b""
    for member, blob in files.items():
        path = dest / member[len("registry/") :]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    for name, blob in (extra or {}).items():
        (dest / name).write_bytes(blob)
    meta = _meta(b, **(meta_over or {}))
    if not a_design:
        meta["classes"] = ["国", "年ごとの記録"]
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return dest


def _refresh(cfg: Settings, snapshot: Path | None = SNAPSHOT) -> None:
    asyncio.run(demo_sample.refresh_bundled_sample(cfg, object(), snapshot))


def _read_meta(dest: Path) -> dict[str, Any]:
    return json.loads((dest / "meta.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTERISM_DEMO_DATASET", raising=False)


def test_refresh_from_a_replaces_files_and_derives_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path)
    before = _read_meta(dest)
    _refresh(_cfg(tmp_path))

    b = _real()
    for member in (
        "registry/mapping.yaml",
        "registry/model.yaml",
        "registry/diagram.md",
        "registry/mapping.rml.ttl",
        "registry/mie.yaml",
        "registry/metadata.ttl",
        "registry/query_tools.yaml",
    ):
        assert (dest / member[len("registry/") :]).read_bytes() == b.members[member], member
    after = _read_meta(dest)
    assert after["classes"] == ["国", "年ごとの記録"]
    assert after["class_count"] == 2
    stamp = after["sample"]
    assert stamp["seq"] == b.seq
    assert stamp["revision"] == b.revision
    assert stamp["held"] == []
    assert stamp["units"] == {u: b.units[u] for u in ("design", "description", "tools", "name")}
    assert not (dest / "history").exists()
    for key in (
        "name",
        "imported",
        "version",
        "versions",
        "promoted_at",
        "published_subjects",
        "created_at",
        "live_graph",
        "status",
        "promoted",
        "canonical_graph",
        "data_seq",
        "origin",
    ):
        assert after[key] == before[key], key
    # 投影は新しい artifacts で 1 回。印の無い環境は説明も 1 回だけ投影し直す（届いたと数えるのは
    # 投影が済んでから。実物の説明は空なので meta graph は空のまま）
    assert len(proj.onto_calls) == 1
    assert proj.onto_calls[0]["mapping.yaml"] == b.members["registry/mapping.yaml"].decode()
    assert "取り込みの記録" in proj.onto_calls[0]["mapping.yaml"]
    assert len(proj.meta_calls) == 1
    assert [p.name for p in dest.iterdir() if p.name.endswith(".tmp")] == []


def test_refresh_twice_writes_nothing_the_second_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path)
    _refresh(_cfg(tmp_path))
    meta_bytes = (dest / "meta.json").read_bytes()
    mtime = (dest / "meta.json").stat().st_mtime_ns
    file_mtimes = {p: p.stat().st_mtime_ns for p in dest.rglob("*") if p.is_file()}
    _refresh(_cfg(tmp_path))
    assert (dest / "meta.json").read_bytes() == meta_bytes
    assert (dest / "meta.json").stat().st_mtime_ns == mtime
    assert {p: p.stat().st_mtime_ns for p in dest.rglob("*") if p.is_file()} == file_mtimes
    assert len(proj.onto_calls) == 1  # 2 回目は投影も呼ばれない


def test_refresh_c_without_stamp_derives_once_then_is_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    files_before = {
        p: p.read_bytes() for p in dest.rglob("*") if p.is_file() and p.name != "meta.json"
    }
    mtimes = {p: p.stat().st_mtime_ns for p in files_before}
    _refresh(_cfg(tmp_path))
    assert {p: p.read_bytes() for p in files_before} == files_before  # ファイルは書かない
    assert {p: p.stat().st_mtime_ns for p in files_before} == mtimes
    assert len(proj.onto_calls) == 1
    assert _read_meta(dest)["sample"]["revision"] == _real().revision
    _refresh(_cfg(tmp_path))
    assert len(proj.onto_calls) == 1


def test_refresh_edited_design_is_held_and_recorded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path)
    mapping = dest / "mapping.yaml"
    mapping.write_bytes(mapping.read_bytes() + b"# my note\n")
    _refresh(_cfg(tmp_path))
    assert mapping.read_bytes().endswith(b"# my note\n")  # 触られた設計は残る
    meta = _read_meta(dest)
    assert meta["classes"] == ["Country", "Observation"]  # 派生もしない
    assert [(h["unit"], h["reason"]) for h in meta["sample"]["held"]] == [("design", "edited")]
    assert "design" not in meta["sample"]["units"]
    assert proj.onto_calls == []
    # held があると毎回判定し直すが、結果が同じなら書かない
    meta_bytes = (dest / "meta.json").read_bytes()
    mtime = (dest / "meta.json").stat().st_mtime_ns
    _refresh(_cfg(tmp_path))
    assert (dest / "meta.json").read_bytes() == meta_bytes
    assert (dest / "meta.json").stat().st_mtime_ns == mtime


@pytest.mark.parametrize("filename", ["handles.json", "reshape.json"])
def test_refresh_decision_file_holds_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, extra={filename: b"{}"})
    _refresh(_cfg(tmp_path))
    meta = _read_meta(dest)
    assert ("design", "decisions") in {(h["unit"], h["reason"]) for h in meta["sample"]["held"]}
    assert (dest / "mapping.yaml").read_bytes() == _a_files()["registry/mapping.yaml"]


def test_refresh_history_directory_is_not_a_reason_to_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """history/ の有無は判定に使わない（ライセンス保存でも作られ、設計は変わらない）。"""
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path)
    (dest / "history" / "20260926T000000Z").mkdir(parents=True)
    _refresh(_cfg(tmp_path))
    assert (dest / "mapping.yaml").read_bytes() == _real().members["registry/mapping.yaml"]
    assert len(proj.onto_calls) == 1


def test_refresh_renamed_environment_keeps_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, meta_over={"name": "わたしの世界の国"})
    _refresh(_cfg(tmp_path))
    meta = _read_meta(dest)
    assert meta["name"] == "わたしの世界の国"
    assert ("name", "edited") in {(h["unit"], h["reason"]) for h in meta["sample"]["held"]}
    assert meta["classes"] == ["国", "年ごとの記録"]  # 他の単位は進む


@pytest.mark.parametrize(
    "case",
    ["not-single-user", "demo-env-0", "no-tar", "no-dataset", "not-a-sample", "retracted"],
)
def test_refresh_does_nothing_when_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    proj = _Projections(monkeypatch)
    over: dict[str, Any] = {}
    if case == "not-a-sample":
        over = {"imported": {"canonical_sha256": "0" * 64}}
    if case == "retracted":
        over = {"status": "retracted"}
    dest = _write_env(tmp_path, meta_over=over)
    snapshot: Path | None = SNAPSHOT
    if case == "demo-env-0":
        monkeypatch.setenv("ASTERISM_DEMO_DATASET", "0")
    if case == "no-tar":
        snapshot = tmp_path / "missing.tar"
    if case == "no-dataset":
        import shutil

        shutil.rmtree(dest)
    snapshot_files = (
        {p: p.read_bytes() for p in dest.rglob("*") if p.is_file()} if dest.exists() else {}
    )
    _refresh(_cfg(tmp_path, single_user=case != "not-single-user"), snapshot)
    assert proj.onto_calls == []
    if dest.exists():
        assert {p: p.read_bytes() for p in dest.rglob("*") if p.is_file()} == snapshot_files
        assert "sample" not in _read_meta(dest)  # 印も書かない
    else:
        assert not (tmp_path / "registry" / DATASET_ID).exists()  # 削除を尊重（再投入しない）


def test_refresh_ignores_a_bundle_whose_ledger_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path)
    members = dict(_real_members())
    members["registry/model.yaml"] += b"# not in the ledger\n"
    broken = tmp_path / "broken.tar"
    broken.write_bytes(_tar_bytes(members))
    _refresh(_cfg(tmp_path), broken)
    assert proj.onto_calls == []
    assert "sample" not in _read_meta(dest)


def test_refresh_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Projections(monkeypatch)
    _write_env(tmp_path)

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(registry, "replace_artifact_bytes", boom)
    _refresh(_cfg(tmp_path))  # 例外は外へ出ない


# ---------------------------------------------------------------------------
# 失敗


def test_refresh_projection_error_keeps_design_out_of_the_stamp_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch, onto=0)
    proj.onto_result = RuntimeError("store down")
    dest = _write_env(tmp_path)
    _refresh(_cfg(tmp_path))
    b = _real()
    # ファイルは新しい・classes は書く（ディスクの図で決まる）・design は印に入らない
    assert (dest / "diagram.md").read_bytes() == b.members["registry/diagram.md"]
    meta = _read_meta(dest)
    assert meta["classes"] == ["国", "年ごとの記録"]
    assert "design" not in meta["sample"]["units"]
    # 次の起動でやり直す → 収束
    proj.onto_result = 5
    _refresh(_cfg(tmp_path))
    assert len(proj.onto_calls) == 2
    assert _read_meta(dest)["sample"]["units"]["design"] == b.units["design"]
    _refresh(_cfg(tmp_path))
    assert len(proj.onto_calls) == 2


def test_refresh_zero_triples_projection_is_not_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch, onto=0)
    dest = _write_env(tmp_path)
    _refresh(_cfg(tmp_path))
    assert "design" not in _read_meta(dest)["sample"]["units"]
    _refresh(_cfg(tmp_path))
    assert len(proj.onto_calls) == 2  # 0 件のあいだは毎回やり直す


def _stamp_of_real(**over: Any) -> dict[str, Any]:
    """C（実物・seq 3）が全部届いたときの印。"""
    stamp = demo_sample.build_stamp(
        _real(), {"design", "description", "tools", "name"}, [], "2026-09-30T00:00:00+00:00"
    )
    stamp.update(over)
    return stamp


_NEW_DESCRIPTION = {
    "registry/mie.yaml": _real_members()["registry/mie.yaml"] + "# 説明を直した版\n".encode(),
    "registry/metadata.ttl": b'@prefix ex: <http://example.org/> . ex:a ex:b "c" .\n',
}


def _write_bundle(tmp_path: Path, mutate: dict[str, bytes]) -> Path:
    path = tmp_path / "newer.tar"
    path.write_bytes(_bundle_tar(mutate))
    return path


def test_plan_description_replaced_and_reprojected_when_the_bundle_changes_it() -> None:
    newer = _make_bundle(_NEW_DESCRIPTION)
    plan = plan_refresh(
        newer,
        meta=_meta(newer, sample=_stamp_of_real()),
        files=_env_files(_real()),
        decision_files=[],
        bundled_subjects=_subjects(newer.design_artifacts()),
    )
    assert plan.held == []
    assert set(plan.replace) == {"mie.yaml", "metadata.ttl"}
    assert plan.replace["metadata.ttl"] == _NEW_DESCRIPTION["registry/metadata.ttl"]
    assert plan.reproject_description is True
    assert "description" not in plan.reached  # 投影が済むまで「届いた」に数えない


def test_plan_description_is_reprojected_until_the_stamp_says_it_arrived() -> None:
    """ファイルが同梱と同じでも、印が「この版の説明まで届いた」と言っていなければやり直す。"""
    b = _real()
    files = _env_files(b)
    without = _stamp_of_real()
    del without["units"]["description"]
    plan = _plan(meta=_meta(b, sample=without), files=files)
    assert plan.replace == {}
    assert plan.reproject_description is True
    assert "description" not in plan.reached
    # 届いたと言っている印なら投影しない
    plan = _plan(meta=_meta(b, sample=_stamp_of_real(held=[{"unit": "name", "reason": "edited"}])))
    assert plan.reproject_description is False
    assert "description" in plan.reached


def test_refresh_description_projection_failure_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """説明のファイルを置き換えた後に meta graph の投影が失敗 → 次の起動でやり直して収束。"""
    proj = _Projections(monkeypatch, meta=0)
    proj.meta_result = RuntimeError("store down")
    dest = _write_env(tmp_path, a_design=False, meta_over={"sample": _stamp_of_real()})
    snapshot = _write_bundle(tmp_path, _NEW_DESCRIPTION)
    newer = read_bundled(snapshot.read_bytes())

    _refresh(_cfg(tmp_path), snapshot)
    assert (dest / "metadata.ttl").read_bytes() == _NEW_DESCRIPTION["registry/metadata.ttl"]
    assert (dest / "mie.yaml").read_bytes() == _NEW_DESCRIPTION["registry/mie.yaml"]
    assert len(proj.meta_calls) == 1
    assert "http://example.org/" in proj.meta_calls[0]["metadata.ttl"]
    stamp = _read_meta(dest)["sample"]
    assert stamp["seq"] == newer.seq
    assert "description" not in stamp["units"]  # 失敗は印に入れない
    assert proj.onto_calls == []  # 設計は変わっていない

    # ファイルは新しくなったので、次の起動は「ファイルが違う」ではなく印で再投影を決める
    proj.meta_result = 7
    _refresh(_cfg(tmp_path), snapshot)
    assert len(proj.meta_calls) == 2
    assert _read_meta(dest)["sample"]["units"]["description"] == newer.units["description"]

    _refresh(_cfg(tmp_path), snapshot)  # 収束したら何もしない
    assert len(proj.meta_calls) == 2


def test_refresh_description_zero_triples_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch, meta=0)
    dest = _write_env(tmp_path, a_design=False, meta_over={"sample": _stamp_of_real()})
    snapshot = _write_bundle(tmp_path, _NEW_DESCRIPTION)
    _refresh(_cfg(tmp_path), snapshot)
    assert "description" not in _read_meta(dest)["sample"]["units"]
    _refresh(_cfg(tmp_path), snapshot)
    assert len(proj.meta_calls) == 2  # 説明があるのに 0 件のあいだは毎回やり直す


def test_refresh_crash_between_the_files_and_the_stamp_still_reprojects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ファイルを置き換えた後・meta を書く前に落ちた環境（印は古い版のまま）。"""
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False, meta_over={"sample": _stamp_of_real()})
    for member, blob in _NEW_DESCRIPTION.items():
        (dest / member[len("registry/") :]).write_bytes(blob)
    _refresh(_cfg(tmp_path), _write_bundle(tmp_path, _NEW_DESCRIPTION))
    assert len(proj.meta_calls) == 1
    assert "description" in _read_meta(dest)["sample"]["units"]


def test_refresh_holds_ids_move_from_the_real_wiring_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開時の ID の作り方が同梱の設計で変わる版 → 設計は保留（環境自身の published_subjects
    でなく、同梱の設計から計算した値と比べていること）。"""
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False, meta_over={"sample": _stamp_of_real()})
    mapping = _real_members()["registry/mapping.yaml"].decode("utf-8")
    assert "wr:country/{country}" in mapping
    moved = mapping.replace("wr:country/{country}", "wr:nation/{country}").encode("utf-8")
    snapshot = _write_bundle(tmp_path, {"registry/mapping.yaml": moved})
    before = {p: p.read_bytes() for p in dest.rglob("*") if p.is_file() and p.name != "meta.json"}
    _refresh(_cfg(tmp_path), snapshot)
    assert {p: p.read_bytes() for p in before} == before  # ファイルは変わらない
    meta = _read_meta(dest)
    assert ("design", "ids_move") in {(h["unit"], h["reason"]) for h in meta["sample"]["held"]}
    assert "design" not in meta["sample"]["units"]
    assert meta["classes"] == ["国", "年ごとの記録"]
    assert proj.onto_calls == []


def test_refresh_tools_held_does_not_hide_a_tool_added_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ツール単位が保留（ファイルが読めない）のあいだに seq だけ進んでも、その版で同梱に
    入った新しいツールを、次の起動で「利用者が消した」と取り違えない。"""
    _Projections(monkeypatch)
    added = _custom_tool("brand_new_tool")
    snapshot = _write_bundle(
        tmp_path,
        {"registry/query_tools.yaml": demo_sample.dump_tools([*_real_tools(), added])},
    )
    dest = _write_env(tmp_path, a_design=False, meta_over={"sample": _stamp_of_real()})
    tools_file = dest / "query_tools.yaml"
    tools_file.write_bytes(b"tools: [unclosed\n")

    _refresh(_cfg(tmp_path), snapshot)
    assert tools_file.read_bytes() == b"tools: [unclosed\n"  # 読めないファイルは触らない
    stamp = _read_meta(dest)["sample"]
    assert stamp["seq"] == 4
    assert stamp["tools_seq"] == 3  # ツールはまだ 4 版まで受け取っていない
    assert "tools" not in stamp["units"]

    # 利用者がファイルを直した（配った版のバイトではない）→ 合流して新しいツールも入る
    tools_file.write_bytes(demo_sample.dump_tools([*_real_tools(), _custom_tool("my_own")]))
    _refresh(_cfg(tmp_path), snapshot)
    names = [t["name"] for t in demo_sample.parse_tools(tools_file.read_text(encoding="utf-8"))]
    assert "brand_new_tool" in names
    assert "my_own" in names
    stamp = _read_meta(dest)["sample"]
    assert stamp["tools_seq"] == 4
    assert "tools" in stamp["units"]

    # 受け取った後に利用者が消したものは、足し直さない
    tools_file.write_bytes(demo_sample.dump_tools([*_real_tools(), _custom_tool("my_own")]))
    _refresh(_cfg(tmp_path), snapshot)
    names = [t["name"] for t in demo_sample.parse_tools(tools_file.read_text(encoding="utf-8"))]
    assert "brand_new_tool" not in names


def test_refresh_still_held_is_logged_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path)
    mapping = dest / "mapping.yaml"
    mapping.write_bytes(mapping.read_bytes() + b"# my note\n")
    _refresh(_cfg(tmp_path))
    with caplog.at_level("INFO", logger="asterism_api.demo_sample"):
        _refresh(_cfg(tmp_path))  # 書き込みは無いが、保留の理由コードは 1 行出る
    assert any(
        "still held" in r.getMessage() and "design:edited" in r.getMessage() for r in caplog.records
    )


def test_refresh_known_tool_file_advances_the_received_version(tmp_path: Path) -> None:
    """環境のツールファイルが配った版そのもの（既知の sha）のときも、受け取った版を今回の
    seq まで進める（進めないと、その版で入った新ツールを利用者が後で消したとき、次の版で
    「消していない」と取り違えて足し直す）。"""
    added = _custom_tool("brand_new_tool")
    newer = _make_bundle(
        {"registry/query_tools.yaml": demo_sample.dump_tools([*_real_tools(), added])}
    )
    assert newer.seq == 4
    plan = _tools_plan(_real_members()["registry/query_tools.yaml"], newer)
    assert plan.replace["query_tools.yaml"] == newer.members["registry/query_tools.yaml"]
    assert "tools" in plan.reached
    assert plan.tools_seq == 4


def test_refresh_unchanged_result_does_not_rewrite_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """投影が 0 件を返し続ける環境で、2 回目の起動は meta.json を書かない
    （classes などの変わらない値だけで書き直さない）。"""
    _Projections(monkeypatch, onto=0)
    dest = _write_env(tmp_path, a_design=False)
    _refresh(_cfg(tmp_path))
    assert "design" not in _read_meta(dest)["sample"]["units"]  # 0 件は届いた扱いにしない
    meta_bytes = (dest / "meta.json").read_bytes()
    mtime = (dest / "meta.json").stat().st_mtime_ns
    _refresh(_cfg(tmp_path))
    assert (dest / "meta.json").read_bytes() == meta_bytes
    assert (dest / "meta.json").stat().st_mtime_ns == mtime


def test_atomic_writers_fsync_before_the_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ファイルへ書いたあと、置き換えの前に fsync する（電源断で空のファイルに
    ならないように）。"""
    root = tmp_path / "registry"
    (root / "ds").mkdir(parents=True)
    (root / "ds" / "meta.json").write_text(json.dumps({"id": "ds"}), encoding="utf-8")
    events: list[str] = []
    real_fsync, real_replace = registry.os.fsync, registry.os.replace
    monkeypatch.setattr(registry.os, "fsync", lambda fd: events.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(
        registry.os, "replace", lambda a, b: events.append("replace") or real_replace(a, b)
    )
    registry.replace_artifact_bytes(root, "ds", {"a.txt": b"x"})
    assert events == ["fsync", "replace"]
    events.clear()
    registry.update_meta_atomic(root, "ds", {"name": "n"})
    assert events == ["fsync", "replace"]


def test_atomic_temp_file_names_are_recognised() -> None:
    assert registry.is_atomic_tmp_name(f".meta.json.{'a' * 32}.tmp")
    assert not registry.is_atomic_tmp_name("meta.json")
    assert not registry.is_atomic_tmp_name("notes.tmp")
    assert not registry.is_atomic_tmp_name(".hidden")


def test_update_meta_atomic_keeps_the_original_when_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "registry"
    (root / "ds").mkdir(parents=True)
    meta_path = root / "ds" / "meta.json"
    meta_path.write_text(json.dumps({"id": "ds", "name": "元の名前"}), encoding="utf-8")
    original = meta_path.read_bytes()

    def fail(src: Any, dst: Any) -> None:
        raise OSError("crash before the swap")

    monkeypatch.setattr(registry.os, "replace", fail)
    with pytest.raises(OSError):
        registry.update_meta_atomic(root, "ds", {"name": "新しい名前"})
    assert meta_path.read_bytes() == original
    assert [p.name for p in (root / "ds").iterdir()] == ["meta.json"]  # 一時ファイルを残さない
    # 既存の書き手（_update_meta 経由）も原子的
    with pytest.raises(OSError):
        registry.rename_dataset(root, "ds", "別の名前")
    assert meta_path.read_bytes() == original


def test_update_meta_atomic_behaves_like_the_old_update_meta(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    (root / "ds").mkdir(parents=True)
    (root / "ds" / "meta.json").write_text(json.dumps({"id": "ds", "a": 1}), encoding="utf-8")
    meta = registry.update_meta_atomic(root, "ds", {"b": 2})
    assert meta == {"id": "ds", "a": 1, "b": 2}
    assert json.loads((root / "ds" / "meta.json").read_text(encoding="utf-8")) == meta
    assert registry.update_meta_atomic(root, "Bad Id!", {"x": 1}) is None
    assert registry.update_meta_atomic(root, "missing", {"x": 1}) is None
    assert registry.rename_dataset(root, "ds", "名前") is not None


@pytest.mark.parametrize(
    "bad", ["../escape.txt", "/etc/passwd", "a/../../escape.txt", "", "meta.json", "..", "."]
)
def test_replace_artifact_bytes_refuses_paths_outside_the_dataset(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "registry"
    (root / "ds").mkdir(parents=True)
    with pytest.raises(ValueError):
        registry.replace_artifact_bytes(root, "ds", {"ok.txt": b"x", bad: b"y"})
    assert not (root / "ds" / "ok.txt").exists()  # 検査は書き込みの前に全部行う
    assert not (root / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_replace_artifact_bytes_writes_verbatim_without_history(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    (root / "ds").mkdir(parents=True)
    (root / "ds" / "a.yaml").write_bytes(b"old")
    blob = "# コメント\r\nkey: 値\n".encode()  # CR も含めてバイトのまま
    registry.replace_artifact_bytes(root, "ds", {"a.yaml": blob, "source/b.csv": b"1,2\n"})
    assert (root / "ds" / "a.yaml").read_bytes() == blob
    assert (root / "ds" / "source" / "b.csv").read_bytes() == b"1,2\n"
    assert not (root / "ds" / "history").exists()
    assert sorted(p.name for p in (root / "ds").iterdir()) == ["a.yaml", "source"]
    with pytest.raises(FileNotFoundError):
        registry.replace_artifact_bytes(root, "nope", {"a": b""})
    with pytest.raises(ValueError):
        registry.replace_artifact_bytes(root, "Bad Id", {"a": b""})


# ---------------------------------------------------------------------------
# 起動の順


def _patch_order(monkeypatch: pytest.MonkeyPatch, log: list[str], **raises: bool) -> None:
    async def seed(home: Any, cfg: Any, client: Any) -> None:
        log.append("seed")
        if raises.get("seed"):
            raise RuntimeError("seed failed")

    async def refresh(cfg: Any, client: Any, snapshot: Any) -> None:
        log.append("refresh")
        if raises.get("refresh"):
            raise RuntimeError("refresh failed")

    async def backfill(cfg: Any, client: Any) -> None:
        log.append("backfill")

    monkeypatch.setattr(local, "seed_demo_dataset", seed)
    monkeypatch.setattr(local.demo_sample, "refresh_bundled_sample", refresh)
    monkeypatch.setattr(local, "backfill_store_projections", backfill)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: SNAPSHOT)


def test_startup_sample_runs_seed_then_refresh_then_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[str] = []
    _patch_order(monkeypatch, log)
    asyncio.run(local.run_startup_sample(tmp_path, _cfg(tmp_path), object()))
    assert log == ["seed", "refresh", "backfill"]


@pytest.mark.parametrize("failing", ["seed", "refresh"])
def test_startup_sample_continues_when_a_step_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    log: list[str] = []
    _patch_order(monkeypatch, log, **{failing: True})
    asyncio.run(local.run_startup_sample(tmp_path, _cfg(tmp_path), object()))
    assert log == ["seed", "refresh", "backfill"]  # 落ちても次へ（backfill は必ず走る）


def test_main_runs_the_startup_sample_before_build_local_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP の型付きツールは build_local_app の中で registry を読んで 1 回だけ登録される。
    種まき・入れ替えの後の registry を読ませるため、その前に呼ぶ。"""
    log: list[str] = []

    async def startup(home: Any, settings: Any, client: Any) -> None:
        log.append("startup")

    class _FakeClient:
        def __init__(self, cfg: Any) -> None:
            log.append("client")

        async def aclose(self) -> None:
            log.append("client-closed")

    def build(**kwargs: Any) -> object:
        log.append("build")
        return object()

    def serve(app: Any, **kwargs: Any) -> None:
        log.append("serve")

    monkeypatch.setattr(local, "run_startup_sample", startup)
    monkeypatch.setattr(local, "OxigraphClient", _FakeClient)
    monkeypatch.setattr(local, "build_local_app", build)
    monkeypatch.setattr(local, "_serve", serve)
    monkeypatch.setattr(local, "local_env", lambda *a, **k: {})
    monkeypatch.setattr(local, "find_ui_dist", lambda _p: None)
    prev = os.umask(0o022)
    try:
        rc = local.main(
            [
                "--data-dir",
                str(tmp_path / "home"),
                "--no-browser",
                "--oxigraph-url",
                "http://127.0.0.1:9",
                "--no-ask",
                "--no-mcp",
            ]
        )
    finally:
        os.umask(prev)
    assert rc == 0
    assert log == ["client", "startup", "client-closed", "build", "serve"]


# ---------------------------------------------------------------------------
# 実 rdflib の Dataset を注入したストア（test_orphan_reclaim_api.py の型）


class _DatasetClient:
    """OxigraphClient の代わり（実 rdflib の Dataset の上の SELECT/UPDATE/Graph-Store POST）。"""

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_graph
        g.parse(data=payload.decode("utf-8"), format="turtle")
        return len(payload)

    async def graph_triple_count(self, graph_iri: str) -> int:
        return len(self.ds.graph(rdflib.URIRef(graph_iri)))

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


_PROV_ACTIVITY = rdflib.URIRef("http://www.w3.org/ns/prov#Activity")


def _activity_labels(ds: rdflib.Dataset, dataset_id: str = DATASET_ID) -> set[str]:
    graph = ds.graph(rdflib.URIRef(substrate.ontology_graph_iri(dataset_id)))
    return {str(o) for o in graph.objects(_PROV_ACTIVITY, rdflib.RDFS.label)}


def test_real_store_old_ontology_label_becomes_the_display_name(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    client = _DatasetClient(ds)
    # 古い ontology graph（A の設計から投影したもの: 来歴の種類の label が Activity）
    asyncio.run(main_mod._project_ontology_graph(client, DATASET_ID, _a_design_artifacts_full()))
    assert _activity_labels(ds) == {"Activity"}
    dest = _write_env(tmp_path)
    asyncio.run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, SNAPSHOT))
    assert _activity_labels(ds) == {"取り込みの記録"}
    assert _read_meta(dest)["sample"]["units"]["design"] == _real().units["design"]


def _a_design_artifacts_full() -> dict[str, str]:
    files = _env_files(_real(), a_design=True)
    return {
        member[len("registry/") :]: blob.decode("utf-8")
        for member, blob in files.items()
        if member.endswith((".yaml", ".ttl", ".md"))
    }


def test_seed_writes_the_stamp_and_the_next_refresh_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新しい環境: 種まき（実 tar・実 import・実 promote・実の投影）が印を書き、直後の
    refresh は書き込みゼロ。"""
    ds = rdflib.Dataset()
    client = _DatasetClient(ds)
    cfg = _cfg(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(local, "find_world_snapshot", lambda: SNAPSHOT)

    asyncio.run(local.seed_demo_dataset(home, cfg, client))
    dest = tmp_path / "registry" / DATASET_ID
    meta = _read_meta(dest)
    b = _real()
    assert meta["promoted"] is True
    assert meta["sample"]["revision"] == b.revision
    assert meta["sample"]["units"] == {
        u: b.units[u] for u in ("design", "description", "tools", "name")
    }
    assert meta["sample"]["held"] == []
    assert _activity_labels(ds) == {"取り込みの記録"}

    meta_bytes = (dest / "meta.json").read_bytes()
    mtime = (dest / "meta.json").stat().st_mtime_ns
    calls: list[str] = []
    real_write = registry.update_meta_atomic
    monkeypatch.setattr(
        registry, "update_meta_atomic", lambda *a, **k: calls.append("meta") or real_write(*a, **k)
    )
    monkeypatch.setattr(
        registry, "replace_artifact_bytes", lambda *a, **k: calls.append("files") or None
    )
    asyncio.run(demo_sample.refresh_bundled_sample(cfg, client, SNAPSHOT))
    assert calls == []
    assert (dest / "meta.json").read_bytes() == meta_bytes
    assert (dest / "meta.json").stat().st_mtime_ns == mtime


def test_seed_without_a_readable_bundle_still_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """印は best-effort — 台帳の無い tar でも、種まきは公開まで済ませて終わる。"""
    members = {k: v for k, v in _real_members().items() if k != LEDGER_MEMBER}
    old = tmp_path / "old.tar"
    old.write_bytes(_tar_bytes(members))
    client = _DatasetClient(rdflib.Dataset())
    cfg = _cfg(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(local, "find_world_snapshot", lambda: old)
    asyncio.run(local.seed_demo_dataset(home, cfg, client))
    meta = _read_meta(tmp_path / "registry" / DATASET_ID)
    assert meta["promoted"] is True
    assert "sample" not in meta
    assert (home / "demo-seeded").is_file()


# ---------------------------------------------------------------------------
# 説明が空の同梱: meta graph の DROP が成功したときだけ「届いた」（実 rdflib のストア）


class _MetaDropFails(_DatasetClient):
    """説明 graph の DROP だけが失敗するストア。"""

    def __init__(self, ds: rdflib.Dataset) -> None:
        super().__init__(ds)
        self.fail = True
        self.meta_drops = 0

    async def sparql_update(self, update: str) -> None:
        if update.startswith("DROP") and substrate.meta_graph_iri(DATASET_ID) in update:
            self.meta_drops += 1
            if self.fail:
                raise RuntimeError("store down")
        await super().sparql_update(update)


_STALE_DESCRIPTION = (
    rdflib.URIRef("http://example.org/old"),
    rdflib.URIRef("http://example.org/says"),
    rdflib.Literal("古い説明"),
)


def _meta_graph_size(ds: rdflib.Dataset) -> int:
    return len(ds.graph(rdflib.URIRef(substrate.meta_graph_iri(DATASET_ID))))


def test_real_store_blank_description_is_reached_only_when_the_drop_succeeds(
    tmp_path: Path,
) -> None:
    """実物の同梱は metadata.ttl が空。DROP が失敗したのに「説明まで届いた」と印に書くと、
    ストアの説明が古いまま固定される。成功したら印に入り、次からは投影しない。"""
    ds = rdflib.Dataset()
    ds.graph(rdflib.URIRef(substrate.meta_graph_iri(DATASET_ID))).add(_STALE_DESCRIPTION)
    client = _MetaDropFails(ds)
    dest = _write_env(tmp_path, a_design=False)  # 印の無い環境（v0.46.0 以降）
    cfg = _cfg(tmp_path)

    asyncio.run(demo_sample.refresh_bundled_sample(cfg, client, SNAPSHOT))
    assert "description" not in _read_meta(dest)["sample"]["units"]
    assert _meta_graph_size(ds) == 1  # 古いまま

    client.fail = False  # ストアが戻った → やり直して収束する
    asyncio.run(demo_sample.refresh_bundled_sample(cfg, client, SNAPSHOT))
    assert _read_meta(dest)["sample"]["units"]["description"] == _real().units["description"]
    assert _meta_graph_size(ds) == 0
    drops = client.meta_drops

    asyncio.run(demo_sample.refresh_bundled_sample(cfg, client, SNAPSHOT))  # 速い道
    assert client.meta_drops == drops


def test_refresh_blank_description_reached_with_a_mocked_projection_and_not_redone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """印の無い環境: 説明が空でも投影が 0 件で成功すれば届いた → 2 回目は投影しない。"""
    proj = _Projections(monkeypatch, meta=0)
    dest = _write_env(tmp_path, a_design=False)
    _refresh(_cfg(tmp_path))
    assert "description" in _read_meta(dest)["sample"]["units"]
    assert len(proj.meta_calls) == 1
    _refresh(_cfg(tmp_path))
    assert len(proj.meta_calls) == 1


def test_seed_does_not_stamp_a_projection_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """種まきで投影が失敗（設計 0 件・説明の DROP 失敗）→ 印は「届いた」と書かず、
    次の refresh がやり直す。"""
    ds = rdflib.Dataset()
    client = _MetaDropFails(ds)
    cfg = _cfg(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(local, "find_world_snapshot", lambda: SNAPSHOT)

    async def no_triples(client: Any, dataset_id: str, artifacts: dict[str, str]) -> int:
        return 0

    monkeypatch.setattr(main_mod, "_project_ontology_graph", no_triples)
    asyncio.run(local.seed_demo_dataset(home, cfg, client))
    dest = tmp_path / "registry" / DATASET_ID
    units = _read_meta(dest)["sample"]["units"]
    assert "design" not in units
    assert "description" not in units
    assert "tools" in units
    assert "name" in units

    # 投影が戻れば、次の refresh が 2 つとも届けて印に入れる
    monkeypatch.undo()
    client.fail = False
    asyncio.run(demo_sample.refresh_bundled_sample(cfg, client, SNAPSHOT))
    units = _read_meta(dest)["sample"]["units"]
    assert "design" in units
    assert "description" in units


# ---------------------------------------------------------------------------
# データが変わる版（PR2）: design・data・tools を 1 つの群として入れ替える

WORLD_TRIPLES = 5958
_EXTRA_TRIPLE = (
    "\n<https://asterism.invalid/datasets/world/resource/country/zz> "
    '<http://www.w3.org/2000/01/rdf-schema#label> "新しい国" .\n'
).encode()
KEY = substrate.canonical_graph_iri(DATASET_ID)
V1 = substrate.versioned_graph_iri(DATASET_ID, 1)


def _v(n: int) -> str:
    return substrate.versioned_graph_iri(DATASET_ID, n)


class _Crash(BaseException):
    """プロセスが落ちた合図（``except Exception`` では捕まらない）。"""


class _RecordingClient(_DatasetClient):
    """書き込みを数える。``crash_on_canonical_post`` なら、新しい graph の途中で落ちる。"""

    def __init__(self, ds: rdflib.Dataset) -> None:
        super().__init__(ds)
        self.updates = 0
        self.posts: list[str] = []
        self.crash_on_canonical_post = False

    async def sparql_update(self, update: str) -> None:
        self.updates += 1
        await super().sparql_update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        self.posts.append(graph_iri or "")
        if self.crash_on_canonical_post and graph_iri and "/canonical/world/v" in graph_iri:
            part = rdflib.Graph()
            part.parse(data=payload.decode("utf-8"), format="turtle")
            target = self.ds.graph(rdflib.URIRef(graph_iri))
            for i, triple in enumerate(part):
                if i >= 100:
                    break
                target.add(triple)
            raise _Crash()
        return await super().post_turtle_bytes(payload, graph_iri)


@cache
def _real_graph() -> rdflib.Graph:
    graph = rdflib.Graph()
    graph.parse(data=_real_members()["graphs/canonical.ttl"].decode("utf-8"), format="turtle")
    return graph


@cache
def _data_release() -> bytes:
    """データが変わる新しい版: canonical に三つ組を 1 つ足し、source を 1 行足し、RML の
    末尾にコメントを足し、source を 1 ファイル増やした合成の版（台帳の seq 4）。"""
    m = _real_members()
    return _bundle_tar(
        {
            "graphs/canonical.ttl": m["graphs/canonical.ttl"] + _EXTRA_TRIPLE,
            "registry/source/activity.csv": m["registry/source/activity.csv"]
            + b"ingest-world-v2,g.json,https://example.org/g,2026-09-30T00:00:00Z,test\n",
            "registry/source/notes.csv": b"a,b\n1,2\n",
            "registry/mapping.rml.ttl": m["registry/mapping.rml.ttl"] + b"\n# new release\n",
        }
    )


def _release_file(tmp_path: Path) -> Path:
    path = tmp_path / "release.tar"
    if not path.exists():
        path.write_bytes(_data_release())
    return path


def _release_members() -> dict[str, bytes]:
    return read_members(_data_release())


def _old_store() -> tuple[rdflib.Dataset, _RecordingClient]:
    """旧版（実物の canonical）が v1 に載り、公開されている実 rdflib のストア。"""
    ds = rdflib.Dataset()
    graph = ds.graph(rdflib.URIRef(V1))
    for triple in _real_graph():
        graph.add(triple)
    control = ds.graph(rdflib.URIRef(substrate.CONTROL_GRAPH_IRI))
    control.add(
        (
            rdflib.URIRef(KEY),
            rdflib.URIRef(substrate.STATUS_PREDICATE),
            rdflib.Literal("promoted"),
        )
    )
    control.add(
        (rdflib.URIRef(KEY), rdflib.URIRef(substrate.LIVE_GRAPH_PREDICATE), rdflib.URIRef(V1))
    )
    return ds, _RecordingClient(ds)


def _write_swap_env(
    tmp_path: Path, *, stamp: bool = False, meta_over: dict[str, Any] | None = None
) -> Path:
    """旧版（C）の見本の registry。source も置く。``stamp`` なら C の印つき。"""
    over = dict(meta_over or {})
    if stamp:
        over["sample"] = _stamp_of_real()
    dest = _write_env(tmp_path, a_design=False, meta_over=over)
    for member, blob in _real_members().items():
        if member.startswith("registry/source/"):
            path = dest / member[len("registry/") :]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
    return dest


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _refresh_release(tmp_path: Path, client: Any) -> None:
    _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, _release_file(tmp_path)))


def _lifespan(tmp_path: Path, client: Any) -> None:
    """アプリの lifespan を 1 回開閉する（``mark_graph_promoted``・孤児の回収が走る）。"""
    cfg = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp_path / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp_path / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp_path / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp_path / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp_path / "registry"),
            "CSV2RDF_OXIGRAPH_URL": "http://test",
            "CSV2RDF_SETTLE_S": "0.0",
            "ASTERISM_APPDATA_ROOT": str(tmp_path / "appdata"),
        }
    )
    app = main_mod.build_app(cfg, oxigraph_client=client, start_watcher=False)
    with TestClient(app):
        pass


def _live(client: Any) -> str | None:
    return _run(substrate.live_graph_of(client, KEY))


def _canon(client: Any) -> list[str]:
    return _run(substrate.canonical_graphs(client))


def _pending(client: Any) -> list[str]:
    return _run(substrate.pending_drops(client, limit=1000))


def _staged(client: Any) -> str | None:
    return _run(substrate.staged_graph_of(client, KEY))


def _size(ds: rdflib.Dataset, graph: str) -> int:
    return len(ds.graph(rdflib.URIRef(graph)))


def _sweep(client: Any) -> None:
    _run(substrate.sweep_pending_drops(client, limit=1000))


def _files_of(dest: Path) -> dict[str, bytes]:
    return {
        p.relative_to(dest).as_posix(): p.read_bytes()
        for p in sorted(dest.rglob("*"))
        if p.is_file() and p.name != "meta.json"
    }


def _nquads(ds: rdflib.Dataset) -> list[str]:
    raw = ds.serialize(format="nquads")
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return sorted(line for line in text.splitlines() if line.strip())


def _assert_public_data_intact(ds: rdflib.Dataset, client: Any) -> None:
    """公開中のデータが消えていない（sweep を挟んでも、公開の graph に三つ組がある）。"""
    for _ in range(2):
        graphs = _canon(client)
        assert graphs, "nothing is published"
        for graph in graphs:
            assert _size(ds, graph) >= WORLD_TRIPLES, graph
        _sweep(client)


# --- 群の判定（純関数） ---------------------------------------------------------


def _data_bundle() -> Bundled:
    return read_bundled(_data_release())


def _plan_data(**over: Any) -> demo_sample.RefreshPlan:
    """C の環境（印なし）へ、データが変わる新しい版（``bundle`` を渡せば、その版）。"""
    new = over.pop("bundle", None) or _data_bundle()
    src = {
        p: hashlib.sha256(b).hexdigest()
        for p, b in _real_members().items()
        if p.startswith("registry/source/")
    }
    params: dict[str, Any] = {
        "meta": _meta(_real()),
        "files": _env_files(_real()),
        "decision_files": [],
        "bundled_subjects": _subjects(new.design_artifacts()),
        "data_env": demo_sample.DataEnv(
            source_shas=src, applied_batches=False, control_live=V1, control_staged=None
        ),
    }
    params.update(over)
    return plan_refresh(new, **params)


def test_plan_data_group_goes_in_when_nothing_was_touched() -> None:
    plan = _plan_data()
    assert plan.swap_data is True
    assert plan.held == []
    assert plan.derive_design is True
    # source（変わった・増えた）・RML が入る。同じものは入れ替えない
    assert set(plan.replace) == {
        "mapping.rml.ttl",
        "source/activity.csv",
        "source/notes.csv",
    }


def test_plan_data_unchanged_release_does_not_touch_the_data_unit() -> None:
    plan = plan_refresh(
        _real(),
        meta=_meta(_real()),
        files=_env_files(_real(), a_design=True),
        decision_files=[],
        bundled_subjects=_subjects(_real().design_artifacts()),
    )
    assert plan.swap_data is False
    assert all(h["unit"] != "data" for h in plan.held)


def test_plan_data_facts_unreadable_holds_the_whole_group_as_data() -> None:
    plan = _plan_data(data_env=None)
    assert plan.swap_data is False
    assert _reasons(plan) >= {("design", "data"), ("data", "data"), ("tools", "data")}
    assert plan.replace == {}
    assert "description" in plan.reached or plan.reproject_description  # 説明は独立に進む


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"meta": {"feed": True}}, "appended"),
        ({"meta": {"append_seq": 2}}, "appended"),
        ({"meta": {"appends": [{"seq": 1}]}}, "appended"),
        ({"meta": {"triples_appended": 3}}, "appended"),
        ({"meta": {"last_appended_at": "2026-09-30T00:00:00+00:00"}}, "appended"),
        ({"applied_batches": True}, "appended"),
        ({"meta": {"ingested": True}}, "reingested"),
        ({"meta": {"graph_iri": V1.replace("v1", "v2")}}, "reingested"),
        ({"control_staged": V1.replace("v1", "v2")}, "reingested"),
        ({"meta": {"live_graph": V1.replace("v1", "v9")}}, "reingested"),
        ({"control_live": V1.replace("v1", "v9")}, "unsettled"),
        ({"control_live": None}, "unsettled"),
        ({"decision_files": ["reshape.json"]}, "decisions"),
        ({"decision_files": ["handles.json"]}, "decisions"),
        ({"source_extra": "source/x.csv"}, "edited"),
        ({"source_edit": "registry/source/world.csv"}, "edited"),
        ({"design_edit": "registry/mapping.yaml"}, "edited"),
        ({"bundled_ids": "moved"}, "ids_move"),
        ({"bundled_ids": None}, "ids_unknown"),
        ({"meta": {"published_subjects": None}}, "ids_unknown"),
    ],
)
def test_plan_data_group_is_held_together_for_the_same_reason(
    over: dict[str, Any], reason: str
) -> None:
    """design・data・tools は同じ理由で全部保留になる（unit はそれぞれ）。"""
    params: dict[str, Any] = {}
    env = _plan_data()  # 既定の DataEnv を作り直すための土台
    assert env.swap_data
    src = {
        p: hashlib.sha256(b).hexdigest()
        for p, b in _real_members().items()
        if p.startswith("registry/source/")
    }
    fields: dict[str, Any] = {
        "source_shas": dict(src),
        "applied_batches": False,
        "control_live": V1,
        "control_staged": None,
    }
    files = _env_files(_real())
    for key, value in over.items():
        if key == "meta":
            params["meta"] = _meta(_real(), **value)
        elif key in ("applied_batches", "control_live", "control_staged"):
            fields[key] = value
        elif key == "source_extra":
            fields["source_shas"]["registry/" + value] = "0" * 64
        elif key == "source_edit":
            fields["source_shas"][value] = "1" * 64
        elif key == "design_edit":
            files[value] = files[value] + b"# edit\n"
        elif key == "bundled_ids":
            params["bundled_subjects"] = (
                None
                if value is None
                else [
                    {**s, "template": s["template"] + "/x"}
                    for s in _subjects(_data_bundle().design_artifacts())
                ]
            )
        else:
            params[key] = value
    plan = _plan_data(files=files, data_env=demo_sample.DataEnv(**fields), **params)
    assert plan.swap_data is False
    assert plan.replace == {}
    for unit in ("design", "data", "tools"):
        assert (unit, reason) in _reasons(plan), (unit, reason, plan.held)


def test_plan_data_a_new_source_file_is_not_a_touch_but_a_deleted_one_is() -> None:
    """同梱の最新の版で初めて入る source が環境に無いのは触った印でない。以前の版から
    あった source が消えているのは触った印。"""
    plan = _plan_data()
    assert plan.swap_data  # notes.csv が環境に無くても入る
    new = _data_bundle()
    env = demo_sample.DataEnv(
        source_shas={
            p: hashlib.sha256(b).hexdigest()
            for p, b in _real_members().items()
            if p == "registry/source/world.csv"
        },
        applied_batches=False,
        control_live=V1,
        control_staged=None,
    )
    plan = plan_refresh(
        new,
        meta=_meta(_real()),
        files=_env_files(_real()),
        decision_files=[],
        bundled_subjects=_subjects(new.design_artifacts()),
        data_env=env,
    )
    assert ("data", "edited") in _reasons(plan)  # activity.csv が消えている


def test_plan_data_a_source_added_by_a_skipped_release_is_not_a_touch() -> None:
    """環境（C = seq 3）が受け取っていない途中の版（seq 4）で足された source が、同梱の最新の版
    （seq 5）にも残っていて環境に無いのは、触った印ではない。環境が seq 4 まで届いていて、
    それが消えていれば触った印。"""
    later = read_bundled(_next_release(keep_notes=True))
    assert "registry/source/notes.csv" in later.files
    # 環境は C（seq 3）: notes.csv は無いが、消したのではない
    plan = _plan_data(bundle=later)
    assert plan.swap_data is True and plan.held == []
    assert "source/notes.csv" in plan.replace  # 入れ替えで足される

    # 環境は seq 4 まで届いていた（imported が seq 4 の canonical）のに notes.csv が無い → 消した
    meta = _meta(_real(), imported={"canonical_sha256": _data_bundle().canonical_sha256})
    plan = _plan_data(bundle=later, meta=meta)
    assert plan.swap_data is False
    for unit in ("design", "data", "tools"):
        assert (unit, "edited") in _reasons(plan)


# --- 実行: 入れ替えの結果 -----------------------------------------------------


def test_data_swap_publishes_the_new_data_and_reclaims_the_old(tmp_path: Path) -> None:
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path, meta_over={"alignment": {"reuse": 1}})
    before = _read_meta(dest)
    _refresh_release(tmp_path, client)

    # 公開はきっかり新へ。旧は掃除待ち・staged は無い
    assert _live(client) == _v(2)
    assert _canon(client) == [_v(2)]
    assert _v(1) in _pending(client)
    assert _staged(client) is None
    assert _size(ds, _v(2)) == WORLD_TRIPLES + 1
    _sweep(client)
    assert _size(ds, _v(1)) == 0
    assert _canon(client) == [_v(2)]

    m = _read_meta(dest)
    new = _data_bundle()
    assert m["live_graph"] == _v(2)
    assert m["data_seq"] == 2
    assert m["triple_count"] == m["triples_promoted"] == WORLD_TRIPLES + 1
    assert m["imported"] == {**before["imported"], "canonical_sha256": new.canonical_sha256}
    assert m["version"] == 2
    assert [v["version"] for v in m["versions"]] == [1, 2]
    assert m["versions"][1]["triples_promoted"] == WORLD_TRIPLES + 1
    assert m["versions"][1]["alignment"] == {"reuse": 1}
    assert m["promoted_at"] != before["promoted_at"]
    for key in (
        "published_subjects",
        "alignment",
        "created_at",
        "name",
        "origin",
        "status",
        "promoted",
        "canonical_graph",
        "ingested",
        "graph_iri",
    ):
        assert m[key] == before[key], key
    stamp = m["sample"]
    assert stamp["seq"] == new.seq
    assert stamp["data"] == {"live_graph": _v(2)}
    assert stamp["pending"] == []
    assert stamp["held"] == []
    assert stamp["units"] == {u: new.units[u] for u in ("design", "description", "tools", "name")}
    assert not (dest / "history").exists()
    # ファイルは同梱と同じ（設計・source・ツール）
    members = _release_members()
    for member, blob in members.items():
        if member.startswith("registry/") and member not in (
            "registry/meta.json",
            "registry/proposal.md",
        ):
            assert (dest / member[len("registry/") :]).read_bytes() == blob, member
    assert [p.name for p in (dest / "source").iterdir() if p.name.endswith(".tmp")] == []
    # 来歴の種類の表示名は新しい設計の ontology graph に
    assert _activity_labels(ds) == {"取り込みの記録"}
    assert [p.name for p in dest.iterdir() if p.name.endswith(".tmp")] == []


def test_data_swap_survives_the_lifespan(tmp_path: Path) -> None:
    ds, client = _old_store()
    _write_swap_env(tmp_path)
    _refresh_release(tmp_path, client)
    _lifespan(tmp_path, client)
    assert _live(client) == _v(2)
    assert _canon(client) == [_v(2)]
    assert _v(2) not in _pending(client)
    assert _staged(client) is None
    _sweep(client)
    assert _size(ds, _v(2)) == WORLD_TRIPLES + 1
    assert _size(ds, _v(1)) == 0


def test_data_swap_second_boot_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    _refresh_release(tmp_path, client)
    _lifespan(tmp_path, client)
    _sweep(client)
    meta_bytes = (dest / "meta.json").read_bytes()
    mtime = (dest / "meta.json").stat().st_mtime_ns
    files = _files_of(dest)
    nquads = _nquads(ds)
    client.updates = 0
    client.posts.clear()
    calls: list[str] = []
    for name in ("update_meta_atomic", "replace_artifact_bytes", "reserve_data_seq"):
        orig = getattr(registry, name)
        monkeypatch.setattr(
            registry, name, lambda *a, _n=name, _o=orig, **k: calls.append(_n) or _o(*a, **k)
        )
    proj = _Projections(monkeypatch)
    _refresh_release(tmp_path, client)
    assert calls == []
    assert client.updates == 0
    assert client.posts == []
    assert (proj.onto_calls, proj.meta_calls) == ([], [])
    assert (dest / "meta.json").read_bytes() == meta_bytes
    assert (dest / "meta.json").stat().st_mtime_ns == mtime
    assert _files_of(dest) == files
    assert _nquads(ds) == nquads


def test_data_swap_with_a_stamp_from_the_previous_release(tmp_path: Path) -> None:
    """印のある環境（C まで届いた）でも同じ。ツールを受け取った版は新しい版まで進む。"""
    _ds, client = _old_store()
    dest = _write_swap_env(tmp_path, stamp=True)
    _refresh_release(tmp_path, client)
    assert _canon(client) == [_v(2)]
    assert _read_meta(dest)["sample"]["tools_seq"] == _data_bundle().seq


def test_data_swap_needs_the_manifest_count_to_match(tmp_path: Path) -> None:
    """件数が合わなければ、その graph を消して中止する（meta・control・ファイルは旧のまま）。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    m = dict(_release_members())
    manifest = json.loads(m["manifest.json"])
    manifest["canonical_triples"] += 1
    m["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    path = tmp_path / "bad.tar"
    path.write_bytes(_tar_bytes(m))
    before_meta = _read_meta(dest)
    before_files = _files_of(dest)
    _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, path))
    assert _live(client) == V1
    assert _canon(client) == [V1]
    assert _size(ds, _v(2)) == 0
    assert _files_of(dest) == before_files
    after = _read_meta(dest)
    assert after["live_graph"] == V1 and after["version"] == 1
    assert after["imported"] == before_meta["imported"]


def test_data_swap_rebuilds_the_hub_after_the_control_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """見本を参加者に持つハブは、公開の切り替えのあとに作り直される。"""
    _ds, client = _old_store()
    _write_swap_env(tmp_path)
    seen: list[list[str]] = []

    async def rebuild(c: Any, root: Path, perspective_id: str = "p") -> dict | None:
        seen.append(await substrate.canonical_graphs(c))
        return None

    monkeypatch.setattr(main_mod, "_perspective_ids_for_dataset", lambda root, did: ["p"])
    monkeypatch.setattr(main_mod, "_rebuild_crosswalk_now", rebuild)
    _refresh_release(tmp_path, client)
    assert seen == [[_v(2)]]


def test_data_swap_a_failing_derivation_is_retried_next_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """例外を出した派生は、印の pending に残って次の起動でやり直される。

    注意: 本物の ``main._maybe_rebuild_crosswalk`` は失敗を握りつぶして例外を出さない
    （契約は「これを使う」と決めている）。なのでハブの再構築の失敗は、この経路では再試行
    されない。再試行が効くのは、プロセスが落ちた場合（下の 6 の途中の注入）と、
    ontology の投影・外部への配信が失敗した場合。ここは、その仕組み自体を固定する。"""
    _ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    calls: list[str] = []

    async def failing(c: Any, root: Path, did: str) -> None:
        calls.append("first")
        raise RuntimeError("hub failed")

    monkeypatch.setattr(main_mod, "_maybe_rebuild_crosswalk", failing)
    _refresh_release(tmp_path, client)
    assert _read_meta(dest)["sample"]["pending"] == ["crosswalk"]
    assert _canon(client) == [_v(2)]

    async def working(c: Any, root: Path, did: str) -> None:
        calls.append("second")

    monkeypatch.setattr(main_mod, "_maybe_rebuild_crosswalk", working)
    _refresh_release(tmp_path, client)
    assert calls == ["first", "second"]
    assert _read_meta(dest)["sample"]["pending"] == []
    assert _read_meta(dest)["version"] == 2  # 入れ替えをやり直していない


def test_data_swap_publishes_to_togomcp_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ds, client = _old_store()
    _write_swap_env(tmp_path)
    cfg = _cfg(tmp_path)
    cfg.togomcp_dir = tmp_path / "togomcp"
    published: list[tuple[str, str]] = []
    from asterism_api import togomcp_sync

    monkeypatch.setattr(
        togomcp_sync,
        "publish_dataset",
        lambda root, did, mie, live, **kw: published.append((did, live)) or {},
    )
    _run(demo_sample.refresh_bundled_sample(cfg, client, _release_file(tmp_path)))
    assert published == [(DATASET_ID, _v(2))]


# --- 番号は再利用しない --------------------------------------------------------


def test_data_swap_never_reuses_a_number_left_in_pending_drops_or_the_store(
    tmp_path: Path,
) -> None:
    ds, client = _old_store()
    _write_swap_env(tmp_path)
    # 落ちた取り込みの印（graph は無い）・孤児の graph・別の印
    _run(substrate.mark_pending_drop(client, _v(2)))
    ds.graph(rdflib.URIRef(_v(3))).add(
        (rdflib.URIRef("https://ex/a"), rdflib.URIRef("https://ex/b"), rdflib.Literal("c"))
    )
    _run(substrate.mark_pending_drop(client, _v(5)))
    _refresh_release(tmp_path, client)
    assert _live(client) == _v(6)
    assert _read_meta(tmp_path / "registry" / DATASET_ID)["data_seq"] == 6
    # 掃除は参照を見ずに消す。印の残った番号を公開していないので、公開中のものは残る
    _lifespan(tmp_path, client)
    _assert_public_data_intact(ds, client)
    assert _size(ds, _v(6)) == WORLD_TRIPLES + 1


# --- 保留: 群は同じ理由で全部保留・ストアもファイルも変わらない --------------------


def _set_meta(dest: Path, **kv: Any) -> None:
    meta = _read_meta(dest)
    for key, value in kv.items():
        if value is _DELETE:
            meta.pop(key, None)
        else:
            meta[key] = value
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")


_DELETE = object()


def _control_live(ds: rdflib.Dataset, graph: str) -> None:
    control = ds.graph(rdflib.URIRef(substrate.CONTROL_GRAPH_IRI))
    control.remove((rdflib.URIRef(KEY), rdflib.URIRef(substrate.LIVE_GRAPH_PREDICATE), None))
    control.add(
        (rdflib.URIRef(KEY), rdflib.URIRef(substrate.LIVE_GRAPH_PREDICATE), rdflib.URIRef(graph))
    )


def _edit(dest: Path, rel: str, extra: bytes = b"# edit\n") -> None:
    path = dest / rel
    path.write_bytes(path.read_bytes() + extra)


_HOLD_CASES: list[tuple[str, Any, str]] = [
    ("feed", lambda d, ds: _set_meta(d, feed=True), "appended"),
    ("append_seq", lambda d, ds: _set_meta(d, append_seq=1), "appended"),
    ("appends", lambda d, ds: _set_meta(d, appends=[{"seq": 1}]), "appended"),
    ("triples_appended", lambda d, ds: _set_meta(d, triples_appended=3), "appended"),
    ("last_appended_at", lambda d, ds: _set_meta(d, last_appended_at="2026-09-30"), "appended"),
    (
        "applied_batches",
        lambda d, ds: (
            (d / "source" / ".applied_batches").mkdir()
            or (d / "source" / ".applied_batches" / "abc").write_bytes(b"")
        ),
        "appended",
    ),
    ("reshape", lambda d, ds: (d / "reshape.json").write_text("{}"), "decisions"),
    ("handles", lambda d, ds: (d / "handles.json").write_text("{}"), "decisions"),
    ("source_edited", lambda d, ds: _edit(d, "source/world.csv"), "edited"),
    ("source_extra", lambda d, ds: (d / "source" / "mine.csv").write_bytes(b"x"), "edited"),
    ("source_deleted", lambda d, ds: (d / "source" / "activity.csv").unlink(), "edited"),
    ("design_edited", lambda d, ds: _edit(d, "mapping.yaml"), "edited"),
    ("rml_edited", lambda d, ds: _edit(d, "mapping.rml.ttl"), "edited"),
    ("live_graph_differs", lambda d, ds: _set_meta(d, live_graph=_v(9)), "reingested"),
    ("ingested", lambda d, ds: _set_meta(d, ingested=True, graph_iri=_v(2)), "reingested"),
    (
        "staged",
        lambda d, ds: ds.graph(rdflib.URIRef(substrate.CONTROL_GRAPH_IRI)).add(
            (
                rdflib.URIRef(KEY),
                rdflib.URIRef(substrate.STAGED_GRAPH_PREDICATE),
                rdflib.URIRef(_v(2)),
            )
        ),
        "reingested",
    ),
    ("control_differs", lambda d, ds: _control_live(ds, _v(7)), "unsettled"),
    (
        "ids_move",
        lambda d, ds: _set_meta(
            d,
            published_subjects=[
                {**s, "template": s["template"] + "/x"} for s in _read_meta(d)["published_subjects"]
            ],
        ),
        "ids_move",
    ),
    ("ids_unknown", lambda d, ds: _set_meta(d, published_subjects=_DELETE), "ids_unknown"),
]


@pytest.mark.parametrize(("name", "alter", "reason"), _HOLD_CASES, ids=[c[0] for c in _HOLD_CASES])
def test_data_swap_is_held_as_a_group_and_changes_nothing(
    tmp_path: Path, name: str, alter: Any, reason: str
) -> None:
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path, stamp=True)
    alter(dest, ds)
    files = _files_of(dest)
    before = _read_meta(dest)
    nquads = _nquads(ds)
    _refresh_release(tmp_path, client)

    assert client.posts == [], "no graph must be loaded"
    assert _nquads(ds) == nquads
    assert _files_of(dest) == files
    after = _read_meta(dest)
    assert {k: v for k, v in after.items() if k != "sample"} == {
        k: v for k, v in before.items() if k != "sample"
    }
    held = after["sample"]["held"]
    for unit in ("design", "data", "tools"):
        assert any(h["unit"] == unit and h["reason"] == reason for h in held), (unit, held)
    assert "data" not in after["sample"]
    assert not (dest / "history").exists()
    # 次の起動でも同じ（書き込みは無い）
    snapshot = (dest / "meta.json").read_bytes()
    _refresh_release(tmp_path, client)
    assert (dest / "meta.json").read_bytes() == snapshot


def test_data_swap_hold_cases_cover_every_reason_code() -> None:
    assert {c[2] for c in _HOLD_CASES} == {
        demo_sample.HELD_EDITED,
        demo_sample.HELD_APPENDED,
        demo_sample.HELD_REINGESTED,
        demo_sample.HELD_UNSETTLED,
        demo_sample.HELD_DECISIONS,
        demo_sample.HELD_IDS_MOVE,
        demo_sample.HELD_IDS_UNKNOWN,
    }


def test_data_swap_description_and_name_still_go_in_while_the_group_is_held(
    tmp_path: Path,
) -> None:
    """群が保留でも、description・name は独立に進む。"""
    _ds, client = _old_store()
    dest = _write_swap_env(tmp_path, stamp=True, meta_over={"name": _real().name})
    _set_meta(dest, feed=True)
    _refresh_release(tmp_path, client)
    reached = _read_meta(dest)["sample"]["units"]
    assert "description" in reached and "name" in reached
    assert "design" not in reached and "tools" not in reached


# --- 途中で落ちたとき: 次の起動で同じ最終状態に収束し、公開中のデータは消えない ---------


def _crash_at(
    position: str, monkeypatch: pytest.MonkeyPatch, client: _RecordingClient, dest: Path
) -> None:
    if position == "2-mid":
        client.crash_on_canonical_post = True
    elif position == "2-3":

        def before_files(*a: Any, **k: Any) -> None:
            raise _Crash()

        monkeypatch.setattr(registry, "replace_artifact_bytes", before_files)
    elif position == "3-mid":
        real = registry.replace_artifact_bytes

        def half(root: Path, dataset_id: str, files: dict[str, bytes]) -> None:
            items = list(files.items())
            real(root, dataset_id, dict(items[: len(items) // 2]))
            # 置き換えの途中で落ちると、同じフォルダに一時ファイルが残りうる
            (dest / "source" / f".activity.csv.{'a' * 32}.tmp").write_bytes(b"partial")
            raise _Crash()

        monkeypatch.setattr(registry, "replace_artifact_bytes", half)
    elif position == "4-5":

        async def no_promote(*a: Any, **k: Any) -> None:
            raise _Crash()

        monkeypatch.setattr(substrate, "promote_to_canonical", no_promote)
    elif position == "5-6":

        async def no_onto(*a: Any, **k: Any) -> int:
            raise _Crash()

        monkeypatch.setattr(main_mod, "_project_ontology_graph", no_onto)
    elif position == "6-mid":

        async def no_hub(*a: Any, **k: Any) -> None:
            raise _Crash()

        monkeypatch.setattr(main_mod, "_maybe_rebuild_crosswalk", no_hub)
    else:  # pragma: no cover
        raise AssertionError(position)


_POSITIONS = ["2-mid", "2-3", "3-mid", "4-5", "5-6", "6-mid"]


def _assert_converged(tmp_path: Path, ds: rdflib.Dataset, client: Any, dest: Path) -> None:
    live = _live(client)
    assert live is not None and live != V1
    assert _canon(client) == [live]
    assert _size(ds, live) == WORLD_TRIPLES + 1
    for graph in _run(substrate.all_version_graphs(client, dataset_id=DATASET_ID)):
        if graph != live:
            assert _size(ds, graph) == 0, graph
    assert _staged(client) is None
    m = _read_meta(dest)
    assert m["live_graph"] == live
    assert m["imported"]["canonical_sha256"] == _data_bundle().canonical_sha256
    assert m["version"] == 2 and len(m["versions"]) == 2
    assert m["triple_count"] == m["triples_promoted"] == WORLD_TRIPLES + 1
    stamp = m["sample"]
    assert stamp["data"] == {"live_graph": live}
    assert stamp["pending"] == [] and stamp["held"] == []
    assert set(stamp["units"]) == {"design", "description", "tools", "name"}
    assert not (dest / "history").exists()
    for member, blob in _release_members().items():
        if member.startswith("registry/") and member not in (
            "registry/meta.json",
            "registry/proposal.md",
        ):
            assert (dest / member[len("registry/") :]).read_bytes() == blob, member
    assert _activity_labels(ds) == {"取り込みの記録"}


@pytest.mark.parametrize("position", _POSITIONS)
def test_data_swap_crash_converges_on_the_next_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    with monkeypatch.context() as patch:
        _crash_at(position, patch, client, dest)
        with pytest.raises(_Crash):
            _refresh_release(tmp_path, client)
    client.crash_on_canonical_post = False
    # 落ちた直後も、掃除を挟んでも、公開中のデータは消えない
    _assert_public_data_intact(ds, client)

    # 次の起動: refresh → lifespan
    _refresh_release(tmp_path, client)
    _lifespan(tmp_path, client)
    _assert_public_data_intact(ds, client)
    _sweep(client)
    _assert_converged(tmp_path, ds, client, dest)
    # その次の起動は書き込みゼロ
    snapshot = (dest / "meta.json").read_bytes()
    client.posts.clear()
    _refresh_release(tmp_path, client)
    assert (dest / "meta.json").read_bytes() == snapshot
    assert client.posts == []


@pytest.mark.parametrize("position", ["4-5", "5-6", "6-mid"])
def test_data_swap_crash_without_a_refresh_the_lifespan_alone_keeps_the_new_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    """refresh が走らない環境（見本の環境変数が 0・tar が無い）でも、meta の後に落ちたなら
    lifespan が control を meta へ寄せ、新しいデータが公開されたまま残る。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    with monkeypatch.context() as patch:
        _crash_at(position, patch, client, dest)
        with pytest.raises(_Crash):
            _refresh_release(tmp_path, client)
    _lifespan(tmp_path, client)
    _assert_public_data_intact(ds, client)
    assert _live(client) == _v(2)
    assert _canon(client) == [_v(2)]
    # staged と live が同じ graph を指す状態を作らない（次の取り込みが公開中の graph を消す）
    assert _staged(client) is None
    _sweep(client)
    assert _size(ds, _v(1)) == 0


def test_data_swap_crash_before_the_meta_write_leaves_the_old_data_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    before = _read_meta(dest)
    with monkeypatch.context() as patch:
        _crash_at("2-3", patch, client, dest)
        with pytest.raises(_Crash):
            _refresh_release(tmp_path, client)
    _lifespan(tmp_path, client)
    _sweep(client)
    assert _live(client) == V1 and _canon(client) == [V1]
    assert _size(ds, V1) == WORLD_TRIPLES
    assert _size(ds, _v(2)) == 0  # 無参照の新しい graph は回収された
    after = _read_meta(dest)
    assert after["live_graph"] == V1 and after["version"] == 1
    assert after["imported"] == before["imported"]


def test_reserve_data_seq_writes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "registry"
    (root / "ds").mkdir(parents=True)
    meta_path = root / "ds" / "meta.json"
    meta_path.write_text(json.dumps({"id": "ds", "data_seq": 4}), encoding="utf-8")
    original = meta_path.read_bytes()
    with monkeypatch.context() as patch:

        def fail(src: Any, dst: Any) -> None:
            raise OSError("crash before the swap")

        patch.setattr(registry.os, "replace", fail)
        with pytest.raises(OSError):
            registry.reserve_data_seq(root, "ds")
    assert meta_path.read_bytes() == original  # 壊れない
    assert registry.reserve_data_seq(root, "ds") == 5
    assert json.loads(meta_path.read_text(encoding="utf-8"))["data_seq"] == 5


# --- 後の版が source を外す・2 回目のデータ変更版 ------------------------------------

_EXTRA_TRIPLE_2 = (
    "\n<https://asterism.invalid/datasets/world/resource/country/yy> "
    '<http://www.w3.org/2000/01/rdf-schema#label> "もう一つの国" .\n'
).encode()


def _next_release(*, keep_notes: bool) -> bytes:
    """データ変更版（seq 4）の次の版（seq 5）: canonical にもう 1 件足す。``keep_notes`` が偽なら、
    seq 4 で配った source（notes.csv）を外す。"""
    members = dict(_release_members())
    members["graphs/canonical.ttl"] = members["graphs/canonical.ttl"] + _EXTRA_TRIPLE_2
    manifest = json.loads(members["manifest.json"])
    manifest["canonical_sha256"] = hashlib.sha256(members["graphs/canonical.ttl"]).hexdigest()
    manifest["canonical_triples"] = _triples_of(members["graphs/canonical.ttl"])
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    if not keep_notes:
        del members["registry/source/notes.csv"]
    old = demo_sample.parse_ledger(members[LEDGER_MEMBER].decode("utf-8"))
    assert len(old) == 4
    entry = entry_from_members(members, seq=5, note={"ja": "次", "en": "next"})
    members[LEDGER_MEMBER] = format_ledger([*old, entry]).encode("utf-8")
    return _tar_bytes(members)


def test_plan_data_a_later_release_may_drop_a_source_it_once_shipped() -> None:
    """後の版が source を外しても、環境のそのファイルが配ったバイトのままなら触った印でない。
    手が入っていれば触った印。"""
    later = read_bundled(_next_release(keep_notes=False))
    assert "registry/source/notes.csv" not in later.files
    shipped = hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    base = {
        p: hashlib.sha256(b).hexdigest()
        for p, b in _real_members().items()
        if p.startswith("registry/source/")
    }
    # 環境は seq 4 まで届いている（notes.csv が置いてある）
    imported = {"canonical_sha256": _data_bundle().canonical_sha256}
    meta = _meta(_real(), imported=imported, live_graph=V1)
    env = demo_sample.DataEnv(
        source_shas={**base, "registry/source/notes.csv": shipped},
        applied_batches=False,
        control_live=V1,
        control_staged=None,
    )
    plan = _plan_data(bundle=later, meta=meta, data_env=env)
    assert plan.swap_data is True and plan.held == []

    env_edited = demo_sample.DataEnv(
        source_shas={**base, "registry/source/notes.csv": "2" * 64},
        applied_batches=False,
        control_live=V1,
        control_staged=None,
    )
    plan = _plan_data(bundle=later, meta=meta, data_env=env_edited)
    assert plan.swap_data is False
    for unit in ("design", "data", "tools"):
        assert (unit, "edited") in _reasons(plan)


@pytest.mark.parametrize("keep_notes", [True, False], ids=["keeps-source", "drops-source"])
def test_data_swap_a_second_data_release_reaches_a_swapped_environment(
    tmp_path: Path, keep_notes: bool
) -> None:
    """1 度入れ替えた環境（印の data.live_graph が v2）へ、次のデータ変更版も届く。
    外れた source は環境に残る（消さない）。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    _refresh_release(tmp_path, client)
    _lifespan(tmp_path, client)
    assert _live(client) == _v(2)

    path = tmp_path / "release5.tar"
    path.write_bytes(_next_release(keep_notes=keep_notes))
    _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, path))
    assert _live(client) == _v(3)
    assert _canon(client) == [_v(3)]
    _lifespan(tmp_path, client)
    _sweep(client)
    assert _size(ds, _v(3)) == WORLD_TRIPLES + 2
    assert _size(ds, _v(2)) == 0
    m = _read_meta(dest)
    assert m["live_graph"] == _v(3) and m["version"] == 3
    assert m["sample"]["seq"] == 5 and m["sample"]["held"] == []
    assert m["sample"]["data"] == {"live_graph": _v(3)} and m["sample"]["pending"] == []
    assert (dest / "source" / "notes.csv").read_bytes() == b"a,b\n1,2\n"
    # 次の起動は書き込みゼロ
    snapshot = (dest / "meta.json").read_bytes()
    client.posts.clear()
    _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, path))
    assert (dest / "meta.json").read_bytes() == snapshot and client.posts == []


def test_data_swap_reaches_an_environment_that_skipped_the_release_which_added_a_source(
    tmp_path: Path,
) -> None:
    """C の環境へ、seq 4 を飛ばして seq 5（seq 4 で足した notes.csv を残す版）が来ても、
    「notes.csv を消した」とは数えず、入れ替わる。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    assert not (dest / "source" / "notes.csv").exists()
    path = tmp_path / "release5.tar"
    path.write_bytes(_next_release(keep_notes=True))
    _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, path))
    assert _live(client) == _v(2)
    assert _size(ds, _v(2)) == WORLD_TRIPLES + 2
    m = _read_meta(dest)
    assert m["sample"]["held"] == []
    assert (dest / "source" / "notes.csv").read_bytes() == b"a,b\n1,2\n"


def test_data_swap_after_a_crash_before_the_files_the_next_release_still_goes_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手順 3 の前で落ち（source は置かれず、meta は C のまま）、次の起動では次の版（seq 5）が
    来る。途中の版で足された source が環境に無いのを、触った印にしない。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    with monkeypatch.context() as patch:
        _crash_at("2-3", patch, client, dest)
        with pytest.raises(_Crash):
            _refresh_release(tmp_path, client)
    assert not (dest / "source" / "notes.csv").exists()
    path = tmp_path / "release5.tar"
    path.write_bytes(_next_release(keep_notes=True))
    _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, path))
    _lifespan(tmp_path, client)
    _assert_public_data_intact(ds, client)
    assert _live(client) == _v(3)  # 落ちた回の graph（v2）の番号は再利用しない
    assert _size(ds, _v(3)) == WORLD_TRIPLES + 2
    m = _read_meta(dest)
    assert m["sample"]["held"] == [] and m["version"] == 2
    assert (dest / "source" / "notes.csv").read_bytes() == b"a,b\n1,2\n"


def _shaped_release() -> bytes:
    """データが変わる版（``_data_release`` と同じ）で、同梱の meta.json の source の欄と、
    図の種類の数（2 → 3）も変えた合成の版。"""
    m = _real_members()
    meta = json.loads(m["registry/meta.json"])
    meta["source_files"] = ["activity.csv", "notes.csv", "world.csv"]
    meta["source_kind"] = "mixed"
    meta["has_source"] = True
    diagram = (
        m["registry/diagram.md"]
        .decode("utf-8")
        .replace(
            "    Observation --> Country",
            '    class Extra["三つ目"] { world:extra }\n    Observation --> Country',
        )
    )
    return _bundle_tar(
        {
            "graphs/canonical.ttl": m["graphs/canonical.ttl"] + _EXTRA_TRIPLE,
            "registry/source/notes.csv": b"a,b\n1,2\n",
            "registry/mapping.rml.ttl": m["registry/mapping.rml.ttl"] + b"\n# new release\n",
            "registry/meta.json": json.dumps(meta, ensure_ascii=False).encode("utf-8"),
            "registry/diagram.md": diagram.encode("utf-8"),
        }
    )


def test_data_swap_writes_the_source_fields_and_classes_in_the_meta_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手順 4 の meta が、同梱の meta の source の欄と、新しい図の種類（数）を書く。
    後段（派生）が書き直す前 — 4 と 5 の間で落とした直後の meta で確かめる。"""
    _ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    before = _read_meta(dest)
    assert before["source_files"] == ["activity.csv", "world.csv"]
    assert before["class_count"] == 2
    path = tmp_path / "shaped.tar"
    path.write_bytes(_shaped_release())
    with monkeypatch.context() as patch:
        _crash_at("4-5", patch, client, dest)
        with pytest.raises(_Crash):
            _run(demo_sample.refresh_bundled_sample(_cfg(tmp_path), client, path))
    m = _read_meta(dest)
    assert m["source_files"] == ["activity.csv", "notes.csv", "world.csv"]
    assert m["source_kind"] == "mixed"
    assert m["has_source"] is True
    assert m["classes"] == ["国", "年ごとの記録", "三つ目"]
    assert m["class_count"] == 3


# --- 途中で落ちたあとの、回復の側の動き ------------------------------------------------


def _hub_recorder(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """ハブの再構築が呼ばれた時点の ``canonical_graphs()`` を記録する（見本が参加者のハブ）。"""
    seen: list[list[str]] = []

    async def rebuild(c: Any, root: Path, perspective_id: str = "p") -> dict | None:
        seen.append(await substrate.canonical_graphs(c))
        return None

    monkeypatch.setattr(main_mod, "_perspective_ids_for_dataset", lambda root, did: ["p"])
    monkeypatch.setattr(main_mod, "_rebuild_crosswalk_now", rebuild)
    return seen


@pytest.mark.parametrize("position", ["4-5", "5-6", "6-mid"])
def test_data_swap_crash_recovery_finishes_the_derivations_on_the_new_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    """meta を書いた後に落ちた環境の次の refresh は、lifespan を待たずに公開を新へ切り替え、
    印の pending に残った派生（ハブ・外部への配信）を、新しい graph に対して 1 回やり直す。"""
    from asterism_api import togomcp_sync

    _ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    cfg = _cfg(tmp_path)
    cfg.togomcp_dir = tmp_path / "togomcp"
    release = _release_file(tmp_path)
    with monkeypatch.context() as patch:
        _crash_at(position, patch, client, dest)
        with pytest.raises(_Crash):
            _run(demo_sample.refresh_bundled_sample(cfg, client, release))
    # 入れ替えの手順 4 が、残りの派生を印に書いている（これが無いと二度とやり直されない）
    stamp = _read_meta(dest)["sample"]
    assert stamp["pending"] == ["design_projection", "crosswalk", "togomcp"]
    assert stamp["data"] == {"live_graph": _v(2)}
    assert _live(client) == (V1 if position == "4-5" else _v(2))

    seen = _hub_recorder(monkeypatch)
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(
        togomcp_sync,
        "publish_dataset",
        lambda root, did, mie, live, **kw: published.append((did, live)) or {},
    )
    # lifespan は挟まない: 回復は refresh の冒頭で完結する
    _run(demo_sample.refresh_bundled_sample(cfg, client, release))
    assert _live(client) == _v(2)
    assert _canon(client) == [_v(2)]
    assert seen == [[_v(2)]]
    assert published == [(DATASET_ID, _v(2))]
    assert _read_meta(dest)["sample"]["pending"] == []
    assert _read_meta(dest)["version"] == 2  # 入れ替えをやり直していない


def test_data_swap_crash_recovery_drops_the_pending_when_the_user_reingested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入れ替えの後で落ち、利用者が取り込み直した（meta.live_graph が印の data.live_graph と
    違う）なら、残っていた派生は要らない — やり直さず、pending を空にする。"""
    _ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    with monkeypatch.context() as patch:
        _crash_at("5-6", patch, client, dest)
        with pytest.raises(_Crash):
            _refresh_release(tmp_path, client)
    assert _read_meta(dest)["sample"]["pending"] == ["design_projection", "crosswalk"]
    _set_meta(dest, live_graph=_v(9))
    seen = _hub_recorder(monkeypatch)
    _refresh_release(tmp_path, client)
    assert seen == []
    assert _read_meta(dest)["sample"]["pending"] == []


def test_data_swap_crash_recovery_does_not_promote_an_emptied_new_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4 と 5 の間で落ち、次の起動までに新しい graph が空になっていたら、公開を空の graph へ
    切り替えない（公開中の旧いデータを失わない）。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    with monkeypatch.context() as patch:
        _crash_at("4-5", patch, client, dest)
        with pytest.raises(_Crash):
            _refresh_release(tmp_path, client)
    assert _read_meta(dest)["live_graph"] == _v(2)
    ds.graph(rdflib.URIRef(_v(2))).remove((None, None, None))
    assert _size(ds, _v(2)) == 0

    promoted: list[str] = []
    real = substrate.promote_to_canonical

    async def spy(c: Any, key: str, graph: str) -> Any:
        promoted.append(graph)
        return await real(c, key, graph)

    monkeypatch.setattr(substrate, "promote_to_canonical", spy)
    _refresh_release(tmp_path, client)
    assert promoted == []
    assert _live(client) == V1
    assert _canon(client) == [V1]
    assert _size(ds, V1) == WORLD_TRIPLES


# --- 番号の下限: 3 つの出どころをそれぞれ単独で -----------------------------------------


@pytest.mark.parametrize(
    "case",
    ["store-orphan", "pending-drop", "meta-live", "other-dataset"],
)
def test_number_floor_counts_each_source_on_its_own(case: str) -> None:
    ds, client = _old_store()  # ストアには v1 だけ
    meta: dict[str, Any] = {}
    expected = 1
    if case == "store-orphan":
        ds.graph(rdflib.URIRef(_v(8))).add(
            (rdflib.URIRef("https://ex/a"), rdflib.URIRef("https://ex/b"), rdflib.Literal("c"))
        )
        expected = 8
    elif case == "pending-drop":
        _run(substrate.mark_pending_drop(client, _v(9)))
        expected = 9
    elif case == "meta-live":
        meta = {"live_graph": _v(7)}
        expected = 7
    else:  # 別のデータセットの番号は数えない
        other = substrate.versioned_graph_iri("other", 50)
        _run(substrate.mark_pending_drop(client, other))
        meta = {"live_graph": other}
    assert _run(demo_sample._number_floor(client, DATASET_ID, meta)) == expected


# --- IRI の土台（rebase） ---------------------------------------------------------------


def test_data_swap_rebases_the_new_graph_like_an_import(tmp_path: Path) -> None:
    ds, client = _old_store()
    _write_swap_env(tmp_path)
    cfg = _cfg(tmp_path, env_extra={"ASTERISM_IRI_BASE": "https://example.test"})
    _run(demo_sample.refresh_bundled_sample(cfg, client, _release_file(tmp_path)))
    assert _live(client) == _v(2)
    subjects = {str(s) for s in ds.graph(rdflib.URIRef(_v(2))).subjects()}
    assert any(s.startswith("https://example.test/datasets/") for s in subjects)
    assert not any(s.startswith("https://asterism.invalid/datasets/") for s in subjects)


def test_data_swap_holds_off_when_a_replaced_file_needs_a_rebase(tmp_path: Path) -> None:
    """カスタムの IRI の土台で、置き換えるファイルに元の土台の IRI が入る版は入れない
    （import と違い、入れ替えはファイルをバイトのまま置くため。何も書かずに止まる）。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path)
    m = _real_members()
    tar = _bundle_tar(
        {
            "graphs/canonical.ttl": m["graphs/canonical.ttl"] + _EXTRA_TRIPLE,
            "registry/mapping.rml.ttl": m["registry/mapping.rml.ttl"]
            + b"\n# https://asterism.invalid/datasets/world/x\n",
        }
    )
    path = tmp_path / "rebase.tar"
    path.write_bytes(tar)
    files = _files_of(dest)
    before = _read_meta(dest)
    nquads = _nquads(ds)
    cfg = _cfg(tmp_path, env_extra={"ASTERISM_IRI_BASE": "https://example.test"})
    _run(demo_sample.refresh_bundled_sample(cfg, client, path))
    assert client.posts == []
    assert _nquads(ds) == nquads
    assert _files_of(dest) == files
    assert _read_meta(dest)["live_graph"] == before["live_graph"]
    assert _live(client) == V1
