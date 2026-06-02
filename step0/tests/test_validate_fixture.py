"""End-to-end validate against the committed starrydata-min fixture (CI gate).

This is the regression net for `csv2rdf-validate`: the fixture under
``tests/fixtures/starrydata_min/`` is a tiny, correct, starrydata-shaped bundle
that must pass T1-T7 (T8/T9 are opt-in). Running it in CI means any change that breaks a trap
checker - or the T1 ingester key recovery - fails the build, without needing the
full starrydata export or an API key.
"""
from __future__ import annotations

from pathlib import Path

from csv2rdf_step0.validate import SchemaBundle, validate_schema

FIXTURE = Path(__file__).parent / "fixtures" / "starrydata_min"


def _bundle(**overrides: object) -> SchemaBundle:
    base: dict[str, object] = {
        "tbox_ttl": FIXTURE / "tbox.ttl",
        "diagram_md": FIXTURE / "diagram.md",
        "mie_yaml": FIXTURE / "mie.yaml",
        "ingester_py": FIXTURE / "ingester.py",
        "source_csvs": [
            FIXTURE / "papers.csv",
            FIXTURE / "samples.csv",
            FIXTURE / "curves.csv",
        ],
    }
    base.update(overrides)
    return SchemaBundle(**base)  # type: ignore[arg-type]


def test_fixture_files_exist() -> None:
    for name in ("tbox.ttl", "diagram.md", "mie.yaml", "ingester.py",
                 "papers.csv", "samples.csv", "curves.csv"):
        assert (FIXTURE / name).is_file(), f"missing fixture file: {name}"


def test_fixture_bundle_passes_all_traps() -> None:
    report = validate_schema(_bundle())
    status = {r.trap_id: r.status for r in report.results}
    # T1-T7 must pass on a correct bundle; T8 (LLM) and T9 (no --rml) are opt-in.
    for trap in ("T1", "T2", "T3", "T4", "T5", "T6", "T7"):
        assert status[trap] == "pass", f"{trap} not pass: {status[trap]} - {report.results}"
    assert status["T8"] == "skip"
    assert status["T9"] == "skip"  # fixture bundle carries no RML mapping
    assert report.exit_code() == 0


def test_fixture_t1_recovers_keys_from_both_mie_and_ingester() -> None:
    """The composite keys should be attributed to *both* the MIE template and
    the ingester builder - proving the Round-3 safety net is wired in CI."""
    report = validate_schema(_bundle())
    t1 = next(r for r in report.results if r.trap_id == "T1")
    evidence = "\n".join(t1.evidence)
    assert "MIE template" in evidence
    assert "ingester" in evidence
    # All three entities routed to their matching CSV.
    assert "papers.csv: sdr:paper" in evidence
    assert "samples.csv: sdr:sample" in evidence
    assert "curves.csv: sdr:curve" in evidence


def test_fixture_t1_catches_single_key_ingester(tmp_path: Path) -> None:
    """If the ingester is broken to mint a single-key sample IRI, T1 must FAIL
    on the full fixture CSVs even though the MIE still documents the composite
    key. This is the safety net the CI fixture exists to guard."""
    broken = (FIXTURE / "ingester.py").read_text(encoding="utf-8").replace(
        'return SDR[f"sample/{sid}-{sample_id}"]',
        'return SDR[f"sample/{sample_id}"]',
    )
    bad_ingester = tmp_path / "ingester.py"
    bad_ingester.write_text(broken, encoding="utf-8")

    report = validate_schema(_bundle(ingester_py=bad_ingester))
    t1 = next(r for r in report.results if r.trap_id == "T1")
    assert t1.status == "fail", t1.detail
    # The failing key is the single-column (sample_id) one against samples.csv.
    assert any("collisions" in e and "(sample_id)" in e for e in t1.evidence)
    assert report.exit_code() == 1
