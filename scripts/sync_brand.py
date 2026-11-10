#!/usr/bin/env python3
"""採用したSVG正本から、アプリとマニュアルのブランド素材を同期する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"
NS = "{http://www.w3.org/2000/svg}"


def artwork(name: str) -> tuple[str, str]:
    root = ET.parse(BRAND / "source" / f"{name}.svg").getroot()
    return root.attrib["viewBox"], " ".join(p.attrib["d"] for p in root.iter(NS + "path"))


def svg(viewbox: str, body: str, color: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" fill="{color}">'
        f'<title>Asterism</title>{body}</svg>\n'
    )


def build() -> dict[Path, str]:
    colors = json.loads((BRAND / "colors.json").read_text())
    mark_box, mark = artwork("mark")
    word_box, word = artwork("wordmark")
    primary = colors["primary"]
    word_width = float(word_box.split()[2])
    # マークの実輪郭と大文字の高さを約1.45:1、間隔を輪郭幅の約0.45倍に揃える。
    logo_box = f"0 0 {1317 + word_width * 0.56:.3f} 1000"
    logo_body = f'<path d="{mark}"/><path transform="translate(1317 220) scale(.56)" d="{word}"/>'
    favicon_body = (
        '<rect width="1000" height="1000" rx="210" fill="#ffffff"/>'
        f'<path d="{mark}"/>'
    )
    files = {
        BRAND / "mark.svg": svg(mark_box, f'<path d="{mark}"/>', primary),
        BRAND / "wordmark.svg": svg(word_box, f'<path d="{word}"/>', primary),
        BRAND / "logo.svg": svg(logo_box, logo_body, primary),
        BRAND / "logo-on-dark.svg": svg(logo_box, logo_body, colors["onDark"]),
        BRAND / "favicon.svg": svg(mark_box, favicon_body, primary),
    }
    files[ROOT / "ui/public/favicon.svg"] = files[BRAND / "favicon.svg"]
    files[ROOT / "ui/src/brand/assets.ts"] = (
        "// scripts/sync_brand.py による生成物。正本は brand/source/。\n"
        f"export const BRAND_MARK_VIEWBOX = {json.dumps(mark_box)}\n"
        f"export const BRAND_MARK_PATH = {json.dumps(mark)}\n"
        f"export const BRAND_WORDMARK_VIEWBOX = {json.dumps(word_box)}\n"
        f"export const BRAND_WORDMARK_PATH = {json.dumps(word)}\n"
    )
    files[ROOT / "desktop/icon.svg"] = svg(
        "0 0 1024 1024",
        '<rect x="101" y="101" width="822" height="822" rx="184" '
        f'fill="#ffffff" stroke="{colors["border"]}" stroke-width="2"/>'
        f'<path transform="translate(152 152) scale(.72)" d="{mark}"/>',
        primary,
    )
    splash_path = ROOT / "desktop/splash/index.html"
    splash = splash_path.read_text()
    inline_logo = (
        f'<svg class="brand-logo" viewBox="{logo_box}" role="img" aria-label="Asterism">'
        f"{logo_body}</svg>"
    )
    start, end = "<!-- brand-logo:start -->", "<!-- brand-logo:end -->"
    replacement = f"{start}\n      {inline_logo}\n      {end}"
    if start not in splash or end not in splash:
        raise ValueError("起動画面にブランド素材の置換位置がありません")
    files[splash_path] = re.sub(
        re.escape(start) + r".*?" + re.escape(end), replacement, splash, flags=re.S
    )
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in build().items():
        if path.exists() and path.read_text() == content:
            continue
        if args.check:
            stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        print("ブランド素材の再生成が必要です: " + ", ".join(stale))
        return 1
    print("ブランド素材は同期しています。" if args.check else "ブランド素材を更新しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
