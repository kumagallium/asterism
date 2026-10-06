# pdf_runtime — デスクトップ版の「PDF を読み取る部品」

設計は `docs/architecture/desktop-pdf-runtime.md`（ADR）。ここは実装と、入れるものの固定ファイル。

| ファイル | 何か |
|---|---|
| `requirements-macos-arm64.txt` | 入れる Python パッケージの版とハッシュの一覧（macOS arm64・cp311） |
| `requirements.in` | 上の一覧の元（直接の依存だけ） |
| `models.json` | モデルの repo・docling が探す参照名・固定したコミット |
| `wheels/` | PyPI に wheel が無い依存の同梱 wheel（`antlr4_python3_runtime-4.9.3`）。pip に `--find-links` で渡す |
| `fetch_models.py` / `check_pipeline.py` | 入れた venv の python で走る小さなスクリプト（取得／オフラインでの初期化確認） |

## 作り直し方

```
cd api/src/asterism_api/pdf_runtime
uv pip compile --generate-hashes --python-platform aarch64-apple-darwin --python-version 3.11 \
  --no-header -o requirements-macos-arm64.txt requirements.in
```

- `requirements.in` の `docling==` は **`infra/docling-sidecar/requirements.txt` と同じ版**にする
  （ずれると `api/tests/test_pdf_runtime.py` が落ちる）。
- docling の版を上げたら、モデルの取得先（参照名）も変わりうる。実際に入れて `models.json` の
  `ref` と `commit` を確かめ直す（docling が `snapshot_download` で取りに行く参照 → 解決された
  コミット）。
- 別のプラットフォームに対応するときは `requirements-<key>.txt` を足し、`__init__.py` の
  `platform_key()` を広げる。

## 同梱 wheel（antlr4）

- `antlr4-python3-runtime==4.9.3`（rapidocr → omegaconf の依存）は PyPI に **wheel が無く sdist だけ**。
  `--only-binary :all:` では入らないので、ハッシュを確かめた sdist から wheel を作って
  `wheels/` に同梱している（`uv build --wheel`。ライセンスは BSD）。
- `uv pip compile` で一覧を作り直すと、antlr4 のハッシュが sdist のものに戻る。同梱 wheel の
  sha256（`shasum -a 256 wheels/*.whl`）に**差し替える**（テストが一致を検査する）。
- 依存を更新したら、「wheel が無いパッケージが増えていないか」を、実際に入れて確かめる
  （`pip install --require-hashes --only-binary :all: --find-links wheels/ -r ...`）。
