# US airports schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). `iata` is the natural key; coordinates map directly as doubles.

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

<#Map> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "airports.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/airport/{iata}" ; rr:class sd:Airport ] ;
  rr:predicateObjectMap [ rr:predicate sd:iataCode ;
    rr:objectMap [ rml:reference "iata" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:name ;
    rr:objectMap [ rml:reference "name" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:city ;
    rr:objectMap [ rml:reference "city" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:state ;
    rr:objectMap [ rml:reference "state" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:country ;
    rr:objectMap [ rml:reference "country" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:latitude ;
    rr:objectMap [ rml:reference "latitude" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:longitude ;
    rr:objectMap [ rml:reference "longitude" ; rr:datatype xsd:double ] ] .
```
