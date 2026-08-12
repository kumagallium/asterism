"""Unit tests for the togomcp publication projection (ADR togomcp-auto-publish.md)."""
from __future__ import annotations

from pathlib import Path

import yaml

from asterism_api import togomcp_sync
from asterism_api.main import Settings

_URL = "http://oxigraph:7878/query"
_LIVE = "https://example.com/graph/canonical/dataset-abc/v3"

_MIE = """\
schema_info:
  title: Demo
  endpoint: http://localhost:8080/api/sparql
  graphs:
    - stale
sparql_query_examples:
  - title: plain
    query: |
      SELECT ?s WHERE { ?s a ?t } LIMIT 5
  - title: author-scoped
    query: |
      SELECT ?s FROM <https://example.com/other> WHERE { ?s a ?t } LIMIT 5
"""


# ----------------------------------------------------------------------------
# project_mie
# ----------------------------------------------------------------------------


def test_project_pins_endpoint_graphs_and_injects_from() -> None:
    out = yaml.safe_load(
        togomcp_sync.project_mie(_MIE, endpoint_url=_URL, live_graph=_LIVE)
    )
    assert out["schema_info"]["endpoint"] == _URL
    assert out["schema_info"]["graphs"] == [_LIVE]
    plain = out["sparql_query_examples"][0]["query"]
    # GRAPH-less example gets pinned to the CURRENT live version graph.
    assert f"FROM <{_LIVE}>" in plain
    assert f"FROM NAMED <{_LIVE}>" in plain
    # An author-scoped example keeps its own dataset clause untouched.
    scoped = out["sparql_query_examples"][1]["query"]
    assert "FROM <https://example.com/other>" in scoped
    assert _LIVE not in scoped


def test_project_refuses_non_mapping() -> None:
    try:
        togomcp_sync.project_mie("- just\n- a list\n", endpoint_url=_URL, live_graph=_LIVE)
    except ValueError:
        pass
    else:  # pragma: no cover - the assertion is the exception
        raise AssertionError("expected ValueError for a non-mapping MIE")


# ----------------------------------------------------------------------------
# publish / unpublish round-trip
# ----------------------------------------------------------------------------


def _seed_foreign_row(root: Path) -> None:
    """The hand-maintained starrydata row must survive every sync operation."""
    path = root / "resources" / "endpoints.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "database,endpoint_url,endpoint_name,keyword_search_api\n"
        "starrydata,http://oxigraph:7878/query,oxigraph,sparql\n",
        encoding="utf-8",
    )


def test_publish_writes_mie_and_upserts_row(tmp_path: Path) -> None:
    _seed_foreign_row(tmp_path)
    result = togomcp_sync.publish_dataset(
        tmp_path, "dataset-abc", _MIE, _LIVE, endpoint_url=_URL, endpoint_name="oxigraph"
    )
    assert result == {"published": True, "database": "datasetabc"}  # canonical form
    published = yaml.safe_load((tmp_path / "mie" / "datasetabc.yaml").read_text())
    assert published["schema_info"]["graphs"] == [_LIVE]
    rows = (tmp_path / "resources" / "endpoints.csv").read_text().splitlines()
    assert rows[0] == "database,endpoint_url,endpoint_name,keyword_search_api"
    assert "starrydata,http://oxigraph:7878/query,oxigraph,sparql" in rows
    assert "datasetabc,http://oxigraph:7878/query,oxigraph,sparql" in rows


def test_republish_is_idempotent_and_repins_live_graph(tmp_path: Path) -> None:
    for version in ("v1", "v2"):
        togomcp_sync.publish_dataset(
            tmp_path,
            "dataset-abc",
            _MIE,
            f"https://example.com/graph/canonical/dataset-abc/{version}",
            endpoint_url=_URL,
            endpoint_name="oxigraph",
        )
    rows = (tmp_path / "resources" / "endpoints.csv").read_text().splitlines()
    assert sum(r.startswith("datasetabc,") for r in rows) == 1  # upsert, not append
    published = yaml.safe_load((tmp_path / "mie" / "datasetabc.yaml").read_text())
    assert published["schema_info"]["graphs"] == [
        "https://example.com/graph/canonical/dataset-abc/v2"
    ]


def test_publish_skips_empty_or_broken_mie_and_bad_id(tmp_path: Path) -> None:
    empty = togomcp_sync.publish_dataset(
        tmp_path, "dataset-abc", "  ", _LIVE, endpoint_url=_URL, endpoint_name="oxigraph"
    )
    assert empty["published"] is False
    broken = togomcp_sync.publish_dataset(
        tmp_path, "dataset-abc", "- list\n", _LIVE, endpoint_url=_URL, endpoint_name="oxigraph"
    )
    assert broken["published"] is False
    bad_id = togomcp_sync.publish_dataset(
        tmp_path, "../escape", _MIE, _LIVE, endpoint_url=_URL, endpoint_name="oxigraph"
    )
    assert bad_id == {"published": False, "reason": "unsafe dataset id"}
    assert not (tmp_path / "mie").exists()  # nothing was written by any of them


def test_unpublish_removes_file_and_row_but_keeps_foreign_rows(tmp_path: Path) -> None:
    _seed_foreign_row(tmp_path)
    togomcp_sync.publish_dataset(
        tmp_path, "dataset-abc", _MIE, _LIVE, endpoint_url=_URL, endpoint_name="oxigraph"
    )
    removed = togomcp_sync.unpublish_dataset(tmp_path, "dataset-abc")
    assert removed == {"published": False, "removed": True}
    assert not (tmp_path / "mie" / "datasetabc.yaml").exists()
    rows = (tmp_path / "resources" / "endpoints.csv").read_text()
    assert "datasetabc" not in rows
    assert "starrydata" in rows  # the hand-maintained row survives
    again = togomcp_sync.unpublish_dataset(tmp_path, "dataset-abc")
    assert again == {"published": False, "removed": False}  # idempotent


# ----------------------------------------------------------------------------
# Settings parsing
# ----------------------------------------------------------------------------


def test_settings_default_disabled_and_env_enables() -> None:
    off = Settings({})
    assert off.togomcp_dir is None
    on = Settings({"ASTERISM_TOGOMCP_DIR": "/data/togomcp"})
    assert on.togomcp_dir == Path("/data/togomcp")
    assert on.togomcp_endpoint_url == "http://oxigraph:7878/query"
    assert on.togomcp_endpoint_name == "oxigraph"


def test_togomcp_database_matches_upstream_normalization() -> None:
    """The published name must equal togomcp's endpoints key: lower, space->_, no hyphen."""
    assert togomcp_sync.togomcp_database("verify-togomcp-815bc56a") == "verifytogomcp815bc56a"
    assert togomcp_sync.togomcp_database("plain") == "plain"
