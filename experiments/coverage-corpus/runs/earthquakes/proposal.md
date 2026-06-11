# USGS earthquakes schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Quake IRI: `sdr:quake/{id}` (id is unique).

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
  rml:logicalSource [
    rml:source "earthquakes.geojson" ;
    rml:referenceFormulation ql:JSONPath ;
    rml:iterator "$.features[*]"
  ] ;

  rr:subjectMap [
    rr:template "https://kumagallium.github.io/asterism/resource/quake/{id}" ;
    rr:class sd:Earthquake
  ] ;

  # id — unique clean string identifier (also kept as a literal)
  rr:predicateObjectMap [ rr:predicate sd:id ;
    rr:objectMap [ rml:reference "id" ] ] ;

  # type — GeoJSON object type ("Feature"), clean string
  rr:predicateObjectMap [ rr:predicate sd:featureType ;
    rr:objectMap [ rml:reference "type" ] ] ;

  # properties.mag — clean double
  rr:predicateObjectMap [ rr:predicate sd:mag ;
    rr:objectMap [ rml:reference "properties.mag" ; rr:datatype xsd:double ] ] ;

  # properties.place — clean string
  rr:predicateObjectMap [ rr:predicate sd:place ;
    rr:objectMap [ rml:reference "properties.place" ] ] ;

  # properties.time — 13-digit epoch milliseconds → dateTime
  rr:predicateObjectMap [ rr:predicate sd:time ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:datetime_iso ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.time" ] ] ] ;
      rr:datatype xsd:dateTime ] ] ;

  # properties.updated — 13-digit epoch milliseconds → dateTime
  rr:predicateObjectMap [ rr:predicate sd:updated ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:datetime_iso ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.updated" ] ] ] ;
      rr:datatype xsd:dateTime ] ] ;

  # properties.tz — clean integer (timezone offset, may be negative)
  rr:predicateObjectMap [ rr:predicate sd:tz ;
    rr:objectMap [ rml:reference "properties.tz" ; rr:datatype xsd:integer ] ] ;

  # properties.url — USGS URL → string-normalized literal
  rr:predicateObjectMap [ rr:predicate sd:url ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:url_canonical ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.url" ] ] ] ] ] ;

  # properties.detail — USGS URL → string-normalized literal
  rr:predicateObjectMap [ rr:predicate sd:detail ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:url_canonical ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.detail" ] ] ] ] ] ;

  # properties.felt — clean integer
  rr:predicateObjectMap [ rr:predicate sd:felt ;
    rr:objectMap [ rml:reference "properties.felt" ; rr:datatype xsd:integer ] ] ;

  # properties.cdi — clean double
  rr:predicateObjectMap [ rr:predicate sd:cdi ;
    rr:objectMap [ rml:reference "properties.cdi" ; rr:datatype xsd:double ] ] ;

  # properties.mmi — empty column (0% non-null); clean string when present
  rr:predicateObjectMap [ rr:predicate sd:mmi ;
    rr:objectMap [ rml:reference "properties.mmi" ] ] ;

  # properties.alert — empty column (0% non-null); clean string when present
  rr:predicateObjectMap [ rr:predicate sd:alert ;
    rr:objectMap [ rml:reference "properties.alert" ] ] ;

  # properties.status — clean string
  rr:predicateObjectMap [ rr:predicate sd:status ;
    rr:objectMap [ rml:reference "properties.status" ] ] ;

  # properties.tsunami — 0/1 boolean flag → boolean
  rr:predicateObjectMap [ rr:predicate sd:tsunami ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:bool_norm ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.tsunami" ] ] ] ;
      rr:datatype xsd:boolean ] ] ;

  # properties.sig — clean integer
  rr:predicateObjectMap [ rr:predicate sd:sig ;
    rr:objectMap [ rml:reference "properties.sig" ; rr:datatype xsd:integer ] ] ;

  # properties.net — clean string (network code)
  rr:predicateObjectMap [ rr:predicate sd:net ;
    rr:objectMap [ rml:reference "properties.net" ] ] ;

  # properties.code — clean string identifier
  rr:predicateObjectMap [ rr:predicate sd:code ;
    rr:objectMap [ rml:reference "properties.code" ] ] ;

  # properties.nst — clean integer
  rr:predicateObjectMap [ rr:predicate sd:nst ;
    rr:objectMap [ rml:reference "properties.nst" ; rr:datatype xsd:integer ] ] ;

  # properties.dmin — clean double
  rr:predicateObjectMap [ rr:predicate sd:dmin ;
    rr:objectMap [ rml:reference "properties.dmin" ; rr:datatype xsd:double ] ] ;

  # properties.rms — clean double
  rr:predicateObjectMap [ rr:predicate sd:rms ;
    rr:objectMap [ rml:reference "properties.rms" ; rr:datatype xsd:double ] ] ;

  # properties.gap — clean double
  rr:predicateObjectMap [ rr:predicate sd:gap ;
    rr:objectMap [ rml:reference "properties.gap" ; rr:datatype xsd:double ] ] ;

  # properties.magType — clean string
  rr:predicateObjectMap [ rr:predicate sd:magType ;
    rr:objectMap [ rml:reference "properties.magType" ] ] ;

  # properties.type — clean string ("earthquake")
  rr:predicateObjectMap [ rr:predicate sd:eventType ;
    rr:objectMap [ rml:reference "properties.type" ] ] ;

  # properties.title — clean string
  rr:predicateObjectMap [ rr:predicate sd:title ;
    rr:objectMap [ rml:reference "properties.title" ] ] ;

  # geometry.type — clean string ("Point")
  rr:predicateObjectMap [ rr:predicate sd:geometryType ;
    rr:objectMap [ rml:reference "geometry.type" ] ] ;

  # properties.ids — comma-wrapped flat list (,ci37868143,) → fn:split explodes to one sd:eventId per id
  rr:predicateObjectMap [ rr:predicate sd:eventId ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:split ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.ids" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_delimiter ; rmlf:inputValueMap [ rmlf:constant "," ] ] ] ] ] ;

  # properties.sources — comma-wrapped flat list (,ci,) → fn:split
  rr:predicateObjectMap [ rr:predicate sd:source ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:split ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.sources" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_delimiter ; rmlf:inputValueMap [ rmlf:constant "," ] ] ] ] ] ;

  # properties.types — comma-wrapped flat list (,geoserve,origin,) → fn:split
  rr:predicateObjectMap [ rr:predicate sd:eventType ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:split ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "properties.types" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_delimiter ; rmlf:inputValueMap [ rmlf:constant "," ] ] ] ] ] ;

  # geometry.coordinates — JSON array [lon, lat, depth] → fn:array_at by fixed index
  rr:predicateObjectMap [ rr:predicate sd:longitude ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:array_at ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "geometry.coordinates" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_index ; rmlf:inputValueMap [ rmlf:constant "0" ] ] ] ;
      rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:latitude ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:array_at ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "geometry.coordinates" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_index ; rmlf:inputValueMap [ rmlf:constant "1" ] ] ] ;
      rr:datatype xsd:double ] ] .
```
