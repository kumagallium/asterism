#!/usr/bin/env python3
"""Keep the manual's "what changed" pages honest against the CHANGELOG.

``manual/ja/release-history.md`` splices ``CHANGELOG.md`` in at build time, so it
is current by construction. The feature roadmap (``manual/ja/roadmap.md``) and the
``<Badge text="vX.Y.Z (YYYY-MM-DD) で追加" />`` badges on chapter headings are
written by hand — and a hand-written version, date or link goes quietly stale
unless something checks it. This script does; CI runs it through
``api/tests/test_manual_drift.py`` (the api job, because a PR that touches
``.github/workflows/`` gets no Actions run — ADR ``workflow-pr-ci-gating``).

Ported from Graphium's ``scripts/check-manual.mjs``. Two deliberate differences:

* The manual is Japanese only, so there is no en/ja parity check.
* The "roadmap fell behind" rule counts **release days**, not minor versions.
  Asterism bumps the minor for every feature PR and ships bursts (ten minors on
  2026-09-02), so a minor-distance rule would go red in the middle of a busy day.
  One roadmap decision per release day is the cadence that fits: the check fails
  once more than ``MAX_RELEASE_DAYS_LAG`` distinct release dates have passed
  since the newest milestone on the page.

What is checked (fenced code and HTML comments are ignored everywhere — the site
renders them verbatim, so a link written inside a code block is not a link):

1. every ``<Badge>`` carries ``text="vX.Y.Z (YYYY-MM-DD) で追加"`` and names a
   version that exists in the CHANGELOG, with its exact date
2. every roadmap row (``| **vX.Y.Z** | YYYY-MM-DD |``) does the same, once
3. the roadmap is not more than ``MAX_RELEASE_DAYS_LAG`` release days behind
4. every ``./page.md#anchor`` link resolves — the anchor is computed with the
   site builder's own ``heading_slug`` so this can never disagree with the HTML
5. every ``../figures/…`` / ``../screenshots/…`` image exists
6. every ``<!--@include: …-->`` target exists

Usage:
    python scripts/check_manual.py            # the repository this script lives in
    python scripts/check_manual.py --root DIR # another tree of the same shape (tests)
"""

# ruff: noqa: RUF001 (日本語のメッセージに全角の括弧・記号を使う)
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_manual_site as site  # noqa: E402  (the builder is a script, not a package)

# 最新の節目より新しいリリースが、この日数分を超えて積もったら落とす。
MAX_RELEASE_DAYS_LAG = 3

_RELEASE = re.compile(r"^## \[?(v\d+\.\d+\.\d+)\]?.*?- (\d{4}-\d{2}-\d{2})\s*$", re.M)
_BADGE = re.compile(r'<Badge\b[^>]*?\btext="[^"]*?(v\d+\.\d+\.\d+)\s*\((\d{4}-\d{2}-\d{2})\)[^"]*"')
_ANY_BADGE = re.compile(r"<Badge\b[^>]*>")
_ROADMAP_ROW = re.compile(r"^\|\s*\*\*(v\d+\.\d+\.\d+)\*\*\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|", re.M)
_ROADMAP_BOLD = re.compile(r"^\|\s*\*\*(v\d+\.\d+\.\d+)\*\*\s*\|", re.M)
_MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\((?![a-z]+:)([^)\s#]+\.md)(#[^)]*)?\)")
_ASSET = re.compile(r"\.\./(figures|screenshots)/([^)\s]+)")
_FENCE = re.compile(r"^```", re.M)


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


def released_versions(changelog: Path) -> list[tuple[str, str]]:
    """[(version, date)] newest first, as the CHANGELOG lists them."""
    return _RELEASE.findall(changelog.read_text(encoding="utf-8"))


def without_fences(text: str) -> str:
    """Drop fenced code blocks (the site shows them verbatim, so nothing inside
    one is a link, an image, a badge or an include)."""
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def prose_of(path: Path) -> str:
    """The parts of a chapter the site renders as prose: no comments, no fences."""
    return without_fences(site._COMMENT.sub("", path.read_text(encoding="utf-8")))


def anchors_of(path: Path, manual: Path) -> set[str]:
    """Heading anchors of one chapter, computed exactly as the site does:
    comments and fenced code are not headings, includes are expanded first."""
    text = site.expand_includes(path.read_text(encoding="utf-8"), manual)
    ids: set[str] = set()
    for line in without_fences(site._COMMENT.sub("", text)).split("\n"):
        if not line.startswith("#"):
            continue
        level = len(line) - len(line.lstrip("#"))
        ids.add(site.heading_slug(line[level:].strip()))
    return ids


def check(root: Path) -> tuple[list[str], str]:
    """Run every check under ``root`` (a repo-shaped tree). Returns (errors, summary)."""
    manual = root / "manual" / "ja"
    changelog = root / "CHANGELOG.md"
    roadmap = manual / "roadmap.md"
    errors: list[str] = []
    rel = lambda p: p.relative_to(root).as_posix()  # noqa: E731
    site.REPO = root  # @include が指せる範囲＝検査している木

    released = released_versions(changelog) if changelog.is_file() else []
    if not released:
        errors.append(
            "CHANGELOG.md からリリースを 1 件も読めませんでした（見出しの形式が変わった？）"
        )
    dates = dict(released)
    latest_version, latest_date = released[0] if released else ("?", "?")
    pages = sorted(manual.glob("*.md"))

    # 1. バッジの版と日付 ------------------------------------------------------
    badge_versions: set[str] = set()
    for page in pages:
        text = prose_of(page)
        if len(_ANY_BADGE.findall(text)) != len(_BADGE.findall(text)):
            errors.append(
                f'{rel(page)}: text="vX.Y.Z (YYYY-MM-DD) で追加" の形でない <Badge> があります'
            )
        for version, date in _BADGE.findall(text):
            badge_versions.add(version)
            if version not in dates:
                errors.append(f"{rel(page)}: バッジの {version} が CHANGELOG に存在しません")
            elif dates[version] != date:
                errors.append(
                    f"{rel(page)}: バッジ {version} の日付が {date} ですが、"
                    f"CHANGELOG では {dates[version]} です"
                )

    # 2. ロードマップの行 --------------------------------------------------------
    roadmap_versions: list[str] = []
    if not roadmap.exists():
        errors.append(f"{rel(roadmap)} がありません")
    else:
        text = prose_of(roadmap)
        rows = _ROADMAP_ROW.findall(text)
        bold_only = _ROADMAP_BOLD.findall(text)
        if not rows:
            errors.append(f"{rel(roadmap)}: 節目を 1 件も読めませんでした（表の形式が変わった？）")
        if len(bold_only) != len(rows):
            errors.append(
                f"{rel(roadmap)}: 2 列目が日付でない節目の行があります"
                "（| **vX.Y.Z** | YYYY-MM-DD | 節目 | の形にしてください）"
            )
        seen: set[str] = set()
        for version, date in rows:
            if version in seen:
                errors.append(f"{rel(roadmap)}: {version} の行が重複しています")
            seen.add(version)
            roadmap_versions.append(version)
            if version not in dates:
                errors.append(f"{rel(roadmap)}: 節目の {version} が CHANGELOG に存在しません")
            elif dates[version] != date:
                errors.append(
                    f"{rel(roadmap)}: {version} の日付が {date} ですが、"
                    f"CHANGELOG では {dates[version]} です"
                )

    # 3. ロードマップが置いていかれていないか ---------------------------------
    if roadmap_versions and released:
        newest = max(roadmap_versions, key=version_key)
        newer = [(v, d) for v, d in released if version_key(v) > version_key(newest)]
        days = sorted({d for _, d in newer})
        if len(days) > MAX_RELEASE_DAYS_LAG:
            versions = ", ".join(v for v, _ in reversed(newer))
            errors.append(
                f"{rel(roadmap)}: 最新の節目は {newest} ({dates.get(newest, '?')}) ですが、"
                f"そのあと {len(days)} 日ぶんのリリースがあります"
                f"（{', '.join(days)} / {versions}）。"
                f"「できること」が変わった版を 1 行足すか、無ければ最新版 {latest_version} の行を"
                f"足してください（許容は {MAX_RELEASE_DAYS_LAG} 日ぶんまで）"
            )

    # 4. 章内リンクとアンカー ----------------------------------------------------
    anchor_cache: dict[Path, set[str]] = {}
    for page in pages:
        text = prose_of(page)
        for target_rel, anchor in _MD_LINK.findall(text):
            target = (page.parent / target_rel).resolve()
            if not target.is_file():
                errors.append(f"{rel(page)}: リンク先 {target_rel} がありません")
                continue
            if anchor and anchor != "#":
                if target not in anchor_cache:
                    try:
                        anchor_cache[target] = anchors_of(target, manual)
                    except SystemExit as e:  # 壊れた @include は生成器が落とす。ここでは指摘に
                        errors.append(f"{rel(target)}: {e}")
                        anchor_cache[target] = set()
                if anchor[1:] not in anchor_cache[target]:
                    errors.append(f"{rel(page)}: {target_rel}{anchor} のアンカーが見つかりません")

        # 5. 画像 --------------------------------------------------------------
        for sub, name in _ASSET.findall(text):
            if not (manual.parent / sub / name).is_file():
                errors.append(f"{rel(page)}: 画像 {sub}/{name} がありません")

        # 6. @include の対象（コメントなので prose_of では消える。フェンスだけ除く） -------
        for m in site._INCLUDE.finditer(without_fences(page.read_text(encoding="utf-8"))):
            if not (page.parent / m.group(1)).resolve().is_file():
                errors.append(f"{rel(page)}: @include の対象 {m.group(1)} がありません")

    summary = (
        f"check_manual — ページ {len(pages)} 件 / バッジの版 {len(badge_versions)} 種 / "
        f"ロードマップの節目 {len(roadmap_versions)} 件 / "
        f"最新リリース {latest_version} ({latest_date})"
    )
    return errors, summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root", type=Path, default=REPO, help="検査する木の根（既定: このリポジトリ）"
    )
    args = ap.parse_args()
    errors, summary = check(args.root.resolve())
    print(summary)
    if errors:
        print(f"\n{len(errors)} 件の問題:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
