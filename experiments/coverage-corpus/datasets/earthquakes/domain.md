## Domain context

- **Dataset**: USGS earthquake feed (GeoJSON FeatureCollection, Feb 2018).
- **Purpose**: model each seismic event with magnitude, place, time, and a detail URL.
- **Entities**: an Earthquake Event (a USGS feature).
- **Notable columns**: `properties.mag` (magnitude), `properties.place` (text), `properties.time`/`properties.updated` (epoch milliseconds), `properties.url`/`properties.detail` (USGS URLs), `properties.ids`/`properties.sources`/`properties.types` (comma-wrapped multi-value strings), `properties.status` (automatic/reviewed).
- **Synonyms**: mag→magnitude/マグニチュード, place→epicenter/震源, time→origin time/発震時刻.
