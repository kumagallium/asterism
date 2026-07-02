"""AI-assisted draft of a CROSSWALK mapping (productize ④ — 手動選択 + AI 補助).

A crosswalk hub joins datasets on a shared concept (e.g. composition). The mapping
that must be human-vetted is: *which predicate in each dataset carries that concept's
value* (starrydata → ``sd:compositionString``, Materials Project → ``mp:formula``).
Given each selected dataset's literal-valued predicates + a sample value, an LLM
suggests that predicate per dataset.

The result is a PROPOSAL, not a built hub: the human reviews/edits the per-dataset
predicate in the authoring UI and then builds (the human review is the vet gate, as
for any crosswalk mapping claim). This module only lowers the authoring barrier;
nothing is auto-built and nothing is executed.
"""
from __future__ import annotations

import json
import re

from asterism_step0.language import language_instruction
from asterism_step0.llm import LLMClient, as_completion

_SYSTEM = """\
You map datasets onto a SHARED CROSSWALK CONCEPT. For each dataset you are given its
literal-valued predicates (full IRIs) with a sample value; pick the ONE predicate
whose values express the target concept (e.g. for "composition": a chemical formula /
composition string like "Bi2Te3", "Ba8Ga16Ge30", "ZnFe2O4").

Output ONLY a single JSON object (no prose, no markdown fence):
  {"participants": [{"dataset_id": "<id>", "predicate": "<full IRI>",
                     "why": "<short reason, <=12 words>"}, ...]}

Hard rules:
- Use ONLY a predicate IRI that appears in that dataset's candidate list — copy it
  verbatim. NEVER invent or guess an IRI (an invented IRI joins nothing).
- Choose by the SAMPLE VALUES, not just the name: the values must look like the
  concept (e.g. a composition string), not an id, a number, a date, or a URL.
- If NO candidate predicate plausibly carries the concept for a dataset, OMIT that
  dataset from "participants" (do not force a wrong mapping).
- Output the JSON object and NOTHING else.
"""


def _user_message(concept: str, datasets: list[dict]) -> str:
    parts = [f"Target shared concept: {concept.strip() or 'composition'}\n"]
    for d in datasets:
        parts.append(f"Dataset id: {d.get('dataset_id')}  (label: {d.get('label', '')})")
        preds = d.get("predicates") or []
        if not preds:
            parts.append("  (no literal-valued predicates found)")
        for p in preds:
            sample = str(p.get("sample", ""))
            if len(sample) > 60:
                sample = sample[:57] + "…"
            parts.append(f"  - {p.get('iri')}   e.g. {sample!r}")
        parts.append("")
    return "\n".join(parts)


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> dict:
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i == -1 or j <= i:
            raise ValueError("the model did not return a JSON object") from None
        obj = json.loads(t[i : j + 1])
    if not isinstance(obj, dict):
        raise ValueError("the model did not return a JSON object")
    return obj


def propose_crosswalk_mapping(
    llm: LLMClient, *, concept: str, datasets: list[dict], language: str | None = None
) -> list[dict]:
    """Suggest the concept-bearing predicate per dataset.

    ``datasets`` is ``[{"dataset_id", "label", "predicates": [{"iri", "sample"}]}]``.
    Returns a list of ``{"dataset_id", "predicate", "why"}`` — only entries whose
    predicate is one of that dataset's candidate IRIs (a guard against an invented
    IRI), so the UI can pre-fill its dropdowns safely. Never raises on an
    unmappable dataset (it is simply omitted).

    ``language`` (e.g. ``"ja"``) switches the human-readable ``why`` reasons;
    ``dataset_id`` and the predicate IRIs are copied verbatim (machine-matched
    against the candidates). The directive rides the user message only, so
    ``_SYSTEM`` stays byte-stable for prompt caching (see
    :mod:`asterism_step0.language`). ``None`` → English.
    """
    if not datasets:
        return []
    candidates = {
        str(d.get("dataset_id")): {str(p.get("iri")) for p in (d.get("predicates") or [])}
        for d in datasets
    }
    user_message = _user_message(concept, datasets)
    lang_block = language_instruction(language)
    if lang_block:
        user_message += (
            f"\n{lang_block}\n\n"
            'In THIS JSON draft the human-readable prose means the "why" values:\n'
            'write those in the language above. "dataset_id" and the predicate\n'
            "IRIs are copied verbatim from the candidates (never translated).\n"
        )
    text = as_completion(llm.complete(_SYSTEM, user_message)).text
    obj = _extract_json_object(text)
    out: list[dict] = []
    for entry in obj.get("participants") or []:
        if not isinstance(entry, dict):
            continue
        dsid = str(entry.get("dataset_id") or "")
        pred = str(entry.get("predicate") or "")
        if dsid in candidates and pred in candidates[dsid]:
            out.append({"dataset_id": dsid, "predicate": pred, "why": str(entry.get("why") or "")})
    return out
