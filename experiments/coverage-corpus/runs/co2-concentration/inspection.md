## CSV: co2-concentration.csv

- Total rows: 80
- Path: `../experiments/coverage-corpus/datasets/co2-concentration/source/co2-concentration.csv`

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `Date` | xsd:date | 100% | 80 | `1958-03-01`, `1959-02-01`, `1959-12-01` |
| `CO2` | xsd:double | 100% | 80 | `315.70`, `316.49`, `315.58` |
| `adjusted CO2` | xsd:double | 100% | 80 | `314.44`, `315.86`, `316.35` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (Date) | 80 | 80 | 0 | ✓ |
| (CO2, Date) | 80 | 80 | 0 | ✓ |
| (adjusted CO2, Date) | 80 | 80 | 0 | ✓ |
| (CO2, adjusted CO2, Date) | 80 | 80 | 0 | ✓ |
| (CO2) | 80 | 80 | 0 | ✓ |
| (adjusted CO2, CO2) | 80 | 80 | 0 | ✓ |
| (adjusted CO2) | 80 | 80 | 0 | ✓ |

