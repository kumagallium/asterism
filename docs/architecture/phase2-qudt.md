# Phase 2 #2: QUDT 量・単位の正規化

starrydata の curve は、同じ物理量を複数の文字列で書く。実データ (curves.csv
全件) を数えると:

| prop_y 文字列 | 件数 |
|---|---|
| `Seebeck coefficient` | 37,370 |
| `thermopower` | 559 |

両者は**同じ量(ゼーベック係数)**だが、`sd:propertyY` で文字列フィルタすると
取りこぼす。単位も同様で `ohm^(-1)*m^(-1)` と `S*m^(-1)` はどちらも S/m。

Phase 2 #2 は、これらを **QUDT** ([qudt.org](https://qudt.org)) の正準 IRI に
マッピングして、AI が 1 つの安定識別子で横断検索できるようにする。

## 方針:additive(後方互換)

設計プラン §4 / handoff §4.2 は「`sd:propertyY` を文字列 → IRI に置換」する案
だったが、本実装は **既存の文字列述語を残したまま QUDT IRI を追加する** additive
方式を採った。

理由:
- 既存データ・既存クエリ・既に deploy 済みの MIE を壊さない(migration 不要)
- AI は「人間可読ラベル(string)」と「正準 IRI」の両方を同時に得られる
  (表示は string、推論・横断検索は IRI、と使い分けできる)
- QUDT に対応概念が無い量(ZT, Power factor 等)は string だけ残せばよく、
  部分被覆でも net で価値が出る

```
Phase 1 (そのまま維持)          Phase 2 で追加 (マップ時のみ)
─────────────────────          ──────────────────────────────
sd:propertyY  "Seebeck coefficient"   sd:propertyYQuantity  qk:SeebeckCoefficient
sd:propertyX  "Temperature"           sd:propertyXQuantity  qk:Temperature
sd:unitYString "V*K^(-1)"             sd:unitY              unit:V-PER-K
sd:unitXString "K"                    sd:unitX              unit:K
```

prefixes:
- `qk:`   = `http://qudt.org/vocab/quantitykind/`
- `unit:` = `http://qudt.org/vocab/unit/`

## キュレーション・マップ

`ingest/src/asterism/qudt_map.yaml` が単一の真実源。材料屋が PR で拡張できるよう
YAML にした。`ingest/src/asterism/qudt.py` が `importlib.resources` で読み、
`quantity_kind_iri()` / `unit_iri()` を提供する。

ルックアップ規則:
- **量種別 (quantity_kinds)**: 大小無視 + strip(英語の量名は大小に意味が無い)
- **単位 (units)**: case-sensitive + strip(`V`≠`v`, `K`≠`k`, `S`≠`s`, `T`≠`t`)

収録した量種別(2026-05-29 時点、全 IRI が qudt.org で HTTP 200 を確認):
`SeebeckCoefficient`(+ synonym `thermopower`), `ThermalConductivity`(+ lattice /
electronic), `ElectricConductivity`, `Resistivity`, `Voltage`, `Mobility`
(carrier / hall), `Magnetization`, `Temperature`。

収録した単位: `K`, `PER-K`, `V-PER-K`, `W-PER-M-K`, `OHM-M`, `S-PER-M`
(ohm⁻¹m⁻¹ と S/m の両表記), `V`, `T`, `A-PER-M`, `PER-M3`, `UNITLESS`。

意図的に未収録:
- `MagneticSusceptibility`(QUDT に該当 quantitykind が無い → 404)
- `ZT` / `Power factor`(QUDT に正準概念無し)
- `magnetization_per_weight` / `_per_volume`(emu/g 等は質量正規化磁化で、QUDT の
  体積磁化 `Magnetization` (A/m) とは次元が違うので誤マップを避けた)
- mobility の単位 `m^2*V^(-1)*s^(-1)`(QUDT の該当 unit IRI を確証できず未収録。
  量種別 `Mobility` は付くが単位 IRI は付かない)

## AI から見た使い方(MIE の sparql_query_examples 参照)

```sparql
# Seebeck を表記ゆれ込みで全部拾う(thermopower も含む)
PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
PREFIX qk: <http://qudt.org/vocab/quantitykind/>
SELECT ?curve ?labelY ?yMax WHERE {
  ?curve sd:propertyYQuantity qk:SeebeckCoefficient ;
         sd:propertyY ?labelY ;
         sd:yMax ?yMax .
} ORDER BY DESC(ABS(?yMax)) LIMIT 20
```

`?labelY` を一緒に取れば、元の文字列(`Seebeck coefficient` か `thermopower` か)
も確認できる。

## 触ったファイル

| ファイル | 変更 |
|---|---|
| `ingest/src/asterism/qudt_map.yaml` | 新規:キュレーション・マップ |
| `ingest/src/asterism/qudt.py` | 新規:ローダ + lookup |
| `ingest/src/asterism/starrydata.py` | `_emit_curve` に QUDT IRI の additive emit |
| `ingest/pyproject.toml` | `pyyaml` 依存追加 + wheel に yaml を artifacts 同梱 |
| `data/togomcp/mie/starrydata.yaml` | CurveShape / sample / sparql 例 / cross_references |
| `docs/ontology/starrydata.ttl` | 4 つの owl:ObjectProperty 定義 + qudt prefix |

> NOTE: `docs/starrydata/ontology/ontology.ttl`(GitHub Pages 用コピー)は本 PR
> では更新していない。Pages を最新にするときは canonical TTL から再 sync が必要。

## 検証

- ユニット: `quantity_kind_iri('thermopower') == quantity_kind_iri('Seebeck coefficient')`
- emitter: curve 6-79-113 が `sd:propertyYQuantity qk:SeebeckCoefficient` と
  `sd:unitY unit:V-PER-K` を持ち、string 述語も保持
- 部分マップ: `ohm*cm`(未収録)は unitY IRI を出さず string だけ残る
- E2E: docker compose で curve を upload → SPARQL で `qk:SeebeckCoefficient`
  横断検索が string ゆれを跨いでヒット

## 次の発展余地

- マップ拡充(battery 系 `Discharge capacity`、Power factor の QUDT 化検討)
- `template_curve_fetch`(Phase 2 #3)の戻り値に QUDT IRI を含める
- 単位の数値変換(QUDT の conversionMultiplier を使った正規化)は Phase 3+
