# Palmer penguins schema

> Authored by the subscription Claude Code agent acting as `propose.SYSTEM_PROMPT`
> (no Anthropic API call). No ID column, so the subject is a composite of identifying fields. Units live in the column names; `Sex` is MALE/FEMALE. No vetted function fits either, so values map directly.

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

<#Map> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "penguins.json" ;
                      rml:referenceFormulation ql:JSONPath ;
                      rml:iterator "$[*]" ] ;
  rr:subjectMap [ rr:template "https://kumagallium.github.io/asterism/resource/penguin/{Species}-{Island}-{Body Mass (g)}-{Beak Length (mm)}" ; rr:class sd:Penguin ] ;
  rr:predicateObjectMap [ rr:predicate sd:species ;
    rr:objectMap [ rml:reference "Species" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:island ;
    rr:objectMap [ rml:reference "Island" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:beakLengthMm ;
    rr:objectMap [ rml:reference "Beak Length (mm)" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:beakDepthMm ;
    rr:objectMap [ rml:reference "Beak Depth (mm)" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:flipperLengthMm ;
    rr:objectMap [ rml:reference "Flipper Length (mm)" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:bodyMassG ;
    rr:objectMap [ rml:reference "Body Mass (g)" ; rr:datatype xsd:integer ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:sex ;
    rr:objectMap [ rml:reference "Sex" ] ] .
```
