# Unemployment-by-industry schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). `date` is already ISO dateTime; `rate` is a numeric percent.

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
  rml:logicalSource [ rml:source "unemployment-across-industries.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/unemp/{series}-{year}-{month}" ; rr:class sd:UnemploymentObs ] ;
  rr:predicateObjectMap [ rr:predicate sd:series ;
    rr:objectMap [ rml:reference "series" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:year ;
    rr:objectMap [ rml:reference "year" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:month ;
    rr:objectMap [ rml:reference "month" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:unemployedCount ;
    rr:objectMap [ rml:reference "count" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:unemploymentRate ;
    rr:objectMap [ rml:reference "rate" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:date ;
    rr:objectMap [ rml:reference "date" ; rr:datatype xsd:dateTime ] ] .
```
