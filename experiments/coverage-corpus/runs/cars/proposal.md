# Cars (fuel economy) schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Car IRI: `sdr:car/{Weight_in_lbs}`.

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

<#CarMap> a rr:TriplesMap ;
  rml:logicalSource [
    rml:source "cars.json" ;
    rml:referenceFormulation ql:JSONPath ;
    rml:iterator "$[*]"
  ] ;

  rr:subjectMap [
    rr:template "https://kumagallium.github.io/asterism/resource/car/{Weight_in_lbs}" ;
    rr:class sd:Car
  ] ;

  # Name — clean string → direct
  rr:predicateObjectMap [
    rr:predicate sd:name ;
    rr:objectMap [ rml:reference "Name" ; rr:datatype xsd:string ]
  ] ;

  # Miles_per_Gallon — bare number (no $/commas/parens) → direct
  rr:predicateObjectMap [
    rr:predicate sd:milesPerGallon ;
    rr:objectMap [ rml:reference "Miles_per_Gallon" ; rr:datatype xsd:double ]
  ] ;

  # Cylinders — bare integer → direct
  rr:predicateObjectMap [
    rr:predicate sd:cylinders ;
    rr:objectMap [ rml:reference "Cylinders" ; rr:datatype xsd:integer ]
  ] ;

  # Displacement — bare integer → direct
  rr:predicateObjectMap [
    rr:predicate sd:displacement ;
    rr:objectMap [ rml:reference "Displacement" ; rr:datatype xsd:integer ]
  ] ;

  # Horsepower — bare integer → direct
  rr:predicateObjectMap [
    rr:predicate sd:horsepower ;
    rr:objectMap [ rml:reference "Horsepower" ; rr:datatype xsd:integer ]
  ] ;

  # Weight_in_lbs — bare integer → direct (also subject key)
  rr:predicateObjectMap [
    rr:predicate sd:weightInLbs ;
    rr:objectMap [ rml:reference "Weight_in_lbs" ; rr:datatype xsd:integer ]
  ] ;

  # Acceleration — bare number → direct
  rr:predicateObjectMap [
    rr:predicate sd:acceleration ;
    rr:objectMap [ rml:reference "Acceleration" ; rr:datatype xsd:double ]
  ] ;

  # Year — full ISO date string (e.g. "1970-01-01") → fn:date_iso (xsd:date)
  rr:predicateObjectMap [
    rr:predicate sd:year ;
    rr:objectMap [
      rml:datatype xsd:date ;
      rmlf:functionExecution [
        rmlf:function fn:date_iso ;
        rmlf:input [
          rmlf:parameter fn:p_value ;
          rmlf:inputValueMap [ rml:reference "Year" ]
        ]
      ]
    ]
  ] ;

  # Origin — region enum (USA/Europe/Japan), not an ISO country → direct
  rr:predicateObjectMap [
    rr:predicate sd:origin ;
    rr:objectMap [ rml:reference "Origin" ; rr:datatype xsd:string ]
  ] .
```
