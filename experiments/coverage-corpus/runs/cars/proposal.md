# Automobile specs schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). All fields map directly; `Year` is already an ISO date, `Origin` is an enum.

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
  rml:logicalSource [ rml:source "cars.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/car/{Name}" ; rr:class sd:Car ] ;
  rr:predicateObjectMap [ rr:predicate sd:name ;
    rr:objectMap [ rml:reference "Name" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:milesPerGallon ;
    rr:objectMap [ rml:reference "Miles_per_Gallon" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:cylinders ;
    rr:objectMap [ rml:reference "Cylinders" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:displacement ;
    rr:objectMap [ rml:reference "Displacement" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:horsepower ;
    rr:objectMap [ rml:reference "Horsepower" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:weightLbs ;
    rr:objectMap [ rml:reference "Weight_in_lbs" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:acceleration ;
    rr:objectMap [ rml:reference "Acceleration" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:modelYear ;
    rr:objectMap [ rml:reference "Year" ; rr:datatype xsd:date ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:origin ;
    rr:objectMap [ rml:reference "Origin" ] ] .
```
