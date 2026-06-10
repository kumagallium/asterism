## JSON: unemployment-across-industries.json

- Records: 80 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/unemployment-industry/source/unemployment-across-industries.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `series` | xsd:string | 100% | 14 | `Government`, `Government`, `Government` |
| `year` | xsd:integer | 100% | 11 | `2000`, `2001`, `2003` |
| `month` | xsd:integer | 100% | 12 | `1`, `11`, `8` |
| `count` | xsd:integer | 100% | 80 | `430`, `420`, `745` |
| `rate` | xsd:double | 100% | 52 | `2.1`, `2.1`, `3.7` |
| `date` | xsd:dateTime | 100% | 80 | `2000-01-01T08:00:00.000Z`, `2001-11-01T08:00:00.000Z`, `2003-08-01T07:00:00.000Z` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (count) | 80 | 80 | 0 | ✓ |
| (date, count) | 80 | 80 | 0 | ✓ |
| (date) | 80 | 80 | 0 | ✓ |

