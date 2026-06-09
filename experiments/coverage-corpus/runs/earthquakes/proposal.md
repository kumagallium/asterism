# USGS earthquake schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`.

### 2. IRI scheme (abbrev.)

- Event IRI: `sdr:quake/{properties.code}` (`properties.code` is 100% unique).
- `properties.time` / `properties.updated` are epoch **milliseconds** — there is
  no vetted Tier 0 function for epoch→dateTime, so they are emitted as integers.
- `properties.ids` / `sources` / `types` are comma-wrapped multi-value strings —
  no function fits, so they use the `…Raw` fallback (one unmapped column must
  not block the rest of the ingest).

### 9. RML declarative mapping

```turtle
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix rmlf: <http://w3id.org/rml/> .
@prefix fn:   <https://kumagallium.github.io/asterism/fn/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix sd:   <https://kumagallium.github.io/asterism/ontology/> .
@prefix sdr:  <https://kumagallium.github.io/asterism/resource/> .

<#QuakeMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "earthquakes.geojson" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$.features[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/quake/{properties.code}" ;
                  rr:class sd:Earthquake ] ;
  rr:predicateObjectMap [ rr:predicate sd:magnitude ;
    rr:objectMap [ rml:reference "properties.mag" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:place ;
    rr:objectMap [ rml:reference "properties.place" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:originTimeMillis ;
    rr:objectMap [ rml:reference "properties.time" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:updatedMillis ;
    rr:objectMap [ rml:reference "properties.updated" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:detailUrl ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:iri_safe ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.url" ] ] ] ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:detailFeed ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:iri_safe ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.detail" ] ] ] ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:status ;
    rr:objectMap [ rml:reference "properties.status" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:significance ;
    rr:objectMap [ rml:reference "properties.sig" ; rr:datatype xsd:integer ] ] ;
  # fallback: properties.ids not expanded (comma-wrapped multi-value string)
  rr:predicateObjectMap [ rr:predicate sd:idsRaw ;
    rr:objectMap [ rml:reference "properties.ids" ] ] ;
  # fallback: properties.sources not expanded (comma-wrapped multi-value string)
  rr:predicateObjectMap [ rr:predicate sd:sourcesRaw ;
    rr:objectMap [ rml:reference "properties.sources" ] ] ;
  # fallback: properties.types not expanded (comma-wrapped multi-value string)
  rr:predicateObjectMap [ rr:predicate sd:typesRaw ;
    rr:objectMap [ rml:reference "properties.types" ] ] .
```
