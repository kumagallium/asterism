# GitHub repositories schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the analyzer.

### 2. IRI scheme (abbrev.)

- Repo IRI: `sdr:repo/{full_name}` (`full_name` = `owner/name`, 100% unique).
- `archived` is a `true`/`false` string → `fn:bool_norm` (`xsd:boolean`).
- `html_url` → `fn:url_canonical`. `created_at` is already ISO dateTime → direct.
- `topics` is a multi-element string array (no fixed pick) → raw fallback.
- `owner.*` / `license.*` are flat nested scalars (dot-path leaves) → direct.

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

<#RepoMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "github-repos.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/repo/{full_name}" ;
                  rr:class sd:Repository ] ;
  rr:predicateObjectMap [ rr:predicate sd:ownerLogin ;
    rr:objectMap [ rml:reference "owner.login" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:ownerType ;
    rr:objectMap [ rml:reference "owner.type" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:language ;
    rr:objectMap [ rml:reference "language" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:licenseId ;
    rr:objectMap [ rml:reference "license.spdx_id" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:stars ;
    rr:objectMap [ rml:reference "stargazers_count" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:forks ;
    rr:objectMap [ rml:reference "forks_count" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:openIssues ;
    rr:objectMap [ rml:reference "open_issues_count" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:createdAt ;
    rr:objectMap [ rml:reference "created_at" ; rr:datatype xsd:dateTime ] ] ;
  # archived — "true"/"false" string → boolean
  rr:predicateObjectMap [ rr:predicate sd:archived ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:bool_norm ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "archived" ] ] ] ;
      rr:datatype xsd:boolean ] ] ;
  # html_url → canonical IRI
  rr:predicateObjectMap [ rr:predicate sd:homepage ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:url_canonical ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "html_url" ] ] ] ] ] ;
  # fallback: topics is a multi-element string array (no fixed pick) — not expanded
  rr:predicateObjectMap [ rr:predicate sd:topicsRaw ;
    rr:objectMap [ rml:reference "topics" ] ] .
```
