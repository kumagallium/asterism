## CSV: disasters.csv

- Total rows: 120
- Path: `../experiments/coverage-corpus/datasets/disasters/source/disasters.csv`

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `Entity` | xsd:string | 100% | 11 | `All natural disasters`, `All natural disasters`, `All natural disasters` |
| `Year` | xsd:integer | 100% | 78 | `1900`, `1908`, `1914` |
| `Deaths` | xsd:integer | 100% | 115 | `1267360`, `75033`, `289` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (Deaths) | 120 | 115 | 5 | ✗ |

