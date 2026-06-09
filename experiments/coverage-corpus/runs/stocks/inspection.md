## CSV: stocks.csv

- Total rows: 80
- Path: `../experiments/coverage-corpus/datasets/stocks/source/stocks.csv`

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `symbol` | xsd:string | 100% | 5 | `MSFT`, `MSFT`, `MSFT` |
| `date` | xsd:string | 100% | 54 | `Jan 1 2000`, `Aug 1 2000`, `Mar 1 2001` |
| `price` | xsd:double | 100% | 80 | `39.81`, `28.4`, `22.25` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (price) | 80 | 80 | 0 | ✓ |

