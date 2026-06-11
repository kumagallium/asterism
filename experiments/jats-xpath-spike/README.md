# JATS → RDF de-risk spike (document-ontology layer, #19 follow-up)

Validates the core claim of [`handoff_to_claude_code_document_ontology.md`](../../../handoff_to_claude_code_document_ontology.md):
a paper's full-text structure (section → paragraph) can be turned into a
`po:contains` tree of `doco:` nodes with verbatim text, **declaratively, on the
existing substrate** — the same mechanism #19 used to add JSON, just `ql:XPath`
instead of `ql:JSONPath`. No engine change.

## Result — PROVEN (real morph-kgc 2.10)

`paper.rml.ttl` over `paper.xml` materializes **17 triples**:
`fabio:ResearchPaper` → `po:contains` → 2 `doco:Section` (with `dcterms:title`)
→ `po:contains` → 3 `doco:Paragraph` (with `nif:isString` verbatim).

```
PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/jats-xpath-spike/run_spike.py
```

## Findings the MVP needs (morph-kgc XML constraints discovered here)

1. **Safety allowlist blocks `.xml` today.** `asterism.rml_safety._ALLOWED_SOURCE_SUFFIXES`
   = `{.csv, .tsv, .json}`. Add `.xml` there — a deliberate one-line safety
   decision (the format is vetted; morph-kgc reads it declaratively). This gate
   is being actively hardened by other work, so coordinate / make it conscious.
2. **References are iterator-RELATIVE only.** No absolute paths
   (`{/article/...}` → *"cannot use absolute path"*) and **no parent/ancestor
   axes in templates** (`{parent::sec/@id}` → *"prefix 'parent' not found"*).
3. **Containment must be parent→child**, via a **multi-valued child reference**
   from the parent's iterator — e.g. iterate `/article/body/sec` and emit
   `po:contains` with object template `…/para/{p/@id}` (one triple per child).
   The child→parent direction (carrying the parent id up an axis) does not work.
4. **Every template needs ≥1 `{ref}`** (a constant-only template is rejected).
   Combined with (2), the **paper-id base is only reachable at the `/article`
   iterator** — so the `sec`/`para` maps use a per-paper constant base here
   (`SID-6`). In production, **inject the paper-IRI base per source file** (the
   ingest is per-paper) rather than trying to pull the ancestor `article-id`
   into child templates.
5. **Verbatim text** comes from `rml:reference "."` (the element's text).

## Implications (carry into the MVP)

- **paper → section → paragraph is pure declarative RML** (this spike). Figures,
  captions, `deo:` rhetorical roles follow the same parent→child pattern.
- **Sentence split + `nif:beginIndex/endIndex` are NOT RML.** JATS has no
  sentence element, and offsets are stateful — these are the deterministic
  post-pass the handoff (§A.4) describes (record as `sd:DocumentParsingActivity`,
  a dated claim, not a verified fact). The MVP can stop at paragraph and add
  sentences/offsets later.
- The substrate, registry-dataset, FROM-merge, promote-flag, and query-tools
  machinery are all reused unchanged — this is "another promoted graph", exactly
  like the JSON path (#19).

## Files

| file | role |
|---|---|
| `paper.xml` | minimal JATS-shaped sample (1 article, 2 sections, 3 paragraphs) |
| `paper.rml.ttl` | the proven `ql:XPath` mapping (article→sec→para tree + titles + verbatim) |
| `run_spike.py` | materialize + assert (widens the allowlist at runtime, spike-only) |
