## JSON: gapminder.json

- Records: 80 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/gapminder/source/gapminder.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `year` | xsd:integer | 100% | 11 | `1955`, `2000`, `1985` |
| `country` | xsd:string | 100% | 62 | `Afghanistan`, `Afghanistan`, `Argentina` |
| `cluster` | xsd:integer | 100% | 6 | `0`, `0`, `3` |
| `pop` | xsd:integer | 100% | 80 | `7971931`, `19542982`, `30287112` |
| `life_expect` | xsd:double | 100% | 79 | `43.88`, `54.73`, `71.73` |
| `fertility` | xsd:double | 100% | 75 | `7.42`, `7.53`, `3.1` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (pop) | 80 | 80 | 0 | ✓ |
| (life_expect, pop) | 80 | 80 | 0 | ✓ |
| (life_expect) | 80 | 79 | 1 | ✗ |

