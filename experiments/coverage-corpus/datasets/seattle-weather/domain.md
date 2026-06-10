## Domain context

- **Dataset**: Seattle daily weather observations (2012–2015).
- **Purpose**: model daily meteorological readings so they can be queried by date, precipitation, temperature range, and a categorical weather summary.
- **Entities**: a daily Observation at a (implicit) Seattle station.
- **Notable columns**: `date` (ISO day), `precipitation`/`temp_max`/`temp_min`/`wind` (numeric), `weather` (controlled vocabulary: drizzle/rain/sun/snow/fog).
- **Synonyms**: weather→天気/気象, precipitation→降水量, temperature→気温.
