"""意味は列の性質、ID と帰属は設計の判断 — ADR meaning-before-identity。

意味は「データを見れば決まる」もので、どんな設計にしても同じ。だから設計より
前に決められるし、決めた意味は生成ラウンドが書き換える側ではない。保管は
`(source, column)`（設計が無くても列にはある識別）で、述語キーの
`display-meta.json` はそこからの投影として残る。

ここで固定するのは 3 つ:

* 設計より前に意味を書く経路（`POST /api/design/column-meanings`）
* 確定した意味が設計に写ること（`/api/propose/continue` の `column_meanings`）
* データセットができたあとの保管と投影（`/api/datasets/{id}/column-meanings`）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import (  # noqa: F401  (healthy_client is a fixture)
    _AUTH,
    _FIX_RECIPE_MD,
    _parse_sse,
    _settings,
    healthy_client,
)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_READINGS = b"reading_id,channel,amplitude,unused\nr1,A,1.5,x\nr2,B,2.5,y\n"

_MEANINGS_ANSWER = {
    "columns": [
        {"source": "readings.csv", "column": "channel", "label": "測定チャンネル"},
        {"source": "readings.csv", "column": "amplitude", "label": "振幅", "unit": "mV"},
        {"source": "readings.csv", "column": "unused", "label": "備考"},
        # 存在しない列 — 決定論のふるいで落ちる
        {"source": "readings.csv", "column": "invented", "label": "無い列"},
    ]
}

_STAGED_SKELETON = {
    "version": 1,
    "prefixes": {"ex": "https://ns.invalid/ns#", "exr": "https://ns.invalid/r/"},
    "maps": [
        {
            "name": "reading",
            "source": "readings.csv",
            "subject": {"template": "exr:reading/{reading_id}", "classes": ["ex:Reading"]},
        }
    ],
}
_STAGED_PERMAP = {
    "properties": [
        {"predicate": "ex:channel", "column": "channel", "label": "AI が書いた意味"},
        {"predicate": "ex:amplitude", "column": "amplitude"},
    ]
}


class _MeaningsMock:
    """意味の段と、骨格→per-map→文書の段を、それぞれの凍結プロンプトで振り分ける。"""

    def __init__(self, key: str | None) -> None:
        self.key = key
        self.systems: list[str] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        from asterism_step0.staged_propose import (
            COLUMN_MEANINGS_SYSTEM_PROMPT,
            DOCUMENT_SYSTEM_PROMPT,
            PERMAP_SYSTEM_PROMPT,
            SKELETON_SYSTEM_PROMPT,
        )

        self.systems.append(system_prompt)
        if system_prompt == COLUMN_MEANINGS_SYSTEM_PROMPT:
            return json.dumps(_MEANINGS_ANSWER)
        if system_prompt == SKELETON_SYSTEM_PROMPT:
            return json.dumps(_STAGED_SKELETON)
        if system_prompt == PERMAP_SYSTEM_PROMPT:
            return json.dumps(_STAGED_PERMAP)
        if system_prompt == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(mock staged design)\n"
        return "UNEXPECTED PROMPT"


def _app(tmp_path: Path, healthy_client):
    return build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: _MeaningsMock(key),
    )


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def _attached_dataset(client: TestClient) -> str:
    ds_id = client.post(
        "/api/materialize", json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor"}
    ).json()["dataset"]["id"]
    attached = client.post(
        f"/api/datasets/{ds_id}/source",
        files={"files": ("readings.csv", _READINGS, "text/csv")},
    )
    assert attached.status_code == 200, attached.text
    return ds_id


# ---------------------------------------------------------------------------
# 設計より前に意味を書く
# ---------------------------------------------------------------------------


def test_design_column_meanings_needs_no_skeleton(tmp_path: Path, healthy_client) -> None:
    """骨格もクラスも述語も無い時点で走り、答えは (source, column) で綴じられる。"""
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        r = client.post(
            "/api/design/column-meanings",
            data={"domain": "sensor readings"},
            files={"files": ("readings.csv", _READINGS, "text/csv")},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        phases = [d.get("phase") for n, d in events if n == "running" and "phase" in d]
        assert "meanings" in phases
        done = next(d for n, d in events if n == "done")["result"]
        assert done["meanings"] == [
            {"source": "readings.csv", "column": "channel", "label": "測定チャンネル"},
            {"source": "readings.csv", "column": "amplitude", "label": "振幅", "unit": "mV"},
            {"source": "readings.csv", "column": "unused", "label": "備考"},
        ]
        # 無い列への意味は人に見せる前に落ちる（答えの綴じ先が無い）
        assert done["rejected"] == ["readings.csv:invented (unknown column)"]


def test_settled_meanings_reach_the_design(tmp_path: Path, healthy_client) -> None:
    """確定した意味は §9 に写り、per-map が書いた label より勝つ。"""
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        r = client.post(
            "/api/propose/continue",
            data={
                "skeleton": json.dumps(_STAGED_SKELETON),
                "domain": "sensor readings",
                "column_meanings": json.dumps(
                    [
                        {"source": "readings.csv", "column": "channel", "label": "測定チャンネル"},
                        {
                            "source": "readings.csv",
                            "column": "amplitude",
                            "label": "振幅",
                            "unit": "mV",
                        },
                    ]
                ),
            },
            files={"files": ("readings.csv", _READINGS, "text/csv")},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        done = next(d for n, d in events if n == "done")["result"]
        spec = yaml.safe_load(
            done["proposal_md"].split("```yaml\n")[-1].split("```")[0]
        )
        rows = {p["column"]: p for p in spec["maps"][0]["properties"] if p.get("column")}
        assert rows["channel"]["label"] == "測定チャンネル"  # AI の言葉ではなくこちら
        assert rows["amplitude"]["label"] == "振幅"
        assert rows["amplitude"]["unit"] == "mV"


def test_malformed_column_meanings_are_a_readable_422(
    tmp_path: Path, healthy_client
) -> None:
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        for bad in ("{", json.dumps({"a": 1}), json.dumps([{"label": "列がない"}])):
            r = client.post(
                "/api/propose/continue",
                data={
                    "skeleton": json.dumps(_STAGED_SKELETON),
                    "column_meanings": bad,
                },
                files={"files": ("readings.csv", _READINGS, "text/csv")},
            )
            assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# データセットができたあと — 保管庫と投影
# ---------------------------------------------------------------------------


def test_column_meanings_are_stored_and_projected(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        before = client.get(f"/api/datasets/{ds_id}").json()["artifacts"]["mapping.rml.ttl"]
        r = client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={
                "meanings": [
                    {
                        "source": "readings.csv",
                        "column": "amplitude",
                        "label": "振幅",
                        "unit": "mV",
                    }
                ]
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["changed"] == ["readings.csv:amplitude"]

        artifacts = client.get(f"/api/datasets/{ds_id}").json()["artifacts"]
        ir = yaml.safe_load(artifacts["mapping.yaml"])
        row = next(p for p in ir["maps"][0]["properties"] if p.get("column") == "amplitude")
        assert row["label"] == "振幅" and row["unit"] == "mV"
        # 表示のための情報。三つ組を作る規則は 1 バイトも変わらない (K8)
        assert artifacts["mapping.rml.ttl"] == before
        # 保管は (source, column) で、GET でそのまま読める
        assert client.get(f"/api/datasets/{ds_id}/column-meanings").json()["meanings"] == [
            {"source": "readings.csv", "column": "amplitude", "label": "振幅", "unit": "mV"}
        ]
        stored = json.loads((tmp_path / "registry" / ds_id / "column-meanings.json").read_text())
        assert stored["meanings"][0]["column"] == "amplitude"


def test_a_meaning_for_an_unmapped_column_is_still_kept(
    tmp_path: Path, healthy_client
) -> None:
    """設計がまだその列を使っていなくても、人が答えた意味は保管する。

    意味は列についての事実で、設計が追いついていないことは答えを間違いにしない。
    """
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        r = client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={"meanings": [{"source": "readings.csv", "column": "unused", "label": "備考"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"dataset_id": ds_id, "changed": [], "stored": True}
        assert client.get(f"/api/datasets/{ds_id}/column-meanings").json()["meanings"] == [
            {"source": "readings.csv", "column": "unused", "label": "備考"}
        ]


def test_an_absent_field_is_kept_and_an_empty_one_clears(
    tmp_path: Path, healthy_client
) -> None:
    """単位だけを直す画面が意味を消してはいけない。空文字は「間違いだった」。"""
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={
                "meanings": [
                    {
                        "source": "readings.csv",
                        "column": "amplitude",
                        "label": "振幅",
                        "unit": "mV",
                    }
                ]
            },
        )
        client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={"meanings": [{"source": "readings.csv", "column": "amplitude", "unit": "µV"}]},
        )
        assert client.get(f"/api/datasets/{ds_id}/column-meanings").json()["meanings"] == [
            {"source": "readings.csv", "column": "amplitude", "label": "振幅", "unit": "µV"}
        ]
        client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={"meanings": [{"source": "readings.csv", "column": "amplitude", "unit": ""}]},
        )
        assert client.get(f"/api/datasets/{ds_id}/column-meanings").json()["meanings"] == [
            {"source": "readings.csv", "column": "amplitude", "label": "振幅"}
        ]
        ir = yaml.safe_load(
            client.get(f"/api/datasets/{ds_id}").json()["artifacts"]["mapping.yaml"]
        )
        row = next(p for p in ir["maps"][0]["properties"] if p.get("column") == "amplitude")
        assert row["label"] == "振幅" and "unit" not in row


def test_column_meanings_refuse_an_unknown_dataset_or_an_empty_body(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        assert client.get("/api/datasets/nope/column-meanings").status_code == 404
        assert (
            client.post("/api/datasets/nope/column-meanings", json={"meanings": []}).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/datasets/{ds_id}/column-meanings", json={"meanings": []}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/datasets/{ds_id}/column-meanings",
                json={"meanings": [{"source": " ", "column": "x", "label": "y"}]},
            ).status_code
            == 422
        )


def test_excluded_columns_never_reach_the_design(tmp_path: Path, healthy_client) -> None:
    """取り込まないと決めた列は設計に載らない。per-map に名指しで伝えたうえで、
    出来上がった §9 からも決定論で外す（お願いと保証の二段）。"""
    mock_seen: list[str] = []

    def factory(key):
        m = _MeaningsMock(key)
        original = m.complete

        def complete(system: str, user: str) -> str:
            from asterism_step0.staged_propose import PERMAP_SYSTEM_PROMPT

            if system == PERMAP_SYSTEM_PROMPT:
                mock_seen.append(user)
            return original(system, user)

        m.complete = complete  # type: ignore[method-assign]
        return m

    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=factory,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/propose/continue",
            data={
                "skeleton": json.dumps(_STAGED_SKELETON),
                "column_decisions": json.dumps(
                    [{"source": "readings.csv", "column": "channel", "action": "exclude"}]
                ),
            },
            files={"files": ("readings.csv", _READINGS, "text/csv")},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        done = next(d for n, d in events if n == "done")["result"]
        spec = yaml.safe_load(done["proposal_md"].split("```yaml\n")[-1].split("```")[0])
        columns = [p.get("column") for p in spec["maps"][0]["properties"]]
        # モックは channel を書いて返す。決定論の段で外れていること。
        assert "channel" not in columns
        assert "amplitude" in columns
        assert any("decided NOT to take in" in u and "`channel`" in u for u in mock_seen)


def test_only_exclude_is_a_pre_design_decision(tmp_path: Path, healthy_client) -> None:
    """設計より前には include も own も置き場が無い（付ける map がまだ無い）。"""
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        r = client.post(
            "/api/propose/continue",
            data={
                "skeleton": json.dumps(_STAGED_SKELETON),
                "column_decisions": json.dumps(
                    [{"source": "readings.csv", "column": "channel", "action": "include"}]
                ),
            },
            files={"files": ("readings.csv", _READINGS, "text/csv")},
        )
        assert r.status_code == 422, r.text


def _continue_with(client, *, meanings, decisions=None):
    data = {"skeleton": json.dumps(_STAGED_SKELETON), "column_meanings": json.dumps(meanings)}
    if decisions:
        data["column_decisions"] = json.dumps(decisions)
    r = client.post(
        "/api/propose/continue",
        data=data,
        files={"files": ("readings.csv", _READINGS, "text/csv")},
    )
    assert r.status_code == 202, r.text
    events = _parse_sse(client.get(f"/api/jobs/{r.json()['job_id']}/stream").text)
    done = next(d for n, d in events if n == "done")["result"]
    spec = yaml.safe_load(done["proposal_md"].split("```yaml\n")[-1].split("```")[0])
    return {p.get("column"): p for p in spec["maps"][0]["properties"] if p.get("column")}


def test_a_column_with_a_meaning_is_taken_in_without_asking_again(
    tmp_path: Path, healthy_client
) -> None:
    """意味の画面では、外さないかぎり全列が「取り込む」。そこで答えたことを、
    設計が落としたからといってもう一度聞かない（ADR §3 / §9）。"""
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        # モックの per-map は `unused` を書かない。意味は付いているので機械が拾う。
        rows = _continue_with(
            client,
            meanings=[
                {"source": "readings.csv", "column": "unused", "label": "備考", "unit": "mV"},
            ],
        )
        assert "unused" in rows
        assert rows["unused"]["label"] == "備考"
        assert rows["unused"]["unit"] == "mV"


def test_an_excluded_column_is_not_taken_back_in(tmp_path: Path, healthy_client) -> None:
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        rows = _continue_with(
            client,
            meanings=[{"source": "readings.csv", "column": "unused", "label": "備考"}],
            decisions=[{"source": "readings.csv", "column": "unused", "action": "exclude"}],
        )
        assert "unused" not in rows


def test_a_kept_column_with_no_meaning_is_still_taken_in(
    tmp_path: Path, healthy_client
) -> None:
    """意味が空でも「取り込む」は取り込む — その 2 つは別の問い。意味の欄が
    空なら項目名は列名のまま（機械が意味を発明したのではなく、まだ誰も
    書いていないだけ・K22）。"""
    with TestClient(_app(tmp_path, healthy_client), headers=_AUTH) as client:
        rows = _continue_with(
            client,
            meanings=[{"source": "readings.csv", "column": "unused", "unit": "mV"}],
        )
        assert "unused" in rows
        assert "label" not in rows["unused"]
        assert rows["unused"]["unit"] == "mV"


def test_design_labels_are_readable_without_the_store(
    tmp_path: Path, healthy_client
) -> None:
    """設計に写った意味は、store（column-meanings.json）が無くても読める。

    store は明示保存の経路しか書かないため、過去に設計まで到達したデータセット
    でも空のことがある — そのとき設計のやり直しが③「意味をつける」を全欄空欄で
    開いていた（利用者報告 2026-09-02: Starrydata）。GET は設計（mapping.yaml）
    からの投影を既定層に持つ。
    """
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        r = client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={
                "meanings": [
                    {"source": "readings.csv", "column": "amplitude", "label": "振幅", "unit": "mV"}
                ]
            },
        )
        assert r.status_code == 200, r.text
        # store を消す — 意味は設計（mapping.yaml）に写っているので、読み取りは
        # そこから立ち直る（= 明示保存を経ていないデータセットの姿）。
        (tmp_path / "registry" / ds_id / "column-meanings.json").unlink()
        meanings = client.get(f"/api/datasets/{ds_id}/column-meanings").json()["meanings"]
        assert {
            "source": "readings.csv",
            "column": "amplitude",
            "label": "振幅",
            "unit": "mV",
        } in meanings


def test_store_wins_over_the_design_projection(tmp_path: Path, healthy_client) -> None:
    """明示 store は投影より強い — 直したそばから設計の古い値に戻されない。"""
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        client.post(
            f"/api/datasets/{ds_id}/column-meanings",
            json={"meanings": [{"source": "readings.csv", "column": "amplitude", "label": "振幅"}]},
        )
        # store だけを書き換える（設計の投影は「振幅」のまま）— 実運用では起きない
        # 順序だが、層の勝敗をこれ以上直接に固定する書き方は無い。
        path = tmp_path / "registry" / ds_id / "column-meanings.json"
        stored = json.loads(path.read_text())
        stored["meanings"][0]["label"] = "振幅の言い直し"
        path.write_text(json.dumps(stored, ensure_ascii=False), "utf-8")
        meanings = client.get(f"/api/datasets/{ds_id}/column-meanings").json()["meanings"]
        row = next(m for m in meanings if m["column"] == "amplitude")
        assert row["label"] == "振幅の言い直し"


# ---------------------------------------------------------------------------
# 初回の流れ: データセットが生まれた時点で、保管庫に写る
# ---------------------------------------------------------------------------
#
# 設計の前に決めたこと（意味と「取り込まない」）は、`/api/propose/continue` が
# 設計に効かせるだけで、データセットの保管庫には誰も書いていなかった — 写して
# いた画面（「数の確認」）を畳んだときに、その仕事の引き継ぎ先が無くなった
# （2026-10-05 に実機で確認: 別のブラウザで見直すと、外した列が「取り込む」に
# 戻る）。データセットを作る `/api/materialize` が、同じ一歩で保管庫に書く。

_SETTLED_MEANINGS = [
    {"source": "readings.csv", "column": "channel", "label": "測定チャンネル"},
    {"source": "readings.csv", "column": "amplitude", "label": "振幅", "unit": "mV"},
]
_SETTLED_DROPS = [{"source": "readings.csv", "column": "unused", "action": "exclude"}]

# AI の作り直しが、外した列を戻し、決めた意味を書き換えた設計。
_AI_REWRITE_MD = _FIX_RECIPE_MD.replace(
    "      - predicate: sn:channel\n        column: channel\n",
    "      - predicate: sn:channel\n        column: channel\n        label: AI の言い直し\n",
).replace(
    "      - predicate: sn:amplitude\n        column: amplitude",
    "      - predicate: sn:amplitude\n"
    "        column: amplitude\n"
    "      - predicate: sn:unused\n"
    "        column: unused",
)


def _staged(client: TestClient) -> str:
    return client.post(
        "/api/staging", files={"files": ("readings.csv", _READINGS, "text/csv")}
    ).json()["staging_id"]


def _first_save(client: TestClient, **extra) -> dict:
    r = client.post(
        "/api/materialize",
        json={
            "proposal_md": _FIX_RECIPE_MD,
            "dataset_name": "sensor",
            "staging_id": _staged(client),
            "column_meanings": _SETTLED_MEANINGS,
            "column_decisions": _SETTLED_DROPS,
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _design_columns(client: TestClient, ds_id: str) -> dict[str, dict]:
    ir = yaml.safe_load(client.get(f"/api/datasets/{ds_id}").json()["artifacts"]["mapping.yaml"])
    return {p["column"]: p for p in ir["maps"][0]["properties"]}


def test_the_first_save_files_what_was_settled_before_the_design(
    tmp_path: Path, healthy_client
) -> None:
    """データセットを作る保存が、③で決めた意味と「取り込まない」を保管庫に書く。"""
    with _client(tmp_path, healthy_client) as client:
        # 何も渡さなければ、外した列は「使っていない列」として知らされる（対照）。
        blind = client.post(
            "/api/materialize",
            json={"proposal_md": _FIX_RECIPE_MD, "persist": False, "staging_id": _staged(client)},
        ).json()
        assert any("never uses: unused" in advisory for advisory in blind["advisories"])

        saved = _first_save(client)
        ds_id = saved["dataset"]["id"]
        home = tmp_path / "registry" / ds_id
        assert json.loads((home / "column-decisions.json").read_text("utf-8")) == {
            "decisions": _SETTLED_DROPS
        }
        assert json.loads((home / "column-meanings.json").read_text("utf-8")) == {
            "meanings": _SETTLED_MEANINGS
        }
        assert (
            client.get(f"/api/datasets/{ds_id}/column-decisions").json()["decisions"]
            == _SETTLED_DROPS
        )
        # 「取り込まない」と決めた列は、最初から「使っていない列」に数えない。
        assert all("unused" not in advisory for advisory in saved["advisories"])
        assert all("unused" not in advisory for advisory in saved["dataset"]["advisories"])
        # 設計にも同じ意味が載っている（保管庫と設計が、生まれた時点で食い違わない）。
        columns = _design_columns(client, ds_id)
        assert columns["channel"]["label"] == "測定チャンネル"
        assert (columns["amplitude"]["label"], columns["amplitude"]["unit"]) == ("振幅", "mV")
        assert "unused" not in columns


def test_a_first_save_with_nothing_settled_leaves_no_store(
    tmp_path: Path, healthy_client
) -> None:
    """何も決めていない保存（詳細モードなど）は、これまでどおり保管庫を作らない。"""
    with _client(tmp_path, healthy_client) as client:
        ds_id = client.post(
            "/api/materialize",
            json={
                "proposal_md": _FIX_RECIPE_MD,
                "dataset_name": "sensor",
                "column_meanings": [],
                "column_decisions": [],
            },
        ).json()["dataset"]["id"]
        home = tmp_path / "registry" / ds_id
        assert not (home / "column-decisions.json").exists()
        assert not (home / "column-meanings.json").exists()


def test_a_later_save_never_writes_the_stores(tmp_path: Path, healthy_client) -> None:
    """すでにあるデータセットへの保存は、渡された意味・判断を保管庫に書かない。

    データセットができたあとの③は「この意味を保存して戻る」だけが保存する
    （「ためすに戻る」は保存しない約束）。作り直しの保存がついでに書いてしまうと、
    ③で書きかけて保存しなかったものまで保管庫に入る。
    """
    with _client(tmp_path, healthy_client) as client:
        ds_id = _attached_dataset(client)
        resaved = client.post(
            "/api/materialize",
            json={
                "proposal_md": _FIX_RECIPE_MD,
                "dataset_name": "sensor",
                "dataset_id": ds_id,
                "column_meanings": _SETTLED_MEANINGS,
                "column_decisions": _SETTLED_DROPS,
            },
        )
        assert resaved.status_code == 200, resaved.text
        home = tmp_path / "registry" / ds_id
        assert not (home / "column-decisions.json").exists()
        assert not (home / "column-meanings.json").exists()
        assert client.get(f"/api/datasets/{ds_id}/column-decisions").json()["decisions"] == []
        # 設計にも写っていない（保存の本文が運んだものは、読まれてもいない）。
        assert "label" not in _design_columns(client, ds_id)["channel"]
        assert any("unused" in advisory for advisory in resaved.json()["advisories"])


def test_what_was_settled_survives_a_resave_before_the_source_is_attached(
    tmp_path: Path, healthy_client
) -> None:
    """生まれた直後（ソースをまだ付けていない）の保存のやり直しでも、外した列は
    戻らず、決めた意味は書き換わらない — 取り込みの完了を待たずに保管庫にあるから。"""
    with _client(tmp_path, healthy_client) as client:
        ds_id = _first_save(client)["dataset"]["id"]
        resaved = client.post(
            "/api/materialize",
            json={"proposal_md": _AI_REWRITE_MD, "dataset_name": "sensor", "dataset_id": ds_id},
        )
        assert resaved.status_code == 200, resaved.text
        columns = _design_columns(client, ds_id)
        assert "unused" not in columns
        assert columns["channel"]["label"] == "測定チャンネル"


class _RewritingLLM:
    """「AI に直してもらう」の 1 回ぶん: 外した列を戻し、意味を書き換えて返す。"""

    def __init__(self, key: str | None) -> None:
        self.key = key

    def complete(self, system_prompt: str, user_message: str) -> str:
        return _AI_REWRITE_MD


def test_what_was_settled_survives_an_ai_round_before_the_ingest(
    tmp_path: Path, healthy_client
) -> None:
    """取り込みの前に走る AI の作り直し（refine）は、保管庫だけを読んで戻す。
    保管庫が取り込みの完了まで空だと、ここで戻す材料が無い。"""
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: _RewritingLLM(key),
    )
    with TestClient(app, headers=_AUTH) as client:
        ds_id = _first_save(client)["dataset"]["id"]
        r = client.post(
            "/api/refine",
            json={
                "schema_md": _FIX_RECIPE_MD,
                "comments": ["fix the design"],
                "dataset_id": ds_id,
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 202, r.text
        events = _parse_sse(client.get(f"/api/jobs/{r.json()['job_id']}/stream").text)
        done = next(d for n, d in events if n == "done")["result"]
        spec = yaml.safe_load(
            done["effective_schema_md"].split("```yaml\n")[-1].split("```")[0]
        )
        columns = {p["column"]: p for p in spec["maps"][0]["properties"]}
        assert "unused" not in columns
        assert columns["channel"]["label"] == "測定チャンネル"


def test_the_first_save_reads_only_pre_design_decisions(
    tmp_path: Path, healthy_client
) -> None:
    """設計の前に言えるのは「取り込まない」だけ — `/api/propose/continue` と同じ検査。"""
    with _client(tmp_path, healthy_client) as client:
        r = client.post(
            "/api/materialize",
            json={
                "proposal_md": _FIX_RECIPE_MD,
                "dataset_name": "sensor",
                "column_decisions": [
                    {"source": "readings.csv", "column": "unused", "action": "include"}
                ],
            },
        )
        assert r.status_code == 422
        assert "exclude" in r.text
        assert client.get("/api/datasets").json()["count"] == 0


@pytest.mark.parametrize(
    "document",
    [
        "## Proposed schema\n\n(truncated)",  # §9 が無い
        _FIX_RECIPE_MD.replace("maps:\n", "maps: [\n"),  # §9 が読めない
    ],
    ids=["no-spec", "unreadable-spec"],
)
def test_a_design_with_no_usable_mapping_spec_is_still_saved_and_the_stores_are_written(
    tmp_path: Path, healthy_client, document: str
) -> None:
    """§9 が無い・読めない設計（途中で切れた出力）は、写す先が無いだけで保存は通る
    （決めたことを添えなければ通っていた保存を、添えたせいで落とさない）。保管庫には
    書くので、次の作り直しがそこから戻せる。"""
    with _client(tmp_path, healthy_client) as client:
        saved = _first_save(client, proposal_md=document)
        ds_id = saved["dataset"]["id"]
        assert (
            client.get(f"/api/datasets/{ds_id}/column-decisions").json()["decisions"]
            == _SETTLED_DROPS
        )
        assert (tmp_path / "registry" / ds_id / "column-meanings.json").is_file()


def test_the_first_save_never_fails_over_a_decision_the_design_cannot_place(
    tmp_path: Path, healthy_client
) -> None:
    """設計が外せない列（ID を作る列・その種類のただ 1 つの項目）の「取り込まない」で、
    データセットを作る保存を落とさない — 設計ループも同じ理由でその列を残している。

    守れなかった判断は保管庫に書かない。書くと、列は取り込まれているのに保管庫は
    「取り込まない」と言い、以後の保存がそのたびに 422 になる。
    """
    with _client(tmp_path, healthy_client) as client:
        saved = _first_save(
            client,
            column_decisions=[
                {"source": "readings.csv", "column": "reading_id", "action": "exclude"},
                *_SETTLED_DROPS,
            ],
        )
        ds_id = saved["dataset"]["id"]
        # ID を作る列は残り、守れた判断だけが保管庫に入る。
        assert "reading/{reading_id}" in saved["artifacts"]["mapping.yaml"]
        assert (
            client.get(f"/api/datasets/{ds_id}/column-decisions").json()["decisions"]
            == _SETTLED_DROPS
        )
        # 以後の保存も通る（保管庫が、設計の守れない判断を抱えていない）。
        resaved = client.post(
            "/api/materialize",
            json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor", "dataset_id": ds_id},
        )
        assert resaved.status_code == 200, resaved.text

        # その種類のただ 1 つの項目になる列も同じ: 先に外せたぶんは外れ、最後の 1 つは残る。
        saved = _first_save(
            client,
            column_decisions=[
                {"source": "readings.csv", "column": "channel", "action": "exclude"},
                {"source": "readings.csv", "column": "amplitude", "action": "exclude"},
            ],
        )
        ds_id = saved["dataset"]["id"]
        assert list(_design_columns(client, ds_id)) == ["amplitude"]
        assert client.get(f"/api/datasets/{ds_id}/column-decisions").json()["decisions"] == [
            {"source": "readings.csv", "column": "channel", "action": "exclude"}
        ]


_KEPT = "stays in the design although it was marked do-not-take-in"


def test_an_exclusion_the_design_cannot_place_is_said_in_the_advisories(
    tmp_path: Path, healthy_client
) -> None:
    """守れなかった「取り込まない」は保管庫に書かない（上のテスト）。その代わり
    advisories で言う — 言わないと、③を開き直すと「取り込む」と出るだけで、
    外した人はなぜその列がデータに入っているのかを知る術が無い（残 ⑤）。
    """
    with _client(tmp_path, healthy_client) as client:
        saved = _first_save(
            client,
            column_decisions=[
                {"source": "readings.csv", "column": "reading_id", "action": "exclude"},
                *_SETTLED_DROPS,
            ],
        )
        said = [a for a in saved["advisories"] if _KEPT in a]
        assert said == [a for a in saved["dataset"]["advisories"] if _KEPT in a], (
            "データセットにも残る — カタログの「見直す」が運ぶ"
        )
        assert len(said) == 1, saved["advisories"]
        # 列とソースを先に、理由（どの種類の ID を作るか）をあとに。
        assert said[0].startswith(f"column reading_id of source readings.csv {_KEPT}: ")
        assert "is an identifier for map 'reading'" in said[0]
        # 守れた判断の列は言わない。
        for drop in _SETTLED_DROPS:
            assert f"column {drop['column']} of source" not in said[0]

        # すでにあるデータセットへの保存は言い直さない — 保管庫に無い判断は、もう無い。
        resaved = client.post(
            "/api/materialize",
            json={
                "proposal_md": _FIX_RECIPE_MD,
                "dataset_name": "sensor",
                "dataset_id": saved["dataset"]["id"],
            },
        )
        assert resaved.status_code == 200, resaved.text
        assert not any(_KEPT in a for a in resaved.json()["advisories"])

        # その種類のただ 1 つの項目になる列も、理由つきで言う。
        saved = _first_save(
            client,
            column_decisions=[
                {"source": "readings.csv", "column": "channel", "action": "exclude"},
                {"source": "readings.csv", "column": "amplitude", "action": "exclude"},
            ],
        )
        said = [a for a in saved["advisories"] if _KEPT in a]
        assert len(said) == 1, saved["advisories"]
        assert said[0].startswith(f"column amplitude of source readings.csv {_KEPT}: ")
        assert "is the only property of map 'reading'" in said[0]
