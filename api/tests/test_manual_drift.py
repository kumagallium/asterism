"""``manual/ja/`` の「更新を知る」ページが CHANGELOG からズレていないかを見る。

リリース履歴は CHANGELOG を取り込むので放っておいても最新になるが、機能ロード
マップ（``roadmap.md``）と各章の ``<Badge text="vX.Y.Z (日付) で追加" />`` は人が
書く。版や日付の写し間違い・章内リンクの切れ・ロードマップの置き去りは、誰かが
気づくまで公開されたままになるので、``scripts/check_manual.py`` に機械で見させる
（Graphium の ``pnpm manual:check`` と同じ役）。

api のテストに置いた理由は ``test_manual_site_fresh.py`` と同じ — ``.github/workflows/``
を触る PR には Actions run がスケジュールされない（ADR ``workflow-pr-ci-gating``）ため、
既存の api ジョブに相乗りする。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKER = _REPO_ROOT / "scripts" / "check_manual.py"


def test_manual_matches_changelog() -> None:
    """バッジ・ロードマップの版と日付が CHANGELOG に実在し、リンクが切れておらず、
    ロードマップが最新リリースに置いていかれていないこと。"""
    if not _CHECKER.exists():  # マニュアルを持たないチェックアウト（sdist 等）では見ない
        return
    proc = subprocess.run(
        [sys.executable, str(_CHECKER)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "manual/ja/ が CHANGELOG や自分自身とズレています。\n"
        "  python scripts/check_manual.py\n"
        "の指摘を直してください。ロードマップの置き去りなら manual/ja/roadmap.md に"
        "節目を 1 行足します。\n\n" + proc.stdout + proc.stderr
    )


# ── 検知ロジックそのものの退行を見る（実データが「たまたま正しい」だけでは通らないように） ──

_CHANGELOG = """# Changelog

## [v0.2.0](https://example/compare/v0.1.0...v0.2.0) - 2026-06-11

- feat: b by @me in https://github.com/o/r/pull/2

## [v0.1.1](https://example/compare/v0.1.0...v0.1.1) - 2026-06-09

- fix: a by @me in https://github.com/o/r/pull/1

## [v0.1.0](https://example/commits/v0.1.0) - 2026-06-02

- feat: first by @me in https://github.com/o/r/pull/0
"""

_CHAPTER = """# 章 — 説明

## 追記する <Badge type="tip" text="v0.1.1 (2026-06-09) で追加" />

本文。[基礎](./roadmap.md#はじめに) と ![図](../figures/a.svg)
"""

_ROADMAP = """# 機能ロードマップ

## はじめに

| バージョン | 日付 | 節目 |
|---|---|---|
| **v0.1.0** | 2026-06-02 | 最初。[追記](./chapter.md#追記する) |
| **v0.2.0** | 2026-06-11 | 次。 |
"""


def _fixture(
    tmp_path: Path,
    *,
    changelog: str = _CHANGELOG,
    chapter: str = _CHAPTER,
    roadmap: str = _ROADMAP,
) -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo", dir=tmp_path))  # 1 テストで何度も作る
    (root / "manual" / "ja").mkdir(parents=True)
    (root / "manual" / "figures").mkdir()
    (root / "manual" / "figures" / "a.svg").write_text("<svg/>", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (root / "manual" / "ja" / "chapter.md").write_text(chapter, encoding="utf-8")
    (root / "manual" / "ja" / "roadmap.md").write_text(roadmap, encoding="utf-8")
    return root


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(_CHECKER), "--root", str(root)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_fixture_passes_as_written(tmp_path: Path) -> None:
    if not _CHECKER.exists():
        return
    code, out = _run(_fixture(tmp_path))
    assert code == 0, out


def test_badge_with_wrong_date_or_unknown_version_fails(tmp_path: Path) -> None:
    if not _CHECKER.exists():
        return
    code, out = _run(_fixture(tmp_path, chapter=_CHAPTER.replace("2026-06-09", "2026-06-10")))
    assert code == 1 and "日付が" in out, out
    code, out = _run(_fixture(tmp_path, chapter=_CHAPTER.replace("v0.1.1", "v9.9.9")))
    assert code == 1 and "存在しません" in out, out
    no_text = _CHAPTER.replace('text="v0.1.1 (2026-06-09) で追加" ', "")
    code, out = _run(_fixture(tmp_path, chapter=no_text))
    assert code == 1 and "形でない" in out, out


def test_roadmap_rows_are_checked(tmp_path: Path) -> None:
    if not _CHECKER.exists():
        return
    wrong_date = _ROADMAP.replace("| 2026-06-11 |", "| 2026-06-12 |")
    code, out = _run(_fixture(tmp_path, roadmap=wrong_date))
    assert code == 1 and "日付が" in out, out
    code, out = _run(_fixture(tmp_path, roadmap=_ROADMAP.replace("**v0.2.0**", "**v0.3.0**")))
    assert code == 1 and "存在しません" in out, out
    dup = _ROADMAP + "| **v0.2.0** | 2026-06-11 | 重複。 |\n"
    code, out = _run(_fixture(tmp_path, roadmap=dup))
    assert code == 1 and "重複" in out, out


def test_roadmap_left_behind_by_too_many_release_days_fails(tmp_path: Path) -> None:
    if not _CHECKER.exists():
        return
    # 最新の節目 v0.1.0 のあと、リリース日が 06-09 / 06-11 / 06-12 の 3 日ぶん → 許容内
    three = _CHANGELOG.replace(
        "## [v0.2.0]",
        "## [v0.3.0](x) - 2026-06-12\n\n- c by @me in https://github.com/o/r/pull/3\n\n## [v0.2.0]",
    )
    only_first = _ROADMAP.split("| **v0.2.0**")[0]
    code, out = _run(_fixture(tmp_path, changelog=three, roadmap=only_first))
    assert code == 0, out
    # 06-13 に 2 版足すと 4 日ぶん → 落ちる（同じ日に何版出ても 1 日と数える）
    four = three.replace(
        "## [v0.3.0]",
        "## [v0.4.1](x) - 2026-06-13\n\n- e by @me in https://github.com/o/r/pull/5\n\n"
        "## [v0.4.0](x) - 2026-06-13\n\n- d by @me in https://github.com/o/r/pull/4\n\n## [v0.3.0]",
    )
    code, out = _run(_fixture(tmp_path, changelog=four, roadmap=only_first))
    assert code == 1 and "4 日ぶん" in out, out


def test_broken_anchor_image_and_include_fail_but_fenced_ones_do_not(tmp_path: Path) -> None:
    if not _CHECKER.exists():
        return
    code, out = _run(_fixture(tmp_path, chapter=_CHAPTER.replace("#はじめに", "#はじめ")))
    assert code == 1 and "アンカー" in out, out
    code, out = _run(_fixture(tmp_path, chapter=_CHAPTER.replace("a.svg", "b.svg")))
    assert code == 1 and "画像" in out, out
    code, out = _run(_fixture(tmp_path, chapter=_CHAPTER + "\n<!--@include: ../../NOPE.md-->\n"))
    assert code == 1 and "@include" in out, out
    fenced = _CHAPTER + (
        "\n```\n[例](./nope.md) ../figures/nope.svg <!--@include: ../../NOPE.md-->\n```\n"
    )
    code, out = _run(_fixture(tmp_path, chapter=fenced))
    assert code == 0, out
