## JSON: openlibrary-books.json

- Records: 40 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/openlibrary-books/source/openlibrary-books.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `title` | xsd:string | 100% | 38 | `Chemistry`, `The science of getting rich, or, financi`, `Chaos` |
| `author_name` | json-array | 100% | 40 | `["Theodore L. Brown", "H. Eugene Lemay",`, `["Wallace D. Wattles", "Ruth L Miller", `, `["James Gleick"]` |
| `first_publish_year` | xsd:integer | 100% | 31 | `1977`, `1910`, `1987` |
| `isbn` | json-array | 100% | 40 | `["0138281874", "9780555054130", "1110131`, `["1101043288", "1516916816", "9780914295`, `["9781453221044", "9780759581166", "9781` |
| `language` | json-array | 100% | 11 | `["por", "spa", "ger", "eng"]`, `["eng", "chi", "spa"]`, `["tur", "por", "fre", "eng", "fin"]` |
| `subject` | json-array | 100% | 40 | `["Textbooks", "Science textbooks", "Chim`, `["Success", "Wealth", "New Thought", "Ri`, `["Chaotic behavior in systems", "Science` |
| `number_of_pages_median` | xsd:integer | 92% | 35 | `1117`, `102`, `364` |

### JSON columns

- `author_name` (array of string)
- `isbn` (array of string)
- `language` (array of string)
- `subject` (array of string)

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (title) | 40 | 38 | 2 | ✗ |
| (author_name, title) | 40 | 40 | 0 | ✓ |
| (isbn, title) | 40 | 40 | 0 | ✓ |
| (subject, title) | 40 | 40 | 0 | ✓ |
| (author_name, isbn, title) | 40 | 40 | 0 | ✓ |
| (author_name, subject, title) | 40 | 40 | 0 | ✓ |
| (isbn, subject, title) | 40 | 40 | 0 | ✓ |
| (author_name) | 40 | 40 | 0 | ✓ |
| (isbn, author_name) | 40 | 40 | 0 | ✓ |
| (subject, author_name) | 40 | 40 | 0 | ✓ |
| (isbn, subject, author_name) | 40 | 40 | 0 | ✓ |
| (isbn) | 40 | 40 | 0 | ✓ |
| (subject, isbn) | 40 | 40 | 0 | ✓ |
| (subject) | 40 | 40 | 0 | ✓ |

