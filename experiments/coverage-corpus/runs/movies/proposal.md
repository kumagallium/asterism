# Film box-office schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = `https://kumagallium.github.io/asterism/ontology/`
- `sdr:` = `https://kumagallium.github.io/asterism/resource/`
- Film IRI: `sdr:film/{Title}` (Title is 100% unique in the inspection).

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

<#FilmMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "movies.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/film/{Title}" ;
                  rr:class sd:Film ] ;
  rr:predicateObjectMap [ rr:predicate sd:title ;
    rr:objectMap [ rml:reference "Title" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:usGross ;
    rr:objectMap [ rml:reference "US Gross" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:worldwideGross ;
    rr:objectMap [ rml:reference "Worldwide Gross" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:productionBudget ;
    rr:objectMap [ rml:reference "Production Budget" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:releaseDate ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:date_iso ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "Release Date" ] ] ] ;
      rr:datatype xsd:date ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:mpaaRating ;
    rr:objectMap [ rml:reference "MPAA Rating" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:majorGenre ;
    rr:objectMap [ rml:reference "Major Genre" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:runningTimeMin ;
    rr:objectMap [ rml:reference "Running Time min" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:imdbRating ;
    rr:objectMap [ rml:reference "IMDB Rating" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:rottenTomatoesRating ;
    rr:objectMap [ rml:reference "Rotten Tomatoes Rating" ; rr:datatype xsd:integer ] ] .
```
