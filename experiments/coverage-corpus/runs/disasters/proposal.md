# Natural-disaster mortality schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Annual death counts keyed by (disaster type, year).

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
  rml:logicalSource [ rml:source "disasters.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/disaster/{Entity}-{Year}" ; rr:class sd:DisasterFigure ] ;
  rr:predicateObjectMap [ rr:predicate sd:entity ;
    rr:objectMap [ rml:reference "Entity" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:year ;
    rr:objectMap [ rml:reference "Year" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:deaths ;
    rr:objectMap [ rml:reference "Deaths" ; rr:datatype xsd:integer ] ] .
```
