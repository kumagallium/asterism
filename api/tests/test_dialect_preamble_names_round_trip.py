"""The design → RML → ingest round trip for a human-chosen preamble name.

It lives HERE, not in ``ingest/tests``: the round trip needs both the design-side
compiler (``asterism_step0``) and the runtime twin (``asterism``), and the ingest
package deliberately does not depend on step0 — an ingest test that imported it
passed locally (a PYTHONPATH with both) and failed in CI, whose ingest venv holds
only ingest (2026-08-20). The api venv is where the two layers legitimately meet.

The ingest side's own half — reading the annotation, and broadcasting the renamed
column — is covered without step0 in ``ingest/tests/test_dialect.py``.
"""

from __future__ import annotations

from pathlib import Path

import rdflib
from asterism.dialect import dialect_rows, dialects_from_mapping
from asterism_step0.mapping_ir import parse_mapping_ir
from asterism_step0.rml_compile import compile_mapping_ir

_IR_YAML = """\
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
dialects:
  "m.txt":
    encoding: cp932
    delimiter: "\\t"
    skip_rows: 1
    preamble: lines
    preamble_names: {"preamble_1": "試料名"}
maps:
  - name: point
    source: m.txt
    subject:
      template: "exr:point/{angle}"
      classes: [ex:Point]
    properties:
      - predicate: ex:intensity
        column: intensity
"""


def test_a_human_chosen_preamble_name_survives_to_the_header(tmp_path: Path) -> None:
    ttl = compile_mapping_ir(parse_mapping_ir(_IR_YAML))
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    dialects = dialects_from_mapping(g)
    (d,) = dialects.values()
    assert d.preamble_names == {"preamble_1": "試料名"}

    src = tmp_path / "m.txt"
    lines = ["サンプル名: 試料A", "2θ (deg)\t強度 (cps)", "10.02\t123"]
    src.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")
    rows = list(dialect_rows(src, d))
    # The header the design sees carries the person's word, not the machine's.
    assert rows[0] == ["2θ (deg)", "強度 (cps)", "試料名"]
    assert "preamble_1" not in rows[0]
