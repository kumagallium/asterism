# Seattle weather schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Dates are already ISO; the weather summary is a controlled vocabulary.

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
  rml:logicalSource [ rml:source "seattle-weather.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/obs/seattle/{date}" ; rr:class sd:Observation ] ;
  rr:predicateObjectMap [ rr:predicate sd:date ;
    rr:objectMap [ rml:reference "date" ; rr:datatype xsd:date ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:precipitation ;
    rr:objectMap [ rml:reference "precipitation" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:tempMax ;
    rr:objectMap [ rml:reference "temp_max" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:tempMin ;
    rr:objectMap [ rml:reference "temp_min" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:wind ;
    rr:objectMap [ rml:reference "wind" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:weather ;
    rr:objectMap [ rml:reference "weather" ] ] .
```
