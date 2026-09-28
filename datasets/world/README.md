# world — Asterism example dataset (object-cards-ui PR E, 見本データ)

The dataset the **first screen** embeds: "世界の国" (the world's countries),
Gapminder Foundation country statistics via `vega-datasets`. Deliberately
**not** materials-science vocabulary — every other bundled example
(`starrydata` / `materials_project` / `papers`) speaks thermoelectrics or
document structure, which a first-time visitor (student / PI / operator) has
no way to judge at a glance. Population, life expectancy and fertility are a
shape everyone already understands, so the initial screen can show a real,
citable subject page instead of an empty catalog (ADR
`docs/architecture/object-cards-ui.md`).

## What it is

62 countries x 11 years (1955-2005, every 5 years) = **682** year-by-country
records: population, life expectancy, fertility, and each country's region
(Gapminder's own six clusters). Two classes:

- `world:Country` — 62 nodes, keyed by the (slugified) country name alone —
  `rdfs:label` (ja + en — the display-label predicate every read path's
  shared picker, `asterism.subjects.pick_label`, checks first; see 「かんたん
  な見出し」below) / `schema:name` (English) / `world:nameJa` (日本語の国名) /
  `world:region` / `world:regionJa`.
- `world:Observation` — 682 nodes, one per (country, year) row — `rdfs:label`
  (ja, e.g. "日本 2005") / `world:year` / `world:population` /
  `world:lifeExpectancy` (unit `unit:YR`) / `world:fertility` /
  `world:ofCountry` back to its country.

### かんたんな見出し（rdfs:label）

実機所見: 見本の 1 件の見出しが「Japan」になっていた。ラベル解決
(`asterism.subjects.LABEL_PREDICATES`: `rdfs:label` → `schema:name` →
`dcterms:title` → … 、言語は ja → en → 無しの順) が `schema:name "Japan"` を
拾い、`world:nameJa`（このデータセット独自の述語）は共通のラベル選びからは
見えなかったため。`country`（ja/en 各 1 件）と `observation`（ja 1 件、
「{country_ja} {year}」の合成）に `rdfs:label` 自体を足したことで、優先度
最上位の述語に日本語の候補ができ、`pick_label` が「日本」を選ぶ。
`schema:name` / `world:nameJa` は既存のまま変更していない。

## Provenance & license

`source/gapminder.json` is the **unmodified** `gapminder.json` published by
the `vega-datasets` npm package:

- Retrieved from: `https://cdn.jsdelivr.net/npm/vega-datasets@2/data/gapminder.json`
- Retrieved on: 2026-09-24
- Upstream data: Gapminder Foundation (population/life-expectancy/fertility
  time series), as curated into `vega-datasets`' six-cluster country table.
- `vega-datasets`' own `package.json` declares its license as
  **`BSD-3-Clause`** (checked against the published npm registry metadata for
  `vega-datasets@2`; its git repository carries no separate top-level
  `LICENSE` file at the time this dataset was built, so the standard BSD
  3-Clause text is reproduced in `LICENSE.md` per that license's own
  redistribution requirement).

`experiments/coverage-corpus/datasets/gapminder/`'s 80-row file is a
**different, smaller excerpt** used by an unrelated coverage-corpus
experiment — it is not the source of this dataset and was not used to build
it (contract memo §1: "抜粋なので使わない").

## 来歴

`source/activity.csv`（1 行）が `mapping.yaml` の `activity` TriplesMap を通じて
1 件の `prov:Activity`（`wr:activity/ingest-world-v1`）として取り込まれ、
`country`（62 件）と `observation`（682 件）の全インスタンスが
`prov:wasGeneratedBy` でそこへ紐づく（合計 744 件。手書きの来歴は無し —
通常の Mapping IR → RML → Morph-KGC 経路が生成した本物のトリプル）。

- 取得元（`prov:used`）: `https://cdn.jsdelivr.net/npm/vega-datasets@2/data/gapminder.json`
- 取得日時（`prov:endedAtTime`）: 2026-09-24T00:00:00Z（上の「Retrieved on」と同じ日）
- 取り込みの道具（`prov:wasAssociatedWith`）: `asterism build_world_demo.py`
  （`dataset.toml` の `software_agent_iri` = `https://github.com/kumagallium/asterism`）

## Real ingest, not a hand-written fixture

Every artifact below `source/gapminder.json` is produced by
`scripts/build_world_demo.py`, which runs the **same declarative path** every
onboarded Asterism dataset goes through:

1. `source/gapminder.json` -> `source/world.csv` (columns: `country`,
   `country_ja`, `region`, `region_ja`, `year`, `population`,
   `life_expectancy`, `fertility`) — deterministic, sorted by (country, year).
   `country_ja` / `region_ja` come from a fixed lookup table in the script
   (common Japanese names for all 62 countries and the 6 Gapminder regions;
   see the table in the script for exact wording — none were left in English).
   `source/activity.csv` (1 row: `activity_id`, `source_file`, `source_url`,
   `retrieved_at`, `tool`) is written alongside it — the take-in's own
   provenance record (see "来歴" below), fixed values, not derived from the
   fetch.
2. `mapping.yaml` (the reviewed Mapping IR, `asterism_step0.mapping_ir`) is
   compiled **deterministically** to `mapping.rml.ttl`
   (`asterism_step0.rml_compile.compile_mapping_ir`) — the exact compiler the
   api's materialize step uses. No hand-written RML.
3. `mapping.rml.ttl` + `source/world.csv` are run through **Morph-KGC**
   (`asterism.substrate.materialize_to_graph`) to produce the real RDF graph —
   no hand-written Turtle, no invented triples.
4. That graph is packaged as `snapshot.tar`, byte-for-byte in the
   `asterism_api.exchange` snapshot format (`manifest.json` +
   `graphs/canonical.ttl` + a `registry/` directory shaped like a normal
   onboarded dataset — `meta.json`, `model.yaml`, `mie.yaml`, `mapping.yaml`,
   `mapping.rml.ttl`, `query_tools.yaml`, `source/world.csv`) so
   `exchange.import_snapshot` can load it exactly as it would any other
   instance's exported dataset — one version graph,
   `.../graph/canonical/world/v1`.

Re-run it any time the source changes:

```bash
cd api && uv run python ../scripts/build_world_demo.py
```

(Runs from the `api` package's venv because that is the one editable-installed
venv in this repo that already has `asterism-step0`, `asterism-ingest[substrate]`
— for Morph-KGC — and `asterism-api` together; no server or store needs to be
running — `materialize_to_graph` runs Morph-KGC directly into memory, and the
snapshot tar is assembled from that graph without touching Oxigraph. Pass
`--oxigraph-bin path/to/oxigraph` to additionally round-trip the graph through
a real, temporary Oxigraph process before packaging, if you want to double-check
byte-for-byte that a real store round-trips it unchanged — not required for a
normal rebuild.)

## Files

| file | role |
|---|---|
| `dataset.toml` | declared identity (namespace IRIs) |
| `model.yaml` | rdf-config TBox (documentation form; `mapping.yaml`'s `label:` fields are what promote actually projects) |
| `mapping.yaml` | the reviewed Mapping IR — source of truth for `mapping.rml.ttl` |
| `mapping.rml.ttl` | compiled RML (generated — do not hand-edit) |
| `mie.yaml` | dataset description (title/keywords/license + example SPARQL) |
| `query_tools.yaml` | 6 human-vetted, typed SPARQL tools (ADR object-cards-ui.md §O24) |
| `source/gapminder.json` | the unmodified vega-datasets extract |
| `source/world.csv` | the tabular source `mapping.yaml` maps (generated) |
| `source/activity.csv` | 1-row provenance source for the `activity` TriplesMap (generated, see "来歴") |
| `snapshot.tar` | the real, importable ingest result (generated) |
| `LICENSE.md` | BSD-3-Clause (vega-datasets' declared license) |

## Regenerating

```bash
cd api && uv run python ../scripts/build_world_demo.py
```

See `ingest/tests/test_world_demo.py` for the checks this build must pass
(row/country/year counts, `mapping.yaml` readable by
`asterism.mapping_ir_read.read_mapping_ir`, `query_tools.yaml` parses and
lints clean, `snapshot.tar` is a well-formed `asterism-snapshot` archive whose
`graphs/canonical.ttl` carries exactly 62 `world:Country` and 682
`world:Observation` instances, 1 `prov:Activity` and 744 `prov:wasGeneratedBy`
links, that Japan's country node carries both `rdfs:label "日本"@ja` and
`rdfs:label "Japan"@en`, that `asterism.subjects.pick_label` actually chooses
"日本" among them, and that `asterism.prov_graph.prov_graph` actually walks
the edge from the embedded "日本" subject to that activity).
