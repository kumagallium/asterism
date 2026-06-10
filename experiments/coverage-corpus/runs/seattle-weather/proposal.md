# Seattle weather schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Observation IRI: `sdr:weather/{date}`.

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

<#WeatherObservationMap> a rr:TriplesMap ;
    rml:logicalSource [
        rml:source "seattle-weather.csv" ;
        rml:referenceFormulation ql:CSV
    ] ;

    rr:subjectMap [
        rr:template "https://kumagallium.github.io/asterism/resource/weather/{date}" ;
        rr:class sd:WeatherObservation
    ] ;

    # date — already clean ISO YYYY-MM-DD (2012-01-01); map DIRECT, no fn:date_iso.
    rr:predicateObjectMap [
        rr:predicate sd:date ;
        rr:objectMap [ rml:reference "date" ; rr:datatype xsd:date ]
    ] ;

    # precipitation — clean double (0.0, 8.1); direct.
    rr:predicateObjectMap [
        rr:predicate sd:precipitation ;
        rr:objectMap [ rml:reference "precipitation" ; rr:datatype xsd:double ]
    ] ;

    # temp_max — clean double (12.8, 3.3); direct.
    rr:predicateObjectMap [
        rr:predicate sd:tempMax ;
        rr:objectMap [ rml:reference "temp_max" ; rr:datatype xsd:double ]
    ] ;

    # temp_min — clean double (5.0, 0.0); direct.
    rr:predicateObjectMap [
        rr:predicate sd:tempMin ;
        rr:objectMap [ rml:reference "temp_min" ; rr:datatype xsd:double ]
    ] ;

    # wind — clean double (4.7, 5.6); direct.
    rr:predicateObjectMap [
        rr:predicate sd:wind ;
        rr:objectMap [ rml:reference "wind" ; rr:datatype xsd:double ]
    ] ;

    # weather — clean enum string (drizzle/snow/sun); plain literal, direct.
    rr:predicateObjectMap [
        rr:predicate sd:weather ;
        rr:objectMap [ rml:reference "weather" ]
    ] .
```
