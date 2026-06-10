## JSON: penguins.json

- Records: 70 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/penguins/source/penguins.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `Species` | xsd:string | 100% | 3 | `Adelie`, `Adelie`, `Adelie` |
| `Island` | xsd:string | 100% | 3 | `Torgersen`, `Torgersen`, `Torgersen` |
| `Beak Length (mm)` | xsd:double | 100% | 58 | `39.1`, `39.3`, `37.8` |
| `Beak Depth (mm)` | xsd:double | 100% | 49 | `18.7`, `20.6`, `17.1` |
| `Flipper Length (mm)` | xsd:integer | 100% | 34 | `181`, `190`, `186` |
| `Body Mass (g)` | xsd:integer | 100% | 49 | `3750`, `3650`, `3300` |
| `Sex` | xsd:string | 99% | 2 | `MALE`, `MALE`, `FEMALE` |

### Uniqueness

(no ID candidate columns detected; supply `fk_hint_columns` if known)

