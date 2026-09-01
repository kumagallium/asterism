"""``docs/manual/`` が ``manual/`` から作り直された状態かを見る。

マニュアルは 2 つの顔を持つ: 人間向けヘルプであり、設計相談チャットに注入される
知識でもある（ADR ``design-consult-chat.md`` D8）。GitHub Pages で公開している
``docs/manual/`` は ``scripts/build_manual_site.py`` の生成物なので、markdown だけ
直して生成を忘れると、**公開されている方だけが古いまま**になる。

同じ理由で ``test_design_consult.py`` が UI 名の陳腐化を見ている。こちらは生成物の
陳腐化を見る。検査を api のテストに置いたのは、``.github/workflows/`` を触る PR には
Actions run がスケジュールされない（ADR ``workflow-pr-ci-gating``）ため — 既存の
api ジョブに相乗りすれば、workflow を変えずに CI で回る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILDER = _REPO_ROOT / "scripts" / "build_manual_site.py"


def test_manual_site_is_regenerated() -> None:
    """`python scripts/build_manual_site.py` を実行してコミットし忘れていないか。"""
    if not _BUILDER.exists():  # マニュアルを持たないチェックアウト（sdist 等）では見ない
        return
    proc = subprocess.run(
        [sys.executable, str(_BUILDER), "--check"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "docs/manual/ が manual/ と同期していません。\n"
        "  python scripts/build_manual_site.py\n"
        "を実行して、生成物も一緒にコミットしてください。\n\n" + proc.stdout + proc.stderr
    )
