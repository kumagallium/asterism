# Natural disasters schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Record IRI: `sdr:disaster/{Entity}-{Year}`.

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

<#DisasterRecord>
    a rr:TriplesMap ;

    rml:logicalSource [
        rml:source "disasters.csv" ;
        rml:referenceFormulation ql:CSV
    ] ;

    rr:subjectMap [
        rr:template "https://kumagallium.github.io/asterism/resource/disaster/{Entity}-{Year}" ;
        rr:class sd:DisasterRecord
    ] ;

    # Entity — clean string (disaster type, 11 distinct e.g. "All natural disasters"); direct.
    rr:predicateObjectMap [
        rr:predicate sd:entity ;
        rr:objectMap [ rml:reference "Entity" ; rr:datatype xsd:string ]
    ] ;

    # Year — clean integer year (1900, 1908, 1914); direct, typed as gYear.
    rr:predicateObjectMap [
        rr:predicate sd:year ;
        rr:objectMap [ rml:reference "Year" ; rr:datatype xsd:gYear ]
    ] ;

    # Deaths — clean integer, no commas/$/parens in samples (1267360, 75033, 289); direct.
    rr:predicateObjectMap [
        rr:predicate sd:deaths ;
        rr:objectMap [ rml:reference "Deaths" ; rr:datatype xsd:integer ]
    ] .
```
