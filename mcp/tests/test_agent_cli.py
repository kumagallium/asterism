"""Tests for ``asterism_mcp.agent_cli`` (``asterism-agent serve``, PR D §4).

Oxigraph-process and MCP-transport seams (``find_oxigraph_binary`` /
``spawn_oxigraph`` / ``wait_oxigraph_ready`` / ``load_facts`` /
``_server_main``) are monkeypatched for the unit tests below, so most of this
file runs with no Oxigraph binary and no real MCP transport. One real-process
test at the bottom exercises the genuine spawn → ready → Graph Store Protocol
load path and is skipped when no ``oxigraph`` binary is on ``PATH``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx
import pytest

from asterism_mcp import agent_cli

_VALID_TRIG = """\
@prefix ex: <http://example.org/> .
GRAPH <http://example.org/g1> {
  ex:s ex:p ex:o .
}
"""

_VALID_TOOLS_YAML = "tools: []\n"

_BROKEN_TOOLS_YAML = """\
tools:
  - name: bad
    query: SELECT ?s WHERE { ?s ?p ?o }
   description: bad indentation makes this invalid YAML
"""


def _write_bundle(root: Path, *, tools_yaml: str = _VALID_TOOLS_YAML) -> Path:
    """A minimal-but-complete exported bundle (agent_bundle.py §3 layout)."""
    (root / "materials.json").write_text("{}", encoding="utf-8")
    (root / "facts").mkdir(parents=True, exist_ok=True)
    (root / "facts" / "facts.trig").write_text(_VALID_TRIG, encoding="utf-8")
    (root / "tools" / "_builtin").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "_builtin" / "query_tools.yaml").write_text(
        tools_yaml, encoding="utf-8"
    )
    return root


class _FakeProcess:
    """Stands in for ``subprocess.Popen`` in the monkeypatched unit tests."""

    def __init__(self) -> None:
        self.pid = 4242
        self.terminated = False

    def poll(self) -> int | None:
        return None if not self.terminated else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


# ----------------------------------------------------------------------------
# bundle validity (§4 step 1)
# ----------------------------------------------------------------------------


def test_check_bundle_reports_each_missing_path(tmp_path: Path) -> None:
    missing = agent_cli.check_bundle(tmp_path)
    assert set(missing) == {"materials.json", "facts/facts.trig", "tools"}


def test_check_bundle_empty_when_complete(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    assert agent_cli.check_bundle(tmp_path) == []


def test_serve_on_incomplete_dir_exits_bundle_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "not-a-bundle"
    empty.mkdir()
    code = agent_cli.main(["serve", str(empty)])
    assert code == agent_cli.EXIT_BUNDLE_INVALID
    err = capsys.readouterr().err
    assert "materials.json" in err
    assert "facts/facts.trig" in err
    assert "tools" in err


# ----------------------------------------------------------------------------
# --check (§4 step 2) — success and failure, no oxigraph involved
# ----------------------------------------------------------------------------


def test_check_succeeds_on_valid_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bundle(tmp_path)
    code = agent_cli.main(["serve", str(tmp_path), "--check"])
    assert code == agent_cli.EXIT_OK
    assert "ok" in capsys.readouterr().out


def test_check_fails_on_broken_query_tools_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bundle(tmp_path, tools_yaml=_BROKEN_TOOLS_YAML)
    code = agent_cli.main(["serve", str(tmp_path), "--check"])
    assert code == agent_cli.EXIT_CHECK_FAILED
    assert "query_tools.yaml" in capsys.readouterr().err


def test_check_fails_when_trig_does_not_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bundle(tmp_path)
    (tmp_path / "facts" / "facts.trig").write_text("not { valid trig at all", encoding="utf-8")
    code = agent_cli.main(["serve", str(tmp_path), "--check"])
    assert code == agent_cli.EXIT_CHECK_FAILED
    assert "facts.trig" in capsys.readouterr().err


# ----------------------------------------------------------------------------
# oxigraph discovery (§4 step 3)
# ----------------------------------------------------------------------------


def test_serve_without_oxigraph_binary_exits_and_prints_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_bundle(tmp_path)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda name: None)
    monkeypatch.delenv("ASTERISM_OXIGRAPH_BIN", raising=False)
    monkeypatch.setattr(agent_cli, "_bundled_oxigraph_candidates", lambda: [])

    code = agent_cli.main(["serve", str(tmp_path)])

    assert code == agent_cli.EXIT_NO_OXIGRAPH
    err = capsys.readouterr().err
    assert "oxigraph binary not found" in err
    assert "brew install oxigraph" in err


def test_find_oxigraph_binary_prefers_which_over_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_cli.shutil, "which", lambda name: "/usr/bin/oxigraph")
    fake_env_binary = tmp_path / "oxigraph-from-env"
    fake_env_binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("ASTERISM_OXIGRAPH_BIN", str(fake_env_binary))
    assert agent_cli.find_oxigraph_binary() == "/usr/bin/oxigraph"


def test_find_oxigraph_binary_falls_back_to_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_cli.shutil, "which", lambda name: None)
    fake_env_binary = tmp_path / "oxigraph-from-env"
    fake_env_binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("ASTERISM_OXIGRAPH_BIN", str(fake_env_binary))
    monkeypatch.setattr(agent_cli, "_bundled_oxigraph_candidates", lambda: [])
    assert agent_cli.find_oxigraph_binary() == str(fake_env_binary)


def test_find_oxigraph_binary_falls_back_to_bundled_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_cli.shutil, "which", lambda name: None)
    monkeypatch.delenv("ASTERISM_OXIGRAPH_BIN", raising=False)
    bundled = tmp_path / "backend" / "oxigraph"
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_cli, "_bundled_oxigraph_candidates", lambda: [bundled])
    assert agent_cli.find_oxigraph_binary() == str(bundled)


# ----------------------------------------------------------------------------
# env assembly + _server_main invocation (§4 steps 5-6), oxigraph fully stubbed
# ----------------------------------------------------------------------------


def test_serve_assembles_env_and_invokes_server_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_bundle(tmp_path)
    fake_process = _FakeProcess()
    calls: dict[str, object] = {}

    monkeypatch.setattr(agent_cli, "find_oxigraph_binary", lambda: "/usr/bin/oxigraph")
    monkeypatch.setattr(
        agent_cli,
        "spawn_oxigraph",
        lambda binary, store_dir, log_path, port: fake_process,
    )
    monkeypatch.setattr(agent_cli, "wait_oxigraph_ready", lambda url, process: True)

    def _fake_load_facts(url: str, directory: Path) -> int:
        calls["load_facts_url"] = url
        return 0

    monkeypatch.setattr(agent_cli, "load_facts", _fake_load_facts)

    def _fake_server_main(argv: list[str]) -> int:
        calls["server_main_argv"] = argv
        calls["env_snapshot"] = {
            key: os.environ.get(key)
            for key in (
                "CSV2RDF_OXIGRAPH_URL",
                "CSV2RDF_REGISTRY_ROOT",
                "ASTERISM_EXPOSE_RAW_SPARQL",
                "ASTERISM_BUNDLED_TOOLS",
            )
        }
        return 0

    monkeypatch.setattr(agent_cli, "_server_main", _fake_server_main)

    env_keys = (
        "CSV2RDF_OXIGRAPH_URL",
        "CSV2RDF_REGISTRY_ROOT",
        "ASTERISM_EXPOSE_RAW_SPARQL",
        "ASTERISM_BUNDLED_TOOLS",
    )
    saved = {key: os.environ.get(key) for key in env_keys}
    try:
        code = agent_cli.main(["serve", str(tmp_path), "--port", "9999"])
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert code == agent_cli.EXIT_OK
    assert calls["server_main_argv"] == ["--transport", "stdio"]
    assert calls["load_facts_url"] == "http://127.0.0.1:9999"
    env_snapshot = calls["env_snapshot"]
    assert env_snapshot == {
        "CSV2RDF_OXIGRAPH_URL": "http://127.0.0.1:9999",
        "CSV2RDF_REGISTRY_ROOT": str(tmp_path / "tools"),
        "ASTERISM_EXPOSE_RAW_SPARQL": "false",
        "ASTERISM_BUNDLED_TOOLS": "0",
    }
    assert fake_process.terminated is True
    # the marker is written so a second run skips re-loading facts.
    assert (tmp_path / ".store" / ".loaded").is_file()


def test_serve_second_run_skips_load_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_bundle(tmp_path)
    (tmp_path / ".store").mkdir()
    (tmp_path / ".store" / ".loaded").write_text("", encoding="utf-8")

    monkeypatch.setattr(agent_cli, "find_oxigraph_binary", lambda: "/usr/bin/oxigraph")
    monkeypatch.setattr(
        agent_cli,
        "spawn_oxigraph",
        lambda binary, store_dir, log_path, port: _FakeProcess(),
    )
    monkeypatch.setattr(agent_cli, "wait_oxigraph_ready", lambda url, process: True)

    def _fail_if_called(url: str, directory: Path) -> int:
        raise AssertionError("load_facts must not run when .loaded already exists")

    monkeypatch.setattr(agent_cli, "load_facts", _fail_if_called)
    monkeypatch.setattr(agent_cli, "_server_main", lambda argv: agent_cli.EXIT_OK)

    code = agent_cli.main(["serve", str(tmp_path)])
    assert code == agent_cli.EXIT_OK


def test_serve_reports_failure_when_oxigraph_never_becomes_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_bundle(tmp_path)
    fake_process = _FakeProcess()
    monkeypatch.setattr(agent_cli, "find_oxigraph_binary", lambda: "/usr/bin/oxigraph")
    monkeypatch.setattr(
        agent_cli,
        "spawn_oxigraph",
        lambda binary, store_dir, log_path, port: fake_process,
    )
    monkeypatch.setattr(agent_cli, "wait_oxigraph_ready", lambda url, process: False)

    code = agent_cli.main(["serve", str(tmp_path)])

    assert code == agent_cli.EXIT_OXIGRAPH_FAILED
    assert fake_process.terminated is True
    assert "did not become ready" in capsys.readouterr().err


# ----------------------------------------------------------------------------
# real oxigraph process (skipped when the binary is not installed)
# ----------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("oxigraph") is None, reason="oxigraph binary not on PATH")
def test_serve_with_real_oxigraph_loads_facts_and_terminates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_bundle(tmp_path)
    processes: list[object] = []
    real_spawn = agent_cli.spawn_oxigraph

    def _spy_spawn(binary: str, store_dir: Path, log_path: Path, port: int):
        proc = real_spawn(binary, store_dir, log_path, port)
        processes.append(proc)
        return proc

    monkeypatch.setattr(agent_cli, "spawn_oxigraph", _spy_spawn)
    urls: dict[str, str] = {}

    def _fake_server_main(argv: list[str]) -> int:
        urls["url"] = os.environ["CSV2RDF_OXIGRAPH_URL"]
        return 0

    monkeypatch.setattr(agent_cli, "_server_main", _fake_server_main)

    code = agent_cli.main(["serve", str(tmp_path)])

    assert code == agent_cli.EXIT_OK
    assert (tmp_path / ".store" / ".loaded").is_file()
    response = httpx.post(
        urls["url"] + "/query",
        content="ASK { GRAPH <http://example.org/g1> { ?s ?p ?o } }",
        headers={
            "Content-Type": "application/sparql-query",
            "Accept": "application/sparql-results+json",
        },
        timeout=5.0,
    )
    assert response.json()["boolean"] is True
    assert processes and processes[0].poll() is not None
