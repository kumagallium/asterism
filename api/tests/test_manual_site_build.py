"""``scripts/build_manual_site.py`` の、生成物では見えにくい 2 つの拡張を見る。

* ``<Badge … />`` が本文ではピルになり、見出しのアンカーからは消えること
  （消えないと ``roadmap.md`` からの ``#anchor`` リンクが、バッジを足した瞬間に切れる）
* ``<!--@include: …{start,end}-->`` が行範囲どおりに取り込まれ、裸の PR URL が
  ``#123`` のリンクになること（``release-history.md`` が CHANGELOG を載せる仕組み）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILDER = _REPO_ROOT / "scripts" / "build_manual_site.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_manual_site", _BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_manual_site", module)
    spec.loader.exec_module(module)
    return module


def test_badge_renders_as_pill_and_leaves_the_anchor_alone() -> None:
    if not _BUILDER.exists():
        return
    site = _load_builder()
    md = '## 追記する <Badge type="tip" text="v0.3.0 (2026-06-13) で追加" />\n\n本文'
    html = site.convert(md)
    assert '<h2 id="追記する">' in html
    assert '<span class="badge">v0.3.0 (2026-06-13) で追加</span>' in html
    assert "<Badge" not in html  # 生のタグが漏れない（漏れるとブラウザが黙って捨てる）
    assert site.heading_slug('追記する <Badge text="v0.3.0 (2026-06-13) で追加" />') == "追記する"
    assert site.title_of('# 設定 <Badge type="tip" text="v0.1.0 (2026-06-02) で追加" />') == "設定"


def test_md_links_with_anchors_are_rewritten_to_html() -> None:
    if not _BUILDER.exists():
        return
    site = _load_builder()
    # roadmap.md は ./章.md#見出し の形で章の中へリンクする。.md の後ろに #… が付いても
    # .html に書き換わること（付かない形しか無かったときの取りこぼし）。
    html = site.inline("[追記](./datasets.md#新しい測定分を足す-追記) と [章](./ask.md)")
    assert 'href="./datasets.html#新しい測定分を足す-追記"' in html
    assert 'href="./ask.html"' in html


def test_heading_slug_drops_link_markup() -> None:
    if not _BUILDER.exists():
        return
    site = _load_builder()
    # CHANGELOG の見出しは版がリンクになっている。アンカーは読める形に落ちること。
    assert site.heading_slug("[v0.44.0](https://example/compare/a...b) - 2026-09-23") == (
        "v0-44-0-2026-09-23"
    )


def test_include_splices_a_line_range_and_links_bare_pr_urls(tmp_path: Path) -> None:
    if not _BUILDER.exists():
        return
    site = _load_builder()
    # expand_includes はリポジトリ内のファイルしか許さないので、REPO を tmp に向ける
    original = site.REPO
    site.REPO = tmp_path
    try:
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n"
            "\n"
            "## [v0.2.0](https://github.com/o/r/compare/v0.1.0...v0.2.0) - 2026-06-11\n"
            "\n"
            "- feat: 追記 by @me in https://github.com/o/r/pull/174\n"
            "- see https://example.org/x\n",
            encoding="utf-8",
        )
        chapter_dir = tmp_path / "manual" / "ja"
        chapter_dir.mkdir(parents=True)
        md = "前書き\n\n<!--@include: ../../CHANGELOG.md{3,}-->\n"
        out = site.expand_includes(md, chapter_dir)
    finally:
        site.REPO = original
    assert "# Changelog" not in out  # 1〜2 行目は範囲外
    assert out.startswith("前書き\n\n## [v0.2.0](")  # 既存のリンクは二重にリンクされない
    assert "[#174](https://github.com/o/r/pull/174)" in out
    assert "[https://example.org/x](https://example.org/x)" in out


def test_check_ignores_content_drift_of_changelog_derived_pages(tmp_path: Path) -> None:
    """release-history.html は CHANGELOG の関数。リリース PR のマージ直後の CI は、tagpr job が
    main で作り直す前に --check を回すので、内容のズレでは落ちてはいけない（無いのは落ちる）。"""
    if not _BUILDER.exists():
        return
    import subprocess

    out = _REPO_ROOT / "docs" / "manual" / "ja" / "release-history.html"
    if not out.exists():
        return
    original = out.read_bytes()
    try:
        out.write_bytes(original + b"<!-- drift -->")
        proc = subprocess.run(
            [sys.executable, str(_BUILDER), "--check"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out.unlink()
        proc = subprocess.run(
            [sys.executable, str(_BUILDER), "--check"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
    finally:
        out.write_bytes(original)


def test_include_rejects_a_slightly_wrong_directive_instead_of_dropping_it(tmp_path: Path) -> None:
    """`<!--@include: X.md {3,}-->`（{ の前に空白）は正規表現に掛からず、そのままだと
    コメントとして黙って消える＝リリース履歴が空になる。落ちること。"""
    if not _BUILDER.exists():
        return
    import pytest

    site = _load_builder()
    original = site.REPO
    site.REPO = tmp_path
    try:
        (tmp_path / "CHANGELOG.md").write_text("# C\n\nx\n", encoding="utf-8")
        d = tmp_path / "manual" / "ja"
        d.mkdir(parents=True)
        with pytest.raises(SystemExit):
            site.expand_includes("<!--@include: ../../CHANGELOG.md {3,}-->", d)
        with pytest.raises(SystemExit):  # 0 始まり・逆転した範囲
            site.expand_includes("<!--@include: ../../CHANGELOG.md{0,}-->", d)
        with pytest.raises(SystemExit):
            site.expand_includes("<!--@include: ../../CHANGELOG.md{3,2}-->", d)
        # 本文で @include に言及するだけのコメントは通る
        md = "<!-- 下の @include を見よ -->\n<!--@include: ../../CHANGELOG.md{3,}-->"
        out = site.expand_includes(md, d)
        assert out.endswith("x\n") or out.endswith("x")
    finally:
        site.REPO = original


def test_autolink_leaves_code_spans_and_trailing_punctuation_alone(tmp_path: Path) -> None:
    if not _BUILDER.exists():
        return
    site = _load_builder()
    original = site.REPO
    site.REPO = tmp_path
    try:
        (tmp_path / "CHANGELOG.md").write_text(
            "- run `curl https://example.org/api` first\n"
            "- see https://github.com/o/r/pull/12.\n"
            "- or https://example.org/x, then\n",
            encoding="utf-8",
        )
        d = tmp_path / "manual" / "ja"
        d.mkdir(parents=True)
        out = site.expand_includes("<!--@include: ../../CHANGELOG.md-->", d)
    finally:
        site.REPO = original
    assert "`curl https://example.org/api`" in out  # コードスパンの中は触らない
    assert "[#12](https://github.com/o/r/pull/12)." in out  # 句読点は URL の外
    assert "[https://example.org/x](https://example.org/x)," in out


def test_badge_without_text_is_dropped_not_leaked() -> None:
    if not _BUILDER.exists():
        return
    site = _load_builder()
    html = site.inline('見出し <Badge type="tip" /> 続き')
    assert "Badge" not in html and "&lt;" not in html
    assert site.heading_slug('見出し <Badge type="tip" />') == "見出し"
    # コードスパンの中の Badge 風の文字列は、アンカーにもタイトルにも残る
    assert site.heading_slug('設定 `<Badge text="x" />` の説明') == "設定-Badge-text-x-の説明"
