"""``registry.extract_classes`` — display names, not flattened Mermaid ids.

Motivating bug (real registry dataset ``xrd-21db86e4``): a class named
``試料`` (non-ASCII) gets a Mermaid-safe id (``_safe_ident`` flattens every
non-ASCII char to ``_``, e.g. ``__``). Before this fix, ``extract_classes``
pulled that flattened id straight out of the diagram, so ``meta.classes`` —
what the Gallery card / dataset "中身" tab actually display — showed ``__``
instead of the class's real name. ir2mermaid now emits the real name as a
Mermaid display label (``class Id["試料"]``) when it differs from the id;
these tests pin that ``extract_classes`` prefers the label over the id, and
falls back to the id unchanged for diagrams that carry no label (byte-for-byte
what every ASCII-only dataset produced before this field existed).
"""

from __future__ import annotations

from asterism_api.registry import extract_classes


def test_prefers_display_label_over_flattened_id() -> None:
    mermaid = (
        "classDiagram\n"
        '    class __["試料"] {\n'
        "        +name xsd_string\n"
        "    }\n"
    )
    assert extract_classes(mermaid) == ["試料"]


def test_falls_back_to_id_when_no_label() -> None:
    """ASCII-only diagrams (no label emitted) behave exactly as before."""
    mermaid = "classDiagram\n    class Sample {\n        +name xsd_string\n    }\n"
    assert extract_classes(mermaid) == ["Sample"]


def test_multiple_classes_mixed_labelled_and_bare() -> None:
    mermaid = (
        "classDiagram\n"
        '    class ____["ピーク値"] {\n'
        "    }\n"
        "    class Sample {\n"
        "    }\n"
    )
    assert extract_classes(mermaid) == ["ピーク値", "Sample"]


def test_legacy_meta_with_flattened_underscores_does_not_crash() -> None:
    """An OLD registry entry's diagram (pre-fix, no label) still extracts cleanly."""
    mermaid = "classDiagram\n    class __ {\n    }\n    class ____ {\n    }\n"
    assert extract_classes(mermaid) == ["__", "____"]
