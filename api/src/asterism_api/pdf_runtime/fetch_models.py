"""入れた venv の python で走る: models.json のコミットを取得し、参照名をそのコミットに向ける。

使い方: ``python fetch_models.py <models.json> <HF_HOME>``

docling 2.102.1 は ``snapshot_download(repo_id, revision=<参照名>)`` を
``allow_patterns`` なしで呼ぶ（リポジトリ全体を取る）ので、ここも同じく全体を取る。
コミット指定で取ると ``refs/<参照名>`` が
書かれないため、オフライン（``HF_HUB_OFFLINE=1``）で docling が参照名から引けるよう自分で書く。
標準ライブラリと huggingface_hub だけを使う（asterism は import しない）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(models_json: str, hf_home: str) -> int:
    from huggingface_hub import snapshot_download

    hub = Path(hf_home) / "hub"
    for entry in json.loads(Path(models_json).read_text(encoding="utf-8")):
        repo_id, ref, commit = entry["repo_id"], entry["ref"], entry["commit"]
        print(f"fetch {repo_id} @ {commit}", flush=True)
        snapshot_download(repo_id=repo_id, revision=commit, cache_dir=str(hub))
        repo_dir = hub / ("models--" + repo_id.replace("/", "--"))
        if not (repo_dir / "snapshots" / commit).is_dir():
            print(f"snapshot {commit} missing for {repo_id}", file=sys.stderr)
            return 1
        ref_file = repo_dir / "refs" / ref
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(commit, encoding="utf-8")
        print(f"ref {ref} -> {commit}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
