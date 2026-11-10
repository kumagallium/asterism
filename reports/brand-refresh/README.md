# ブランド反映の確認（2026-11-10）

`feat/asterism-brand-refresh` のローカル検証。マージ・リリース・公開は未実施。

| 表示 | 証跡 |
|---|---|
| UI（新規の隔離データ領域、同梱サンプル） | [app.jpg](app.jpg) |
| 生成済みマニュアル | [manual.jpg](manual.jpg) |
| 起動画面の HTML | [splash.jpg](splash.jpg) |

画像は実ブラウザの 1280×720。UI・マニュアル・起動画面の紺色と新ロゴ、
文字の読みやすさ、横方向の収まりを確認した。ブラウザの警告・エラーログは 0 件。
マニュアルの `home-empty.png` は同じ UI 撮影画像から PNG に変換した。
他の操作例のスクリーンショットは手順を変更していないため既存画像を保持しており、
旧配色・旧マークを含む。全操作例を新たに撮り直したという意味ではない。

## 検査

- `npm run build`（live 設定でも確認）、`npm run lint`、`npm run lint:i18n`: 成功。
- `npm test`: 69 ファイル、1,123 件成功。
- マニュアルビルダ・生成物・素材参照・ドリフトの pytest: 18 件成功。
- `python3 scripts/sync_brand.py --check`: 成功。
- `python3 scripts/build_manual_site.py --check`、`python3 scripts/check_manual.py`: 成功。
- 変更した Python の `ruff check`、`git diff --check`: 成功。
- Tauri CLI で全プラットフォームのアイコンを再生成。PNG・ICNS・ICO の形式を確認。
- 128px の OS アイコンを目視確認。

## 検証範囲

- macOS のインストール済みアプリやユーザーデータは更新していない。
- 起動画面は HTML の実表示を確認。Tauri のパッケージ全体の再ビルドは未実施。
- ブラウザの viewport 指定が反映されなかったため、モバイル寸法での実表示は未確認。
- マニュアルの明色表示を目視確認。暗色用 SVG と CSS は生成・参照検査まで。
- UI ビルドは既存の 500kB 超のチャンク警告、pytest は検証用環境の
  pytest-timeout 未導入による設定警告 2 件がある。失敗はない。
