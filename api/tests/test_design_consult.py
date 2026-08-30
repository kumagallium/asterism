"""POST /api/design/consult (ADR design-consult-chat.md): stateless, tool-free
design-consult chat turn. Mock LLM, no oxigraph client needed (this route never
touches the store)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api.main import CONSULT_MANUAL_TEXT, CONSULT_SYSTEM_PROMPT, Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}

# api/tests -> api -> repo root. The manual (D8: single source of truth for
# both the human help text and the AI's navigation knowledge) and the ja UI
# locales it must stay consistent with both live under the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANUAL_DIR = _REPO_ROOT / "manual" / "ja"
_UI_JA_LOCALES_DIR = _REPO_ROOT / "ui" / "src" / "i18n" / "locales" / "ja"

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# The manual's own naming convention (documented in its own header comment,
# stripped above before matching so the convention's example text — which
# itself contains a literal 「文言」ボタン — is never mistaken for a real claim).
_QUOTED_BUTTON_OR_TAB = re.compile(r"「([^」]+)」(?:ボタン|タブ)")
_QUOTED_MENU = re.compile(r"メニューの「([^」]+)」")
# 画面・欄の名前も UI の実文言でなければならない。ボタン/タブ/メニューだけを見て
# いたときに、実在しない画面名 "ID を確かめる" [正しくは "ID のつけかた" の 2 番目]
# がマニュアルに入り、検査を素通りした。文型を 「…」 + 画面/欄 に絞ってあるのは、
# 比喩や例示の引用 ["わからないこと" など] まで拾わないため。
_QUOTED_SCREEN = re.compile(r"「([^」]+)」(?:の)?(?:画面|欄)")


def _settings(tmp: Path) -> Settings:
    s = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": "http://test",
            "CSV2RDF_SETTLE_S": "0.0",
        }
    )
    s.api_token = _TEST_TOKEN
    return s


class _MockLLM:
    """Records the rendered prompt and returns canned text (no network)."""

    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.captured["system"] = system_prompt
        self.captured["user"] = user_message
        return "Quality はデータの信頼度を表す 5 段階の等級です。"


def _app(tmp_path: Path, captured: dict[str, object]):
    return build_app(
        _settings(tmp_path),
        start_watcher=False,
        llm_factory=lambda key: _MockLLM(captured),
    )


def test_consult_returns_reply(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={"messages": [{"role": "user", "content": "Quality 列って何?"}]},
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "reply" in body
        assert "Quality" in body["reply"]


def test_consult_rejects_empty_messages(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/design/consult", json={"messages": []})
        assert r.status_code == 400
        r2 = client.post(
            "/api/design/consult", json={"messages": [{"role": "user", "content": "   "}]}
        )
        assert r2.status_code == 400


def test_consult_weaves_context_into_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [{"role": "user", "content": "この列はどういう意味?"}],
                "context": {
                    "step": "項目の意味",
                    "dataset": "XRDサンプル",
                    "skeleton_summary": "1行=1測定点",
                    "focus_column": {"name": "Quality", "samples": ["A", "B", "C"]},
                },
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "Quality" in user_message
        assert "A" in user_message and "B" in user_message and "C" in user_message
        assert "項目の意味" in user_message
        assert "XRDサンプル" in user_message
        assert "この列はどういう意味" in user_message


def test_consult_weaves_pending_and_confirmed_columns_into_prompt(tmp_path: Path) -> None:
    """2026-08-25 real-LLM dogfood: asked about "the 17 columns not yet
    imported", the AI replied "which columns?" because the S6 table on screen
    never rode along in the context. Pins that the pending/confirmed column
    tables reach the prompt with real column names and sample values."""
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [
                    {"role": "user", "content": "まだ取り込んでいない項目の意味を答えられますか?"}
                ],
                "context": {
                    "step": "項目の意味",
                    "pending_columns": [
                        {"name": "CSD", "samples": ["N AL1935 (NIST)"]},
                        {"name": "Name", "samples": ["Aluminum Vanadium"]},
                    ],
                    "columns": [
                        {"name": "d", "meaning": "面間隔 d", "unit": "Å"},
                        {"name": "2theta", "meaning": "回折角 2θ", "unit": "deg"},
                    ],
                },
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "まだ取り込んでいない項目" in user_message
        assert "CSD" in user_message
        assert "N AL1935 (NIST)" in user_message
        assert "Name" in user_message
        assert "Aluminum Vanadium" in user_message
        assert "意味が確定している項目" in user_message
        assert "d = 面間隔 d" in user_message
        assert "2theta = 回折角 2θ" in user_message


def test_consult_weaves_missing_meaning_columns_into_prompt(tmp_path: Path) -> None:
    """2026-08-25 real-LLM dogfood (A): asked "意味が空欄の列の候補を出して",
    the AI asked back for column names — `_render_confirmed_columns` skipped
    every column whose meaning was still blank, so an already-mapped-but-
    unlabeled column never reached the prompt at all. Pins that the new
    "意味が未入力の項目" line carries the blank columns' names AND real sample
    values, and that it sits alongside (not instead of) the confirmed line."""
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [
                    {"role": "user", "content": "意味が空欄の列の候補を出してください"}
                ],
                "context": {
                    "step": "項目の意味",
                    "columns": [
                        {"name": "d", "meaning": "面間隔 d", "unit": "Å"},
                        {
                            "name": "Additional Patterns",
                            "samples": ["See PDF 03-065-5860", "and 03-065-5861"],
                        },
                        {"name": "CSD", "samples": ["N AL1935 (NIST)"]},
                    ],
                },
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "意味が確定している項目" in user_message
        assert "d = 面間隔 d" in user_message
        assert "意味が未入力の項目 (2 件)" in user_message
        assert "Additional Patterns" in user_message
        assert "See PDF 03-065-5860" in user_message
        assert "CSD" in user_message
        assert "N AL1935 (NIST)" in user_message


def test_consult_weaves_kinds_into_prompt(tmp_path: Path) -> None:
    """2026-08-25 (B): S4 の「1 件が表すもの」欄も同じ導線で埋めたい、という要望。
    Pins that `context.kinds` renders as "データの種類: <map> (ID: ..., 種類名:
    ...)" — with the ID recipe from key_columns and 未入力 for a blank kind."""
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [{"role": "user", "content": "peak の種類名を提案して"}],
                "context": {
                    "step": "データの数えかた",
                    "kinds": [
                        {"map": "peak", "source": "xrd.txt", "key_columns": ["No", "(hkl)"]},
                        {
                            "map": "sample",
                            "source": "xrd.txt",
                            "key_columns": ["No"],
                            "kind_name": "試料",
                        },
                    ],
                },
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "データの種類" in user_message
        assert "peak (ID: No+(hkl), 種類名: 未入力)" in user_message
        assert "sample (ID: No, 種類名: 試料)" in user_message


def test_consult_context_without_kinds_is_backward_compatible(tmp_path: Path) -> None:
    """A context payload with no ``kinds`` key at all (every pre-D10-extension-B
    client) must keep working exactly as before — ``kinds`` defaults to ``[]``
    and renders nothing."""
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [{"role": "user", "content": "この列はどういう意味?"}],
                "context": {"step": "項目の意味", "dataset": "XRDサンプル"},
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "データの種類" not in user_message


def test_consult_system_prompt_instructs_kinds_suggestions() -> None:
    """D10 extension (B): the model is told it MAY offer a kind-name candidate
    via ``kinds`` in the suggestions block, that ``map`` must be the on-screen
    map name verbatim, and that it must not propose an ID recipe or an
    include/exclude decision — only the kind name."""
    assert '"kinds"' in CONSULT_SYSTEM_PROMPT
    assert '"map"' in CONSULT_SYSTEM_PROMPT
    assert "ID の作り方" in CONSULT_SYSTEM_PROMPT
    assert "取り込む/取り込まないの裁定" in CONSULT_SYSTEM_PROMPT


def test_consult_weaves_kind_columns_into_prompt(tmp_path: Path) -> None:
    """ADR kind-splitting-and-consult-suggestions D3: ``splits``/``owners`` name
    COLUMNS, so each kind's carried items have to reach the prompt — otherwise
    the model can only ask "which columns?" (the same gap ``suggestions``
    closed for S6)."""
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [{"role": "user", "content": "この表をどう分けるとよいですか"}],
                "context": {
                    "step": "ID のつけかた",
                    "kinds": [
                        {
                            "map": "card",
                            "source": "xrd.txt",
                            "key_columns": ["No"],
                            "columns": ["Name", "Cell", "Volume"],
                        }
                    ],
                },
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "card (ID: No, 種類名: 未入力, 項目: Name・Cell・Volume)" in user_message


def test_consult_system_prompt_instructs_structural_suggestions() -> None:
    """ADR kind-splitting-and-consult-suggestions D3: the model is told it may
    offer ``splits`` / ``owners`` / ``identifiers``, that ``identifiers``
    REQUIRES a ``reason`` (K22 — give the human something to decide from, do
    not just make them press a button), that an owner move is limited to kinds
    counted the same way (`meaning-before-identity.md`), and that the ID recipe
    itself stays off the table (非目標: it cannot be moved after publication)."""
    for key in ('"splits"', '"owners"', '"identifiers"', '"reason"', '"from"'):
        assert key in CONSULT_SYSTEM_PROMPT, key
    assert "`reason` は必須" in CONSULT_SYSTEM_PROMPT
    assert "1 件の数えかたが同じ種類どうしでしか動かせません" in CONSULT_SYSTEM_PROMPT
    assert "ID の作り方(IRI の形・どの列で数えるか)は提案しません" in CONSULT_SYSTEM_PROMPT


def _manual_ui_phrases() -> list[tuple[str, str]]:
    """Every UI-name claim the manual makes — (phrase, source filename) —
    per the getting-started.md/screens.md header-comment convention: buttons
    as 「文言」ボタン, tabs as 「文言」タブ, sidebar entries as メニューの「文言」,
    and screens/fields as 「文言」画面 / 「文言」欄.
    HTML comments are stripped first so the convention's own explanatory
    example (which necessarily contains a literal 「文言」ボタン) is never
    mistaken for a real navigation claim."""
    out: list[tuple[str, str]] = []
    for path in sorted(_MANUAL_DIR.glob("*.md")):
        text = _HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))
        for m in _QUOTED_BUTTON_OR_TAB.finditer(text):
            out.append((m.group(1), path.name))
        for m in _QUOTED_MENU.finditer(text):
            out.append((m.group(1), path.name))
        # 画面名は行またぎで折り返されることがあるので、改行を畳んでから探す。
        for m in _QUOTED_SCREEN.finditer(text.replace("\n", "")):
            out.append((m.group(1), path.name))
    return out


def _all_ja_ui_strings() -> list[str]:
    """Every string VALUE (any nesting depth) across ui/src/i18n/locales/ja/*.json."""
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            out.append(node)

    for path in sorted(_UI_JA_LOCALES_DIR.glob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return out


def test_manual_ui_names_exist_in_ui_locales() -> None:
    """Machine detection of manual staleness (ADR D8 / real-LLM dogfood
    2026-08-25: the AI invented "左側メニューの「データ設計」" and "プロジェクト
    一覧の「設定をリセット」" — neither exists — because the old hardcoded
    catalog had drifted from the real UI). Every button/tab/menu name the
    manual claims must be a real substring somewhere in the ja UI locales;
    a UI rename that forgets to update the manual fails HERE instead of in a
    user's session."""
    phrases = _manual_ui_phrases()
    assert phrases, (
        "manual/ja/*.md named no UI buttons/tabs/menus at all "
        "— check _QUOTED_BUTTON_OR_TAB / _QUOTED_MENU against the manual's own convention"
    )
    ja_strings = _all_ja_ui_strings()
    missing = [
        (phrase, source) for phrase, source in phrases if not any(phrase in s for s in ja_strings)
    ]
    assert not missing, (
        "manual の UI 名が UI に見当たらない — UI 変更にマニュアルが追従していません:\n"
        + "\n".join(f"  {source}: 「{phrase}」" for phrase, source in missing)
    )


def test_consult_system_prompt_includes_manual() -> None:
    """CONSULT_SYSTEM_PROMPT gets its navigation knowledge from manual/ja/*.md
    (D8), not a hardcoded catalog — this is what makes
    test_manual_ui_names_exist_in_ui_locales's guarantee actually reach the
    LLM call. `_load_consult_manual` degrades silently when the manual dir is
    missing (main.py), but running from a real repo checkout (this test's
    environment) it MUST be found."""
    assert CONSULT_MANUAL_TEXT, (
        "manual/ja/*.md was not loaded into CONSULT_MANUAL_TEXT — "
        "check _find_consult_manual_dir()/ASTERISM_MANUAL_DIR and the repo layout"
    )
    assert "設計を見直す" in CONSULT_MANUAL_TEXT  # sanity: a screens.md phrase actually made it in
    assert CONSULT_MANUAL_TEXT in CONSULT_SYSTEM_PROMPT
    assert "発明しては" in CONSULT_SYSTEM_PROMPT
    assert "聞き返して" in CONSULT_SYSTEM_PROMPT


def test_consult_system_prompt_instructs_suggestion_blocks() -> None:
    """D9: the model is told how to offer meaning/unit candidates the UI can
    parse — the exact fence tag, the JSON shape, that ``column`` must be the
    on-screen name verbatim, and that the block is a candidate (a human still
    confirms it) rather than the model editing anything itself."""
    from asterism_api.main import CONSULT_SUGGESTIONS_FENCE

    assert CONSULT_SUGGESTIONS_FENCE in CONSULT_SYSTEM_PROMPT
    assert f"```{CONSULT_SUGGESTIONS_FENCE}" in CONSULT_SYSTEM_PROMPT
    assert '"suggestions"' in CONSULT_SYSTEM_PROMPT
    assert '"column"' in CONSULT_SYSTEM_PROMPT
    assert '"meaning"' in CONSULT_SYSTEM_PROMPT
    assert '"unit"' in CONSULT_SYSTEM_PROMPT
    assert "採用と確定は必ず" in CONSULT_SYSTEM_PROMPT
