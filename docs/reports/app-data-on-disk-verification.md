# 検証レポート: デスクトップのチャット・設定を PC のディスクへ

2026-08-18 / 関連: [`docs/architecture/app-data-on-disk.md`](../architecture/app-data-on-disk.md)（決定の正）

## Question

デスクトップ版の Ask に「このブラウザ内に保存」と出ていた。実装を移したあと、

1. 単一ユーザー（`asterism-local`）で、チャットと設定は**本当に PC のディスク**に保存されるか。
2. 共有 api（本番 / docker）の挙動は**一切変わっていない**か。
3. API キーはディスクに**出ないか** — 実データの形（ネストした `models[].apiKey`）でも。
4. 表示は実態と合っているか。

## Method

- **api 単体**: `api/tests/test_appdata.py`（新規 13 件）+ 既存全件。
- **実機一周**: バンドル版 oxigraph（`/Applications/Asterism.app/Contents/Resources/backend/oxigraph`）を
  使って実 `asterism-local` を **検証専用のデータホーム**で起動
  （`--data-dir <scratchpad>/verify-home`・`--port 8799`）。実データディレクトリには触れていない。
  `curl` で API を直叩きしたあと、**実ブラウザ**で同じインスタンスを開いて画面を確認した。

## Result

| 確かめたこと | 結果 |
|---|---|
| `GET /api/appdata/info` | `{"single_user": true, "home": "<verify-home>"}` |
| スレッド PUT → GET | 往復した |
| ディスク上の実体 | `<home>/appdata/ask/<uuid>.json`・パーミッション `0600` |
| 設定 PUT → GET（`models[].apiKey` をネストで混入） | 応答から `apiKey` が**消え**、`apiBase` と `sortKey` は**残った** |
| `settings.json` の実物を grep | 混入させた `sk-LEAK` は **0 件**（ディスクに書かれていない） |
| スレッド DELETE | ファイルごと消えた |
| 実ブラウザ: 履歴一覧 | localStorage を一切使わずに、**ディスクに置いたスレッドが一覧に出た** |
| 実ブラウザ: 設定モデル一覧 | API で入れた設定（`https://x`）が反映された |
| 実ブラウザ: Ask の空状態 | 「…ここに履歴が残ります**（この PC に保存）**」 |
| 実ブラウザ: ストレージタブ | 現在の場所を表示。ブラウザからは変更不可の旨のみ（移行の注意は出さない） |
| 共有 api（env なし） | `info` 以外すべて 404。`local_env()` は `asterism-local` からしか呼ばれず docker 経路は不変 |
| テスト | api 411 passed（回帰なし）・ruff 緑・UI build 緑・UI lint 0 error |

検証中に見つけて直したもの:

- **秘密の除去がトップレベルだけだった** — 実データの形（`{"models": {"models": [{"apiKey": …}]}}`）に
  防御が効いていなかった。再帰化し、マーカーも絞り直した（`sortKey` のような正当な名前を
  巻き添えで消さない）。
- **深さ上限を超えたコンテナが素通しだった** — キーを検査していない以上、落とすように変更。
- **`SettingsContext` が models を初期化時に 1 度しか読まなかった** — 移行後の次回起動で
  一覧が空に見える窓があった。ディスクの値が届いたら差し替える（ユーザーが先に編集して
  いたらそちらを優先）。
- **スレッド数上限のカウントにゴミが混ざった** — uuid4 形式のファイルだけを数えるようにした。

### デスクトップ実機（Tauri）

開発ビルド（`tauri dev`・データホームは検証用に固定）を実際に起動して確認した。

| 確かめたこと | 結果 |
|---|---|
| サイドカー経由で appdata が動くか | `verify-home-desktop/appdata/settings.json` が**書かれた** |
| 中身 | `lang` / `workbenchTier` / `models`（`provider`・`modelId`・`apiBase`）— **API キーは無し** |
| localStorage からの移行（D5） | 8765 を取れた回に**実際に走った**（実在のモデル定義がディスクへ上がった） |
| 「変更…」→ フォルダ選択ダイアログ | **開いた**。選んだパスが `com.kumagallium.asterism/settings.json` の `data_home_override` に保存された |
| 未反映のうちは何も起きないか | 選んだフォルダは空のまま（再起動で反映＝設計どおり） |
| 実アプリ（v0.17.2）への影響 | なし。`data_home_override` を読むコードを含まないため |

実アプリの localStorage（現行オリジン）は `models` / `keys` とも**無傷**であることを
sqlite を読み取り専用で開いて確認した。

### 既定の保存先

`~/Documents/Asterism` を新しい既定にした（D4）。**旧既定にデータがあればそちらを
使い続ける**ので既存ユーザーには何も起きない。ユニットテストで 4 通り
（フレッシュ / 旧既定あり / 旧既定が空 / 副作用なし）を確認した。

## Conclusion

「ブラウザにしか置けない」はプラットフォームの制約ではなく実装だった。単一ユーザーで動いて
いるときは、チャットも設定も **PC のディスク上の、人が開いて読める JSON** になった。共有 api の
挙動は変わっていない。API キーはディスクに出ない。

## Limitations

- **「再起動して新しい場所が実際に使われる」ところまでは未確認**。上書きの保存と、
  未反映のうちは何も起きないことまでを確認した。
- 配布物（`.dmg`）での確認は未了。上記は `tauri dev` の開発ビルド。
- Windows / Linux のデータホーム変更は未確認。
- 移行（localStorage → ディスク）は**実データでの一周が未了**。テストと実装レビューまで。

## Reproduce

```bash
cd api
ASTERISM_OXIGRAPH_BIN=/Applications/Asterism.app/Contents/Resources/backend/oxigraph \
  uv run asterism-local --port 8799 --data-dir /tmp/verify-home --no-browser --no-ask

curl -s http://127.0.0.1:8799/api/appdata/info
curl -s -X PUT http://127.0.0.1:8799/api/appdata/settings -H 'content-type: application/json' \
  -d '{"models":{"models":[{"id":"m1","apiBase":"https://x","sortKey":3,"apiKey":"sk-LEAK"}]}}'
grep -c sk-LEAK /tmp/verify-home/appdata/settings.json   # → 0
```
