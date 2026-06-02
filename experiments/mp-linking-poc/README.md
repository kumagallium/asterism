# mp-linking-poc — Starrydata × Materials Project 構造リンク

実験サンプル（starrydata）に欠けがちな **structure（結晶構造）** を、Materials Project の
**母相の理想結晶構造**で補完する PoC。PSPP（process–structure–property–performance）で言うと、
starrydata は process / property / performance を持つが structure が無い。そこを埋める。

`docs/ontology/README.md` の Phase 2 ロードマップ
「EMMO や Materials Project などの上位 ontology に subclassOf を張る」の最初の試作にあたる。

## やること（3ステップ）

1. `composition` と `MaterialFamily` から **母相（host）**を正規化（ドープ・非化学量論を剥がす）
2. 母相 → **結晶構造記述子**を解決（demo=内蔵テーブル / live=mp-api + pymatgen）
3. 既存 ABox と**同じ** `sdr:sample/{SID}-{sample_id}` IRI に、構造リンクを**追加的に**出力

## ファイル

| ファイル | 役割 |
|---|---|
| `link_mp.py` | 本体（正規化 → 構造解決 → RDF 出力 / demo・live 両対応） |
| `mp_link_tbox.ttl` | 新規語彙の TBox 拡張案（canonical には未取り込み） |
| `out/sample_mp_links.demo.ttl` | demo 実行で生成した ABox（既存グラフにマージ可能） |

## 実行

```bash
# demo（MP API キー不要 / 確証のある母相のみ・mp-id はプレースホルダ）
python link_mp.py --csv ../../../starrydata_dataset/starrydata_samples.csv \
                  --out out/sample_mp_links.demo.ttl --limit 40 --mode demo

# live（実 mp-id・空間群・プロトタイプを解決）— 隔離ランナー推奨
export MP_API_KEY=...        # 取得: https://next-gen.materialsproject.org/api
bash run_live.sh             # 既定: 先頭40行 -> out/sample_mp_links.live.ttl
```

### 非干渉の前提（本体構築セッションを邪魔しない）

`run_live.sh` は次を守る:

- 依存は **この実験フォルダ内の独立 venv (`.venv`)** に入れる。本体の venv / システム Python を触らない（`.venv` は root `.gitignore` で無視済み）。
- 生成 TTL は `out/` にのみ書く（`out/` はローカル `.gitignore` 済み）。
- **git 操作は一切しない**。API キーはファイルに書かず環境変数から読む。
- live は **標準ライブラリ + rdflib のみ**。mp-api / pymatgen は不要（MP の REST API を直接叩き、空間群・mp-id は `symmetry` フィールドから取得）。重い依存のビルド失敗が起きない。

### うまくいかない時

- **HTTP 403 / Cloudflare error 1010**（`browser_signature_banned`）: urllib 既定の UA が弾かれている。本スクリプトはブラウザ風 User-Agent を付与して回避済み。それでも 1010 が続く場合は TLS 指紋ベースの遮断の可能性 → `requests` か `curl_cffi` に切替える。
- **MP の JSON で 401/403**: API キーが無効/未設定（`echo $MP_API_KEY` で確認）。
- **resolved=0**: 母相式が MP の `formula` と一致していない。母相を還元式（例 `Bi2Te3`）に整える。

## 母相判定（候補生成・テーブル非依存）

`normalize_host` は composition と MaterialFamily から母相式の候補を優先順に作り、希薄元素
（原子分率 < 6%）は点欠陥(ドープ)として分離する。live は候補を順に MP へ問い合わせ、最初に
ヒットした式を採用（MP が母相存在の最終判定者）。確からしさは導出法で決まる:

| confidence | 導出 | 例 |
|---|---|---|
| high   | MaterialFamily がそのまま式 | `Bi2Te3`, `ZnO` |
| medium | 組成が（ほぼ）化学量論の整数式 | `Sb2Te3`, `PbTe` |
| low    | 非化学量論を丸め／固溶を主成分寄せ | `TiSe1.89→TiSe2`, `Hf0.75Zr0.25NiSn→HfNiSn`, `Co4Sb12→CoSb3` |

`Polymer`/`Organic` など式に解けないものは unresolved（試行は PROV で残すので穴は queryable）。

## 実行結果の目安（先頭 40 行）

- **demo**（オフライン・内蔵テーブルのみ）: resolved 31 / unresolved 9
- **live**（MP REST）: 候補生成で `TiSe2`・`ZnFe2O4`・`Sb2Te3` 等も解け、resolved はさらに増える

SPARQL で `sample → 空間群 / プロトタイプ / MP参照 / ドープ数` が引けることを確認済み。

## 設計の要点

- **`owl:sameAs` を使わない**。実サンプル ≠ 純粋計算相。`sd:idealizedFrom` は
  `prov:wasDerivedFrom` のサブプロパティで「母相参照」に留める。
- **ドープ = 理想母相からのズレ = 点欠陥**（`sd:PointDefect`、PODO 整合）。
- **リンク自体を由来づけ**（`sd:StructureMatchActivity`：方法・一致度・MP汎関数・日時）。
  既存の `sd:IngestionActivity` / `sd:DigitizationActivity` と同じ PROV パターン。
- 母相は**個別 mp-id より prototype（構造型）で見る**方がドープ違いに頑健。

## 既知の限界（正直な線引き）

- MP が埋めるのは **理想結晶構造＋電子構造**のみ。熱電の κ_lattice を左右する
  **微視組織（粒界・配向・気孔）は対象外** → 論文の XRD/SEM など別ソースが要る。
- demo の mp-id は **プレースホルダ**（`mp-DEMO-*`）。実 ID は `--mode live` で解決。
- ドープ判定は原子分率の閾値（既定 6%）ヒューリスティック。固溶／ドープ境界は要調整。
- 内蔵母相テーブルは少数（Bi2Te3 / PbTe / ZnO / PbSe / SnSe）。本番は live か拡張で。

## 本番化への道

1. `--mode live` で実 mp-id・空間群を解決（MP REST API・標準ライブラリのみ）。prototype を
   厳密化したい場合だけ pymatgen `SpacegroupAnalyzer` / `AflowPrototypeMatcher` を追加。
2. `mp_link_tbox.ttl` の語彙を canonical `docs/ontology/starrydata.ttl` へ昇格
   （プロジェクト規約に従い ingester + ttl + Mermaid の 3 点セット PR）。
3. 構造の RDF 化は **atomRDF + CMSO** に寄せると、MP 構造から CMSO 準拠グラフを自動生成でき、
   ドープは **PODO**、粒界は **PLDO** で同じグラフに表現できる。上位は **EMMO**。
