"""``asterism-agent`` — serve one exported agent bundle as a local stdio MCP server.

PR D §4 (contract memo ``contract_pr_d.md``): a bundle produced by
``ingest/src/asterism/agent_bundle.py`` (``POST /api/subjects/export``) is a
self-contained directory — ``facts/*.trig``, a ``tools/`` registry of declared
query tools, and ``materials.json`` — with **no HTTP dependency on Asterism
itself** (§5.7 of the handoff). This module is the thing a user (or Claude
Desktop / Claude Code, via ``mcp.json``) actually runs to talk to that bundle:

    asterism-agent serve <dir> [--port N] [--check]

It does five things, in order:

1. checks the directory looks like an exported bundle at all;
2. under ``--check``, validates the bundle's content (TriG parses, every
   declared query tool parses and lints clean) and exits — no Oxigraph needed;
3. otherwise finds an Oxigraph binary, spawns it against ``<dir>/.store``, and
   waits for it to come up;
4. on first run only (``<dir>/.store/.loaded`` marker absent), loads
   ``facts/*.trig`` into it over the Graph Store Protocol;
5. sets the same env vars ``asterism_mcp.server`` already reads
   (``CSV2RDF_OXIGRAPH_URL`` / ``CSV2RDF_REGISTRY_ROOT`` / raw-SPARQL and
   bundled-tools opt-outs) and calls ``asterism_mcp.server._main`` **in this
   same process**, stdio transport, so the parent's stdio is the MCP
   transport — then terminates the Oxigraph child on the way out.

The Oxigraph child-process helpers (``find_oxigraph_binary`` /
``spawn_oxigraph`` / ``wait_oxigraph_ready`` / ``_terminate``) are a small,
deliberate duplication of ``api/src/asterism_api/local.py``'s local-mode
helpers, not an import of them: §0 of the contract memo requires the agent
bundle to carry no dependency on the ``asterism_api`` package (an exported
bundle must run with only ``asterism-ingest`` + ``asterism-mcp-tools``
installed, not the whole api surface). Keep the two in sync by hand if
Oxigraph's CLI or the readiness probe ever changes.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import httpx
import yaml
from asterism.query_tools import QueryToolError, lint_query_tool, parse_query_tools

from asterism_mcp.server import _main as _server_main

logger = logging.getLogger("asterism_mcp.agent_cli")

# ----------------------------------------------------------------------------
# exit codes
# ----------------------------------------------------------------------------

EXIT_OK: Final[int] = 0
#: ``--check`` ran but found problems (broken TriG, a tool that fails to
#: parse or lint).
EXIT_CHECK_FAILED: Final[int] = 1
#: ``<dir>`` is missing a file/folder an exported bundle must carry.
EXIT_BUNDLE_INVALID: Final[int] = 2
#: No Oxigraph binary could be found anywhere this CLI looks.
EXIT_NO_OXIGRAPH: Final[int] = 3
#: Oxigraph was spawned but never answered its readiness probe.
EXIT_OXIGRAPH_FAILED: Final[int] = 4

# Paths (relative to the bundle directory) an ``agent_bundle.py`` export
# always carries — §3 of the contract memo. Their absence means ``<dir>`` is
# not an exported bundle (a stray folder, a half-unzipped download, …), so
# step 1 of §4 refuses to guess and exits instead of spawning Oxigraph.
_REQUIRED_RELATIVE_PATHS: Final[tuple[str, ...]] = (
    "materials.json",
    "facts/facts.trig",
    "tools",
)

# Same guidance ``asterism_api.local._NO_OXIGRAPH_MSG`` gives (the two must
# not drift into different advice), minus the ``asterism-local
# --oxigraph-url`` line — this CLI has no such flag; ``ASTERISM_OXIGRAPH_BIN``
# still works because ``find_oxigraph_binary`` below reads it.
_NO_OXIGRAPH_MSG = (
    "oxigraph binary not found. `asterism-agent serve` runs Oxigraph (a "
    "single static binary) as a child process to hold this bundle's facts.\n"
    "  install:  brew install oxigraph            (macOS)\n"
    "            cargo install oxigraph-cli       (any platform)\n"
    "            https://github.com/oxigraph/oxigraph/releases\n"
    "  (ASTERISM_OXIGRAPH_BIN overrides binary discovery)"
)


# ----------------------------------------------------------------------------
# §4 step 1: does <dir> look like an exported bundle?
# ----------------------------------------------------------------------------


def check_bundle(directory: Path) -> list[str]:
    """Required paths missing under ``directory`` (empty ⇒ looks intact)."""
    return [rel for rel in _REQUIRED_RELATIVE_PATHS if not (directory / rel).exists()]


# ----------------------------------------------------------------------------
# §4 step 2 / ``--check``: validate content, no Oxigraph needed
# ----------------------------------------------------------------------------


def check_trig(directory: Path) -> list[str]:
    """Parse every ``facts/*.trig`` with rdflib; one message per failure."""
    import rdflib  # local import: the common `serve` path never needs rdflib

    facts_dir = directory / "facts"
    if not facts_dir.is_dir():
        return [f"missing directory: {facts_dir}"]
    trig_files = sorted(facts_dir.glob("*.trig"))
    if not trig_files:
        return [f"no *.trig files under {facts_dir}"]
    problems: list[str] = []
    for path in trig_files:
        dataset = rdflib.Dataset()
        try:
            dataset.parse(str(path), format="trig")
        except Exception as exc:  # rdflib's parser errors are not one type
            problems.append(f"{path.name}: {exc}")
    return problems


def check_query_tools(directory: Path) -> list[str]:
    """Parse + lint every ``tools/**/query_tools.yaml``; one message per failure.

    Mirrors §3's own test contract ("``_builtin`` の宣言が lint を通る"): a
    tool that fails to *parse* (bad YAML, an invalid declaration) or that
    *lints with errors* (undeclared prefix, a template that will not run) is
    reported the same way — this CLI cannot tell which the bundle's author
    would rather know about first.
    """
    tools_dir = directory / "tools"
    if not tools_dir.is_dir():
        return [f"missing directory: {tools_dir}"]
    yaml_files = sorted(tools_dir.glob("*/query_tools.yaml"))
    if not yaml_files:
        return [f"no query_tools.yaml under {tools_dir}"]
    problems: list[str] = []
    for path in yaml_files:
        label = path.parent.name
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            tools = parse_query_tools(data)
        except (yaml.YAMLError, QueryToolError) as exc:
            problems.append(f"{label}/query_tools.yaml: {exc}")
            continue
        for tool in tools:
            lint = lint_query_tool(tool)
            if lint.errors:
                problems.append(
                    f"{label}/query_tools.yaml: tool {tool.name!r}: "
                    + "; ".join(lint.errors)
                )
    return problems


def _run_check(directory: Path) -> int:
    problems = check_trig(directory) + check_query_tools(directory)
    if problems:
        for msg in problems:
            print(msg, file=sys.stderr)
        print(f"asterism-agent check: {len(problems)} problem(s)", file=sys.stderr)
        return EXIT_CHECK_FAILED
    print("asterism-agent check: ok")
    return EXIT_OK


# ----------------------------------------------------------------------------
# §4 step 3: find an Oxigraph binary
#
# Small, deliberate duplication of api/src/asterism_api/local.py — see the
# module docstring for why this does not simply import that helper.
# ----------------------------------------------------------------------------


def _bundled_oxigraph_candidates() -> list[Path]:
    """Where a ``.app``-bundled Oxigraph would sit next to this interpreter.

    The desktop bundle (``desktop/scripts/bundle-backend.sh`` /
    ``desktop/src-tauri/src/lib.rs``) lays out
    ``Resources/backend/{oxigraph, uv-python/cpython-*/bin/python3, …}`` — a
    console script run from that embedded Python has ``sys.executable`` a few
    directories below ``backend/``. Walk a bounded number of ancestors of
    ``sys.executable`` looking for an ``oxigraph`` file directly inside one of
    them, rather than hardcoding the exact depth (robust to that layout
    growing or shrinking a level).
    """
    exe = Path(sys.executable).resolve()
    return [ancestor / "oxigraph" for ancestor in list(exe.parents)[:6]]


def find_oxigraph_binary() -> str | None:
    """``shutil.which`` → ``ASTERISM_OXIGRAPH_BIN`` → bundled-app candidates."""
    which = shutil.which("oxigraph")
    if which:
        return which
    override = (os.environ.get("ASTERISM_OXIGRAPH_BIN") or "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path)
    for candidate in _bundled_oxigraph_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def spawn_oxigraph(
    binary: str, store_dir: Path, log_path: Path, port: int
) -> subprocess.Popen[bytes]:
    """Start ``oxigraph serve`` on loopback (fixed argv, no shell)."""
    store_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [binary, "serve", "--location", str(store_dir), "--bind", f"127.0.0.1:{port}"]
    with open(log_path, "ab") as log_file:
        return subprocess.Popen(
            argv, stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )


def wait_oxigraph_ready(
    url: str, process: subprocess.Popen[bytes], timeout_s: float = 20.0
) -> bool:
    """Poll the child with the same trivial ASK the api's ``/health`` uses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            response = httpx.post(
                url + "/query",
                content="ASK { ?s ?p ?o }",
                headers={
                    "Content-Type": "application/sparql-query",
                    "Accept": "application/sparql-results+json",
                },
                timeout=2.0,
            )
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


# ----------------------------------------------------------------------------
# §4 step 5: first-run load of facts/*.trig over the Graph Store Protocol
# ----------------------------------------------------------------------------


def load_facts(oxigraph_url: str, directory: Path) -> int:
    """POST every ``facts/*.trig`` to the child; return total bytes sent.

    A bare ``POST /store`` (no ``?default`` / ``?graph`` — unlike
    ``OxigraphClient.post_turtle_bytes``'s Turtle path) with a quads content
    type loads each quad into the named graph its own ``GRAPH <...> { }``
    block declares, which is exactly how ``agent_bundle.py`` wrote
    ``facts.trig`` / ``control.trig`` (one ``GRAPH`` block per version graph /
    per control triple set) — no separate default-graph merge step needed.
    """
    total = 0
    for path in sorted((directory / "facts").glob("*.trig")):
        payload = path.read_bytes()
        response = httpx.post(
            oxigraph_url + "/store",
            content=payload,
            headers={"Content-Type": "application/trig"},
            timeout=httpx.Timeout(20.0, read=120.0, write=120.0),
        )
        response.raise_for_status()
        total += len(payload)
    return total


# ----------------------------------------------------------------------------
# §4 step 6: env this process hands to asterism_mcp.server
# ----------------------------------------------------------------------------


def build_env(directory: Path, oxigraph_url: str) -> dict[str, str]:
    """The env ``asterism_mcp.server.Settings`` / ``asterism.catalog`` read.

    ``ASTERISM_EXPOSE_RAW_SPARQL=false``: a bundle exposes only the vetted
    typed tools it shipped, never a graph-wide SPARQL escape (§0 "束は
    Asterism への HTTP 依存を持たない" reads as a scope contract too — an
    agent should answer from what the bundle's author chose to include).
    ``ASTERISM_BUNDLED_TOOLS=0``: never mix in the repo's demo datasets —
    this store holds exactly one bundle's facts.
    """
    return {
        "CSV2RDF_OXIGRAPH_URL": oxigraph_url,
        "CSV2RDF_REGISTRY_ROOT": str(directory / "tools"),
        "ASTERISM_EXPOSE_RAW_SPARQL": "false",
        "ASTERISM_BUNDLED_TOOLS": "0",
    }


# ----------------------------------------------------------------------------
# §4 step 7: SIGTERM → a clean unwind through the `finally` that stops Oxigraph
# ----------------------------------------------------------------------------


class _Terminated(SystemExit):
    """Raised from the SIGTERM handler so ``cmd_serve``'s ``finally`` runs.

    Plain ``SIGTERM`` kills the process before any Python ``finally`` block
    executes; converting it into an exception first (the same trick
    ``asterism_api.local``'s ``_Server`` uses, see its docstring) lets the
    Oxigraph child get its own SIGTERM instead of being orphaned.
    """


def _install_sigterm_handler() -> None:
    def _handler(signum: int, frame: object) -> None:
        raise _Terminated(EXIT_OK)

    signal.signal(signal.SIGTERM, _handler)


# ----------------------------------------------------------------------------
# orchestration
# ----------------------------------------------------------------------------


def cmd_serve(directory: Path, port: int | None, check_only: bool) -> int:
    missing = check_bundle(directory)
    if missing:
        for rel in missing:
            print(f"not an agent bundle: missing {directory / rel}", file=sys.stderr)
        return EXIT_BUNDLE_INVALID

    if check_only:
        return _run_check(directory)

    binary = find_oxigraph_binary()
    if binary is None:
        print(_NO_OXIGRAPH_MSG, file=sys.stderr)
        return EXIT_NO_OXIGRAPH

    store_dir = directory / ".store"
    oxi_port = port if port is not None else _free_port()
    oxigraph_url = f"http://127.0.0.1:{oxi_port}"
    log_path = store_dir / "oxigraph.log"
    child = spawn_oxigraph(binary, store_dir, log_path, oxi_port)
    try:
        if not wait_oxigraph_ready(oxigraph_url, child):
            print(f"oxigraph did not become ready — see {log_path}", file=sys.stderr)
            return EXIT_OXIGRAPH_FAILED

        loaded_marker = store_dir / ".loaded"
        if not loaded_marker.is_file():
            load_facts(oxigraph_url, directory)
            store_dir.mkdir(parents=True, exist_ok=True)
            loaded_marker.write_text("", encoding="utf-8")

        os.environ.update(build_env(directory, oxigraph_url))

        _install_sigterm_handler()
        try:
            return _server_main(["--transport", "stdio"])
        except (KeyboardInterrupt, _Terminated):
            return EXIT_OK
    finally:
        _terminate(child)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="asterism-agent",
        description="Serve one exported Asterism agent bundle as a local stdio MCP server.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="serve <dir> as a stdio MCP server")
    serve.add_argument("dir", type=Path, help="the agent bundle directory (as unzipped)")
    serve.add_argument(
        "--port", type=int, default=None, help="oxigraph port (default: a free loopback port)"
    )
    serve.add_argument(
        "--check",
        action="store_true",
        help="validate the bundle's TriG and declared tools, then exit — no oxigraph needed",
    )
    serve.add_argument("--log-level", default="warning")

    args = parser.parse_args(argv)
    logging.basicConfig(level=str(args.log_level).upper(), format="%(asctime)s %(message)s")

    directory = Path(args.dir).expanduser().resolve()
    return cmd_serve(directory, args.port, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
