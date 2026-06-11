# Scholarly works (Crossref) schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = ontology, `sdr:` = resource. Work IRI: `sdr:work/{DOI}` (DOI is 100% unique).
- `DOI` (`10.xxxx/...`) → `fn:doi_norm` (bare lowercase form).
- `published.date-parts` is a nested date-parts array (`[[2018, 11, 3]]`); the
  honest scalar we can compute is the 4-digit year → `fn:year_only` (xsd:gYear).
- JSON is tabularized to CSV at ingest, so arrays arrive as JSON-string cells:
  `title` / `container-title` (one-element arrays) → `fn:json_array_single`, and
  `author` (array of objects) → `fn:json_pluck` per sub-field (`family`) — no `…Raw`.

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

<#WorkMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "crossref-works.csv" ;
                      rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/work/{DOI}" ;
                  rr:class sd:Work ] ;
  # DOI — normalize to bare lowercase form
  rr:predicateObjectMap [ rr:predicate sd:doi ;
    rr:objectMap [
      rmlf:functionExecution [
        rmlf:function fn:doi_norm ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "DOI" ] ] ] ] ] ;
  # type — clean enum string; direct
  rr:predicateObjectMap [ rr:predicate sd:type ;
    rr:objectMap [ rml:reference "type" ] ] ;
  # is-referenced-by-count — clean integer; direct
  rr:predicateObjectMap [ rr:predicate sd:citationCount ;
    rr:objectMap [ rml:reference "is-referenced-by-count" ; rr:datatype xsd:integer ] ] ;
  # published.date-parts — nested date-parts array; extract 4-digit year
  rr:predicateObjectMap [ rr:predicate sd:publishedYear ;
    rr:objectMap [
      rmlf:functionExecution [
        rmlf:function fn:year_only ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "published.date-parts" ] ] ] ;
      rr:datatype xsd:gYear ] ] ;
  # title — one-element JSON array (["Soziale Innovation"]) → fn:json_array_single
  rr:predicateObjectMap [ rr:predicate sd:title ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:json_array_single ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "title" ] ] ] ] ] ;
  # container-title — one-element JSON array → fn:json_array_single
  rr:predicateObjectMap [ rr:predicate sd:containerTitle ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:json_array_single ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "container-title" ] ] ] ] ] ;
  # author — array of OBJECTS ([{given,family},…]) as a JSON-string cell after
  # tabularize; json_pluck explodes each object's `family` to its own triple
  rr:predicateObjectMap [ rr:predicate sd:authorFamily ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:json_pluck ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "author" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_field ;
                     rmlf:inputValueMap [ rmlf:constant "family" ] ] ] ] ] .
```
