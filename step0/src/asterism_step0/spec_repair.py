"""Surgical §9 repair — regenerate ONLY the mapping spec (Phase 2).

Why this exists (ADR ``mapping-ir-phase2-guided-repair.md``)
------------------------------------------------------------
The autocorrect loop used to fix an IR-level issue by asking ``refine_schema``
to rewrite the WHOLE 9-section document: ~8k output tokens per round, a real
truncation risk on reasoning models (observed live), and the model freely
"improving" unrelated sections while fixing one row. But an IR issue lives in
one small YAML block — so the repair call now regenerates only that block,
under a guided-JSON contract when the provider supports it (the JSON Schema
makes the observed invention families unrepresentable at generation time),
and the result is spliced back into the document deterministically.

The repaired spec goes through the SAME parse → validate → compile → RML
gates as any other round — guided decoding narrows generation, it never
replaces validation.
"""

from __future__ import annotations

from asterism_step0.materialize import materialize_schema
from asterism_step0.spec_yaml import load_spec_yaml

__all__ = [
    "SPEC_REPAIR_SYSTEM_PROMPT",
    "build_spec_repair_user",
    "parse_spec_json",
    "replace_mapping_spec_block",
]

# Frozen + byte-stable (cacheable), same rules as the propose §9 contract in
# compact form. The per-round variables (current spec, issues, oracle) ride
# the user message.
SPEC_REPAIR_SYSTEM_PROMPT = """\
You repair the MAPPING SPEC of an RDF dataset design (the §9 artifact a
deterministic compiler turns into RML — you never write RML/Turtle).

You receive the current spec (YAML), a list of machine-detected issues, and a
closed reference menu (real files, real columns, vetted Tier-0 functions).
Return the corrected spec as a SINGLE JSON object — no markdown fence, no
prose, no comments. JSON is the same data model as the YAML you received.

Rules:
- Fix ONLY what the issues name; keep every other map/property row unchanged
  (same order, same values). Never drop a map or a property row unless an
  issue explicitly says to.
- Use ONLY filenames, column names and function names from the reference
  menu, exactly as written.
- Shape: version:1; prefixes; maps[] each {name, source, subject{template|
  constant, classes[], transform{}}, properties[]}. A property row has
  exactly ONE object form: column | columns | object_template | constant.
  'function' applies to column/columns only — never combined with
  object_template/constant. Constant args go in args:{} by name. 'function'
  never casts types: a bare column already emits a string literal; type a
  literal with datatype (e.g. xsd:double). Predicates/classes are plain
  CURIEs (no *, ?, + suffixes). Multi-valued cells use the multi-value
  functions (split / json_array / json_pluck) — one literal per element;
  irreducible structures go to a …Raw predicate with fallback:true.
- Keep each property's optional 'label' (human-readable meaning) and 'unit'
  (human-readable notation like µV/K) when present, and add them when an issue
  asks for a column's meaning/unit. These are DISPLAY METADATA only — never a
  value change and never a substitute for the unit-conversion functions.
"""


def build_spec_repair_user(spec_yaml: str, issues: list[str], oracle: str) -> str:
    """The per-round user message: current spec + issues + the closed menu."""
    bullets = "\n".join(f"- {i}" for i in issues)
    return (
        f"# Current mapping spec (YAML)\n\n{spec_yaml.strip()}\n\n"
        f"# Issues to fix (fix ONLY these)\n\n{bullets}\n\n"
        f"{oracle}\n\n"
        "Return the corrected mapping spec as a single JSON object."
    )


def carry_subject_labels(new_doc: dict, old_doc: object) -> dict:
    """再生成された骨格・IR に、直前の ``subject.label`` を map 名で決定論に戻す。

    種類の表示名は表示メタで、モデルは書き戻さない（書けても保証が無い）。
    答えの map に label が無く、同じ名前の map が直前の設計に label を持っていれば
    コピーする。答えが自分で label を書いていれば、それを保つ。新しい dict を返す。
    """
    if not isinstance(new_doc, dict) or not isinstance(old_doc, dict):
        return new_doc
    labels: dict[str, str] = {}
    for m in old_doc.get("maps") or []:
        if not isinstance(m, dict) or not isinstance(m.get("subject"), dict):
            continue
        label = m["subject"].get("label")
        if isinstance(label, str) and label.strip():
            labels.setdefault(str(m.get("name") or ""), label)
    if not labels or not isinstance(new_doc.get("maps"), list):
        return new_doc
    maps: list = []
    for m in new_doc["maps"]:
        if isinstance(m, dict):
            name = str(m.get("name") or "")
            subject = m.get("subject")
            if name in labels and (subject is None or isinstance(subject, dict)):
                subject = dict(subject or {})
                if not str(subject.get("label") or "").strip():
                    subject["label"] = labels[name]
                    m = {**m, "subject": subject}
        maps.append(m)
    return {**new_doc, "maps": maps}


def parse_spec_json(raw: str) -> str:
    """LLM output → §9 YAML block text.

    Accepts a bare JSON object (the guided path), a fenced block, or YAML (the
    ungated fallback path — YAML is a superset of JSON, so one parse covers
    both). Returns readable YAML for the document block. Raises ``ValueError``
    with a loop-feedable message when nothing parses.
    """
    import yaml

    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        closing = text.rfind("```")
        if first_nl != -1 and closing > first_nl:
            text = text[first_nl + 1 : closing].strip()
    try:
        doc = load_spec_yaml(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"the repaired mapping spec is not valid JSON/YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("the repaired mapping spec must be a single JSON object")
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def replace_mapping_spec_block(schema_md: str, new_spec_yaml: str) -> str:
    """Splice a new spec into the document's §9 yaml block, byte-preserving
    everything else. Uses the SAME extractor materialize uses to find the
    block, so what gets replaced is exactly what downstream would extract.

    Reads ``mapping_ir_source`` — the block as it literally appears — NOT
    ``mapping_ir_yaml``, which materialize re-serializes through its
    deterministic overlays (``enrich_units`` / ``apply_source_dialects``).
    PyYAML normalizes list indentation and quoting, so the overlaid text is not
    a substring of the document and every splice raised "could not locate the
    mapping-spec block". Live consequence (2026-08-17, XRD reference card): a
    ``unit:`` auto-filled from the column name ``RIR(I/Ic)`` was enough to make
    EVERY surgical repair round fail and be discarded — the loop then stopped on
    ``no_progress`` with the design unfixed, and the user clicked
    "AI に直してもらう" over and over with nothing changing."""
    mat = materialize_schema(schema_md, ".", "splice", write=False)
    current = mat.mapping_ir_source or mat.mapping_ir_yaml
    if current is None:
        raise ValueError("the document has no mapping-spec block to replace")
    if current not in schema_md:
        # extract_code_blocks joins body lines with \n — a document with \r\n
        # endings would not contain the body verbatim. Normalize and retry.
        schema_md = schema_md.replace("\r\n", "\n")
        if current not in schema_md:
            raise ValueError("could not locate the mapping-spec block in the document")
    return schema_md.replace(current, new_spec_yaml.strip("\n"), 1)
