# Gapminder country-year schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). All indicators are clean numerics; subject is the (country, year) composite.

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
  rml:logicalSource [ rml:source "gapminder.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/cy/{country}-{year}" ; rr:class sd:CountryYear ] ;
  rr:predicateObjectMap [ rr:predicate sd:year ;
    rr:objectMap [ rml:reference "year" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:country ;
    rr:objectMap [ rml:reference "country" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:regionCluster ;
    rr:objectMap [ rml:reference "cluster" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:population ;
    rr:objectMap [ rml:reference "pop" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:lifeExpectancy ;
    rr:objectMap [ rml:reference "life_expect" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:fertility ;
    rr:objectMap [ rml:reference "fertility" ; rr:datatype xsd:double ] ] .
```
