# Gapminder development schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Observation IRI: `sdr:obs/{pop}`.
- `pop` is the chosen subject key: the uniqueness report shows (pop) is unique
  across all 80 rows (80 distinct, 0 collisions), so it is a faithful single-column
  primary key — no composite/slug needed.

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

<#GapminderObservation> a rr:TriplesMap ;
  rml:logicalSource [
    rml:source "../experiments/coverage-corpus/datasets/gapminder/source/gapminder.json" ;
    rml:referenceFormulation ql:JSONPath ;
    rml:iterator "$[*]"
  ] ;

  rr:subjectMap [
    rr:template "https://kumagallium.github.io/asterism/resource/obs/{pop}" ;
    rr:class sd:Observation
  ] ;

  # year — clean xsd:integer (1955, 2000, 1985); direct reference, NOT year_only.
  rr:predicateObjectMap [
    rr:predicate sd:year ;
    rr:objectMap [ rml:reference "year" ; rr:datatype xsd:integer ]
  ] ;

  # country — clean country NAME (Afghanistan, Argentina); fn:lookup country_iso3166.
  rr:predicateObjectMap [
    rr:predicate sd:country ;
    rr:objectMap [
      rmlf:functionExecution [
        rmlf:function fn:lookup ;
        rmlf:input [
          rmlf:parameter fn:p_value ;
          rmlf:inputValueMap [ rml:reference "country" ]
        ] ;
        rmlf:input [
          rmlf:parameter fn:p_table ;
          rmlf:inputValueMap [ rmlf:constant "country_iso3166" ]
        ]
      ]
    ]
  ] ;

  # countryName — keep the raw country name verbatim for provenance/fidelity.
  rr:predicateObjectMap [
    rr:predicate sd:countryName ;
    rr:objectMap [ rml:reference "country" ; rr:datatype xsd:string ]
  ] ;

  # cluster — clean xsd:integer (0, 3); direct reference.
  rr:predicateObjectMap [
    rr:predicate sd:cluster ;
    rr:objectMap [ rml:reference "cluster" ; rr:datatype xsd:integer ]
  ] ;

  # pop — clean xsd:integer (7971931); no $/commas, direct reference.
  rr:predicateObjectMap [
    rr:predicate sd:pop ;
    rr:objectMap [ rml:reference "pop" ; rr:datatype xsd:integer ]
  ] ;

  # life_expect — clean xsd:double (43.88, 71.73); direct reference.
  rr:predicateObjectMap [
    rr:predicate sd:lifeExpect ;
    rr:objectMap [ rml:reference "life_expect" ; rr:datatype xsd:double ]
  ] ;

  # fertility — clean xsd:double (7.42, 3.1); direct reference.
  rr:predicateObjectMap [
    rr:predicate sd:fertility ;
    rr:objectMap [ rml:reference "fertility" ; rr:datatype xsd:double ]
  ] .
```
