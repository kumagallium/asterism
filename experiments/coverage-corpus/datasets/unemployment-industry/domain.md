## Domain context

- **Dataset**: US unemployment by industry, monthly (vega-datasets).
- **Purpose**: model an unemployment time series keyed by (series, year, month).
- **Entities**: a monthly labor Observation for an industry Series.
- **Notable columns**: `series` (industry, controlled vocabulary), `year`, `month`, `count` (unemployed, thousands), `rate` (percent), `date` (ISO dateTime).
- **Synonyms**: series→industry/業種, rate→unemployment rate/失業率.
