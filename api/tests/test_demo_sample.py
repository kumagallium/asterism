"""同梱の見本を、すでにある環境へ届ける（ADR kantan K59）。

契約メモ contract_sample_refresh.md のテスト節に 1 対 1 で対応する。

* 実物の検査（台帳・単位の表・データの歯止め）— 直しを外すと落ちる
* ``plan_refresh``（純関数）
* ツールの合流
* 実行（tmp の registry に A の版のファイルを置いて refresh）
* 失敗（投影の例外・原子的な書き込み・dataset の外へのパス）
* 起動の順（``run_startup_sample``・``main()``）
* 実 rdflib の Dataset を注入したストアで ontology graph が入れ替わる

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


def test_data_changing_release_is_stopped_here() -> None:
    """データが変わる版は、いまは既存の環境に届かない（データの入れ替えは PR2）。
    台帳の全エントリで、データ（canonical_sha256・RML・source）が同じであること。"""
    ledger = demo_sample.load_ledger_file(LEDGER_FILE)
    first = ledger[0]

    def data_files(entry: dict[str, Any]) -> dict[str, str]:
        return {
            p: s
            for p, s in entry["files"].items()
            if p == "registry/mapping.rml.ttl" or p.startswith("registry/source/")
        }

    message = (
        "データが変わる版は既存の環境に届きません。ADR kantan K59 の『データが変わる版』を"
        "読み、データの入れ替えを先に作ってください"
    )
    for entry in ledger:
        assert entry["canonical_sha256"] == first["canonical_sha256"], message
        assert data_files(entry) == data_files(first), message


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
    ["display-meta.json", "column-decisions.json", "column-meanings.json", "handles.json"],
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


def _cfg(tmp_path: Path, *, single_user: bool = True) -> Settings:
    env = {
        "CSV2RDF_REGISTRY_ROOT": str(tmp_path / "registry"),
        "ASTERISM_APPDATA_ROOT": str(tmp_path / "appdata"),
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


def test_refresh_decision_file_holds_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, extra={"handles.json": b"{}"})
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
