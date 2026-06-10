# Scholarly works (Crossref) schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`.

### 2. IRI scheme (abbrev.)

- Work IRI: `sdr:work/{DOI}` (DOI is 100% unique; used verbatim as the key).
- `title` / `container-title` are single-element JSON arrays; `author` is a
  multi-valued array of objects; `published.date-parts` is a nested array.
  None has a vetted Tier 0 function (no array-of-object expander, no
  date-parts parser), so each uses the `…Raw` fallback.

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
  rml:logicalSource [ rml:source "crossref-works.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/work/{DOI}" ;
                  rr:class sd:Work ] ;
  rr:predicateObjectMap [ rr:predicate sd:doi ;
    rr:objectMap [ rml:reference "DOI" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:type ;
    rr:objectMap [ rml:reference "type" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:citationCount ;
    rr:objectMap [ rml:reference "is-referenced-by-count" ; rr:datatype xsd:integer ] ] ;
  # fallback: title not expanded (JSON array)
  rr:predicateObjectMap [ rr:predicate sd:titleRaw ;
    rr:objectMap [ rml:reference "title" ] ] ;
  # fallback: author not expanded (multi-valued JSON array of objects)
  rr:predicateObjectMap [ rr:predicate sd:authorRaw ;
    rr:objectMap [ rml:reference "author" ] ] ;
  # fallback: published.date-parts not expanded (nested JSON array)
  rr:predicateObjectMap [ rr:predicate sd:publishedDatePartsRaw ;
    rr:objectMap [ rml:reference "published.date-parts" ] ] ;
  # fallback: container-title not expanded (JSON array)
  rr:predicateObjectMap [ rr:predicate sd:containerTitleRaw ;
    rr:objectMap [ rml:reference "container-title" ] ] .
```
