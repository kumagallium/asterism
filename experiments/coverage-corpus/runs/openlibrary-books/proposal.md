# OpenLibrary books schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the analyzer.

### 2. IRI scheme (abbrev.)

- Book IRI: `sdr:book/{title}-{first_publish_year}` (title is near-unique; year disambiguates).
- `author_name` / `isbn` / `language` are **multi-element** string arrays — take the
  first element with `fn:array_at` (primary author / ISBN / language). `subject`
  is a multi-element topical list with no fixed position to pick — emit raw
  (a full subject list needs RML iteration / a nested map at Tier 0).

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

<#BookMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "openlibrary-books.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/book/{title}-{first_publish_year}" ;
                  rr:class sd:Book ] ;
  rr:predicateObjectMap [ rr:predicate sd:title ;
    rr:objectMap [ rml:reference "title" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:firstPublishYear ;
    rr:objectMap [ rml:reference "first_publish_year" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:pageCount ;
    rr:objectMap [ rml:reference "number_of_pages_median" ; rr:datatype xsd:integer ] ] ;
  # author_name — multi-element array → primary author at index 0
  rr:predicateObjectMap [ rr:predicate sd:primaryAuthor ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:array_at ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "author_name" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_index ; rmlf:inputValueMap [ rmlf:constant "0" ] ] ] ] ] ;
  # isbn — multi-element array → primary ISBN at index 0
  rr:predicateObjectMap [ rr:predicate sd:primaryIsbn ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:array_at ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "isbn" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_index ; rmlf:inputValueMap [ rmlf:constant "0" ] ] ] ] ] ;
  # language — multi-element array → primary language at index 0
  rr:predicateObjectMap [ rr:predicate sd:primaryLanguage ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:array_at ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "language" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_index ; rmlf:inputValueMap [ rmlf:constant "0" ] ] ] ] ] ;
  # fallback: subject is a multi-element topical list (no fixed pick) — not expanded
  rr:predicateObjectMap [ rr:predicate sd:subjectRaw ;
    rr:objectMap [ rml:reference "subject" ] ] .
```
