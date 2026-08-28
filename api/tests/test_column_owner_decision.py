"""Settling a duplicated column WITHOUT an AI round (ADR kantan K44 / G1).

A column two kinds both record is not a repair task. Which entity the column is
ABOUT is world knowledge — and where the rows can adjudicate at all, the answer
is already deterministic — so handing it back to the model just returns the same
advisory. Observed live 2026-08-28: eight presses of 「AI に直してもらう」 on the
same 「同じ列が 2 つのものに二重に書かれています」 card, 3 columns.

These pin the whole path the wizard's chooser rides: the finding arrives
MACHINE-READABLE from materialize, a human verdict removes the twin
deterministically, the advisory is gone on the re-check, and the verdict
survives a later round that puts the column back.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import (  # noqa: F401  (healthy_client is a fixture)
    _AUTH,
    _settings,
    healthy_client,
)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

# diameter is constant across the run: the ONE sample owns it (normalization),
# not each of the three readings. Both maps transcribe it — the live defect.
_CSV = (
    b"sample_id,reading_id,amplitude,diameter\n"
    b"s1,r1,1.5,5.0\n"
    b"s1,r2,2.5,5.0\n"
    b"s1,r3,3.5,5.0\n"
)

_DUP_MD = """## Schema proposal

### Class diagram
```mermaid
classDiagram
    class Reading
```

### MIE
```yaml
schema_info:
  title: Sensor readings
  keywords: [reading]
  categories: []
```

### Ingester
```python
import csv
def emit(path):
    open(path, encoding="utf-8-sig")
```

### 9. Declarative mapping spec
```yaml
version: 1
prefixes:
  sn: "https://example.com/sn#"
  snr: "https://example.com/sn/resource/"
maps:
  - name: sample
    source: readings.csv
    subject:
      template: "snr:sample/{sample_id}"
      classes: [sn:Sample]
    properties:
      - predicate: sn:hasReading
        object_template: "snr:reading/{reading_id}"
        object_type: iri
      - predicate: sn:diameter
        column: diameter
  - name: reading
    source: readings.csv
    subject:
      template: "snr:reading/{reading_id}"
      classes: [sn:Reading]
    properties:
      - predicate: sn:amplitude
        column: amplitude
      - predicate: sn:readingDiameter
        column: diameter
```
"""


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def _stage(client: TestClient) -> str:
    r = client.post("/api/staging", files={"files": ("readings.csv", _CSV, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["staging_id"]


def _materialize(client: TestClient, staging_id: str, dataset_id: str | None = None) -> dict:
    body: dict = {
        "proposal_md": _DUP_MD,
        "dataset_name": "sensor",
        "staging_id": staging_id,
    }
    if dataset_id:
        body["dataset_id"] = dataset_id
    r = client.post("/api/materialize", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _duplicate_advisories(advisories: list[str]) -> list[str]:
    return [a for a in advisories if "plain datatype property by" in a]


def _columns(ir: dict, map_name: str) -> list[str]:
    m = next(m for m in ir["maps"] if m["name"] == map_name)
    return [str(p.get("column")) for p in m["properties"] if p.get("column")]


def test_materialize_hands_over_the_choice_not_just_the_sentence(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        result = _materialize(client, _stage(client))
        assert len(_duplicate_advisories(result["advisories"])) == 1
        (finding,) = result["duplicate_columns"]
        assert finding["source"] == "readings.csv"
        assert finding["column"] == "diameter"
        assert finding["actionable"] is True
        # Both candidates, with the count that makes the choice answerable.
        assert [(m["map"], m["label"], m["entities"]) for m in finding["maps"]] == [
            ("ReadingMap", "Reading", 3),
            ("SampleMap", "Sample", 1),
        ]
        # One value for the whole run -> the single Sample owns it.
        assert finding["owner"] == "SampleMap"
        # The sentence and the structure come from one pass and cannot disagree.
        assert finding["text"] in result["advisories"]


def test_a_human_verdict_removes_the_twin_and_clears_the_advisory(
    tmp_path: Path, healthy_client
) -> None:
    """The acceptance condition: settle it, re-check, the finding is gone —
    with the source still only STAGED (the wizard decides at S5, before attach),
    and with no LLM anywhere in the path."""
    with _client(tmp_path, healthy_client) as client:
        staging_id = _stage(client)
        ds_id = _materialize(client, staging_id)["dataset"]["id"]
        r = client.post(
            f"/api/datasets/{ds_id}/column-decisions",
            json={
                "staging_id": staging_id,
                # The human overrules the machine's recommendation (SampleMap):
                # the verdict on screen is theirs, not a confirmation dialog.
                # `/rules`-style compiled id, like the include path accepts.
                "decisions": [
                    {
                        "source": "readings.csv",
                        "column": "diameter",
                        "action": "own",
                        "map": "ReadingMap",
                    }
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["changed"] == ["diameter"]
        data = client.get(f"/api/datasets/{ds_id}").json()
        ir = yaml.safe_load(data["artifacts"]["mapping.yaml"])
        assert _columns(ir, "reading") == ["amplitude", "diameter"]
        assert _columns(ir, "sample") == []  # the link row stays, the copy is gone
        assert _duplicate_advisories(data["meta"]["advisories"]) == []
        # …and the finding no longer arrives as a choice either.
        again = _materialize(client, staging_id, ds_id)
        assert again["duplicate_columns"] == []
        assert _duplicate_advisories(again["advisories"]) == []


def test_the_verdict_survives_a_later_round_that_puts_the_column_back(
    tmp_path: Path, healthy_client
) -> None:
    """A refine round rewrites §9 from memory and the twin comes back. A fact a
    human established must not depend on which round happened to be last (ADR
    data-facts-invariant) — materialize re-asserts it, so the advisory does not
    return and the person is not asked the same question twice."""
    with _client(tmp_path, healthy_client) as client:
        staging_id = _stage(client)
        ds_id = _materialize(client, staging_id)["dataset"]["id"]
        client.post(
            f"/api/datasets/{ds_id}/column-decisions",
            json={
                "staging_id": staging_id,
                "decisions": [
                    {
                        "source": "readings.csv",
                        "column": "diameter",
                        "action": "own",
                        "map": "sample",
                    }
                ],
            },
        )
        # The original markdown IS the "model wrote it again" case.
        result = _materialize(client, staging_id, ds_id)
        assert result["duplicate_columns"] == []
        ir = yaml.safe_load(result["artifacts"]["mapping.yaml"])
        assert _columns(ir, "sample") == ["diameter"]
        assert _columns(ir, "reading") == ["amplitude"]


def test_an_owner_verdict_needs_a_map_and_a_known_one(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        staging_id = _stage(client)
        ds_id = _materialize(client, staging_id)["dataset"]["id"]
        base = {"source": "readings.csv", "column": "diameter", "action": "own"}
        no_map = client.post(
            f"/api/datasets/{ds_id}/column-decisions",
            json={"staging_id": staging_id, "decisions": [base]},
        )
        assert no_map.status_code == 422
        unknown = client.post(
            f"/api/datasets/{ds_id}/column-decisions",
            json={"staging_id": staging_id, "decisions": [{**base, "map": "nope"}]},
        )
        assert unknown.status_code == 422
