# Asterism のブランド素材

2026-11-10 に採用した、曲線でつながる三つ星の A とロゴタイプ 01。
データを星、理解に基づく関係を星座として表す。記号そのものがオントロジーや
データ参照の正しさを保証するものではない。

| 正本 | 内容 |
|---|---|
| `source/mark.svg` | 採用画像 `04e-symmetric-a.png` の輪郭。三つ星と曲線を一体化 |
| `source/wordmark.svg` | `01-wordmark-comparison.png` の 01 の文字を輪郭化 |
| `colors.json` | 紺色 `#002869` と共通の基調色 |

元画像はロゴ制作作業の `output/asterism-logo/2026-11-10/` に保存した
`linked-stars-refinement/04e-symmetric-a.png` と `wordmark/01-wordmark-comparison.png`。
SVG 正本はこれらの輪郭を Potrace で変換し、表示領域を正規化したもの。
再生成には元画像や Potrace は不要。文字は特定のインストール済みフォントに依存しない。

## 使用

- `mark.svg`: マーク単体。SVG の色は `#002869`。
- `logo.svg`: マークと文字の横組み。縦横比を維持し、余白を潰さない。
- `logo-on-dark.svg`: 暗い背景用。読みやすさを保つ同系色 `#b7ccff`。
- `favicon.svg`: 暗いブラウザタブでも読める白い背景付き。
- UI のロゴは `BrandMark` / `BrandWordmark` を使用。色は `currentColor`。
- PROV のデータ・処理色や成功・警告・エラー色は、ブランド色とは独立して維持する。

## 再生成

リポジトリのルートから実行する。

```sh
python3 scripts/sync_brand.py
python3 scripts/build_manual_site.py
python3 scripts/sync_brand.py --check
python3 scripts/build_manual_site.py --check
```

`sync_brand.py` はこのディレクトリの配布用 SVG、UI のパス定数と favicon、
desktop の SVG アイコンと起動画面のロゴを同期する。
配色トークンの詳細は [UI ガイドライン](../docs/design/ui-guidelines.md) を参照。

OS 用バイナリアイコンの更新は、[desktop の手順](../desktop/README.md#icon)に従う。
マニュアルの HTML とコピーされた画像は生成物なので、正本を更新してから再生成する。
