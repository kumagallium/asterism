# Palmer penguins schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Penguin IRI: `sdr:penguin/{<keys>}`.
- No ID column was detected, so the subject is a composite of the most
  discriminating fields (species + island + bill measurements + body mass),
  effectively unique per observation row.

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

<#PenguinMap> a rr:TriplesMap ;
    rml:logicalSource [
        rml:source "penguins.json" ;
        rml:referenceFormulation ql:JSONPath ;
        rml:iterator "$[*]"
    ] ;
    rr:subjectMap [
        rr:template "https://kumagallium.github.io/asterism/resource/penguin/{Species}-{Island}-{Beak Length (mm)}-{Beak Depth (mm)}-{Body Mass (g)}" ;
        rr:class sd:Penguin
    ] ;

    # Species — clean categorical string, direct
    rr:predicateObjectMap [
        rr:predicate sd:species ;
        rr:objectMap [ rml:reference "Species" ; rr:datatype xsd:string ]
    ] ;

    # Island — clean categorical string, direct
    rr:predicateObjectMap [
        rr:predicate sd:island ;
        rr:objectMap [ rml:reference "Island" ; rr:datatype xsd:string ]
    ] ;

    # Beak Length (mm) — clean double, direct
    rr:predicateObjectMap [
        rr:predicate sd:beakLengthMm ;
        rr:objectMap [ rml:reference "Beak Length (mm)" ; rr:datatype xsd:double ]
    ] ;

    # Beak Depth (mm) — clean double, direct
    rr:predicateObjectMap [
        rr:predicate sd:beakDepthMm ;
        rr:objectMap [ rml:reference "Beak Depth (mm)" ; rr:datatype xsd:double ]
    ] ;

    # Flipper Length (mm) — clean integer, direct
    rr:predicateObjectMap [
        rr:predicate sd:flipperLengthMm ;
        rr:objectMap [ rml:reference "Flipper Length (mm)" ; rr:datatype xsd:integer ]
    ] ;

    # Body Mass (g) — clean integer, direct
    rr:predicateObjectMap [
        rr:predicate sd:bodyMassG ;
        rr:objectMap [ rml:reference "Body Mass (g)" ; rr:datatype xsd:integer ]
    ] ;

    # Sex — categorical enum (MALE/FEMALE), NOT a boolean, direct
    rr:predicateObjectMap [
        rr:predicate sd:sex ;
        rr:objectMap [ rml:reference "Sex" ; rr:datatype xsd:string ]
    ] .
```
