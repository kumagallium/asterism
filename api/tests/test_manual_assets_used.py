# ruff: noqa: RUF002, RUF003 (日本語の文に全角の括弧・記号を使う)
"""``manual/`` の画像が、どこからも参照されないまま残っていないかを見る。

例題を差し替えるとき、本文と自作図（SVG）は grep で追えるが、**スクリーンショット
は画像なので grep に映らない**。実際、#567 で入門章をレシピの例に書き換えたとき、
本文と図は直したのに XRD の画面写真 2 枚が残り、目視でしか気づけなかった
（#569 で除去）。

生成サイトは ``manual/screenshots/`` と ``manual/figures/`` を丸ごと配信するので、
参照の切れた画像はそのまま公開容量になり、次に誰かが「使われている」と誤解する
材料にもなる。参照の有無は決定論で分かるので、機械に見張らせる。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANUAL = _REPO_ROOT / "manual"
_REF = re.compile(r"\.\./(screenshots|figures)/([^)\s]+)")


def _referenced() -> set[str]:
    out: set[str] = set()
    for md in sorted((_MANUAL / "ja").glob("*.md")):
        for m in _REF.finditer(md.read_text(encoding="utf-8")):
            out.add(f"{m.group(1)}/{m.group(2)}")
    return out


def test_every_manual_asset_is_referenced() -> None:
    """マニュアルに置いた画像は、どれかの章から参照されていること。"""
    if not _MANUAL.exists():  # マニュアルを持たないチェックアウトでは見ない
        return
    refs = _referenced()
    orphans = []
    for sub in ("screenshots", "figures"):
        d = _MANUAL / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and not p.name.startswith(".") and f"{sub}/{p.name}" not in refs:
                orphans.append(f"{sub}/{p.name} ({p.stat().st_size // 1024} KB)")
    assert not orphans, (
        "manual/ に、どの章からも参照されていない画像があります。\n"
        "  章を書き換えて使わなくなったなら消す、まだ使うなら本文から参照してください:\n"
        + "\n".join(f"    {o}" for o in orphans)
    )
