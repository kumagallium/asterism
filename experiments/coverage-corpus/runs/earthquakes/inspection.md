## JSON: earthquakes.geojson

- Records: 40 (iterator `$.features[*]`)
- Path: `../experiments/coverage-corpus/datasets/earthquakes/source/earthquakes.geojson`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$.features[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `type` | xsd:string | 100% | 1 | `Feature`, `Feature`, `Feature` |
| `properties.mag` | xsd:double | 100% | 34 | `2`, `4.8`, `4.2` |
| `properties.place` | xsd:string | 100% | 38 | `4km W of Castaic, CA`, `17km E of Hualian, Taiwan`, `11km ESE of Kitaibaraki, Japan` |
| `properties.time` | xsd:integer | 100% | 40 | `1517966773840`, `1517945795710`, `1517925429280` |
| `properties.updated` | xsd:integer | 100% | 40 | `1517966996303`, `1517947892040`, `1517955947581` |
| `properties.tz` | xsd:integer | 100% | 7 | `-480`, `480`, `540` |
| `properties.url` | xsd:string | 100% | 40 | `https://earthquake.usgs.gov/earthquakes/`, `https://earthquake.usgs.gov/earthquakes/`, `https://earthquake.usgs.gov/earthquakes/` |
| `properties.detail` | xsd:string | 100% | 40 | `https://earthquake.usgs.gov/earthquakes/`, `https://earthquake.usgs.gov/earthquakes/`, `https://earthquake.usgs.gov/earthquakes/` |
| `properties.felt` | xsd:integer | 15% | 5 | `2`, `1`, `7` |
| `properties.cdi` | xsd:double | 15% | 6 | `2`, `4.9`, `4.8` |
| `properties.mmi` | xsd:string | 0% | 0 | (no values) |
| `properties.alert` | xsd:string | 0% | 0 | (no values) |
| `properties.status` | xsd:string | 100% | 2 | `automatic`, `reviewed`, `reviewed` |
| `properties.tsunami` | xsd:integer | 100% | 1 | `0`, `0`, `0` |
| `properties.sig` | xsd:integer | 100% | 29 | `62`, `354`, `272` |
| `properties.net` | xsd:string | 100% | 8 | `ci`, `us`, `us` |
| `properties.code` | xsd:string | 100% | 40 | `37868143`, `1000chmg`, `1000chrs` |
| `properties.ids` | xsd:string | 100% | 40 | `,ci37868143,`, `,us1000chmg,`, `,us1000chrs,` |
| `properties.sources` | xsd:string | 100% | 9 | `,ci,`, `,us,`, `,us,` |
| `properties.types` | xsd:string | 100% | 7 | `,geoserve,nearby-cities,origin,phase-dat`, `,geoserve,origin,phase-data,`, `,dyfi,geoserve,origin,phase-data,` |
| `properties.nst` | xsd:integer | 80% | 19 | `7`, `7`, `11` |
| `properties.dmin` | xsd:double | 92% | 37 | `0.04214`, `0.276`, `1.118` |
| `properties.rms` | xsd:double | 100% | 27 | `0.35`, `0.67`, `0.97` |
| `properties.gap` | xsd:double | 92% | 35 | `174`, `41`, `156` |
| `properties.magType` | xsd:string | 100% | 4 | `ml`, `mb`, `mb` |
| `properties.type` | xsd:string | 100% | 2 | `earthquake`, `earthquake`, `earthquake` |
| `properties.title` | xsd:string | 100% | 40 | `M 2.0 - 4km W of Castaic, CA`, `M 4.8 - 17km E of Hualian, Taiwan`, `M 4.2 - 11km ESE of Kitaibaraki, Japan` |
| `geometry.type` | xsd:string | 100% | 1 | `Point`, `Point`, `Point` |
| `geometry.coordinates` | json-array | 100% | 40 | `[-118.6671667, 34.4945, 26.49]`, `[121.7751, 23.9506, 7.95]`, `[140.8677, 36.7501, 50.92]` |
| `id` | xsd:string | 100% | 40 | `ci37868143`, `us1000chmg`, `us1000chrs` |

### JSON columns

- `geometry.coordinates` (array of number)

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (properties.place) | 40 | 38 | 2 | ✗ |
| (properties.time, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, properties.updated, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, properties.url, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, properties.detail, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, properties.cdi, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.time, properties.code, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, properties.ids, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.time, properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.url, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.detail, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.cdi, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.updated, properties.code, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.ids, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.updated, properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.updated, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.detail, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.cdi, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.url, properties.code, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.ids, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.url, properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.url, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.cdi, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.detail, properties.code, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.ids, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.detail, properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.detail, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.detail, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.code, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.ids, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.dmin, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.title, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.cdi, geometry.coordinates, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.cdi, id, properties.place) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.ids, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.code, properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.code, properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.code, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.code, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.ids, properties.title, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.ids, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.place) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.place) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.place) | 40 | 40 | 0 | ✓ |
| (properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.time) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.url, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.detail, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.cdi, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.updated, properties.code, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.ids, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, properties.dmin, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.updated, properties.title, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated, id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.detail, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.cdi, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.url, properties.code, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.ids, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.dmin, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.url, properties.title, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.url, id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.cdi, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.detail, properties.code, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.ids, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.dmin, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.detail, properties.title, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.detail, geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.detail, id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.code, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.ids, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.dmin, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.title, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.cdi, geometry.coordinates, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.cdi, id, properties.time) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.ids, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.code, properties.dmin, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.code, properties.title, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.code, geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.code, id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.ids, properties.title, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.ids, id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.time) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.time) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.time) | 40 | 40 | 0 | ✓ |
| (properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.updated) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.updated) | 40 | 40 | 0 | ✓ |
| (id, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.detail, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.cdi, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.url, properties.code, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.ids, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, properties.dmin, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.url, properties.title, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, geometry.coordinates, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url, id, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.cdi, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.detail, properties.code, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.ids, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.dmin, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.detail, properties.title, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.detail, geometry.coordinates, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.detail, id, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.code, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.ids, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.dmin, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.title, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.cdi, geometry.coordinates, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.cdi, id, properties.updated) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.ids, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.code, properties.dmin, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.code, properties.title, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.code, geometry.coordinates, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.code, id, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.ids, properties.title, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.ids, id, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.updated) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.updated) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.updated) | 40 | 40 | 0 | ✓ |
| (properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.url) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.url) | 40 | 40 | 0 | ✓ |
| (id, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.cdi, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.detail, properties.code, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.ids, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail, properties.dmin, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.detail, properties.title, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail, geometry.coordinates, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail, id, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.code, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.ids, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.dmin, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.title, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.cdi, geometry.coordinates, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.cdi, id, properties.url) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.ids, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.code, properties.dmin, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.code, properties.title, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.code, geometry.coordinates, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.code, id, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.ids, properties.title, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.ids, id, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.url) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.url) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.url) | 40 | 40 | 0 | ✓ |
| (properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.detail) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.detail) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.detail) | 40 | 40 | 0 | ✓ |
| (id, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.cdi, properties.code, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.ids, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.dmin, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.cdi, properties.title, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.cdi, geometry.coordinates, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.cdi, id, properties.detail) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.ids, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.code, properties.dmin, properties.detail) | 37 | 37 | 0 | ✓ |
| (properties.code, properties.title, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.code, geometry.coordinates, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.code, id, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.detail) | 37 | 37 | 0 | ✓ |
| (properties.ids, properties.title, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.ids, id, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.detail) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.detail) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.detail) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.detail) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.detail) | 40 | 40 | 0 | ✓ |
| (properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.ids, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.dmin, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.title, properties.cdi) | 6 | 6 | 0 | ✓ |
| (geometry.coordinates, properties.cdi) | 6 | 6 | 0 | ✓ |
| (id, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.ids, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.dmin, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code, properties.title, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code, geometry.coordinates, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code, id, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.ids, properties.title, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.ids, id, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.dmin, properties.title, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.dmin, id, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.title, id, properties.cdi) | 6 | 6 | 0 | ✓ |
| (geometry.coordinates, id, properties.cdi) | 6 | 6 | 0 | ✓ |
| (properties.code) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.code) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.code) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.code) | 40 | 40 | 0 | ✓ |
| (id, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.ids, properties.dmin, properties.code) | 37 | 37 | 0 | ✓ |
| (properties.ids, properties.title, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.ids, geometry.coordinates, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.ids, id, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.code) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.code) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.code) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.code) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.code) | 40 | 40 | 0 | ✓ |
| (properties.ids) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.ids) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.ids) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.ids) | 40 | 40 | 0 | ✓ |
| (id, properties.ids) | 40 | 40 | 0 | ✓ |
| (properties.dmin, properties.title, properties.ids) | 37 | 37 | 0 | ✓ |
| (properties.dmin, geometry.coordinates, properties.ids) | 37 | 37 | 0 | ✓ |
| (properties.dmin, id, properties.ids) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.ids) | 40 | 40 | 0 | ✓ |
| (properties.title, id, properties.ids) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.ids) | 40 | 40 | 0 | ✓ |
| (properties.dmin) | 37 | 37 | 0 | ✓ |
| (properties.title, properties.dmin) | 37 | 37 | 0 | ✓ |
| (geometry.coordinates, properties.dmin) | 37 | 37 | 0 | ✓ |
| (id, properties.dmin) | 37 | 37 | 0 | ✓ |
| (properties.title, geometry.coordinates, properties.dmin) | 37 | 37 | 0 | ✓ |
| (properties.title, id, properties.dmin) | 37 | 37 | 0 | ✓ |
| (geometry.coordinates, id, properties.dmin) | 37 | 37 | 0 | ✓ |
| (properties.title) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, properties.title) | 40 | 40 | 0 | ✓ |
| (id, properties.title) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates, id, properties.title) | 40 | 40 | 0 | ✓ |
| (geometry.coordinates) | 40 | 40 | 0 | ✓ |
| (id, geometry.coordinates) | 40 | 40 | 0 | ✓ |
| (id) | 40 | 40 | 0 | ✓ |

