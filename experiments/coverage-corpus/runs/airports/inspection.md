## CSV: airports.csv

- Total rows: 90
- Path: `../experiments/coverage-corpus/datasets/airports/source/airports.csv`

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `iata` | xsd:string | 100% | 90 | `00M`, `0B1`, `0Q5` |
| `name` | xsd:string | 100% | 89 | `Thigpen`, `Col. Dyke`, `Shelter Cove` |
| `city` | xsd:string | 100% | 89 | `Bay Springs`, `Bethel`, `Shelter Cove` |
| `state` | xsd:string | 100% | 36 | `MS`, `ME`, `CA` |
| `country` | xsd:string | 100% | 1 | `USA`, `USA`, `USA` |
| `latitude` | xsd:double | 100% | 90 | `31.95376472`, `44.42506444`, `40.02764333` |
| `longitude` | xsd:double | 100% | 90 | `-89.23450472`, `-70.80784778`, `-124.0733639` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (iata) | 90 | 90 | 0 | ✓ |
| (name, iata) | 90 | 90 | 0 | ✓ |
| (city, iata) | 90 | 90 | 0 | ✓ |
| (latitude, iata) | 90 | 90 | 0 | ✓ |
| (longitude, iata) | 90 | 90 | 0 | ✓ |
| (name, city, iata) | 90 | 90 | 0 | ✓ |
| (name, latitude, iata) | 90 | 90 | 0 | ✓ |
| (name, longitude, iata) | 90 | 90 | 0 | ✓ |
| (city, latitude, iata) | 90 | 90 | 0 | ✓ |
| (city, longitude, iata) | 90 | 90 | 0 | ✓ |
| (latitude, longitude, iata) | 90 | 90 | 0 | ✓ |
| (name) | 90 | 89 | 1 | ✗ |
| (city, name) | 90 | 89 | 1 | ✗ |
| (latitude, name) | 90 | 90 | 0 | ✓ |
| (longitude, name) | 90 | 90 | 0 | ✓ |
| (city, latitude, name) | 90 | 90 | 0 | ✓ |
| (city, longitude, name) | 90 | 90 | 0 | ✓ |
| (latitude, longitude, name) | 90 | 90 | 0 | ✓ |
| (city) | 90 | 89 | 1 | ✗ |
| (latitude, city) | 90 | 90 | 0 | ✓ |
| (longitude, city) | 90 | 90 | 0 | ✓ |
| (latitude, longitude, city) | 90 | 90 | 0 | ✓ |
| (latitude) | 90 | 90 | 0 | ✓ |
| (longitude, latitude) | 90 | 90 | 0 | ✓ |
| (longitude) | 90 | 90 | 0 | ✓ |

