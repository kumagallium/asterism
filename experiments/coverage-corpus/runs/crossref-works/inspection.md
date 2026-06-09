## JSON: crossref-works.json

- Records: 40 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/crossref-works/source/crossref-works.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `DOI` | xsd:string | 100% | 40 | `10.1007/978-3-658-17671-6_18-1`, `10.1007/978-3-031-23161-2_300726`, `10.1016/b978-0-08-102696-0.00020-8` |
| `title` | json-array | 100% | 40 | `["Soziale Innovation"]`, `["Loot Crates"]`, `["Sensor data analysis, reduction, and f` |
| `author` | json-array | 82% | 33 | `[{"given": "Jürgen", "family": "Howaldt"`, `[{"given": "D.", "family": "Zonta", "seq`, `[{"given": "Alessandro", "family": "Bale` |
| `published.date-parts` | json-array | 100% | 23 | `[[2018, 11, 3]]`, `[[2024]]`, `[[2022]]` |
| `container-title` | json-array | 100% | 40 | `["Handbuch Innovationsforschung"]`, `["Encyclopedia of Computer Graphics and `, `["Sensor Technologies for Civil Infrastr` |
| `type` | xsd:string | 100% | 2 | `book-chapter`, `book-chapter`, `book-chapter` |
| `is-referenced-by-count` | xsd:integer | 100% | 5 | `2`, `0`, `0` |

### JSON columns

- `title` (array of string)
- `author` (array of object)
- `published.date-parts` (array of mixed)
- `container-title` (array of string)

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (DOI) | 40 | 40 | 0 | ✓ |
| (title, DOI) | 40 | 40 | 0 | ✓ |
| (author, DOI) | 33 | 33 | 0 | ✓ |
| (container-title, DOI) | 40 | 40 | 0 | ✓ |
| (title, author, DOI) | 33 | 33 | 0 | ✓ |
| (title, container-title, DOI) | 40 | 40 | 0 | ✓ |
| (author, container-title, DOI) | 33 | 33 | 0 | ✓ |
| (title) | 40 | 40 | 0 | ✓ |
| (author, title) | 33 | 33 | 0 | ✓ |
| (container-title, title) | 40 | 40 | 0 | ✓ |
| (author, container-title, title) | 33 | 33 | 0 | ✓ |
| (author) | 33 | 33 | 0 | ✓ |
| (container-title, author) | 33 | 33 | 0 | ✓ |
| (container-title) | 40 | 40 | 0 | ✓ |

