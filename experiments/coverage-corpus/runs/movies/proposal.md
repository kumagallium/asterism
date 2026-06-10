# Film box-office schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). Only the §9 RML block is consumed by the coverage
> analyzer; the surrounding sections are abbreviated.

### 2. IRI scheme (abbrev.)

- `sd:` = `https://kumagallium.github.io/asterism/ontology/`
- `sdr:` = `https://kumagallium.github.io/asterism/resource/`
- Film IRI: `sdr:film/{Title}` — `Title` is the unique key (50 rows / 50
  distinct / 0 collisions). Morph-KGC percent-encodes the template value, so
  spaces/apostrophes in titles are IRI-safe.

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

  # Title — clean string → direct.
  rr:predicateObjectMap [ rr:predicate sd:title ;
    rr:objectMap [ rml:reference "Title" ] ] ;

  # US Gross — clean integer (146083, 15200000), no $/commas → direct.
  rr:predicateObjectMap [ rr:predicate sd:usGross ;
    rr:objectMap [ rml:reference "US Gross" ; rr:datatype xsd:integer ] ] ;

  # Worldwide Gross — clean integer → direct.
  rr:predicateObjectMap [ rr:predicate sd:worldwideGross ;
    rr:objectMap [ rml:reference "Worldwide Gross" ; rr:datatype xsd:integer ] ] ;

  # US DVD Sales — clean integer → direct.
  rr:predicateObjectMap [ rr:predicate sd:usDvdSales ;
    rr:objectMap [ rml:reference "US DVD Sales" ; rr:datatype xsd:integer ] ] ;

  # Production Budget — clean integer → direct.
  rr:predicateObjectMap [ rr:predicate sd:productionBudget ;
    rr:objectMap [ rml:reference "Production Budget" ; rr:datatype xsd:integer ] ] ;

  # Release Date — messy date "Jun 12 1998" → fn:date_iso → xsd:date.
  rr:predicateObjectMap [ rr:predicate sd:releaseDate ;
    rr:objectMap [
      rmlf:functionExecution [ rmlf:function fn:date_iso ;
        rmlf:input [ rmlf:parameter fn:p_value ;
                     rmlf:inputValueMap [ rml:reference "Release Date" ] ] ] ;
      rr:datatype xsd:date ] ] ;

  # MPAA Rating — clean categorical string (R, PG-13, Not Rated) → direct.
  rr:predicateObjectMap [ rr:predicate sd:mpaaRating ;
    rr:objectMap [ rml:reference "MPAA Rating" ] ] ;

  # Running Time min — clean integer → direct.
  rr:predicateObjectMap [ rr:predicate sd:runningTimeMin ;
    rr:objectMap [ rml:reference "Running Time min" ; rr:datatype xsd:integer ] ] ;

  # Distributor — clean string → direct.
  rr:predicateObjectMap [ rr:predicate sd:distributor ;
    rr:objectMap [ rml:reference "Distributor" ] ] ;

  # Source — clean string → direct.
  rr:predicateObjectMap [ rr:predicate sd:source ;
    rr:objectMap [ rml:reference "Source" ] ] ;

  # Major Genre — clean string → direct.
  rr:predicateObjectMap [ rr:predicate sd:majorGenre ;
    rr:objectMap [ rml:reference "Major Genre" ] ] ;

  # Creative Type — clean string → direct.
  rr:predicateObjectMap [ rr:predicate sd:creativeType ;
    rr:objectMap [ rml:reference "Creative Type" ] ] ;

  # Director — clean string → direct.
  rr:predicateObjectMap [ rr:predicate sd:director ;
    rr:objectMap [ rml:reference "Director" ] ] ;

  # Rotten Tomatoes Rating — clean integer (90, 57, 100), NOT "85%" → direct.
  rr:predicateObjectMap [ rr:predicate sd:rottenTomatoesRating ;
    rr:objectMap [ rml:reference "Rotten Tomatoes Rating" ; rr:datatype xsd:integer ] ] ;

  # IMDB Rating — clean double (6.1, 5.6, 7.2) → direct.
  rr:predicateObjectMap [ rr:predicate sd:imdbRating ;
    rr:objectMap [ rml:reference "IMDB Rating" ; rr:datatype xsd:double ] ] ;

  # IMDB Votes — clean integer → direct.
  rr:predicateObjectMap [ rr:predicate sd:imdbVotes ;
    rr:objectMap [ rml:reference "IMDB Votes" ; rr:datatype xsd:integer ] ] .
```
