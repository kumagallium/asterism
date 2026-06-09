## JSON: cars.json

- Records: 60 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/cars/source/cars.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `Name` | xsd:string | 100% | 56 | `chevrolet chevelle malibu`, `plymouth fury iii`, `amc rebel sst (sw)` |
| `Miles_per_Gallon` | xsd:double | 98% | 41 | `18`, `14`, `22` |
| `Cylinders` | xsd:integer | 100% | 4 | `8`, `8`, `8` |
| `Displacement` | xsd:integer | 100% | 34 | `307`, `440`, `360` |
| `Horsepower` | xsd:integer | 98% | 37 | `130`, `215`, `175` |
| `Weight_in_lbs` | xsd:integer | 100% | 60 | `3504`, `4312`, `3850` |
| `Acceleration` | xsd:double | 100% | 38 | `12`, `8.5`, `11` |
| `Year` | xsd:date | 100% | 12 | `1970-01-01`, `1970-01-01`, `1970-01-01` |
| `Origin` | xsd:string | 100% | 3 | `USA`, `USA`, `USA` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (Weight_in_lbs) | 60 | 60 | 0 | ✓ |

