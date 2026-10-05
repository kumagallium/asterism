# pandoc（同梱物のライセンス表示）

デスクトップ版 Asterism は、Word（.docx）を取り込むために **pandoc** を同梱しています。

| 項目 | 内容 |
|---|---|
| 同梱物 | pandoc 公式リリースの**無改変バイナリ**（`bin/pandoc` のみ） |
| 版 | 3.12 |
| 取得元 | https://github.com/jgm/pandoc/releases/download/3.12/ （`desktop/scripts/bundle-backend.sh` が sha256 を検証して取得） |
| ライセンス | GPL-2.0-or-later（同ディレクトリの `COPYING.md` と `COPYRIGHT`。pandoc リポジトリのタグ `3.12` のものを無改変で収録） |
| 対応するソース | https://github.com/jgm/pandoc/releases/tag/3.12 のソースアーカイブ／https://hackage.haskell.org/package/pandoc-3.12 |

## Asterism 本体との関係

- Asterism 本体（Apache-2.0）は pandoc を**別プログラムとして子プロセスで呼ぶだけ**で、リンクしていません。
- pandoc には手を加えていません。
- アプリ内では `Contents/Resources/backend/licenses/pandoc/` にこの 3 ファイルを収めています。
