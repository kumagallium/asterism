"""Tests for ``asterism-metadata migrate`` (ADR dataset-description-in-the-store.md
§8, spec PR5 §A/§B).

A tmp registry with four dataset shapes (normal / no mie.yaml / broken YAML /
unknown-keys-heavy) exercises the CLI's own decisions — what it writes, what it
refuses to write, and when it talks to the store — while the semantics of the
build/project round trip themselves stay covered by ``tests/test_metadata.py``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from asterism import metadata as m
from asterism import metadata_cli as cli

pyoxigraph = pytest.importorskip("pyoxigraph")


# ----------------------------------------------------------------------------
# Fixtures: a tmp registry with the four required dataset shapes
# ----------------------------------------------------------------------------

_NORMAL_DOC = {
    "schema_info": {
        "description": "A normal, fully-modeled MIE.",
        "keywords": ["thermoelectric", "sample", "curve"],
        "categories": ["materials-science"],
    },
    "sparql_query_examples": [
        {"title": "Count samples", "description": "How many.", "query": "SELECT * WHERE {}"},
    ],
    "anti_patterns": "Do not mint bare sample IRIs; sample_id collides across papers.",
    "architectural_notes": "Why: source data has no global key. Alternatives: none.",
}

_UNKNOWN_HEAVY_DOC = {
    "schema_info": {"title": "Unknown-key dataset", "version": "2.1", "license": "CC-BY-4.0"},
    "common_errors": "a free-text paragraph about frequent mistakes",
    "cross_references": [
        {"source_property": "schema:identifier", "target_db": "DOI", "description": "…"}
    ],
    "data_statistics": {"papers": 10, "samples": 200, "note": "as of last ingest"},
}

_BROKEN_YAML = "schema_info: {title: 'unterminated\n  keywords: [a, b\n"


def _write_dataset(
    registry: Path,
    dataset_id: str,
    *,
    mie_doc: object = "__omit__",
    mie_text: str | None = None,
    promoted: bool = False,
    rml_ttl: str | None = None,
) -> Path:
    d = registry / dataset_id
    d.mkdir(parents=True)
    (d / cli._META_FILE).write_text(
        json.dumps({"id": dataset_id, "promoted": promoted}), encoding="utf-8"
    )
    if mie_text is not None:
        (d / cli._MIE_FILE).write_text(mie_text, encoding="utf-8")
    elif mie_doc != "__omit__":
        (d / cli._MIE_FILE).write_text(m.dump_mie_yaml(mie_doc), encoding="utf-8")
    if rml_ttl is not None:
        (d / cli._RML_FILE).write_text(rml_ttl, encoding="utf-8")
    return d


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    reg = tmp_path / "registry"
    reg.mkdir()
    _write_dataset(reg, "ds-normal", mie_doc=_NORMAL_DOC, promoted=True)
    _write_dataset(reg, "ds-nomie", mie_doc="__omit__")  # no mie.yaml at all
    _write_dataset(reg, "ds-badyaml", mie_text=_BROKEN_YAML)
    _write_dataset(reg, "ds-unknown", mie_doc=_UNKNOWN_HEAVY_DOC, promoted=False)
    # A dataset directory the CLI must never touch: no meta.json.
    (reg / "ds-no-meta").mkdir()
    (reg / "ds-no-meta" / cli._MIE_FILE).write_text(m.dump_mie_yaml(_NORMAL_DOC), encoding="utf-8")
    # A history/ snapshot under a real dataset, with its own mie.yaml — must
    # never be visited (spec §A: "history/ は触らない").
    hist = reg / "ds-normal" / "history" / "20260101T000000Z"
    hist.mkdir(parents=True)
    (hist / cli._MIE_FILE).write_text("poisoned: true\n", encoding="utf-8")
    # Non-dataset siblings real registries carry (catalog.py's own convention).
    (reg / "_staging").mkdir()
    (reg / "_usage").mkdir()
    return reg


def _outcome_lines(out: str) -> dict[str, str]:
    """Map dataset id -> full status line (everything after the id column)."""
    lines: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].startswith("ds-"):
            lines[parts[0]] = parts[1]
    return lines


class _FakeStoreClient:
    """Records DROP / POST calls; implements asterism.metadata.SupportsMetadataStore."""

    def __init__(self) -> None:
        self.drops: list[str] = []
        self.posts: list[tuple[bytes, str | None]] = []

    async def sparql_update(self, update: str) -> None:
        self.drops.append(update)

    async def sparql_construct(self, query: str) -> str:  # pragma: no cover - unused by migrate
        return ""

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        self.posts.append((payload, graph_iri))
        return len(payload)


# ----------------------------------------------------------------------------
# Dry-run: never writes anything
# ----------------------------------------------------------------------------


def test_dry_run_writes_nothing(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = {
        p: p.read_bytes() for p in registry.rglob("*") if p.is_file()
    }

    code = cli._run(registry, apply=False, store_url=None, only=None)

    out = capsys.readouterr().out
    lines = _outcome_lines(out)
    assert lines["ds-normal"].startswith("triples=") and "ok" in lines["ds-normal"]
    assert lines["ds-nomie"] == "triples=0  sections=[]  skip (no mie.yaml)"
    assert "error (unparseable)" in lines["ds-badyaml"]
    assert "ok" in lines["ds-unknown"]
    assert "ds-no-meta" not in lines  # no meta.json -> never a candidate

    # nothing on disk changed, and no new files (metadata.ttl / .authored) appeared
    after = {p: p.read_bytes() for p in registry.rglob("*") if p.is_file()}
    assert after == before
    for ds in ("ds-normal", "ds-unknown"):
        assert not (registry / ds / cli._METADATA_TTL_FILE).exists()
        assert not (registry / ds / cli._MIE_AUTHORED_FILE).exists()

    # exit code reflects the one real error (ds-badyaml), not the skip
    assert code == 1


def test_history_snapshot_never_visited(registry: Path) -> None:
    """The poisoned mie.yaml planted under ds-normal/history/... must never be
    read or written to, even with --apply."""
    poisoned = registry / "ds-normal" / "history" / "20260101T000000Z" / cli._MIE_FILE
    before = poisoned.read_bytes()
    cli._run(registry, apply=True, store_url=None, only=None)
    assert poisoned.read_bytes() == before
    hist_dir = registry / "ds-normal" / "history" / "20260101T000000Z"
    assert not (hist_dir / cli._METADATA_TTL_FILE).exists()


# ----------------------------------------------------------------------------
# --apply: the three files, per spec §A.6
# ----------------------------------------------------------------------------


def test_apply_writes_three_files_for_lossless_datasets(registry: Path) -> None:
    code = cli._run(registry, apply=True, store_url=None, only=None)
    assert code == 1  # ds-badyaml is still an error

    for ds in ("ds-normal", "ds-unknown"):
        d = registry / ds
        assert (d / cli._MIE_AUTHORED_FILE).is_file()
        assert (d / cli._METADATA_TTL_FILE).is_file()
        assert (d / cli._MIE_FILE).is_file()
        # metadata.ttl actually parses as Turtle and is non-trivial
        ttl = (d / cli._METADATA_TTL_FILE).read_text(encoding="utf-8")
        assert len(m.graph_from_turtle(ttl)) > 0

    # mie.yaml is now the *projected* document, not the original bytes
    projected_text = (registry / "ds-normal" / cli._MIE_FILE).read_text(encoding="utf-8")
    projected_doc = yaml.safe_load(projected_text)
    assert m.normalize_document(projected_doc) == m.normalize_document(_NORMAL_DOC)

    # datasets that never got a mie.yaml or failed to parse get none of the three
    for ds in ("ds-nomie", "ds-badyaml"):
        d = registry / ds
        assert not (d / cli._MIE_AUTHORED_FILE).exists()
        assert not (d / cli._METADATA_TTL_FILE).exists()


def test_apply_not_lossless_shows_diff_and_does_not_write(
    registry: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The CLI's own reaction to a lossy round trip: refuse to write, show a
    diff, report the dataset as an error. Forced via monkeypatch — the round
    trip itself is proven lossless for every real shape in test_metadata.py;
    this exercises the CLI's guard, not the compiler's correctness."""

    def _lossy_project(graph: object, dataset_id: str) -> dict[str, object]:
        return {"schema_info": {"description": "SOMETHING ELSE ENTIRELY"}}

    monkeypatch.setattr(cli.m, "project_mie_document", _lossy_project)

    caplog.set_level(logging.WARNING)
    code = cli._run(registry, apply=True, store_url=None, only={"ds-normal"})

    assert code == 1
    d = registry / "ds-normal"
    assert not (d / cli._METADATA_TTL_FILE).exists()
    assert not (d / cli._MIE_AUTHORED_FILE).exists()
    # original mie.yaml is untouched
    assert "SOMETHING ELSE ENTIRELY" not in (d / cli._MIE_FILE).read_text(encoding="utf-8")
    assert any("not lossless" in r.message for r in caplog.records)
    assert any("SOMETHING ELSE ENTIRELY" in r.message for r in caplog.records)  # the diff


def test_apply_never_overwrites_existing_authored_file(registry: Path) -> None:
    d = registry / "ds-normal"
    preserved = "# hand-preserved original, do not touch\n"
    (d / cli._MIE_AUTHORED_FILE).write_text(preserved, encoding="utf-8")

    cli._run(registry, apply=True, store_url=None, only={"ds-normal"})

    assert (d / cli._MIE_AUTHORED_FILE).read_text(encoding="utf-8") == preserved


# ----------------------------------------------------------------------------
# Idempotency: a second --apply changes nothing
# ----------------------------------------------------------------------------


def test_second_apply_is_a_no_op(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code1 = cli._run(registry, apply=True, store_url=None, only=None)
    capsys.readouterr()  # discard first run's output
    snapshot = {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in registry.rglob("*") if p.is_file()
    }

    code2 = cli._run(registry, apply=True, store_url=None, only=None)
    out2 = capsys.readouterr().out
    lines2 = _outcome_lines(out2)

    assert code1 == code2 == 1  # ds-badyaml is a stable error both times
    for ds in ("ds-normal", "ds-unknown"):
        assert "unchanged" in lines2[ds]

    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in registry.rglob("*") if p.is_file()}
    assert after == snapshot  # byte-for-byte AND mtime-for-mtime: nothing was rewritten


# ----------------------------------------------------------------------------
# --store: only promoted datasets reach the store
# ----------------------------------------------------------------------------


def test_store_writes_only_promoted_datasets(registry: Path) -> None:
    fake = _FakeStoreClient()

    code = cli._run(registry, apply=True, store_url=None, only=None, client=fake)

    assert code == 1  # ds-badyaml still errors
    # ds-normal is promoted=True -> exactly one drop + one post for it
    assert len(fake.drops) == 1
    assert len(fake.posts) == 1
    payload, graph_iri = fake.posts[0]
    assert graph_iri is not None
    from asterism import substrate

    assert graph_iri == substrate.meta_graph_iri("ds-normal")
    assert len(m.graph_from_turtle(payload.decode("utf-8"))) > 0


def test_store_skips_unpromoted_dataset(registry: Path) -> None:
    fake = _FakeStoreClient()
    # ds-unknown is promoted=False; migrating it alone must never touch the store.
    cli._run(registry, apply=True, store_url=None, only={"ds-unknown"}, client=fake)
    assert fake.drops == []
    assert fake.posts == []


def test_store_never_touched_on_dry_run(registry: Path) -> None:
    fake = _FakeStoreClient()
    cli._run(registry, apply=False, store_url=None, only=None, client=fake)
    assert fake.drops == []
    assert fake.posts == []


class _UnreachableStoreClient:
    """Simulates a store that cannot be reached at all — every call raises,
    the way ``httpx.ConnectError`` would for a dead ``--store`` URL."""

    async def sparql_update(self, update: str) -> None:
        raise ConnectionError("[Errno 61] Connection refused")

    async def sparql_construct(self, query: str) -> str:  # pragma: no cover - unused by migrate
        return ""

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        raise AssertionError("post_turtle_bytes must not be reached when the DROP itself fails")


def test_store_unreachable_does_not_crash_or_abort_other_datasets(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store the CLI cannot reach must mark only the promoted dataset it was
    trying to write as an error — never raise an uncaught exception, and never
    stop datasets alphabetically after it (ds-nomie, ds-unknown) from being
    migrated and reported. ds-normal (the only promoted dataset, and the one
    the fake client fails for) is processed before both of them."""
    fake = _UnreachableStoreClient()

    code = cli._run(registry, apply=True, store_url=None, only=None, client=fake)

    assert code == 1
    out = capsys.readouterr().out
    lines = _outcome_lines(out)

    # Every dataset still got exactly one reported line — nothing was silently
    # dropped by the failure on ds-normal.
    assert set(lines) == {"ds-badyaml", "ds-normal", "ds-nomie", "ds-unknown"}
    assert "store-error" in lines["ds-normal"]
    assert "Connection refused" in lines["ds-normal"]
    assert "written" in lines["ds-unknown"]  # apply=True: not the promoted dataset -> no store call
    assert lines["ds-nomie"] == "triples=0  sections=[]  skip (no mie.yaml)"

    # The local files were still written for ds-normal before the store call
    # was attempted — a dead store must not undo already-successful disk work.
    d = registry / "ds-normal"
    assert (d / cli._MIE_AUTHORED_FILE).is_file()
    assert (d / cli._METADATA_TTL_FILE).is_file()

    # And a store failure never counted as a completed write (2 errors:
    # ds-badyaml's pre-existing unparseable-yaml error, plus ds-normal's new
    # store-write error; ds-unknown is the only ok).
    summary = [line for line in out.splitlines() if line.startswith("4 dataset(s)")]
    assert summary == ["4 dataset(s) [apply]: 1 ok, 1 skip, 2 error"]


class _DropOkPostFailsClient:
    """DROP succeeds, POST fails — the non-atomic window (metadata.py
    write_metadata_graph) where the store's meta/{id} graph is left empty."""

    async def sparql_update(self, update: str) -> None:
        pass

    async def sparql_construct(self, query: str) -> str:  # pragma: no cover - unused by migrate
        return ""

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        raise ConnectionError("simulated: connection dropped mid-POST")


def test_store_post_failure_after_drop_warns_graph_is_now_empty(
    registry: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """When the DROP half of the store write already succeeded and only the
    POST failed, the reported line (and the log) must say the store-side
    graph is now empty and point at the fix (re-run --apply --store) — not
    just "store write failed", which reads as "nothing happened"."""
    caplog.set_level(logging.WARNING)
    fake = _DropOkPostFailsClient()

    code = cli._run(registry, apply=True, store_url=None, only={"ds-normal"}, client=fake)

    assert code == 1
    out = capsys.readouterr().out
    lines = _outcome_lines(out)
    assert "store-error" in lines["ds-normal"]
    assert "now empty" in lines["ds-normal"]
    assert "re-run --apply --store" in lines["ds-normal"]
    assert any("now empty" in r.message for r in caplog.records)


# ----------------------------------------------------------------------------
# --dataset filter
# ----------------------------------------------------------------------------


def test_dataset_filter_restricts_to_named_ids(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._run(registry, apply=False, store_url=None, only={"ds-normal"})
    out = capsys.readouterr().out
    lines = _outcome_lines(out)
    assert set(lines) == {"ds-normal"}


def test_dataset_filter_unknown_id_warns_and_errors(
    registry: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A --dataset id that matches nothing under --registry (typo, or an id
    that was never onboarded) must not look like a clean, empty success —
    it must warn which id(s) were not found and exit non-zero."""
    caplog.set_level(logging.WARNING)

    code = cli._run(registry, apply=False, store_url=None, only={"does-not-exist"})

    assert code == 1
    out = capsys.readouterr().out
    lines = _outcome_lines(out)
    assert lines == {}  # nothing was actually processed
    summary = [line for line in out.splitlines() if line.startswith("0 dataset(s)")]
    assert summary == ["0 dataset(s) [dry-run]: 0 ok, 0 skip, 0 error"]
    assert any(
        "does-not-exist" in r.message and "not found" in r.message for r in caplog.records
    )


def test_dataset_filter_mix_of_known_and_unknown_ids(
    registry: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """One valid id and one bogus id: the valid one is still processed and
    reported normally, but the run as a whole still exits non-zero and warns
    about the id that was never found — the operator must not miss the typo
    just because the *other* id happened to work."""
    caplog.set_level(logging.WARNING)

    code = cli._run(
        registry, apply=False, store_url=None, only={"ds-normal", "does-not-exist"}
    )

    assert code == 1
    out = capsys.readouterr().out
    lines = _outcome_lines(out)
    assert set(lines) == {"ds-normal"}
    assert "ok" in lines["ds-normal"]
    assert any("does-not-exist" in r.message for r in caplog.records)


# ----------------------------------------------------------------------------
# argparse-level CLI entry point
# ----------------------------------------------------------------------------


def test_main_dry_run_default_and_apply_flag(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli._main(["migrate", "--registry", str(registry)])
    assert code == 1
    assert not (registry / "ds-normal" / cli._METADATA_TTL_FILE).exists()

    code = cli._main(["migrate", "--registry", str(registry), "--apply"])
    assert code == 1
    assert (registry / "ds-normal" / cli._METADATA_TTL_FILE).exists()


def test_main_rejects_missing_registry_dir(tmp_path: Path) -> None:
    code = cli._main(["migrate", "--registry", str(tmp_path / "does-not-exist")])
    assert code == 1


def test_store_url_unreachable_does_not_crash(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end through --store (real OxigraphClient, real httpx.ConnectError)
    rather than a fake client: an unreachable --store URL must still produce
    one line per dataset and a clean exit code, never an uncaught traceback."""
    code = cli._run(
        registry,
        apply=True,
        store_url="http://127.0.0.1:1/does-not-listen",
        only=None,
        client=None,
    )

    assert code == 1
    out = capsys.readouterr().out
    lines = _outcome_lines(out)
    assert set(lines) == {"ds-badyaml", "ds-normal", "ds-nomie", "ds-unknown"}
    assert "error" in lines["ds-normal"]
