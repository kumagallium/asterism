#!/usr/bin/env python3
"""git で追跡しているテキストのソースに、生の NUL 文字（0x00）が無いかを見る。

git は、先頭 8000 バイトに NUL があるファイルをバイナリとして扱う。``git diff`` も
GitHub の PR も「Binary files differ」になり、**差分をレビューできなくなる**
（``git diff --text`` でしか読めない）。grep は NUL がどこにあっても黙る
（``grep -a`` が要る）。
実例: ``ui/src/shapeGraph.ts`` と ``ui/src/SkeletonGate.tsx`` が、鍵の区切りに生の NUL を
文字列へ直に書いていた。前者は PR で中身が見えず、後者は NUL が 8000 バイトより後ろに
あったので git の判定だけはすり抜けていた（grep には映らない）。どちらも同じ穴なので、
位置によらず 1 つでもあれば落とす。

区切りに NUL を使うこと自体は正しい（名前に空白や記号が入り得るので、名前に現れない
文字を選んでいる）。直すのは**書き方**だけ — ``\\u0000``（Python なら ``\\x00``）の
ようにエスケープで書けば、実行時の値は同じ NUL 1 文字で、ファイルはテキストのままになる。

見る範囲:

* ``git ls-files`` が返すファイルだけ（``node_modules`` やビルド生成物は追跡していない
  ので、除外の一覧を持たずに済む）
* そのうち、拡張子かファイル名がテキストのソースのもの（``TEXT_SUFFIXES`` /
  ``TEXT_NAMES``）。画像・``.docx``・``.wasm`` などのバイナリは NUL を含んで当然なので
  見ない。``.csv`` / ``.log`` は取り込みの入力や実行の記録で、ソースではないので見ない
  （UTF-16 の CSV を試験の入力に置く余地を残す）

新しい種類のテキストを置くようになったら ``TEXT_SUFFIXES`` に足す。

CI では ``api/tests/test_no_nul_bytes_in_source.py`` から回す（api ジョブに置く理由は
``test_manual_drift.py`` と同じ — ``.github/workflows/`` を触る PR には Actions run が
スケジュールされないため、既存のジョブに相乗りする）。

Usage:
    python scripts/check_no_nul_bytes.py            # このスクリプトのあるリポジトリ
    python scripts/check_no_nul_bytes.py --root DIR # 別の git の作業ツリー（テスト用）
"""

# ruff: noqa: RUF001 (日本語のメッセージに全角の括弧・記号を使う)
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

NUL = b"\x00"

TEXT_SUFFIXES = frozenset(
    {
        # コード
        ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".sh", ".css", ".html",
        # 文書
        ".md", ".txt",
        # 設定・宣言
        ".json", ".geojson", ".yaml", ".yml", ".toml", ".ini", ".xml", ".svg", ".plist",
        ".service", ".lock", ".example",
        # RDF・問い合わせ
        ".ttl", ".shex", ".rq", ".sparql",
    }
)  # fmt: skip
TEXT_NAMES = frozenset(
    {
        "Dockerfile", "Caddyfile", "Makefile", "LICENSE", "VERSION",
        ".gitignore", ".gitattributes", ".dockerignore", ".tagpr",
    }
)  # fmt: skip

# 1 ファイルについて行番号を並べる上限（UTF-16 のファイルは全行に NUL がある）。
_MAX_LINES_SHOWN = 10


def is_text_source(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def tracked_files(root: Path) -> list[Path]:
    """``root`` の作業ツリーで git が追跡しているファイル。git で読めなければ落とす
    （読めないまま「問題なし」と言わない）。"""
    try:
        proc = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=False)
    except FileNotFoundError:
        raise SystemExit("git が見つかりません。追跡しているファイルを一覧できません。") from None
    if proc.returncode != 0:
        raise SystemExit(
            f"git ls-files が失敗しました（{root}）:\n{proc.stderr.decode('utf-8', 'replace')}"
        )
    return [root / os.fsdecode(raw) for raw in proc.stdout.split(NUL) if raw]


def nul_lines(data: bytes) -> list[int]:
    """生の NUL を含む行の番号（1 始まり）。"""
    if NUL not in data:
        return []
    return [i for i, line in enumerate(data.split(b"\n"), 1) if NUL in line]


def check(root: Path) -> tuple[list[str], str]:
    errors: list[str] = []
    scanned = 0
    for path in tracked_files(root):
        # 追跡しているが作業ツリーから消えているファイルや、ディレクトリを指すリンクは飛ばす
        if not is_text_source(path) or not path.is_file():
            continue
        scanned += 1
        data = path.read_bytes()
        lines = nul_lines(data)
        if not lines:
            continue
        shown = ", ".join(str(n) for n in lines[:_MAX_LINES_SHOWN])
        if len(lines) > _MAX_LINES_SHOWN:
            shown += f" ほか {len(lines) - _MAX_LINES_SHOWN} 行"
        rel = path.relative_to(root).as_posix()
        errors.append(f"{rel}: {data.count(NUL)} 個（{shown} 行目）")
    return errors, f"check_no_nul_bytes — 追跡しているテキストのソース {scanned} 件"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root", type=Path, default=REPO, help="検査する木の根（既定: このリポジトリ）"
    )
    args = ap.parse_args()
    errors, summary = check(args.root.resolve())
    print(summary)
    if errors:
        print(f"\n生の NUL 文字（0x00）を含むファイルが {len(errors)} 件:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\n生の NUL があると git や grep がファイルをバイナリとして扱い、"
            "差分を読めなくなります。\n"
            "文字列の中の NUL は \\u0000（Python なら \\x00）のようにエスケープで書いて"
            "ください。\n"
            "実行時の値は同じです。エディタは NUL を空白に化けさせることがあるので、"
            "置き換えはバイト単位で:\n"
            "  python3 -c \"import sys; p=sys.argv[1]; b=open(p,'rb').read(); "
            "open(p,'wb').write(b.replace(b'\\x00', b'\\\\u0000'))\" <ファイル>",
            file=sys.stderr,
        )
        return 1
    print("問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
