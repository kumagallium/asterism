#!/usr/bin/env python3
"""Build the static user-manual site under ``docs/manual/`` from ``manual/ja/``.

``manual/ja/*.md`` is the single source of truth: it is the human help text, the
knowledge the design-consult chat is given (``_load_consult_manual`` in the api),
and the target of the UI-name staleness test (``api/tests/test_design_consult.py``).
This script projects that markdown onto GitHub Pages so the manual is reachable
without cloning the repo — it never edits the markdown, and nothing but the
markdown decides what the site says.

The markdown subset used by the manual is small and closed (headings, paragraphs,
bullet/numbered lists, pipe tables, fenced code, images, links, ``**bold**`` and
inline code), so the converter is written here rather than pulling in a markdown
dependency — the same "self-contained repo" rule the rest of the tooling follows.
Figures are self-contained SVGs (``manual/figures/*.svg`` carry their own styles
and both themes), so a rendered page needs no JavaScript at all.

Relative links survive the projection unchanged: ``manual/ja/x.md`` refers to
``../figures/y.svg`` and ``../screenshots/z.png``, and the generated
``docs/manual/ja/x.html`` sits at the same depth over copies of those directories.
Only ``.md`` link targets are rewritten to ``.html``.

Two small extensions serve the "what changed" pages (both borrowed from Graphium's
manual, which uses VitePress for the same job):

* ``<!--@include: ../../CHANGELOG.md{3,}-->`` splices another file in at build
  time (1-based inclusive line range, either end optional). Bare URLs in the
  spliced text become links, and pull-request URLs are shown as ``#123``. This is
  how ``release-history.md`` carries the whole CHANGELOG without anyone copying it.
* ``<Badge type="tip" text="v0.23.0 (2026-08-28) で追加" />`` renders as a small
  pill and is dropped from heading anchors. ``scripts/check_manual.py`` verifies
  every badge against the CHANGELOG, so a badge can never name a version or date
  that was not released.

Usage:
    python scripts/build_manual_site.py            # write docs/manual/
    python scripts/build_manual_site.py --check    # fail if the output is stale
                                                   #   (release-history.html: only if missing)
"""

# ruff: noqa: RUF001 (日本語のメッセージに全角の括弧・記号を使う)
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "manual"
OUT = REPO / "docs" / "manual"

# Reading order of the site's sidebar. index.md is the landing page and is not
# listed as a chapter; a file missing here would be built but unreachable, so the
# builder fails loudly instead (see `chapter_order`).
ORDER = [
    "getting-started.md",
    "add-data.md",
    "datasets.md",
    "crosswalk.md",
    "ask.md",
    "vocab-and-grounding.md",
    "rdf-basics.md",
    "dataset-files.md",
    "consult.md",
    "settings.md",
    "desktop.md",
    "screens.md",
    "roadmap.md",
    "release-history.md",
]
GROUPS = [
    ("はじめに", ["getting-started.md"]),
    ("データを育てる", ["add-data.md", "datasets.md", "crosswalk.md"]),
    ("つかう", ["ask.md", "vocab-and-grounding.md"]),
    ("しくみを知る", ["rdf-basics.md", "dataset-files.md"]),
    ("そばに置く", ["consult.md", "settings.md", "desktop.md"]),
    ("困ったときは", ["screens.md"]),
    ("更新を知る", ["roadmap.md", "release-history.md"]),
]

# Output that is a function of CHANGELOG.md, not of manual/. tagpr rewrites the
# CHANGELOG on every release, and the tagpr job regenerates these on main right
# after the tag (ADR manual-roadmap-and-release-history.md D4) — but the CI run on
# the release PR's merge commit sees the new CHANGELOG before that regeneration
# lands. So `--check` reports these as stale only when they are MISSING, never for
# content drift; a human build still rewrites them every time.
CHANGELOG_DERIVED = frozenset({"ja/release-history.html"})

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INCLUDE = re.compile(r"<!--\s*@include:\s*(\S+?)(?:\{(\d*),(\d*)\})?\s*-->")
# text 属性は任意にしておき、無いものは本文から落とす（生タグを漏らさない）。
# 版と日付の形は scripts/check_manual.py が別途検査する。
_BADGE = re.compile(r'<Badge\b(?:[^>]*?\btext="([^"]*)")?[^>]*?/?>')
_INCLUDE_LEFTOVER = re.compile(r"<!--\s*@include")  # コメント先頭の @include だけ（本文の言及は可）
_TRAILING_PUNCT = re.compile(r"[.,;:、。]+$")
# 裸の URL（既にリンク・コード・括弧の中にあるものは除く）。include した文にだけ使う。
_BARE_URL = re.compile(r'(?<![(\["<`])https?://[^\s<>()\[\]]+')
_PR_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/(\d+)$")
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ASCII_EDGE = re.compile(r"[0-9A-Za-z),.;:!?\"']$")
_ASCII_HEAD = re.compile(r"^[0-9A-Za-z(\"']")


def expand_includes(md: str, base: Path) -> str:
    """Resolve ``<!--@include: path{start,end}-->`` against ``base`` (the chapter's
    own directory). The spliced text is markdown, so it goes through the same
    converter afterwards; the only rewrite here is turning bare URLs into links
    (tagpr writes the CHANGELOG with raw ``https://…/pull/123`` URLs)."""

    def autolink(m: re.Match[str]) -> str:
        url = m.group(0)
        tail = _TRAILING_PUNCT.search(url)  # 文末の句読点は URL に含めない
        if tail:
            url = url[: tail.start()]
        pr = _PR_URL.match(url)
        label = f"#{pr.group(1)}" if pr else url
        return f"[{label}]({url}){tail.group(0) if tail else ''}"

    def autolink_outside_code(text: str) -> str:
        spans: list[str] = []

        def stash(m: re.Match[str]) -> str:
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"

        text = _BARE_URL.sub(autolink, _CODE.sub(stash, text))
        return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)

    def splice(m: re.Match[str]) -> str:
        target = (base / m.group(1)).resolve()
        if REPO not in target.parents:
            raise SystemExit(f"@include はリポジトリの外を指せません: {m.group(1)}")
        if not target.is_file():
            raise SystemExit(f"@include の対象がありません: {m.group(1)}")
        lines = target.read_text(encoding="utf-8").split("\n")
        start = int(m.group(2)) if m.group(2) else 1
        end = int(m.group(3)) if m.group(3) else len(lines)
        if start < 1 or end < start:
            raise SystemExit(f"@include の行範囲が不正です（1 始まり・両端含む）: {m.group(0)}")
        return autolink_outside_code("\n".join(lines[start - 1 : end]))

    # フェンスの中は逐語表示なので触らない（@include の書き方を説明する章が書けるように）
    out: list[str] = []
    segment: list[str] = []
    in_fence = False

    def flush() -> None:
        text = _INCLUDE.sub(splice, "\n".join(segment))
        leftover = _INCLUDE_LEFTOVER.search(text)
        if leftover:  # 構文がわずかに違う @include は、黙ってコメントとして消えてしまう
            raise SystemExit(
                f"@include の書き方が違います（例: <!--@include: ../../X.md{{3,}}-->）: "
                f"{leftover.group(0)}…"
            )
        out.append(text)
        segment.clear()

    for line in md.split("\n"):
        if line.strip().startswith("```"):
            if not in_fence:
                flush()
            in_fence = not in_fence
            segment.append(line)
            if not in_fence:
                out.append("\n".join(segment))
                segment.clear()
            continue
        segment.append(line)
    flush()
    return "\n".join(out)


def strip_badges(text: str) -> str:
    """Heading text without its ``<Badge … />`` tags (for anchors and titles).
    Code spans are left alone, as ``inline()`` leaves them alone."""
    spans: list[str] = []

    def stash(m: re.Match[str]) -> str:
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    text = _BADGE.sub("", _CODE.sub(stash, text))
    text = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)
    return re.sub(r"\s{2,}", " ", text).strip()


def heading_slug(text: str) -> str:
    """Anchor id of a heading. Badges and link markup are dropped first so
    ``## 追記する <Badge … />`` and ``## [v0.44.0](…) - 2026-09-23`` get stable,
    readable ids. ``scripts/check_manual.py`` imports this to resolve ``#anchor``
    links exactly the way the site does."""
    plain = _LINK.sub(r"\1", strip_badges(text))
    return re.sub(r"[^\w一-龯ぁ-んァ-ヶー]+", "-", plain).strip("-")


def join_wrapped(lines: list[str]) -> str:
    """Join a paragraph's source lines. Japanese wraps without a space; a break
    between two ASCII runs keeps one (so `foo`\\n`bar` does not become `foobar`)."""
    out = ""
    for line in lines:
        line = line.strip()
        if not out:
            out = line
            continue
        if _ASCII_EDGE.search(out) and _ASCII_HEAD.match(line):
            out += " " + line
        else:
            out += line
    return out


def inline(text: str) -> str:
    """Inline markdown -> HTML. Code spans are extracted first so their contents
    are never re-parsed as markdown (a `**` inside a span stays literal)."""
    spans: list[str] = []

    def stash(m: re.Match[str]) -> str:
        spans.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = _CODE.sub(stash, text)

    def badge(m: re.Match[str]) -> str:
        if not m.group(1):
            return ""  # text の無い Badge は出さない（check_manual.py が別途落とす）
        spans.append(f'<span class="badge">{html.escape(m.group(1))}</span>')
        return f"\x00{len(spans) - 1}\x00"

    text = _BADGE.sub(badge, text)
    text = html.escape(text, quote=False)
    def image(m: re.Match[str]) -> str:
        src = html.escape(m.group(2), quote=True)
        alt = html.escape(m.group(1), quote=True)
        return f'<img src="{src}" alt="{alt}">'

    text = _IMG.sub(image, text)

    def link(m: re.Match[str]) -> str:
        href = m.group(2)
        path, hash_, anchor = href.partition("#")
        if path.endswith(".md"):
            href = path[:-3] + ".html" + hash_ + anchor
        return f'<a href="{html.escape(href, quote=True)}">{m.group(1)}</a>'

    text = _LINK.sub(link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def convert(md: str) -> str:
    """Convert one manual chapter's markdown body to HTML."""
    md = _COMMENT.sub("", md)
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):  # fenced code
            lang = stripped[3:].strip()
            i += 1
            body: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>")
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            slug = heading_slug(text)
            out.append(f'<h{level} id="{html.escape(slug, quote=True)}">{inline(text)}</h{level}>')
            i += 1
            continue

        if stripped.startswith("|"):  # pipe table
            head = cells(lines[i])
            i += 2  # header + separator
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            thead = "".join(f"<th>{inline(c)}</th>" for c in head)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(
                '<div class="tablewrap"><table><thead><tr>'
                f"{thead}</tr></thead><tbody>{tbody}</tbody></table></div>"
            )
            continue

        bullet = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if bullet:
            ordered = bool(re.match(r"^\d+\.$", bullet.group(2)))
            tag = "ol" if ordered else "ul"
            items: list[list[str]] = []
            while i < len(lines):
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if m:
                    items.append([m.group(3)])
                    i += 1
                elif lines[i].startswith("  ") and lines[i].strip() and items:
                    items[-1].append(lines[i])  # wrapped continuation of the item
                    i += 1
                else:
                    break
            body = "".join(f"<li>{inline(join_wrapped(it))}</li>" for it in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        # paragraph (or a standalone image / emphasised caption)
        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*(#|\||```|[-*]\s|\d+\.\s)", lines[i]
        ):
            para.append(lines[i])
            i += 1
        text = join_wrapped(para)
        if _IMG.fullmatch(text.strip()):
            # 画面写真は縮んで細部が読めなくなるので、原寸を開けるようにしておく
            m = _IMG.fullmatch(text.strip())
            src = html.escape(m.group(2), quote=True)
            out.append(f'<figure><a href="{src}" title="原寸で開く">{inline(text)}</a></figure>')
        elif text.startswith("*") and text.endswith("*") and not text.startswith("**"):
            out.append(f'<p class="caption">{inline(text[1:-1])}</p>')
        else:
            out.append(f"<p>{inline(text)}</p>")
    return "\n".join(out)


def title_of(md: str) -> str:
    for line in _COMMENT.sub("", md).split("\n"):
        if line.startswith("# "):
            return strip_badges(line[2:])
    return "Asterism マニュアル"


def sidebar(current: str, titles: dict[str, str]) -> str:
    parts = ['<nav class="toc" aria-label="目次">',
             '<a class="toc-home" href="index.html">Asterism マニュアル</a>']
    for group, names in GROUPS:
        parts.append(f"<h2>{html.escape(group)}</h2><ol>")
        for name in names:
            href = name[:-3] + ".html"
            label = titles[name].split(" — ")[0]
            cls = ' class="here"' if name == current else ""
            parts.append(f'<li><a{cls} href="{href}">{html.escape(label)}</a></li>')
        parts.append("</ol>")
    parts.append("</nav>")
    return "".join(parts)


PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Asterism マニュアル</title>
<link rel="stylesheet" href="../style.css">
</head>
<body class="page-{page}">
<div class="wrap">
<div class="cols">
{sidebar}
<main>
{body}
<footer>この文書は <a href="https://github.com/kumagallium/asterism">kumagallium/asterism</a>
の <code>manual/ja/{source}</code> から生成されています。</footer>
</main>
</div>
</div>
</body>
</html>
"""


def build() -> dict[Path, str | bytes]:
    """Render every artifact into memory so --check can diff without writing."""
    files: dict[Path, str | bytes] = {}
    srcs = {p.name: p.read_text(encoding="utf-8") for p in sorted((SRC / "ja").glob("*.md"))}
    missing = set(srcs) - set(ORDER) - {"index.md"}
    if missing:
        raise SystemExit(f"manual/ja に ORDER 未登録の章があります: {sorted(missing)}")
    titles = {name: title_of(text) for name, text in srcs.items()}

    for name, text in srcs.items():
        files[OUT / "ja" / (name[:-3] + ".html")] = PAGE.format(
            page=html.escape(name[:-3], quote=True),  # ページ固有の CSS の足がかり
            title=html.escape(titles[name]),
            sidebar=sidebar(name, titles),
            body=convert(expand_includes(text, SRC / "ja")),
            source=name,
        )

    files[OUT / "style.css"] = (SRC / "site" / "style.css").read_text(encoding="utf-8")
    files[OUT / "index.html"] = (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0; url=ja/index.html">'
        '<title>Asterism マニュアル</title></head>'
        '<body><p><a href="ja/index.html">Asterism マニュアル</a></p></body></html>\n'
    )
    for sub in ("figures", "screenshots"):
        for asset in sorted((SRC / sub).iterdir()):
            if asset.is_file() and not asset.name.startswith("."):
                files[OUT / sub / asset.name] = asset.read_bytes()
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="生成物が最新か検査する（書き込まない）")
    args = ap.parse_args()
    files = build()

    if args.check:
        stale = []
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            if not path.exists():
                stale.append(path.relative_to(REPO))
            elif path.relative_to(OUT).as_posix() in CHANGELOG_DERIVED:
                continue  # CHANGELOG の関数。リリース直後に機械が作り直す（上の注記）
            elif path.read_bytes() != data:
                stale.append(path.relative_to(REPO))
        extra = [
            p.relative_to(REPO)
            for p in OUT.rglob("*")
            if p.is_file() and p not in files
        ]
        if stale or extra:
            print("docs/manual が manual/ と同期していません。")
            for p in stale:
                print(f"  更新が必要: {p}")
            for p in extra:
                print(f"  余分なファイル: {p}")
            print("\n  python scripts/build_manual_site.py を実行してコミットしてください。")
            return 1
        print(f"docs/manual は最新です（{len(files)} ファイル）。")
        return 0

    if OUT.exists():
        shutil.rmtree(OUT)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
    print(f"docs/manual を生成しました（{len(files)} ファイル）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
