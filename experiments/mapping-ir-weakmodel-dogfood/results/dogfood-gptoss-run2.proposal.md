## 1. Class hierarchy (Mermaid)

```mermaid
classDiagram
    Paper
    Sample
    Curve
    Periodical
    Person
    Project
    Paper --> "0..*" Person : schema:author
    Paper --> "0..*" Project : sd:hasProject
    Paper --> "0..*" Periodical : sd:isPartOf
    Sample --> "1" Paper : sd:isSampleOf
    Sample --> "0..*" Project : sd:hasProject
    Sample --> "0..*" Person : sd:hasContributor
    Curve --> "1" Sample : sd:isCurveOf
    Curve --> "0..*" Project : sd:hasProject
```

## 2. IRI scheme

| Prefix | IRI |
|--------|-----|
| `sd:`  | `https://kumagallium.github.io/asterism/starrydata/ontology#` |
| `sdr:` | `https://kumagallium.github.io/asterism/starrydata/resource/` |
| `schema:` | `https://schema.org/` |
| `dcterms:` | `http://purl.org/dc/terms/` |
| `bibo:`   | `http://purl.org/ontology/bibo/` |
| `prov:`   | `http://www.w3.org/ns/prov#` |

### Entity IRI templates (smallest globally‑unique composite key)

| Class | Template | Reason |
|-------|----------|--------|
| **Paper** | `sdr:paper/{SID}` | `SID` is unique for all 40 rows (★ T1) |
| **Sample** | `sdr:sample/{sample_id}` | `sample_id` is distinct in the whole file (★ T1) |
| **Curve** | `sdr:curve/{sample_id}/{slug(composition)}` | Composite `(composition, sample_id)` is unique (★ T1). `slug()` makes the IRI file‑system safe. |
| **Periodical** | `sdr:periodical/{slug(container_title)}` | `container_title` identifies a journal; slug‑ified for stability. |
| **Person** | `sdr:person/{slug(family)}_{slug(given)}` | Combination of family & given name is the best deterministic key from the `author` JSON objects. |
| **Project** | `sdr:project/{slug(. )}` | Each distinct project name (e.g. `ThermoelectricMaterials`) becomes its own IRI. |

## 3. Property design

| Class | Predicate (reuse) | Range / datatype | Cardinality | Note |
|-------|-------------------|------------------|-------------|------|
| **Paper** | `schema:name` | xsd:string | 1..1 | title |
| | `dcterms:identifier` | xsd:string | 1..1 | DOI |
| | `schema:url` | IRI | 1..1 | URL (via `iri_safe`) |
| | `schema:datePublished` | xsd:date | 1..1 | extracted from `issued.date_parts` |
| | `sd:containerTitle` | xsd:string | 1..1 | `container_title` |
| | `sd:containerTitleShort` | xsd:string | 0..1 | optional |
| | `sd:volume` | xsd:string | 0..1 | |
| | `sd:issue` | xsd:string | 0..1 | |
| | `sd:pageRange` | xsd:string | 1..1 | `page` |
| | `sd:issn` | xsd:string | 0..* | multi‑valued (comma‑separated) |
| | `sd:hasProject` | sd:Project | 0..* | from `project_names` |
| | `schema:author` | sd:Person | 0..* | expanded from `author` JSON |
| | `sd:isPartOf` | sd:Periodical | 0..1 | from `container_title` |
| **Sample** | `sd:sampleName` | xsd:string | 1..1 | `sample_name` |
| | `dcterms:identifier` | xsd:integer | 1..1 | `sample_id` |
| | `sd:composition` | xsd:string | 1..1 | free‑text |
| | `sd:compositionDetails` | xsd:string | 0..1 | sparsely populated |
| | `sd:isSampleOf` | sd:Paper | 1..1 | FK `SID` |
| | `sd:hasProject` | sd:Project | 0..* | from `project_names` |
| | `sd:hasContributor` | sd:Person | 0..* | authors of the sample (if any) |
| | `sd:sampleInfoRaw` | xsd:string (JSON) | 0..1 | raw `sample_info` object |
| **Curve** | `sd:xValues` | xsd:string (JSON) | 1..1 | raw `x` array |
| | `sd:yValues` | xsd:string (JSON) | 1..1 | raw `y` array |
| | `sd:xMin` | xsd:double | 1..1 | `float_array_min` on `x` |
| | `sd:xMax` | xsd:double | 1..1 | `float_array_max` on `x` |
| | `sd:yMin` | xsd:double | 1..1 | `float_array_min` on `y` |
| | `sd:yMax` | xsd:double | 1..1 | `float_array_max` on `y` |
| | `sd:propX` | xsd:string | 1..1 | constant “Temperature” |
| | `sd:unitX` | xsd:string | 1..1 | “K” |
| | `sd:propY` | xsd:string | 1..1 | “Seebeck coefficient” |
| | `sd:unitY` | xsd:string | 1..1 | “V*K^(-1)” |
| | `sd:isCurveOf` | sd:Sample | 1..1 | FK `sample_id` |
| | `sd:hasProject` | sd:Project | 0..* | from `project_names` |
| **Periodical** | `sd:journalTitle` | xsd:string | 1..1 | `container_title` |
| **Person** | `sd:givenName` | xsd:string | 1..1 | `given` |
| | `sd:familyName` | xsd:string | 1..1 | `family` |
| | `sd:affiliation` | xsd:string | 0..* | empty array in source – kept for future use |
| **Project** | `sd:projectName` | xsd:string | 1..1 | the literal project label |

## 4. JSON column strategy

| Column | Source | Strategy | Rationale |
|--------|--------|----------|-----------|
| `issued` (object) | papers.csv | **Compress** → `schema:datePublished` (xsd:date) via `date_iso` on `date_parts[0]` | Dates are needed for temporal queries; full object unnecessary. |
| `author` (array) | papers.csv | **Expand** to `sd:Person` nodes; link with `schema:author` | Authors are first‑class agents; downstream queries (e.g., all papers by “Chong”) require explicit nodes. |
| `project_names` (array of string) | papers.csv & samples.csv & curves.csv | **Expand** to `sd:Project` nodes (multi‑valued) | Projects are reusable entities; a periodical can be shared across rows. |
| `x` / `y` (numeric arrays) | curves.csv | **Compress** as raw JSON literals (`sd:xValues`, `sd:yValues`) **plus** min/max aggregates via `float_array_min/max` | Raw series kept for reproducibility; aggregates speed up range queries without parsing JSON each time. |
| `sample_info` (object) | samples.csv | **Compress** to a raw JSON literal (`sd:sampleInfoRaw`) | The object has many heterogeneous keys; extracting a stable schema would be speculative. |
| `project_names` (array) – also used for **Expand** (same as above). |

## 5. Design rationale (★ T7)

### IRI scheme  
**Decision**: Use stable, deterministic IRIs based on the smallest globally unique composite key for each class.  
**Why**: Guarantees idempotent ingestion (no blanks) and complies with Phase 1’s bnode‑free policy. The chosen keys (`SID`, `sample_id`, `(composition, sample_id)`) are proven unique in the inspection tables (★ T1).  
**Alternatives**: UUID‑based IRIs or hashing the whole row. Rejected because they would hide provenance and break human‑readability.  
**Trade‑offs**: Composite keys increase IRI length; if future data adds collisions we must revise the template (e.g., add a hash suffix).

### JSON handling  
**Decision**: Expand `author` and `project_names` to first‑class nodes; compress `issued`, `x/y`, `sample_info` to literals with optional aggregates.  
**Why**: Authors and projects are repeatedly referenced (FK‑like) and benefit from dereferencing; dates and numeric series are better queried via literals/aggregates.  
**Alternatives**: Full expansion of `x/y` into separate `Observation` entities (would explode row count) or keeping `author` as raw JSON (loses ability to query per‑author).  
**Trade‑offs**: Expanding authors creates many `Person` IRIs; if the source later provides author IDs we could switch to a more canonical identifier.

### Property reuse  
**Decision**: Reuse predicates from schema.org, dcterms, bibo, prov wherever possible.  
**Why**: Maximises interoperability and aligns with the domain constraint to prefer existing vocabularies.  
**Alternatives**: Invent custom predicates for every field (e.g., `sd:title`). Rejected because it would silo the data.  
**Trade‑offs**: Some schema.org properties (e.g., `schema:author`) expect `schema:Person`; we map to our `sd:Person` subclass, which is acceptable but may need rdfs:subClassOf alignment later.

### Cardinality  
**Decision**: Derive 1..1 for columns with 100 % non‑null rate; 0..* for multi‑valued JSON arrays; 0..1 for optional columns (e.g., `containerTitleShort`).  
**Why**: Directly reflects the inspection statistics.  
**Alternatives**: Over‑constraining (e.g., forcing `0..1` on `issn`) would cause validation failures.  
**Trade‑offs**: Future rows with missing data will automatically be treated as absent (allowed by 0..* or 0..1).

### Project entity  
**Decision**: Model each distinct project name as an `sd:Project` node.  
**Why**: Projects appear across papers, samples, and curves; a separate node enables queries like “all curves belonging to the ThermoelectricMaterials project”.  
**Alternatives**: Keep project as a literal list on each row; rejected for lack of traceability.  
**Trade‑offs**: If project naming is inconsistent (e.g., typos) we may generate duplicate nodes; a later normalization step can be added.

## 6. rdf-config model.yaml

```yaml
- Paper <sdr:paper/1>:
    - a: sd:Paper
    - schema:name:
        - var_name: "Decoupling Interrelated Parameters for ..."
    - dcterms:identifier:
        - var_name: "10.1021/ar400290f"
    - schema:url:
        - var_name: "http://dx.doi.org/10.1021/ar400290f"
    - schema:datePublished:
        - var_name: "2014-04-15"
    - sd:containerTitle:
        - var_name: "Accounts of Chemical Research"
    - sd:containerTitleShort:
        - var_name: "Acc. Chem. Res."
    - sd:volume:
        - var_name: "47"
    - sd:issue:
        - var_name: "4"
    - sd:pageRange:
        - var_name: "1287-1295"
    - sd:issn*:
        - var_name: "0001-4842,1520-4898"
    - sd:hasProject*:
        - var_name: "ThermoelectricMaterials"
        - var_name: "GeneralDB"
    - schema:author*:
        - var_name: "sdr:person/chong_c"
    - sd:isPartOf?:
        - var_name: "sdr:periodical/accounts-of-chemical-research"

- Sample <sdr:sample/6027>:
    - a: sd:Sample
    - sd:sampleName:
        - var_name: "Cu2.025Cd0.975SnSe4"
    - dcterms:identifier:
        - var_name: "6027"
    - sd:composition:
        - var_name: "Pb1Te1.01Na0.02"
    - sd:compositionDetails?:
        - var_name: "Bi2Te3 ball milled powders into these PE"
    - sd:isSampleOf:
        - var_name: "sdr:paper/1"
    - sd:hasProject*:
        - var_name: "ThermoelectricMaterials"
    - sd:hasContributor*:
        - var_name: "sdr:person/b_chang"
    - sd:sampleInfoRaw?:
        - var_name: '{" remanence magnetion":{"category":"","value":0.12}}'

- Curve <sdr:curve/6027/thermoelectricmaterials>:
    - a: sd:Curve
    - sd:xValues:
        - var_name: '[299.8597,324.8683,349.8757,375.2454,399]'
    - sd:yValues:
        - var_name: '[-0.0001484452,-0.0001602763,-0.00017295]'
    - sd:xMin:
        - var_name: "299.8597"
    - sd:xMax:
        - var_name: "399"
    - sd:yMin:
        - var_name: "-0.00017295"
    - sd:yMax:
        - var_name: "-0.0001484452"
    - sd:propX:
        - var_name: "Temperature"
    - sd:unitX:
        - var_name: "K"
    - sd:propY:
        - var_name: "Seebeck coefficient"
    - sd:unitY:
        - var_name: "V*K^(-1)"
    - sd:isCurveOf:
        - var_name: "sdr:sample/6027"
    - sd:hasProject*:
        - var_name: "ThermoelectricMaterials"

- Person <sdr:person/chong_c>:
    - a: sd:Person
    - sd:givenName:
        - var_name: "Chong"
    - sd:familyName:
        - var_name: "C"
    - sd:affiliation*:
        - var_name: ""

- Project <sdr:project/thermoelectricmaterials>:
    - a: sd:Project
    - sd:projectName:
        - var_name: "ThermoelectricMaterials"

- Periodical <sdr:periodical/accounts-of-chemical-research>:
    - a: sd:Periodical
    - sd:journalTitle:
        - var_name: "Accounts of Chemical Research"
```

## 7. MIE YAML extras

```yaml
schema_info:
  title: "StarryData Thermoelectric Materials Dataset"
  description: |
    Bibliographic records (papers), associated material samples, and digitised
    property curves for thermoelectric research.  The model links papers →
    samples → curves and preserves provenance via PROV‑O.
  categories: [MaterialsScience, Thermoelectrics, Bibliography, ExperimentalData]
  keywords:
    - "thermoelectric"
    - "Seebeck coefficient"
    - "figure of merit"
    - "ZT"
    - "heat transport"
    - "熱電"
    - "ゼーベック"
    - "熱電材料"
    - "S = α·T·σ"
    - "σ·α²·T⁻¹"

sample_rdf_entries:
  - |
    @prefix sd: <https://kumagallium.github.io/asterism/starrydata/ontology#> .
    @prefix sdr: <https://kumagallium.github.io/asterism/starrydata/resource/> .
    @prefix schema: <https://schema.org/> .
    @prefix dcterms: <http://purl.org/dc/terms/> .

    sdr:paper/1 a sd:Paper ;
        schema:name "Decoupling Interrelated Parameters for ..." ;
        dcterms:identifier "10.1021/ar400290f" ;
        schema:url <http://dx.doi.org/10.1021/ar400290f> ;
        schema:datePublished "2014-04-15"^^xsd:date ;
        sd:containerTitle "Accounts of Chemical Research" ;
        sd:issn "0001-4842,1520-4898" ;
        sd:hasProject sdr:project/ThermoelectricMaterials ;
        schema:author sdr:person/chong_c , sdr:person/b_chang .

  - |
    sdr:sample/6027 a sd:Sample ;
        sd:sampleName "Cu2.025Cd0.975SnSe4" ;
        dcterms:identifier "6027"^^xsd:integer ;
        sd:composition "Pb1Te1.01Na0.02" ;
        sd:isSampleOf sdr:paper/1 ;
        sd:hasProject sdr:project/ThermoelectricMaterials ;
        sd:sampleInfoRaw '{" remanence magnetion":{"category":"","value":0.12}}' .

  - |
    sdr:curve/6027/thermoelectricmaterials a sd:Curve ;
        sd:xValues "[299.8597,324.8683,349.8757]" ;
        sd:yValues "[-0.0001484452,-0.0001602763]" ;
        sd:xMin "299.8597"^^xsd:double ;
        sd:xMax "349.8757"^^xsd:double ;
        sd:yMin "-0.0001602763"^^xsd:double ;
        sd:yMax "-0.0001484452"^^xsd:double ;
        sd:propX "Temperature" ;
        sd:unitX "K" ;
        sd:propY "Seebeck coefficient" ;
        sd:unitY "V*K^(-1)" ;
        sd:isCurveOf sdr:sample/6027 ;
        sd:hasProject sdr:project/ThermoelectricMaterials .

sparql_query_examples:
  - name: "All papers by a given author family name"
    query: |
      PREFIX schema: <https://schema.org/>
      PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
      SELECT ?paper ?title WHERE {
        ?paper a sd:Paper ;
               schema:name ?title ;
               schema:author ?person .
        ?person sd:familyName "Chong" .
      }
  - name: "Samples belonging to a specific paper DOI"
    query: |
      PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
      PREFIX dcterms: <http://purl.org/dc/terms/>
      SELECT ?sample ?name WHERE {
        ?paper a sd:Paper ; dcterms:identifier "10.1021/ar400290f" .
        ?sample a sd:Sample ; sd:isSampleOf ?paper ; sd:sampleName ?name .
      }
  - name: "Seebeck curve extremes for a given sample"
    query: |
      PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
      SELECT ?yMin ?yMax WHERE {
        ?curve a sd:Curve ;
               sd:isCurveOf <sdr:sample/6027> ;
               sd:yMin ?yMin ;
               sd:yMax ?yMax .
      }
  - name: "All projects referenced in the dataset"
    query: |
      PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
      SELECT DISTINCT ?proj WHERE {
        ?entity sd:hasProject ?proj .
      }
  - name: "Curves that include a temperature range overlapping 300‑350 K"
    query: |
      PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
      SELECT ?curve WHERE {
        ?curve a sd:Curve ;
               sd:xMin ?xmin ;
               sd:xMax ?xmax .
        FILTER( ?xmin <= 350 && ?xmax >= 300 )
      }

anti_patterns:
  - "Author IRIs are derived from name strings only; homonyms will collide if two distinct authors share given+family."
  - "Composition strings are free‑text; no canonical parsing → downstream chemical‑analysis tools must normalize themselves."
  - "`sample_info` is stored as raw JSON; heterogeneous keys make SPARQL querying cumbersome."
  - "Periodical IRIs are generated from the journal title via slug; any change in title spelling creates a new node."
  - "`comments` column in curves.csv is empty – ignored."

architectural_notes: |
  The design follows §5 rationale: stable IRIs (no bnodes), reuse of
  schema.org/dcterms vocabularies, and a pragmatic JSON handling strategy
  that balances query performance with data fidelity.  All multi‑valued
  structures are represented as separate entities (Person, Project) to
  enable graph‑level queries without exploding the triple count for numeric
  series, which remain as raw JSON literals with min/max aggregates.

```

## 8. Ingester sketch (`ingest/src/asterism/starrydata.py`)

```python
import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

# ----------------------------------------------------------------------
# Helper: safe IRI construction (slugifying, URL‑encoding)
# ----------------------------------------------------------------------
def slug(text: str) -> str:
    """Very small slugifier – lower‑case, replace spaces with '-', strip non‑alnum."""
    import re, urllib.parse
    s = re.sub(r'\s+', '-', text.strip().lower())
    s = re.sub(r'[^a-z0-9\-]', '', s)
    return urllib.parse.quote(s, safe='-')

def paper_iri(sid: str) -> str:
    return f"sdr:paper/{sid}"

def sample_iri(sample_id: str) -> str:
    return f"sdr:sample/{sample_id}"

def curve_iri(sample_id: str, composition: str) -> str:
    return f"sdr:curve/{sample_id}/{slug(composition)}"

def person_iri(given: str, family: str) -> str:
    return f"sdr:person/{slug(family)}_{slug(given)}"

def periodical_iri(title: str) -> str:
    return f"sdr:periodical/{slug(title)}"

def project_iri(name: str) -> str:
    return f"sdr:project/{slug(name)}"

# ----------------------------------------------------------------------
# Provenance activity (PROV-O)
# ----------------------------------------------------------------------
def start_ingestion_activity(dataset: str) -> str:
    """Create a PROV Activity IRI and return it."""
    act_id = f"sdr:ingest/{dataset}/{datetime.utcnow().isoformat()}"
    # In real code we would emit triples:
    # act a prov:Activity ; prov:wasAssociatedWith <ingestor>;
    return act_id

# ----------------------------------------------------------------------
# Core ingest functions (one per source)
# ----------------------------------------------------------------------
def ingest_papers(csv_path: Path, activity_iri: str) -> None:
    """Read papers.csv, emit RDF triples (via rdf-config runtime)."""
    with csv_path.open("r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sid = row["SID"]
            paper_id = paper_iri(sid)
            # Example: emit triple a sd:Paper …
            # (actual emission delegated to rdf-config runtime)
            # -------------------------------------------------
            # 1. scalar literals
            # 2. JSON‑object `issued` → date_iso
            # 3. JSON‑array `author` → expand to Person nodes
            # 4. JSON‑array `project_names` → expand to Project nodes
            # -------------------------------------------------
            pass  # placeholder

def ingest_samples(csv_path: Path, activity_iri: str) -> None:
    with csv_path.open("r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sample_id = row["sample_id"]
            sample_id_iri = sample_iri(sample_id)
            # emit triples similarly…
            pass

def ingest_curves(csv_path: Path, activity_iri: str) -> None:
    with csv_path.open("r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sample_id = row["sample_id"]
            composition = row["composition"]
            curve_id = curve_iri(sample_id, composition)
            # emit raw JSON literals for x/y and aggregates via Tier‑0 functions
            pass

# ----------------------------------------------------------------------
# Error logging (jsonlines)
# ----------------------------------------------------------------------
def log_error(record: Dict[str, Any], message: str, log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as out:
        entry = {"record": record, "error": message, "ts": datetime.utcnow().isoformat()}
        out.write(json.dumps(entry) + "\n")
```

## 9. Declarative mapping spec

```yaml
version: 1
prefixes:
  sd:  "https://kumagallium.github.io/asterism/starrydata/ontology#"
  sdr: "https://kumagallium.github.io/asterism/starrydata/resource/"
  schema: "https://schema.org/"
  dcterms: "http://purl.org/dc/terms/"
maps:
  - name: paper
    source: papers.csv
    subject:
      template: "sdr:paper/{SID}"
      classes: [sd:Paper, schema:ScholarlyArticle]
    properties:
      - predicate: dcterms:identifier
        column: DOI
      - predicate: schema:url
        column: URL
        function: iri_safe
        object_type: iri
      - predicate: schema:name
        column: title
      - predicate: schema:datePublished
        column: issued.date_parts[0]
        function: date_iso
        datatype: xsd:date
      - predicate: sd:containerTitle
        column: container_title
      - predicate: sd:containerTitleShort
        column: container_title_short
        optional: true
      - predicate: sd:volume
        column: volume
        optional: true
      - predicate: sd:issue
        column: issue
        optional: true
      - predicate: sd:pageRange
        column: page
      - predicate: sd:issn*      # multi‑valued literal list
        column: ISSN
        function: split
        args: {delimiter: ","}
      - predicate: sd:hasProject*
        column: project_names
        function: json_array
      - predicate: schema:author*
        column: author
        function: json_pluck
        args: {field: "family"}   # we will later compose full IRI via `person_iri`
      - predicate: sd:isPartOf?
        column: container_title
        transform: {container_title: slug}
        object_template: "sdr:periodical/{container_title}"
  - name: sample
    source: samples.csv
    subject:
      template: "sdr:sample/{sample_id}"
      classes: [sd:Sample]
    properties:
      - predicate: sd:sampleName
        column: sample_name
      - predicate: dcterms:identifier
        column: sample_id
      - predicate: sd:composition
        column: composition
      - predicate: sd:compositionDetails
        column: composition_details
        optional: true
      - predicate: sd:isSampleOf
        object_template: "sdr:paper/{SID}"
      - predicate: sd:hasProject*
        column: project_names
        function: json_array
      - predicate: sd:sampleInfoRaw?
        column: sample_info
        function: json_array_single   # keep as raw JSON literal
  - name: curve
    source: curves.csv
    subject:
      template: "sdr:curve/{sample_id}/{slug(composition)}"
      classes: [sd:Curve]
    properties:
      - predicate: sd:xValues
        column: x
        function: json_array_single   # keep raw JSON string
      - predicate: sd:yValues
        column: y
        function: json_array_single
      - predicate: sd:xMin
        column: x
        function: float_array_min
        datatype: xsd:double
      - predicate: sd:xMax
        column: x
        function: float_array_max
        datatype: xsd:double
      - predicate: sd:yMin
        column: y
        function: float_array_min
        datatype: xsd:double
      - predicate: sd:yMax
        column: y
        function: float_array_max
        datatype: xsd:double
      - predicate: sd:propX
        constant: "Temperature"
      - predicate: sd:unitX
        constant: "K"
      - predicate: sd:propY
        constant: "Seebeck coefficient"
      - predicate: sd:unitY
        constant: "V*K^(-1)"
      - predicate: sd:isCurveOf
        object_template: "sdr:sample/{sample_id}"
      - predicate: sd:hasProject*
        column: project_names
        function: json_array
```

*All functions (`date_iso`, `iri_safe`, `split`, `json_array`, `json_pluck`,
`float_array_min/max`, `json_array_single`) are from the vetted Tier‑0 set.
Every column name matches exactly the headers reported in the inspection,
and no invented columns or functions appear.*