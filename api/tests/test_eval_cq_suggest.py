"""``scripts/eval_cq_suggest.py``（問いの提案の物差し・PR 10d）の動作テスト。

実 LLM は使わない。固定の応答を返す小さな OpenAI 互換の HTTP サーバをテストの中で立て、
金標準の事例 1 つを流して、数え方（完全一致・重なり・答え方も一致・閉じた形に落ちた率）と
結果の Markdown を確かめる。金標準の YAML そのものの形（件数・対応）も固定する。
"""

# ruff: noqa: RUF001 (日本語の文に全角の記号を使う)
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_cq_suggest.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("eval_cq_suggest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_cq_suggest"] = mod
    spec.loader.exec_module(mod)
    return mod


def _q(question, op, **kw):
    row = {
        "question": question,
        "mode": kw.pop("mode", "data"),
        "op": op,
        "kind": None,
        "property": None,
        "property2": None,
        "kind2": None,
        "value": None,
        "match": None,
        "target": None,
        "direction": None,
        "reason": "ためし",
    }
    row.update(kw)
    return row


#: スーパーの価格表への固定の応答。1 本目は gold と完全一致・2 本目は言い回しが違う
#: （重なりで当たる）・3 本目は存在しない列（後処理が落とす）。
STUB_QUESTIONS = [
    _q("1 つあたりの値段がいちばん高い記録は？", "top", kind="値段の記録", property="price"),
    _q("店の名前ごとの値段の記録は何件？", "group", kind="値段の記録", property="shop"),
    _q("在庫の多い店は？", "range", property="stock_count"),
]


@pytest.fixture
def stub_url():
    seen: list[str] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静かに
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length", 0))).decode("utf-8")
            seen.append(raw)
            content = json.dumps({"questions": STUB_QUESTIONS}, ensure_ascii=False)
            body = json.dumps(
                {
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "stub",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/v1", seen
    finally:
        srv.shutdown()
        srv.server_close()


def test_gold_files_have_the_promised_shape(ev):
    cases = ev.load_cases()
    by_file: dict[str, list] = {}
    for c in cases:
        by_file.setdefault(c.file, []).append(c)
        assert len(c.gold_cqs) == len(c.expected), c.id
    gold = {f: sum(len(c.gold_cqs) for c in cs) for f, cs in by_file.items()}
    closed = {f: sum(len(c.closed_indexes()) for c in cs) for f, cs in by_file.items()}
    assert gold == {"starrydata.yaml": 10, "samples.yaml": 8, "papers.yaml": 6}
    assert closed == {"starrydata.yaml": 8, "samples.yaml": 8, "papers.yaml": 6}
    # 値は入れない: 入力に出てよいのは見出し・型のヒント・名前・要約の文だけ
    for c in cases:
        assert c.source.get("screen") in ("dataset", "paper")
        if c.source["screen"] == "paper":
            assert 100 <= len(c.source["text"]) <= 600  # 要約であって原文ではない


def test_tokens_and_jaccard(ev):
    assert ev.tokens("SnSe を含む組成の試料は？") >= {"snse", "組成", "試料"}
    assert ev.jaccard("組成ごとの試料は？", "組成ごとの試料は？") == 1.0
    assert ev.jaccard("粒径がいちばん大きい試料は？", "論文は何本あるか？") < 0.5
    assert ev.jaccard("", "試料") == 0.0


def test_evaluate_case_with_a_stub_llm(ev, stub_url, monkeypatch):
    from asterism_step0.llm import make_llm

    url, seen = stub_url
    monkeypatch.setenv("ASTERISM_LLM_STREAM", "0")
    client = make_llm("openai", model="stub", api_base=url, api_key="k")
    case = next(c for c in ev.load_cases() if c.id == "sm-prices")
    res = ev.evaluate_case(client, case, lang="ja")
    assert res.errors == []
    assert (res.proposed, res.shown) == (3, 2)  # 存在しない列の問いは後処理が落とす
    assert res.gold_closed == 2
    assert res.exact == 1  # 1 本目は gold と文が同じ
    assert res.overlap == 2  # 2 本目は言い回しが違うが重なりで当たる
    assert res.shape == 2  # op と名前（kind / property）も expected_answers と同じ
    assert res.overlap_proposed == 2
    assert [r["op"] for r in res.first_shown] == ["top", "group"]
    # AI に渡すのは名前だけ: 列の見出し・型のヒント・データセット名は入り、データの値は入らない
    prompt = seen[0]
    assert "スーパーの価格表" in prompt and "price | xsd:integer" in prompt
    assert "にんじん" not in prompt


def test_main_writes_results_table(ev, stub_url, tmp_path, capsys):
    url, _ = stub_url
    out = tmp_path / "results.md"
    rc = ev.main(
        [
            "--base-url", url, "--model", "stub-model", "--api-key", "k",
            "--no-stream", "--only", "sm-prices", "--date", "2026-10-21", "--out", str(out),
        ]
    )  # fmt: skip
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "# 問いの提案の評価結果（2026-10-21）" in md
    assert "stub-model" in md and "127.0.0.1" in md
    assert "2/2 (100%)" in md  # 重なり ≥ 0.5 の再現率
    assert "2/3 (67%)" in md  # 閉じた形に落ちた率（出た 2 ÷ 返した 3）
    assert "`sm-prices`" in md
    assert "結果を書きました" in capsys.readouterr().out


def test_template_writes_unmeasured_without_calling_an_llm(ev, tmp_path):
    out = tmp_path / "t.md"
    assert ev.main(["--template", "--date", "2026-10-21", "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    assert "未計測" in md
    assert "24 本" in md  # gold 10 + 8 + 6
    assert "`starrydata.yaml`" in md and "`pp-snse`" in md


def test_main_needs_an_llm_unless_template(ev, tmp_path, monkeypatch):
    keys = ("ASTERISM_EVAL_BASE_URL", "ASTERISM_EVAL_MODEL")
    keys += ("ASTERISM_EVAL_API_KEY", "OPENAI_API_KEY")
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    assert ev.main(["--out", str(tmp_path / "x.md")]) == 2
    assert not (tmp_path / "x.md").exists()
