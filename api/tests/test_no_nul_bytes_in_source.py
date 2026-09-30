"""追跡しているテキストのソースに、生の NUL 文字（0x00）が入っていないかを見る。

生の NUL が先頭 8000 バイトに入ると git がそのファイルをバイナリとして扱い、
``git diff`` も GitHub の PR も「Binary files differ」になって**差分をレビューできない**
（grep は NUL の位置によらず黙る）。実際に ``ui/src/shapeGraph.ts`` と
``ui/src/SkeletonGate.tsx`` が、鍵の区切りの NUL を文字列へ直に書いていて、前者は PR で
中身が見えなかった（``\\u0000`` と書けば実行時の値は同じで、ファイルはテキストのままになる）。

見た目では分からず（エディタには何も映らないか空白に見える）、eslint も tsc も
通るので、``scripts/check_no_nul_bytes.py`` に機械で見させる。

api のテストに置いた理由は ``test_manual_drift.py`` と同じ — ``.github/workflows/`` を
触る PR には Actions run がスケジュールされない（ADR ``workflow-pr-ci-gating``）ため、
既存の api ジョブに相乗りする。
"""

# ruff: noqa: RUF001 (検査の日本語のメッセージと突き合わせるので、全角の括弧をそのまま書く)
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKER = _REPO_ROOT / "scripts" / "check_no_nul_bytes.py"

_HAS_GIT = shutil.which("git") is not None


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(_CHECKER), "--root", str(root)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_tracked_sources_have_no_raw_nul() -> None:
    """このリポジトリが追跡しているテキストのソースに、生の NUL が無いこと。"""
    # git の作業ツリーでないチェックアウト（sdist 等）では「追跡している」が決まらない
    if not _CHECKER.exists() or not (_REPO_ROOT / ".git").exists() or not _HAS_GIT:
        return
    code, out = _run(_REPO_ROOT)
    assert code == 0, (
        "追跡しているソースに生の NUL 文字（0x00）があります。\n"
        "  python scripts/check_no_nul_bytes.py\n"
        "の指摘を、エスケープ（\\u0000 など）に書き直してください。\n\n" + out
    )


# ── 検知ロジックそのものの退行を見る（実データが「たまたま正しい」だけでは通らないように） ──


def _fixture(tmp_path: Path, files: dict[str, bytes], *, untracked: tuple[str, ...] = ()) -> Path:
    """``files`` を置いた git の作業ツリーを作る。``untracked`` に挙げたものは add しない。"""
    root = Path(tempfile.mkdtemp(prefix="repo", dir=tmp_path))  # 1 テストで何度も作る
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    tracked = [name for name in files if name not in untracked]
    if tracked:
        subprocess.run(["git", "add", "--", *tracked], cwd=root, check=True, capture_output=True)
    return root


_CLEAN = "const key = `${a}\\u0000${b}` // 区切りはエスケープで書く\n".encode()
_RAW = b"const a = 1\nconst key = `${a}\x00${b}`\nconst c = [x, y].join('\x00')\n"


def test_escaped_nul_passes(tmp_path: Path) -> None:
    if not _CHECKER.exists() or not _HAS_GIT:
        return
    code, out = _run(_fixture(tmp_path, {"ui/src/a.ts": _CLEAN, "README.md": b"# x\n"}))
    assert code == 0 and "2 件" in out, out


def test_raw_nul_fails_and_names_the_file_and_lines(tmp_path: Path) -> None:
    if not _CHECKER.exists() or not _HAS_GIT:
        return
    code, out = _run(_fixture(tmp_path, {"ui/src/a.ts": _CLEAN, "ui/src/b.tsx": _RAW}))
    assert code == 1, out
    assert "ui/src/b.tsx: 2 個（2, 3 行目）" in out, out
    assert "ui/src/a.ts" not in out, out
    # git のバイナリ判定（先頭 8000 バイト）より後ろの NUL も見る — SkeletonGate.tsx の形
    late = b"// x\n" * 2000 + b"const k = [a, b].join('\x00')\n"
    code, out = _run(_fixture(tmp_path, {"ui/src/late.tsx": late}))
    assert code == 1 and "ui/src/late.tsx: 1 個（2001 行目）" in out, out
    # 名前だけで決まるファイル（拡張子なし）と、大文字の拡張子も見る
    code, out = _run(_fixture(tmp_path, {"infra/Dockerfile": b"FROM x\x00\n", "A.MD": b"\x00"}))
    assert code == 1 and "infra/Dockerfile: 1 個" in out and "A.MD: 1 個" in out, out


def test_binaries_data_and_untracked_files_are_not_checked(tmp_path: Path) -> None:
    if not _CHECKER.exists() or not _HAS_GIT:
        return
    files = {
        "ui/src/a.ts": _CLEAN,
        "manual/screenshots/a.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        "datasets/x/utf16.csv": "a,b\n1,2\n".encode("utf-16"),
        "ui/node_modules/pkg/index.js": _RAW,
    }
    root = _fixture(tmp_path, files, untracked=("ui/node_modules/pkg/index.js",))
    code, out = _run(root)
    assert code == 0 and "1 件" in out, out


def test_a_tree_git_cannot_list_is_an_error_not_a_pass(tmp_path: Path) -> None:
    """git で一覧できない木を「問題なし」と言わない（黙って通る検査にしない）。"""
    if not _CHECKER.exists() or not _HAS_GIT:
        return
    root = Path(tempfile.mkdtemp(prefix="plain", dir=tmp_path))
    (root / "a.ts").write_bytes(_RAW)
    # tmp が何かの作業ツリーの中にあっても、親をたどらせない
    proc = subprocess.run(
        [sys.executable, str(_CHECKER), "--root", str(root)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent)},
    )
    assert proc.returncode != 0 and "問題なし" not in proc.stdout, proc.stdout + proc.stderr
