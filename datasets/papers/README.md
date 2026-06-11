# papers — Asterism example dataset (document-ontology layer)

The **third** example dataset, and the first of a *different shape*: not tabular
facts about materials, but the **structured full text of a research paper** —
section → subsection → paragraph → sentence, plus figures/captions — addressed by
resolvable, citable IRIs. It proves the Asterism core proposition (design ontology
+ stable IRIs + deterministic derivation + PROV + citation) holds for "the shape of
prose", not just "the shape of data" (ADR
`docs/architecture/document-ontology-layer.md`).

## What it is

The seed paper is **real**: **PMC5951533** — *On the Phase Separation in n-Type
Thermoelectric Half-Heusler Materials* (Schwall & Balke, MDPI *Materials*, 2018).
A genuine Starrydata source paper (47 samples). Its full text becomes a
`po:contains` tree of `doco:` nodes; the **measurement-condition sentence** in
§4 (Materials and Methods) and **Figure 3** (the transport-properties figure) are
the data↔text fusion targets.

## Provenance & license

The article is **CC-BY 4.0** (MDPI gold open access), so the JATS XML and the
generated `paper.ttl` are committed (like materials_project's CC-BY `mp.ttl`,
unlike the licensed starrydata demo seed). The JATS was retrieved from the
Europe PMC full-text service (`.../PMC5951533/fullTextXML`). Citation: *M. Schwall,
B. Balke, On the Phase Separation in n-Type Thermoelectric Half-Heusler Materials,
Materials 11(4), 649 (2018). https://doi.org/10.3390/ma11040649*.

## Ontology

```mermaid
classDiagram
    class ResearchPaper { lit:pmcid; dcterms:identifier; dcterms:title }
    class Section { dcterms:title; lit:structuralPath; (deo: role) }
    class Paragraph { nif:isString }
    class Sentence { nif:anchorOf; nif:beginIndex; nif:endIndex }
    class Figure { rdfs:label }
    class Caption { nif:isString }
    class DocumentParsingActivity { lit:sourceFormat; lit:parser; prov:endedAtTime }
    ResearchPaper --> Section : po:contains
    Section --> Section : po:contains (subsection)
    Section --> Paragraph : po:contains
    Section --> Figure : po:contains
    Paragraph --> Sentence : po:contains
    Figure --> Caption : po:contains
    Sentence --> ResearchPaper : prov:wasQuotedFrom
    Sentence ..> DocumentParsingActivity : prov:wasGeneratedBy
```

- Reused standard vocab (引く, don't invent): `fabio:` / `doco:` / `deo:` / `cito:`
  / `po:` (SPAR), `nif:` (offsets), `prov:` / `dcterms:` / `rdfs:`.
- Dataset-own terms `lit:` = `https://kumagallium.github.io/asterism/papers/ontology#`
  (addressing + the parse-provenance envelope only — nothing about prose *meaning*).
- Resources `…/papers/resource/paper/<PMCID>/{sec,fig,fulltext,…}`.

## Hybrid ingest (declarative RML + deterministic post-pass)

Real JATS `<p>` elements have **no `@id`**, and Morph-KGC's stdlib XML reader
truncates mixed content — so responsibilities split (ADR §4):

- **`jats/PMC5951533.rml.ttl`** (declarative `ql:XPath`, Tier 0 only, NO generated
  code) produces the **id-bearing skeleton**: the paper, the `<sec>` tree (incl.
  nested sections — all have `@id`), `<fig>` (`@id` + label), `po:contains`, and
  `lit:structuralPath` via the Tier 0 `fn:structural_slug`. This is the spike's
  claim, proven on real data. Its output is a **strict subset** of `seed/paper.ttl`.
- **`seed/build_paper_graph.py`** (content tool, stdlib, deterministic, LLM-free —
  the twin of materials_project's `build_seed.py`) adds what RML cannot on real
  JATS: `doco:Paragraph` (positional), `doco:Sentence` (segmented), faithful
  `nif:isString` verbatim, `nif:` offsets, and the fusion links — recorded under a
  dated `lit:DocumentParsingActivity` **claim** (the sentence boundaries are a
  parser's claim, not a verified fact).

## Typed query tools (`query_tools.yaml`)

| tool | what it does |
|---|---|
| `search_text` | full-text search down to the sentence (verbatim + offset + path) |
| `quote_with_citation` | a sentence + dereferenceable citation (IRI + path + PROV) |
| `fetch_passage` | a paper's section(s) with paragraph verbatim |
| `measurement_provenance` | **fusion**: a curve → its figure + its measurement-condition sentence |

The MCP server auto-registers these as typed tools; the fusion tool returns rows
only when both the curve overlay and the paper graph are in canonical scope.

## Files

| file | role |
|---|---|
| `dataset.toml` | declared identity (namespace IRIs) |
| `model.yaml` | rdf-config TBox → projected to the ontology graph at promote (#20 step5) |
| `query_tools.yaml` | typed, parameterized, read-only SPARQL tools (content) |
| `jats/PMC5951533.xml` | the real CC-BY JATS full text (committed source) |
| `jats/PMC5951533.rml.ttl` | declarative `ql:XPath` RML — the id-bearing skeleton |
| `seed/build_paper_graph.py` | content tool: JATS → `seed/paper.ttl` (deterministic) |
| `seed/paper.ttl` | the committed full structure graph (the promoted graph) |
| `seed/load.py` | load `paper.ttl` into the `canonical/papers` named graph |
| `fusion/fusion.ttl` | data↔text fusion overlay (demo curve → figure + condition sentence) |

## Seed-load (local)

```bash
python seed/load.py http://localhost:7878
# -> loads paper.ttl into …/asterism/graph/canonical/papers
```

Real-stack verification of all gates: `docs/reports/document-ontology-mvp.md`.
