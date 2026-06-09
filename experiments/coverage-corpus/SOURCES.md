# Coverage corpus — sources & licences

Every dataset here is a **small, evenly-spaced slice** of a public dataset,
fetched and downsampled by [`fetch_corpus.py`](fetch_corpus.py). Slices keep
only enough rows to expose each column's *shape* (types, sample values,
multi-valued / JSON cells); they are not analytical extracts. Re-fetch with
`python3 fetch_corpus.py` to audit provenance.

## vega-datasets (BSD-3-Clause)

Datasets 1–11 are redistributed via [`vega/vega-datasets`](https://github.com/vega/vega-datasets)
(`data/` on the `main` branch), which is licensed **BSD-3-Clause**. The original
upstream sources are noted for attribution; consult vega-datasets' own
`SOURCES.md` for full provenance.

| id | vega file | domain | original upstream (attribution) |
|---|---|---|---|
| `seattle-weather` | `seattle-weather.csv` | meteorology | NOAA daily summaries (Seattle) |
| `cars` | `cars.json` | automotive | UCI "auto-mpg" dataset |
| `movies` | `movies.json` | film / box office | vega-datasets community compilation |
| `stocks` | `stocks.csv` | equities | historical monthly closing prices |
| `gapminder` | `gapminder.json` | global development | Gapminder (CC-BY) |
| `penguins` | `penguins.json` | ecology | palmerpenguins (Horst et al., CC0) |
| `disasters` | `disasters.csv` | natural disasters | Our World in Data / EM-DAT |
| `unemployment-industry` | `unemployment-across-industries.json` | labor economics | US Bureau of Labor Statistics |
| `co2-concentration` | `co2-concentration.csv` | climate | Scripps / NOAA Mauna Loa CO₂ |
| `airports` | `airports.csv` | aviation / geography | OpenFlights / ourairports |
| `earthquakes` | `earthquakes.json` (saved as `.geojson`) | seismology | USGS earthquake feed (public domain) |

## Crossref (open metadata)

| id | source | domain | licence |
|---|---|---|---|
| `crossref-works` | [Crossref REST API](https://api.crossref.org/works) `?select=DOI,title,author,published,…` | scholarly bibliography | Crossref metadata is openly reusable (factual bibliographic metadata; Crossref applies no additional rights). |

The Crossref slice is whatever the public `/works` endpoint returned at fetch
time (no query terms), so it is a neutral sample of recent registered works.

## Why these

The set is deliberately **non-materials-heavy and cross-domain** (weather,
cars, film, finance, demography, ecology, disasters, labor, climate, aviation,
seismology, bibliography) so the coverage numbers reflect *arbitrary* onboarding
rather than the starrydata shape the Tier 0 library grew up around. Columns that
need real computation are well represented: messy dates (`movies`, `stocks`),
epoch-millis timestamps (`earthquakes`), DOIs and multi-valued author arrays
(`crossref-works`), comma-wrapped multi-value strings (`earthquakes`), units in
column names (`penguins`, `co2-concentration`), and booleans/enums (`penguins`,
`seattle-weather`).
