"""Tests for propose-time schema grounding (asterism.grounding.ground_model_yaml).

It reads an rdf-config model.yaml and, for each MINTED class/predicate, suggests the
matching famous-standard candidates from the closed catalog — so the AI design surfaces
"your data could lean on this standard". Same invariants as the term search: closed-set
(no fabrication) + deterministic; here also: reused (known-prefix) terms are skipped and
only confident candidates surface.
"""

from __future__ import annotations

from asterism.grounding import ground_model_yaml
from asterism.grounding.catalog import _all_terms

# A compact stand-in for a materials propose §6 model.yaml.
MODEL_YAML = """
- Material <https://example.org/asterism/mp/resource/material/mp-1>:
    - a: mp:Material
    - mp:formula:
        - formula: "Bi2Te3"
    - schema:url?:
        - mp_url: <https://example.org/mp-1>
    - mp:hasCrystalStructure:
        - structure: CrystalStructure
- CrystalStructure <https://example.org/asterism/mp/resource/structure/mp-1>:
    - a: mp:CrystalStructure
    - mp:spaceGroupSymbol:
        - sg: "R-3m"
"""


def _by_curie(groundings):
    return {g.source_curie: g for g in groundings}


def test_class_grounds_to_cmso_exact_first() -> None:
    g = _by_curie(ground_model_yaml(MODEL_YAML))
    assert "mp:Material" in g
    assert g["mp:Material"].kind == "class"
    assert g["mp:Material"].candidates[0].iri == "http://purls.helmholtz-metadaten.de/cmso/Material"
    assert "mp:CrystalStructure" in g
    assert (
        g["mp:CrystalStructure"].candidates[0].curie == "cmso:CrystalStructure"
    )


def test_predicate_grounds() -> None:
    g = _by_curie(ground_model_yaml(MODEL_YAML))
    assert "mp:spaceGroupSymbol" in g
    assert g["mp:spaceGroupSymbol"].kind == "property"
    assert any(c.curie == "cmso:hasSpaceGroupSymbol" for c in g["mp:spaceGroupSymbol"].candidates)


def test_reused_known_prefix_terms_are_skipped() -> None:
    """schema:url is already reused — it must not be offered for grounding."""
    curies = {g.source_curie for g in ground_model_yaml(MODEL_YAML)}
    assert not any(c.startswith("schema:") for c in curies)


def test_candidates_are_a_closed_set() -> None:
    catalog = {t.iri for t in _all_terms()}
    for g in ground_model_yaml(MODEL_YAML):
        for c in g.candidates:
            assert c.iri in catalog


def test_min_score_drops_weak_overlap() -> None:
    """A high cutoff yields only exact-ish matches; a low one lets weak overlaps in."""
    strict = ground_model_yaml(MODEL_YAML, min_score=90)
    for g in strict:
        assert all(c.score >= 90 for c in g.candidates)
    loose = ground_model_yaml(MODEL_YAML, min_score=10)
    assert sum(len(g.candidates) for g in loose) >= sum(len(g.candidates) for g in strict)


def test_optional_marker_stripped() -> None:
    y = """
- Thing <https://example.org/x>:
    - a: x:Material
    - x:spaceGroupNumber?:
        - n: 166
"""
    g = _by_curie(ground_model_yaml(y))
    assert "x:spaceGroupNumber" in g  # the "?" is not part of the name


def test_deterministic() -> None:
    a = [(g.source_curie, [c.iri for c in g.candidates]) for g in ground_model_yaml(MODEL_YAML)]
    b = [(g.source_curie, [c.iri for c in g.candidates]) for g in ground_model_yaml(MODEL_YAML)]
    assert a == b


def test_malformed_or_empty_yaml_is_safe() -> None:
    assert ground_model_yaml("") == []
    assert ground_model_yaml("not: [a, valid: model") == []  # YAML error -> empty
    assert ground_model_yaml("just a scalar") == []  # not a list -> empty


def test_to_dict_shape() -> None:
    g = ground_model_yaml(MODEL_YAML)[0]
    d = g.to_dict()
    assert set(d) == {"name", "kind", "source_curie", "candidates"}
    assert d["candidates"] and set(d["candidates"][0]) >= {"iri", "curie", "score"}
