## Domain context

- **Dataset**: Monthly closing stock prices for a handful of tech tickers.
- **Purpose**: model a price time series keyed by (symbol, month).
- **Entities**: a price Quote for a Company on a date.
- **Notable columns**: `symbol` (ticker), `date` (messy "Jan 1 2000" form), `price` (USD).
- **Synonyms**: symbol→ticker/銘柄, price→株価, quote→気配値.
