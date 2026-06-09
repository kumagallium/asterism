## CSV: seattle-weather.csv

- Total rows: 90
- Path: `../experiments/coverage-corpus/datasets/seattle-weather/source/seattle-weather.csv`

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `date` | xsd:date | 100% | 90 | `2012-01-01`, `2012-01-17`, `2012-02-03` |
| `precipitation` | xsd:double | 100% | 30 | `0.0`, `8.1`, `0.0` |
| `temp_max` | xsd:double | 100% | 41 | `12.8`, `3.3`, `14.4` |
| `temp_min` | xsd:double | 100% | 35 | `5.0`, `0.0`, `2.2` |
| `wind` | xsd:double | 100% | 38 | `4.7`, `5.6`, `5.3` |
| `weather` | xsd:string | 100% | 5 | `drizzle`, `snow`, `sun` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (date) | 90 | 90 | 0 | ✓ |

