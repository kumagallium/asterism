"""入れた venv の python で走る: オフラインで、サイドカーと同じ設定のパイプラインを初期化できるか。

使い方: ``HF_HOME=<models> HF_HUB_OFFLINE=1 python check_pipeline.py <sidecar dir>``

サイドカーの ``app._build_converter``（OCR なし・表あり）をそのまま使い、PDF 用パイプライン
（レイアウト・表のモデルを読む）を初期化する。足りないモデルがあればネットワークへ出ずに失敗する。
"""
from __future__ import annotations

import os
import sys


def main(sidecar_dir: str) -> int:
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        print("HF_HUB_OFFLINE=1 is required", file=sys.stderr)
        return 2
    sys.path.insert(0, sidecar_dir)
    import app  # infra/docling-sidecar/app.py（無改修）
    from docling.datamodel.base_models import InputFormat

    converter = app._build_converter()
    converter.initialize_pipeline(InputFormat.PDF)
    print("pipeline ok:", app._converter_version(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
