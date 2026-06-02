#!/usr/bin/env bash
# Starrydata -> Materials Project 構造リンクの live 実行 (隔離版)。
#
# 非干渉の方針:
#   - 依存は **この実験フォルダ内の独立 venv (.venv)** に入れる。本体セッションの
#     venv / システム Python を一切触らない。.venv は root .gitignore で無視される。
#   - 生成 TTL は out/ にのみ書く (out/ は .gitignore 済み)。
#   - git 操作は一切しない。
#   - API キーはチャットやファイルに書かず、環境変数 MP_API_KEY から読む。
#
# 使い方:
#   export MP_API_KEY=あなたのキー        # 取得: https://next-gen.materialsproject.org/api
#   bash run_live.sh                       # 既定: 先頭40行, out/sample_mp_links.live.ttl
#   bash run_live.sh <csv> <out.ttl> <limit>   # 任意で上書き
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${MP_API_KEY:-}" ]; then
  echo "[!] MP_API_KEY が未設定です。次を実行してから再試行してください:" >&2
  echo "      export MP_API_KEY=あなたのキー" >&2
  exit 1
fi

CSV="${1:-../../../starrydata_dataset/starrydata_samples.csv}"
OUT="${2:-out/sample_mp_links.live.ttl}"
LIMIT="${3:-40}"

# 独立 venv を用意 (初回のみ作成)
if [ ! -d .venv ]; then
  echo "[*] 独立 venv を作成: $(pwd)/.venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo "[*] 依存をインストール (この venv 内のみ / rdflib だけ・数十秒)"
python -m pip install --upgrade pip
python -m pip install rdflib
# live は標準ライブラリ urllib で MP REST API を叩くので mp-api/pymatgen は不要
python -c "import rdflib; print('[ok] rdflib', rdflib.__version__)"

echo "[*] live 実行: $CSV (先頭 $LIMIT 行) -> $OUT"
python link_mp.py --csv "$CSV" --out "$OUT" --limit "$LIMIT" --mode live

echo "[done] $OUT"
echo "      確認: python -c \"from rdflib import Graph; g=Graph().parse('$OUT'); print(len(g),'triples')\""
