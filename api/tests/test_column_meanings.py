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
