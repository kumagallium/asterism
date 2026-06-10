## JSON: movies.json

- Records: 50 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/movies/source/movies.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `Title` | xsd:string | 100% | 50 | `The Land Girls`, `Sweet Sweetback's Baad Asssss Song`, `The Bridges of Madison County` |
| `US Gross` | xsd:integer | 100% | 49 | `146083`, `15200000`, `71516617` |
| `Worldwide Gross` | xsd:integer | 100% | 49 | `146083`, `15200000`, `175516617` |
| `US DVD Sales` | xsd:integer | 22% | 11 | `3788940`, `970318`, `34715888` |
| `Production Budget` | xsd:integer | 100% | 38 | `8000000`, `150000`, `22000000` |
| `Release Date` | xsd:string | 100% | 49 | `Jun 12 1998`, `Jan 01 1971`, `Jun 02 1995` |
| `MPAA Rating` | xsd:string | 82% | 5 | `R`, `PG-13`, `Not Rated` |
| `Running Time min` | xsd:integer | 46% | 18 | `133`, `103`, `110` |
| `Distributor` | xsd:string | 92% | 19 | `Gramercy`, `Warner Bros.`, `First Run/Icarus` |
| `Source` | xsd:string | 92% | 7 | `Based on Book/Short Story`, `Based on Real Life Events`, `Based on Real Life Events` |
| `Major Genre` | xsd:string | 92% | 10 | `Drama`, `Documentary`, `Drama` |
| `Creative Type` | xsd:string | 92% | 7 | `Historical Fiction`, `Factual`, `Dramatization` |
| `Director` | xsd:string | 58% | 27 | `Clint Eastwood`, `Oliver Stone`, `Norman Jewison` |
| `Rotten Tomatoes Rating` | xsd:integer | 68% | 28 | `90`, `57`, `100` |
| `IMDB Rating` | xsd:double | 90% | 29 | `6.1`, `5.6`, `7.2` |
| `IMDB Votes` | xsd:integer | 90% | 45 | `1071`, `1769`, `21923` |

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (Title) | 50 | 50 | 0 | ✓ |
| (US Gross, Title) | 50 | 50 | 0 | ✓ |
| (Worldwide Gross, Title) | 50 | 50 | 0 | ✓ |
| (US DVD Sales, Title) | 11 | 11 | 0 | ✓ |
| (Release Date, Title) | 50 | 50 | 0 | ✓ |
| (IMDB Votes, Title) | 45 | 45 | 0 | ✓ |
| (US Gross, Worldwide Gross, Title) | 50 | 50 | 0 | ✓ |
| (US Gross, US DVD Sales, Title) | 11 | 11 | 0 | ✓ |
| (US Gross, Release Date, Title) | 50 | 50 | 0 | ✓ |
| (US Gross, IMDB Votes, Title) | 45 | 45 | 0 | ✓ |
| (Worldwide Gross, US DVD Sales, Title) | 11 | 11 | 0 | ✓ |
| (Worldwide Gross, Release Date, Title) | 50 | 50 | 0 | ✓ |
| (Worldwide Gross, IMDB Votes, Title) | 45 | 45 | 0 | ✓ |
| (US DVD Sales, Release Date, Title) | 11 | 11 | 0 | ✓ |
| (US DVD Sales, IMDB Votes, Title) | 10 | 10 | 0 | ✓ |
| (Release Date, IMDB Votes, Title) | 45 | 45 | 0 | ✓ |
| (US Gross) | 50 | 49 | 1 | ✗ |
| (Worldwide Gross, US Gross) | 50 | 49 | 1 | ✗ |
| (US DVD Sales, US Gross) | 11 | 11 | 0 | ✓ |
| (Release Date, US Gross) | 50 | 50 | 0 | ✓ |
| (IMDB Votes, US Gross) | 45 | 45 | 0 | ✓ |
| (Worldwide Gross, US DVD Sales, US Gross) | 11 | 11 | 0 | ✓ |
| (Worldwide Gross, Release Date, US Gross) | 50 | 50 | 0 | ✓ |
| (Worldwide Gross, IMDB Votes, US Gross) | 45 | 45 | 0 | ✓ |
| (US DVD Sales, Release Date, US Gross) | 11 | 11 | 0 | ✓ |
| (US DVD Sales, IMDB Votes, US Gross) | 10 | 10 | 0 | ✓ |
| (Release Date, IMDB Votes, US Gross) | 45 | 45 | 0 | ✓ |
| (Worldwide Gross) | 50 | 49 | 1 | ✗ |
| (US DVD Sales, Worldwide Gross) | 11 | 11 | 0 | ✓ |
| (Release Date, Worldwide Gross) | 50 | 50 | 0 | ✓ |
| (IMDB Votes, Worldwide Gross) | 45 | 45 | 0 | ✓ |
| (US DVD Sales, Release Date, Worldwide Gross) | 11 | 11 | 0 | ✓ |
| (US DVD Sales, IMDB Votes, Worldwide Gross) | 10 | 10 | 0 | ✓ |
| (Release Date, IMDB Votes, Worldwide Gross) | 45 | 45 | 0 | ✓ |
| (US DVD Sales) | 11 | 11 | 0 | ✓ |
| (Release Date, US DVD Sales) | 11 | 11 | 0 | ✓ |
| (IMDB Votes, US DVD Sales) | 10 | 10 | 0 | ✓ |
| (Release Date, IMDB Votes, US DVD Sales) | 10 | 10 | 0 | ✓ |
| (Release Date) | 50 | 49 | 1 | ✗ |
| (IMDB Votes, Release Date) | 45 | 45 | 0 | ✓ |
| (IMDB Votes) | 45 | 45 | 0 | ✓ |

