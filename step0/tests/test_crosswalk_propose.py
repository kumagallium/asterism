"""AI crosswalk-mapping draft: propose_crosswalk_mapping suggests the
concept-bearing predicate per dataset. The LLM is faked here (no network)."""
from __future__ import annotations

from asterism_step0.crosswalk_propose import _SYSTEM, propose_crosswalk_mapping

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


def test_language_rides_user_message_only() -> None:
    """language= appends the Output-language block to the USER message; the
    cacheable system prompt stays byte-stable (prompt-caching contract)."""
    llm = _FakeLLM('{"participants": []}')
    propose_crosswalk_mapping(llm, concept="composition", datasets=_DATASETS, language="ja")
    system, user = llm.calls[0]
    assert "# Output language" in user
    assert "Japanese (日本語)" in user
    assert system == _SYSTEM
    assert "# Output language" not in system


def test_no_language_keeps_legacy_message() -> None:
    llm = _FakeLLM('{"participants": []}')
    propose_crosswalk_mapping(llm, concept="composition", datasets=_DATASETS)
    assert "# Output language" not in llm.calls[0][1]


def test_propose_keeps_the_kind_the_candidate_was_listed_under() -> None:
    """The same predicate can carry different things on different kinds (rdfs:label
    on Composition vs on Doi), so a pick is a (predicate, kind): the kind the model
    copied back, or — when it omitted one — the first kind the predicate was listed
    under; an untyped listing keeps no kind (crosswalk-kind-scoped-fields.md)."""
    rdfs_label = "http://www.w3.org/2000/01/rdf-schema#label"
    comp, doi = "https://x.invalid/o#Composition", "https://x.invalid/o#Doi"
    datasets = [
        {
            "dataset_id": "ds-k",
            "label": "k",
            "predicates": [
                {"iri": rdfs_label, "sample": "Bi2Te3", "subject_class": comp},
                {"iri": rdfs_label, "sample": "10.1000/x", "subject_class": doi},
            ],
        },
        {
            "dataset_id": "ds-b",
            "label": "b",
            "predicates": [{"iri": "https://x.invalid/o#comp", "sample": "Bi2Te3"}],
        },
    ]
    answer = (
        '{"participants": ['
        f'{{"dataset_id": "ds-k", "predicate": "{rdfs_label}", "subject_class": "{doi}", '
        '"why": "x"}, {"dataset_id": "ds-b", "predicate": "https://x.invalid/o#comp", "why": "y"}]}'
    )
    out = propose_crosswalk_mapping(_FakeLLM(answer), concept="composition", datasets=datasets)
    by_id = {p["dataset_id"]: p for p in out}
    assert by_id["ds-k"]["subject_class"] == doi  # the model's (listed) kind is kept
    assert "subject_class" not in by_id["ds-b"]
    # Omitted kind -> the first kind listed for that predicate (the busiest).
    answer2 = (
        f'{{"participants": [{{"dataset_id": "ds-k", "predicate": "{rdfs_label}", "why": "x"}}]}}'
    )
    out2 = propose_crosswalk_mapping(_FakeLLM(answer2), concept="composition", datasets=datasets)
    assert out2[0]["subject_class"] == comp
    # The prompt shows the kind beside the predicate so it CAN be copied verbatim.
    llm = _FakeLLM('{"participants": []}')
    propose_crosswalk_mapping(llm, concept="composition", datasets=datasets)
    assert f"kind: {comp}" in llm.calls[0][1]
