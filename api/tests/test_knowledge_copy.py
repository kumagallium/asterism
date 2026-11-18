"""知識の束を 1 つのデータセットに写す口（ADR ``knowledge-copy.md``）。

固定するもの: 束 → 設計（機械が組む）→ 取り込み → 公開が 1 回の呼び出しで通ること・取り込み元ごとに
写しは 1 つで 2 回目は作り直しになること・断る条件・MCP の口の信頼境界（単一ユーザー限定・有効な
サーバだけ・道具名は設定の 1 つ）・照合の規則が論文の道と同じであること。

例は作り物（パン作り）。Morph-KGC は差し替えない（組み込みの設計が本当に行を作ることを見る）。
"""

# ruff: noqa: RUF001  — 照合の見本に全角の英字とハイフン類を使う

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from asterism import knowledge_bundle as kb
from asterism import substrate, term_match
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport

from asterism_api import chat_paper, registry
from asterism_api.knowledge_copy import (
    CopyError,
    copy_is_current,
    copy_knowledge,
    find_copy_dataset,
)
from asterism_api.main import build_app
from asterism_api.mcp_client import McpClient, parse_source_provider

from .test_ingest import _AUTH, _settings
from .test_mcp_client import _single_user_settings, _write_settings
from .test_mcp_resync_rows import _RecOxi

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")

_CLAIM = "https://notes.test/claim/"
_SOURCE = "https://notes.test/source/"
_TERM = "https://notes.test/term/"
_AT = "2026-11-18T00:00:00.000Z"
_KN = "https://kumagallium.github.io/asterism/vocab/knowledge#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"


def _claim(cid: str, title: str, state: str = "candidate") -> dict:
    return {
        "id": cid,
        "iri": _CLAIM + cid,
        "source_url": f"https://notes.test/app/#knowledge/claim/{cid}",
        "title": title,
        "review_state": state,
        "epistemic_status": "observation",
        "modal_qualifier": None,
        "level": "finding",
        "source_iri": [_SOURCE + "note-1"],
        "generated_at": _AT,
        "exported_at": _AT,
    }


def _candidate(cid: str, name: str, kind: str, role: str | None, origin: str) -> dict:
    return {
        "id": cid,
        "name": name,
        "kind": kind,
        "role": role,
        "aliases": [],
        "origin_id": origin,
        "origin_iri": _CLAIM + origin,
        "source_url": f"https://notes.test/app/#knowledge/topic/{origin}",
        "exported_at": _AT,
    }


def _bundle() -> dict:
    """パン作りの束: 知見 3・トピック 3・資料 1・語の候補 5（うち 2 つは同じ語の表記ゆれ）。"""
    topics = [
        ("tp-flour", "強力粉 の 吸水率", "material", "強力粉", "吸水率"),
        ("tp-autolyse", "オートリーズ", "method", None, None),
        ("tp-flour2", "強力粉", "material", "強力粉", None),
    ]
    return {
        "format": kb.FORMAT,
        "title": "Notes",
        "exported_at": _AT,
        "bases": {"claim": _CLAIM, "source": _SOURCE, "term": _TERM},
        "files": {
            "claims.json": [
                _claim("c1", "強力粉は吸水率が高い", "verified"),
                _claim("c2", "オートリーズで強力粉の生地がまとまりやすくなる"),
                _claim("c3", "発酵の温度を上げると膨らみが速い"),
            ],
            "topics.json": [
                {
                    "id": tid,
                    "iri": _CLAIM + tid,
                    "source_url": f"https://notes.test/app/#knowledge/topic/{tid}",
                    "title": title,
                    "topic_type": ttype,
                    "material": mat,
                    "property": prop,
                    "claim_iri": [],
                    "source_iri": [_SOURCE + "note-1"],
                    "generated_at": _AT,
                    "exported_at": _AT,
                }
                for tid, title, ttype, mat, prop in topics
            ],
            "sources.json": [
                {
                    "id": "note-1",
                    "iri": _SOURCE + "note-1",
                    "name": "パンの試作ノート",
                    "kind": "note",
                    "exported_at": _AT,
                }
            ],
            "term_candidates.json": [
                _candidate("tp-flour.m", "強力粉", "class", "material", "tp-flour"),
                _candidate("tp-flour.p", "吸水率", "property", None, "tp-flour"),
                _candidate("tp-autolyse", "オートリーズ", "class", "method", "tp-autolyse"),
                _candidate("tp-flour2", "強力粉", "class", "material", "tp-flour2"),
            ],
            "term_claims.json": [],
            "term_relations.json": [
                {
                    "from_term": "tp-flour.m",
                    "rel": "has_property",
                    "to_term": "tp-flour.p",
                    "origin_id": "tp-flour",
                    "origin_iri": _CLAIM + "tp-flour",
                }
            ],
        },
    }


def _app(tmp_path: Path, oxi: _RecOxi):
    return build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)


def _term_id(copied: Path, name: str) -> str:
    terms = json.loads((copied / "terms.json").read_text(encoding="utf-8"))
    return next(t["id"] for t in terms if t["name"] == name)


def test_copy_designs_ingests_and_publishes_in_one_call(tmp_path: Path) -> None:
    oxi = _RecOxi()
    with TestClient(_app(tmp_path, oxi), headers=_AUTH) as client:
        r = client.post("/api/knowledge-copies", json={"source": "notes", "bundle": _bundle()})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] is True and out["triples"] > 0
    # 表記ゆれ（同じ「強力粉」が 2 つのトピックから）は 1 語に束ねる
    assert out["counts"] == {
        "claims.json": 3,
        "topics.json": 3,
        "sources.json": 1,
        "terms.json": 3,
        "term_claims.json": 4,
        "term_relations.json": 1,
    }
    root = tmp_path / "registry"
    dataset_id = out["dataset_id"]
    meta = registry.load_meta(root, dataset_id)
    assert meta["promoted"] is True and meta["live_graph"] == out["version"]
    assert meta["name"] == "Notes の知見"
    ks = meta["knowledge_source"]
    assert ks["name"] == "notes" and ks["format"] == kb.FORMAT and ks["copied_at"]
    assert ks["version"] == out["version"] and "syncing" not in ks
    assert copy_is_current(meta)
    assert find_copy_dataset(root, "notes") == dataset_id

    # 写しの source は束ねた後の 6 ファイル（語の候補そのものは残さない）
    src = root / dataset_id / "source"
    assert sorted(p.name for p in src.glob("*.json")) == [
        "claims.json",
        "sources.json",
        "term_claims.json",
        "term_relations.json",
        "terms.json",
        "topics.json",
    ]
    flour, rate = _term_id(src, "強力粉"), _term_id(src, "吸水率")
    body = oxi.stored[out["version"]]
    # 知見は題と確認の状態を持ち、出た語へ線が引かれる（c1 は強力粉と吸水率の両方）
    assert f'<{_CLAIM}c1> <{_RDFS}label> "強力粉は吸水率が高い"' in body
    assert f'<{_CLAIM}c1> <{_KN}reviewState> "verified"' in body
    assert f"<{_CLAIM}c1> <{_KN}mentions> <{_TERM}{flour}>" in body
    assert f"<{_CLAIM}c1> <{_KN}mentions> <{_TERM}{rate}>" in body
    assert f"<{_CLAIM}c3> <{_KN}mentions>" not in body  # 語の出ない知見には線が無い
    # 語は種類と役割の札を持つ（物性は項目・役割なし）
    assert f'<{_TERM}{flour}> <{_KN}role> "material"' in body
    assert f'<{_TERM}{rate}> <{_KN}termKind> "property"' in body
    assert f"<{_TERM}{rate}> <{_KN}role>" not in body
    # 資料・トピック・題から来た関係の候補
    assert f"<{_SOURCE}note-1>" in body and f"<{_CLAIM}tp-autolyse>" in body
    assert f'<{_KN}relation> "has_property"' in body


def test_second_copy_rebuilds_the_same_dataset(tmp_path: Path) -> None:
    oxi = _RecOxi()
    smaller = _bundle()
    smaller["files"]["claims.json"] = smaller["files"]["claims.json"][:2]
    with TestClient(_app(tmp_path, oxi), headers=_AUTH) as client:
        first = client.post(
            "/api/knowledge-copies", json={"source": "notes", "bundle": _bundle()}
        ).json()
        second = client.post("/api/knowledge-copies", json={"source": "notes", "bundle": smaller})
        other = client.post(
            "/api/knowledge-copies", json={"source": "other", "bundle": _bundle(), "lang": "en"}
        ).json()
    assert second.status_code == 200, second.text
    out = second.json()
    assert out["dataset_id"] == first["dataset_id"] and out["created"] is False
    assert out["version"] == substrate.versioned_graph_iri(out["dataset_id"], 2) != first["version"]
    # 取り込み元で消えた知見は、作り直した版に無い
    assert f"<{_CLAIM}c3>" in oxi.stored[first["version"]]
    assert f"<{_CLAIM}c3>" not in oxi.stored[out["version"]]
    assert registry.load_meta(tmp_path / "registry", out["dataset_id"])["promoted"] is True
    # 別の取り込み元は別のデータセット
    assert other["created"] is True and other["dataset_id"] != first["dataset_id"]
    other_meta = registry.load_meta(tmp_path / "registry", other["dataset_id"])
    assert other_meta["name"] == "Notes knowledge"


def test_retracted_copy_is_not_reused(tmp_path: Path) -> None:
    oxi = _RecOxi()
    root = tmp_path / "registry"
    with TestClient(_app(tmp_path, oxi), headers=_AUTH) as client:
        first = client.post(
            "/api/knowledge-copies", json={"source": "notes", "bundle": _bundle()}
        ).json()
        registry.update_meta_atomic(root, first["dataset_id"], {"status": "retracted"})
        assert find_copy_dataset(root, "notes") is None
        again = client.post(
            "/api/knowledge-copies", json={"source": "notes", "bundle": _bundle()}
        ).json()
    assert again["created"] is True and again["dataset_id"] != first["dataset_id"]


def _without_claims() -> dict:
    b = _bundle()
    b["files"]["claims.json"] = []
    return b


def _with_base(base: str) -> dict:
    b = _bundle()
    b["bases"]["claim"] = base
    return b


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"source": "notes", "bundle": _without_claims()}, 422),
        ({"source": "notes", "bundle": _with_base("https://example.org/claim/")}, 422),
        ({"source": "notes", "bundle": _with_base("https://notes.test/claim")}, 422),
        ({"source": "notes", "bundle": {**_bundle(), "format": "something/9"}}, 422),
        ({"source": "notes", "bundle": None}, 422),
        ({"source": "no/slash", "bundle": _bundle()}, 400),
        ({"bundle": _bundle()}, 400),
    ],
    ids=["no-claims", "placeholder", "open-base", "format", "no-bundle", "bad-name", "no-name"],
)
def test_copy_refusals_create_nothing(tmp_path: Path, body: dict, status: int) -> None:
    oxi = _RecOxi()
    with TestClient(_app(tmp_path, oxi), headers=_AUTH) as client:
        r = client.post("/api/knowledge-copies", json=body)
    assert r.status_code == status, r.text
    assert registry.list_datasets(tmp_path / "registry") == []
    assert oxi.stored == {}


def test_copy_requires_the_write_token(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _RecOxi())) as client:
        r = client.post("/api/knowledge-copies", json={"source": "notes", "bundle": _bundle()})
        m = client.post("/api/mcp-servers/fake/knowledge-copy")
    assert r.status_code == 401 and m.status_code == 401


# ---- MCP の口 ---------------------------------------------------------------------------------

_SERVERS = {
    "fake": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": True},
    "off": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": False},
}


def _mcp_client(tmp_path: Path, oxi: _RecOxi, provider: dict) -> tuple[TestClient, dict]:
    """束を出す道具を持つ偽サーバを transport に差し込む。設定は settings.json に実際に書く。"""
    settings = _single_user_settings(tmp_path)
    _write_settings(tmp_path, {"mcpServers": _SERVERS, "sourceProvider": provider})
    app = build_app(settings, oxigraph_client=oxi.client, start_watcher=False)
    seen: dict = {}
    srv = FastMCP("fake-knowledge")

    @srv.tool
    def export_knowledge() -> str:
        seen["calls"] = seen.get("calls", 0) + 1
        return json.dumps(_bundle(), ensure_ascii=False)

    @srv.tool
    def list_media() -> str:
        seen["list"] = True
        return json.dumps({"items": []})

    app.state.mcp_registry.client = McpClient(transport_factory=lambda cfg: FastMCPTransport(srv))
    return TestClient(app, headers=_AUTH), seen


def test_mcp_route_pulls_the_bundle_with_the_configured_tool(tmp_path: Path) -> None:
    oxi = _RecOxi()
    client, seen = _mcp_client(tmp_path, oxi, {"knowledge": "export_knowledge"})
    with client:
        r = client.post("/api/mcp-servers/fake/knowledge-copy")
        listed = client.get("/api/mcp-servers").json()
        # 束を出す道具は、汎用の /call からは呼べない
        call = client.post("/api/mcp-servers/fake/call", json={"tool": "export_knowledge"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["server"] == "fake" and out["created"] is True
    assert seen == {"calls": 1}
    # 写しの持ち主は MCP のサーバ名
    assert find_copy_dataset(tmp_path / "registry", "fake") == out["dataset_id"]
    assert listed["source_provider"]["knowledge"] == "export_knowledge"
    assert call.status_code == 403


def test_mcp_route_trust_boundary(tmp_path: Path) -> None:
    oxi = _RecOxi()
    client, seen = _mcp_client(tmp_path, oxi, {"list": "list_media"})
    with client:
        # 道具名が設定に無い・未有効・未登録は、どれも繋がない
        assert client.post("/api/mcp-servers/fake/knowledge-copy").status_code == 409
        assert client.post("/api/mcp-servers/off/knowledge-copy").status_code == 409
        assert client.post("/api/mcp-servers/nope/knowledge-copy").status_code == 404
    assert seen == {} and oxi.stored == {}
    # 単一ユーザーでなければ口ごと 404
    with TestClient(_app(tmp_path / "shared", _RecOxi()), headers=_AUTH) as shared:
        assert shared.post("/api/mcp-servers/fake/knowledge-copy").status_code == 404


def test_knowledge_tool_name_is_a_source_provider_setting() -> None:
    assert parse_source_provider({}).knowledge == ""
    p = parse_source_provider({"sourceProvider": {"knowledge": " export_knowledge "}})
    assert (p.knowledge, p.list) == ("export_knowledge", "")


# ---- 設計と照合 -------------------------------------------------------------------------------


def test_built_in_design_passes_the_design_checks(tmp_path: Path) -> None:
    """組み込みの設計は、保存の検査（materialize）を指摘なしで通る。"""
    from asterism_api.chat_attach import _materialize_problems

    bundle = kb.parse_bundle(copy.deepcopy(_bundle()))
    with TestClient(_app(tmp_path, _RecOxi()), headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={"proposal_md": kb.design_document(bundle, name="t"), "dataset_name": "t"},
        )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert _materialize_problems(saved) == []
    rml = registry.load_dataset(tmp_path / "registry", saved["dataset"]["id"])["artifacts"][
        "mapping.rml.ttl"
    ]
    for base in (_CLAIM, _SOURCE, _TERM):
        assert base in rml


_PAIRS = [
    ("thermal conductivity", "The thermal conductivities of both samples were low."),
    ("Thermal Conductivity", "lattice thermal conductivity drops"),
    ("ZT", "the ZT value is high"),
    ("Bi2Te3", "Bi2Te3-based alloys"),
    ("Bi2Te3", "Bi2Te3Se is different"),
    ("matrix", "the matrices were dense"),
    ("analysis", "two analyses agree"),
    ("熱伝導率", "格子熱伝導率が下がる"),
    ("オートリーズ", "オート リーズで生地がまとまる"),
    ("X-ray diffraction", "X‑ray‐diffraction patterns"),
    ("thermo-electric", "thermo-\nelectric materials"),
    ("Ｓｅｅｂｅｃｋ coefficient", "the Seebeck coefficients"),
    ("gas", "gases and liquids"),
    ("series", "a series of tests"),
    ("the bread's crust", "The bread's crust browned."),
    ("crust", "encrusted"),
    ("相", "相が変わる"),
    ("", "anything"),
]


def test_term_match_agrees_with_the_paper_path() -> None:
    """``asterism.term_match`` と論文の道（``chat_paper``）は、同じ入力に同じ返りを返す。
    片方だけ直すと、同じ語が論文では当たり、知見では当たらない、が起きる。"""
    for name, text in _PAIRS:
        assert term_match.term_key(name) == chat_paper.term_key(name), name
        assert term_match.prep_text(text) == chat_paper.prep_text(text), text
        norm = chat_paper.prep_text(text)
        assert term_match.mentions(norm, name) == chat_paper.mentions(norm, name), (name, text)
        assert bool(term_match.name_spans(norm, name)) == chat_paper.mentions(norm, name)


# ---- 途中で落ちたとき・同時に呼んだとき・細工した束 ---------------------------------------------


def test_failed_rebuild_keeps_the_live_version_and_says_the_copy_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    """作り直しの取り込みが落ちても、公開中の版はそのまま。source は新しい束に置き換わっているので、
    source を読む側が気づけるよう印（``syncing``）が残る。次に通れば直る。"""
    oxi = _RecOxi()
    root = tmp_path / "registry"
    smaller = _bundle()
    smaller["files"]["claims.json"] = smaller["files"]["claims.json"][:1]
    real = substrate.materialize_to_nt_file
    with TestClient(_app(tmp_path, oxi), headers=_AUTH) as client:
        first = client.post(
            "/api/knowledge-copies", json={"source": "notes", "bundle": _bundle()}
        ).json()
        dataset_id = first["dataset_id"]

        def boom(*a, **k):
            raise RuntimeError("store went away")

        monkeypatch.setattr(substrate, "materialize_to_nt_file", boom)
        failed = client.post("/api/knowledge-copies", json={"source": "notes", "bundle": smaller})
        assert failed.status_code == 500 and "store went away" in failed.text
        meta = registry.load_meta(root, dataset_id)
        assert meta["promoted"] is True and meta["live_graph"] == first["version"]
        ks = meta["knowledge_source"]
        # 前の版の記録は残り、ずれていることが分かる
        assert ks["syncing"] is True and ks["version"] == first["version"] and ks["copied_at"]
        assert ks["counts"]["claims.json"] == 3
        assert not copy_is_current(meta)
        assert find_copy_dataset(root, "notes") == dataset_id

        monkeypatch.setattr(substrate, "materialize_to_nt_file", real)
        healed = client.post("/api/knowledge-copies", json={"source": "notes", "bundle": smaller})
    assert healed.status_code == 200, healed.text
    out = healed.json()
    assert out["dataset_id"] == dataset_id and out["created"] is False
    meta = registry.load_meta(root, dataset_id)
    assert copy_is_current(meta) and meta["knowledge_source"]["counts"]["claims.json"] == 1


@pytest.mark.asyncio
async def test_second_concurrent_copy_of_the_same_source_is_409(tmp_path: Path) -> None:
    import asyncio

    oxi = _RecOxi()
    app = _app(tmp_path, oxi)
    root = tmp_path / "registry"
    token = _AUTH["X-Asterism-Token"]

    async def one(source: str):
        try:
            return await copy_knowledge(
                app, root, source=source, raw_bundle=_bundle(), token=token
            )
        except CopyError as exc:
            return exc

    async with app.router.lifespan_context(app):
        a, b, other = await asyncio.gather(one("notes"), one("notes"), one("other"))
    assert isinstance(b, CopyError) and b.status == 409
    assert a["created"] is True and other["created"] is True
    assert len(registry.list_datasets(root)) == 2
    # 終わったら同じ取り込み元をまた写せる（印が残らない）
    assert app.state.knowledge_copy_running == set()


def test_a_crafted_row_does_not_break_the_whole_copy(tmp_path: Path) -> None:
    """IRI でない値・制御文字・書き出せない文字が 1 行に入っていても、写しは作られる
    （悪い値だけ落ちる）。そのまま置くと、ストアは版を丸ごと受け付けない。"""
    oxi = _RecOxi()
    bad = _bundle()
    c = bad["files"]["claims.json"][0]
    c["source_iri"] = ["not an iri", _SOURCE + "note-1"]
    c["iri"] = "https://elsewhere.test/x"
    c["title"] = "強力粉は\x00吸水率が高い\ud800"
    with TestClient(_app(tmp_path, oxi), headers=_AUTH) as client:
        # 対にならないサロゲートは JSON の \\uD800 として届く（UTF-8 では送れない）
        r = client.post(
            "/api/knowledge-copies",
            content=json.dumps({"source": "notes", "bundle": bad}).encode("ascii"),
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["dropped"] == {"claims.json:bad_iri": 1}
    body = oxi.stored[out["version"]]
    assert "not an iri" not in body and "elsewhere.test" not in body
    assert f"<{_CLAIM}c1> <http://www.w3.org/ns/prov#wasDerivedFrom> <{_SOURCE}note-1>" in body
    import io

    import pyoxigraph

    # 出来た N-Triples は、本物のストアの読み手で読める（pyoxigraph 0.3 の呼び方）
    assert len(list(pyoxigraph.parse(io.BytesIO(body.encode("utf-8")), "application/n-triples")))
