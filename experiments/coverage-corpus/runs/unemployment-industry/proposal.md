# Unemployment by industry schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Record IRI: `sdr:unemp/{series}-{year}-{month}`.
- `series` carries human-readable industry labels (some with spaces, e.g.
  `Wholesale and Retail Trade`), so the subject template slugs it for IRI safety
  while the `sd:series` predicate keeps the verbatim label.

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

<#UnemploymentRecordMap>
    a rr:TriplesMap ;

    rml:logicalSource [
        rml:source "../experiments/coverage-corpus/datasets/unemployment-industry/source/unemployment-across-industries.json" ;
        rml:referenceFormulation ql:JSONPath ;
        rml:iterator "$[*]"
    ] ;

    rr:subjectMap [
        rr:template "https://kumagallium.github.io/asterism/resource/unemp/{series}-{year}-{month}" ;
        rr:class sd:UnemploymentRecord
    ] ;

    # series — clean industry label (e.g. "Government"); direct reference
    rr:predicateObjectMap [
        rr:predicate sd:series ;
        rr:objectMap [ rml:reference "series" ; rr:datatype xsd:string ]
    ] ;

    # year — clean integer (2000, 2001, ...); direct reference
    rr:predicateObjectMap [
        rr:predicate sd:year ;
        rr:objectMap [ rml:reference "year" ; rr:datatype xsd:integer ]
    ] ;

    # month — clean integer (1..12); direct reference
    rr:predicateObjectMap [
        rr:predicate sd:month ;
        rr:objectMap [ rml:reference "month" ; rr:datatype xsd:integer ]
    ] ;

    # count — clean integer headcount (430, 745, ...); direct reference
    rr:predicateObjectMap [
        rr:predicate sd:count ;
        rr:objectMap [ rml:reference "count" ; rr:datatype xsd:integer ]
    ] ;

    # rate — clean double (2.1, 3.7, ...); direct reference, no '%' present
    rr:predicateObjectMap [
        rr:predicate sd:rate ;
        rr:objectMap [ rml:reference "rate" ; rr:datatype xsd:double ]
    ] ;

    # date — already valid ISO 8601 (2000-01-01T08:00:00.000Z); direct reference, no transform
    rr:predicateObjectMap [
        rr:predicate sd:date ;
        rr:objectMap [ rml:reference "date" ; rr:datatype xsd:dateTime ]
    ] .
```
