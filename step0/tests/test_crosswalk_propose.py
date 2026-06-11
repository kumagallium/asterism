"""AI crosswalk-mapping draft: propose_crosswalk_mapping suggests the
concept-bearing predicate per dataset. The LLM is faked here (no network)."""
from __future__ import annotations

from asterism_step0.crosswalk_propose import propose_crosswalk_mapping

_DATASETS = [
    {
        "dataset_id": "starrydata-1",
        "label": "starrydata",
        "predicates": [
            {"iri": "https://ex/sd#compositionString", "sample": "Bi2Te3"},
            {"iri": "https://ex/sd#sampleId", "sample": "S-0001"},
        ],
    },
    {
        "dataset_id": "mp-2",
        "label": "materials_project",
        "predicates": [
            {"iri": "https://ex/mp#formula", "sample": "Bi2 Te3"},
            {"iri": "https://ex/mp#bandgap", "sample": "0.15"},
        ],
    },
]


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


def test_suggests_composition_predicate_per_dataset() -> None:
    resp = (
        '{"participants": ['
        '{"dataset_id": "starrydata-1",'
        ' "predicate": "https://ex/sd#compositionString", "why": "formula strings"},'
        '{"dataset_id": "mp-2", "predicate": "https://ex/mp#formula", "why": "formula"}]}'
    )
    out = propose_crosswalk_mapping(_FakeLLM(resp), concept="composition", datasets=_DATASETS)
    assert {p["dataset_id"]: p["predicate"] for p in out} == {
        "starrydata-1": "https://ex/sd#compositionString",
        "mp-2": "https://ex/mp#formula",
    }


def test_drops_invented_predicate_not_in_candidates() -> None:
    # The model returns an IRI that is NOT a candidate for mp-2 -> dropped (guard
    # against an invented IRI that would join nothing).
    resp = (
        '{"participants": ['
        '{"dataset_id": "starrydata-1",'
        ' "predicate": "https://ex/sd#compositionString", "why": "ok"},'
        '{"dataset_id": "mp-2", "predicate": "https://ex/mp#INVENTED", "why": "guess"}]}'
    )
    out = propose_crosswalk_mapping(_FakeLLM(resp), concept="composition", datasets=_DATASETS)
    assert [p["dataset_id"] for p in out] == ["starrydata-1"]


def test_tolerates_code_fence_and_passes_samples() -> None:
    resp = '```json\n{"participants": []}\n```'
    llm = _FakeLLM(resp)
    out = propose_crosswalk_mapping(llm, concept="composition", datasets=_DATASETS)
    assert out == []
    user = llm.calls[0][1]
    # samples + candidate IRIs reached the model
    assert "Bi2Te3" in user and "compositionString" in user
