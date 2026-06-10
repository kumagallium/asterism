# Mauna Loa CO2 schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). `Date` is already ISO; concentrations are ppm (unit implicit in the property).

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
  rml:logicalSource [ rml:source "co2-concentration.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/co2/{Date}" ; rr:class sd:Co2Measurement ] ;
  rr:predicateObjectMap [ rr:predicate sd:date ;
    rr:objectMap [ rml:reference "Date" ; rr:datatype xsd:date ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:co2Ppm ;
    rr:objectMap [ rml:reference "CO2" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:co2AdjustedPpm ;
    rr:objectMap [ rml:reference "adjusted CO2" ; rr:datatype xsd:double ] ] .
```
