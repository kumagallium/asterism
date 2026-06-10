# Tier 0 coverage report

**Gate** — corpus `…Raw` rate 36.8% ≥ target 15.0% → ❌ FAIL

- Datasets analysed: 12 (of 12 in corpus)
- Computed columns (function + `…Raw`): 19; of those, raw fallbacks: 7

## Per-dataset

| dataset | object maps | function | raw | direct | computed | `…Raw` rate |
|---|--:|--:|--:|--:|--:|--:|
| airports | 7 | 1 | 0 | 6 | 1 | 0.0% |
| cars | 9 | 1 | 0 | 8 | 1 | 0.0% |
| co2-concentration | 3 | 0 | 0 | 3 | 0 | n/a |
| crossref-works | 7 | 2 | 3 | 2 | 5 | 60.0% |
| disasters | 3 | 0 | 0 | 3 | 0 | n/a |
| earthquakes | 30 | 5 | 4 | 21 | 9 | 44.4% |
| gapminder | 7 | 1 | 0 | 6 | 1 | 0.0% |
| movies | 16 | 1 | 0 | 15 | 1 | 0.0% |
| penguins | 7 | 0 | 0 | 7 | 0 | n/a |
| seattle-weather | 6 | 0 | 0 | 6 | 0 | n/a |
| stocks | 3 | 1 | 0 | 2 | 1 | 0.0% |
| unemployment-industry | 6 | 0 | 0 | 6 | 0 | n/a |

## Per-function usage (which head functions earn their place)

| function | uses |
|---|--:|
| `fn:date_iso` | 3 |
| `fn:lookup` | 2 |
| `fn:datetime_iso` | 2 |
| `fn:url_canonical` | 2 |
| `fn:doi_norm` | 1 |
| `fn:year_only` | 1 |
| `fn:bool_norm` | 1 |

## T9 misses (referenced-but-undefined functions = demand signal)

_(none — every referenced function is in the closed set)_

## Demand by category (heuristic — does NOT feed the gate)

Columns whose values look like they need a transform, and how the proposal actually handled them. `direct`/`unmapped` rows in a category with no covering function are the strongest Track A signals.

| category | function | raw | direct | unmapped |
|---|--:|--:|--:|--:|
| boolean | 1 | 0 | 1 | 0 |
| doi | 1 | 0 | 0 | 0 |
| epoch_millis | 2 | 0 | 0 | 0 |
| messy_date | 2 | 0 | 0 | 0 |
| multivalue_or_json | 1 | 4 | 0 | 0 |
| url | 2 | 0 | 0 | 0 |
| value_with_unit_name | 0 | 0 | 4 | 0 |

