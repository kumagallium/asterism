# Dogfood report — `csv2rdf-inspect` on the full starrydata CSVs

Date: 2026-05-28
Command:

```bash
csv2rdf-inspect \
    starrydata_papers.csv \
    starrydata_samples.csv \
    starrydata_curves.csv \
    --fk SID --output starrydata_inspection.md
```

Inputs (from `starrydata_dataset/`):

| CSV | Size | Rows |
|---|---|---|
| starrydata_papers.csv | 60 MB | 56,389 |
| starrydata_samples.csv | 113 MB | 104,846 |
| starrydata_curves.csv | 148 MB | 233,103 |

Runtime on macOS Darwin 25.3.0 (Apple Silicon): ~30 s (whole-file in-memory analysis).

---

## Key findings (uniqueness, ★ Phase 1 trap T1)

| Composite key | Rows considered | Distinct | Collisions | Unique? | Phase 1 assumption |
|---|---|---|---|---|---|
| papers: `(SID)` | 56,389 | 56,361 | **28** | ✗ | **assumed unique** (`sdr:paper/{SID}`) |
| papers: `(SID, DOI)` | 56,389 | 56,388 | 1 | ✗ | — |
| samples: `(SID, sample_id)` | 104,846 | 104,846 | **0** | ✓ | confirmed (`sdr:sample/{SID}-{sample_id}`) |
| curves: `(SID, sample_id, figure_id)` | 233,103 | 233,103 | **0** | ✓ | confirmed (`sdr:curve/{SID}-{figure_id}-{sample_id}`) |
| curves: `(SID, figure_id)` | 233,103 | 65,790 | 167,313 | ✗ | confirmed (need 3-way composite) |

### ⚠ Finding 1: papers.csv `SID` has 28 collisions

Phase 1 [`starrydata.py::_emit_paper`](../../ingest/src/csv2rdf/starrydata.py) mints the Paper IRI as `sdr:paper/{SID}`. The dogfood data shows **`SID` has 28 collisions in the full papers.csv**.

Drilling down:
- `(SID)`: 28 collisions (28 SIDs reused)
- `(SID, DOI)`: 1 collision → 27 of the collided SIDs have *different* DOIs (= 27 distinct papers being silently merged into 27 + 1 IRIs)
- 1 exact (SID, DOI) duplicate → 1 truly duplicated row (set-semantics dedup'd by Oxigraph)

**Implication**: 27 distinct papers are being collapsed into 27 fewer `sdr:paper/{SID}` IRIs in the live Oxigraph store. The samples / curves that point to them via `sd:fromPaper` may also be merged incorrectly.

Suggested actions (out of scope for this PR — to be filed as follow-up):
- (a) Verify in the live Oxigraph store: `SELECT ?p (COUNT(*) as ?n) WHERE { ?s sd:fromPaper ?p } GROUP BY ?p ORDER BY DESC(?n)` and check distribution
- (b) Update Phase 1 Paper IRI to use `(SID, DOI)` composite key, OR file an upstream data fix
- (c) Add a Phase 1 regression test that asserts ID uniqueness before ingest

### ✓ Finding 2: samples / curves composite IRIs validated at full scale

Phase 1 [design-rationale §1](../../docs/architecture/design-rationale.md#1-iri-命名--複合キー-composite-iri) discovered the collision pattern at smaller scale. This dogfood confirms it at full scale:

- `(SID, sample_id)` is unique across all 104,846 sample rows
- `(SID, sample_id, figure_id)` is unique across all 233,103 curve rows

The Phase 1 fix in PR #8 holds at scale.

### ✓ Finding 3: JSON column detection works on starrydata's real shapes

Detected as `json-object`: `author`, `issued`, `project_names` (papers); `sample_info` (samples).
Detected as `json-array`: `x`, `y` (curves), `composition` (samples — note: this is `json-object` actually, plain string here — needs investigation).

Wait — `composition` shows as xsd:string in our output (sample value: `Pb1Te1.01Na0.02`). That matches Phase 1's treatment.

Top-level keys auto-extracted for `sample_info`:
```
LaserFlash, Form, ElectricalMeasurements, ThermalMeasurements, Composition,
MaterialFamily, AdditionalMaterials, Substrate, MagneticMeasurements, ...
```

These are exactly the Descriptor names Phase 1 emits.

### ⚠ Finding 4: `dcterms:created` etc. detected as xsd:string

samples.csv `created_at`: `Thu Aug 09 2018 16:48:52 GMT+0900 (Japan Standard Time)` is correctly detected as xsd:string (not date / datetime). This matches the Phase 1 [design-rationale §11](../../docs/architecture/design-rationale.md) decision to keep it raw.

### ⚠ Finding 5: `issued` shows as json-object in inspection but ABox uses `xsd:date`

papers.csv `issued` column samples like `{"date_parts": [[2014, 4, 15]]}` are detected as `json-object`. Phase 1 parses these with `parse_issued()` and emits `xsd:date` literals. The inspection tool surfaces the **raw** format; the LLM is expected to suggest the same transformation strategy (`parse_issued` helper).

This matches the workflow's intent: surface raw structure → let LLM propose the transformation.

---

## Tool behaviour summary

- **8 trap T1 (uniqueness)**: ✓ working, 3-way composite search now triggers via companion pool fix (was 2-way only at first run)
- **8 trap T2 (BOM)**: ✓ working (`utf-8-sig` default)
- **JSON detection**: ✓ working for both array (curves x/y) and object (author / sample_info)
- **non_null rate**: ✓ correct (`composition_details` 18%, `sample_info` 98%)
- **Speed**: ~30s for ~320 MB of CSV on M-series laptop. Acceptable for one-off Step 0 inspection; future high-throughput use would need pandas optional dep + streaming uniqueness via Counter chunks.

## Next steps

1. ✅ CSV inspection working at full scale — `propose_schema` MCP tool can ground on this
2. ⏭ Write `propose_schema` that takes `(csv_paths, domain_hint)` → calls inspect → calls LLM → emits rdf-config model.yaml
3. ⏭ File follow-up issue for **papers.csv SID collisions** (Phase 1 regression risk)
4. ⏭ `validate_schema` MCP tool will use the same `inspect_csv_set` API for its uniqueness validator
