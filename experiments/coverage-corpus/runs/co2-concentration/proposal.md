# CO2 concentration schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Reading IRI: `sdr:co2reading/{Date}`.

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

<#CO2ReadingMap>
    a rr:TriplesMap ;

    rml:logicalSource [
        rml:source "co2-concentration.csv" ;
        rml:referenceFormulation ql:CSV
    ] ;

    rr:subjectMap [
        rr:template "https://kumagallium.github.io/asterism/resource/co2reading/{Date}" ;
        rr:class sd:CO2Reading
    ] ;

    # Date — sample values "1958-03-01", "1959-02-01", "1959-12-01" are already
    # clean ISO YYYY-MM-DD strings, so a direct reference typed xsd:date is
    # faithful (no messy/epoch shape that would require fn:date_iso).
    rr:predicateObjectMap [
        rr:predicate sd:date ;
        rr:objectMap [ rml:reference "Date" ; rr:datatype xsd:date ]
    ] ;

    # CO2 — sample values "315.70", "316.49", "315.58" are clean doubles
    # (no $, commas, or parens), so a direct reference typed xsd:double is
    # faithful (no fn:number_clean warranted).
    rr:predicateObjectMap [
        rr:predicate sd:co2Ppm ;
        rr:objectMap [ rml:reference "CO2" ; rr:datatype xsd:double ]
    ] ;

    # adjusted CO2 — sample values "314.44", "315.86", "316.35" are clean
    # doubles, so a direct reference typed xsd:double is faithful.
    rr:predicateObjectMap [
        rr:predicate sd:adjustedCo2Ppm ;
        rr:objectMap [ rml:reference "adjusted CO2" ; rr:datatype xsd:double ]
    ] .
```
