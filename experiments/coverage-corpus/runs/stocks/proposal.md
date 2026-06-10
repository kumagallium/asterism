# Stock prices schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Quote IRI: `sdr:quote/{symbol}-{price}`.

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

<#StockQuote> a rr:TriplesMap ;
    rml:logicalSource [
        rml:source "stocks.csv" ;
        rml:referenceFormulation ql:CSV
    ] ;

    rr:subjectMap [
        rr:template "https://kumagallium.github.io/asterism/resource/quote/{symbol}-{price}" ;
        rr:class sd:StockQuote
    ] ;

    # symbol — clean ticker (100% non-null, 5 distinct) → direct reference
    rr:predicateObjectMap [
        rr:predicate sd:symbol ;
        rr:objectMap [
            rml:reference "symbol" ;
            rr:datatype xsd:string
        ]
    ] ;

    # date — messy date string ("Jan 1 2000") → fn:date_iso (xsd:date)
    rr:predicateObjectMap [
        rr:predicate sd:date ;
        rr:objectMap [
            rmlf:functionExecution [
                rmlf:function fn:date_iso ;
                rmlf:input [
                    rmlf:parameter fn:p_value ;
                    rmlf:inputValueMap [ rml:reference "date" ]
                ]
            ] ;
            rr:datatype xsd:date
        ]
    ] ;

    # price — clean double (39.81, 100% non-null) → direct reference
    rr:predicateObjectMap [
        rr:predicate sd:price ;
        rr:objectMap [
            rml:reference "price" ;
            rr:datatype xsd:double
        ]
    ] .
```
