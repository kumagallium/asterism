"""Thin async HTTP client for Oxigraph SPARQL endpoint.

Used by the Phase 2 watcher / upload API:

- ``POST /store?default`` (or ``?graph=<URI>``) with ``Content-Type:
  text/turtle`` puts a Turtle payload into the default graph (or a named
  graph), per the SPARQL 1.1 Graph Store Protocol. We default to the *default
  graph* so that GRAPH-less SPARQL — the MIE example queries and the Phase 1
  smoke tests — see the data; passing a ``graph_iri`` opts into a named graph.
  Existing IRI-keyed triples are deduplicated by Oxigraph's set semantics so
  re-running the same ingest is safe (the only delta is a new
  ``sd:IngestionActivity``).
- ``ASK { ?s ?p ?o }`` is used for liveness.

Retries: Oxigraph itself is usually local, so failures are typically "server
not up yet". We do up to 3 retries with exponential backoff (200ms, 400ms,
800ms). If all retries fail we propagate the exception to the caller, which
is responsible for logging it into the jobs jsonl.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

DEFAULT_TIMEOUT_S: Final[float] = 30.0
# A Graph Store ``POST /store`` for an ingest can carry a large Turtle payload
# (a sizable subset materializes to many MB). The default 30s write/read timeout
# is too short for those, so the store POST gets a much longer write/read budget
# (connect still uses the short default, so a down Oxigraph fails fast).
DEFAULT_STORE_TIMEOUT_S: Final[float] = 300.0
DEFAULT_RETRIES: Final[int] = 3
DEFAULT_BACKOFF_S: Final[float] = 0.2


@dataclass(frozen=True)
class OxigraphConfig:
    base_url: str = "http://localhost:7878"
    timeout_s: float = DEFAULT_TIMEOUT_S
    store_timeout_s: float = DEFAULT_STORE_TIMEOUT_S
    retries: int = DEFAULT_RETRIES
    backoff_s: float = DEFAULT_BACKOFF_S


class OxigraphClient:
    """Async client. Construct once per process and reuse the HTTPX pool."""

    def __init__(
        self,
        config: OxigraphConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or OxigraphConfig()
        # Injectable client lets tests pass an httpx.MockTransport.
        self._client = client or httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout_s,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OxigraphClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Returns True if Oxigraph responds to a trivial ASK."""
        try:
            r = await self._client.post(
                "/query",
                content="ASK { ?s ?p ?o }",
                headers={
                    "Content-Type": "application/sparql-query",
                    "Accept": "application/sparql-results+json",
                },
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def post_turtle(
        self, turtle_path: Path | str, graph_iri: str | None = None
    ) -> int:
        """POST a Turtle file and return bytes uploaded.

        ``graph_iri=None`` (the default) targets the default graph; a non-None
        value targets that named graph. Raises ``httpx.HTTPError`` after
        exhausting retries.
        """
        path = Path(turtle_path)
        payload = path.read_bytes()
        return await self.post_turtle_bytes(payload, graph_iri)

    async def post_turtle_bytes(
        self, payload: bytes, graph_iri: str | None = None
    ) -> int:
        # SPARQL 1.1 Graph Store Protocol indirect graph identification:
        # ``?default`` (a valueless flag) targets the default graph, while
        # ``?graph=<uri>`` targets a named graph.
        if graph_iri is None:
            url = "/store?default"
            params: dict[str, str] | None = None
        else:
            url = "/store"
            params = {"graph": graph_iri}
        last_exc: Exception | None = None
        for attempt in range(self.config.retries):
            try:
                r = await self._client.post(
                    url,
                    params=params,
                    content=payload,
                    headers={"Content-Type": "text/turtle; charset=utf-8"},
                    # Large ingest payloads need a generous write/read budget;
                    # keep connect/pool on the short default (fail fast if down).
                    timeout=httpx.Timeout(
                        self.config.timeout_s,
                        read=self.config.store_timeout_s,
                        write=self.config.store_timeout_s,
                    ),
                )
                # Oxigraph returns 200 (graph existed, triples merged) or
                # 201 (graph created). Treat both as success.
                if r.status_code in (200, 201, 204):
                    return len(payload)
                raise httpx.HTTPStatusError(
                    f"unexpected status {r.status_code}: {r.text[:200]}",
                    request=r.request,
                    response=r,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self.config.retries - 1:
                    await asyncio.sleep(self.config.backoff_s * (2**attempt))
        assert last_exc is not None
        raise last_exc

    async def sparql_select(self, query: str) -> dict[str, object]:
        """Run a SPARQL SELECT and return the parsed SPARQL-Results JSON.

        Raises ``httpx.HTTPStatusError`` if Oxigraph returns non-2xx.
        """
        r = await self._client.post(
            "/query",
            content=query,
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
        )
        r.raise_for_status()
        # mypy: ``r.json()`` is typed as Any; explicit cast keeps callers honest.
        return r.json()  # type: ignore[no-any-return]

    async def sparql_update(self, update: str) -> None:
        """Run a SPARQL 1.1 Update (INSERT/DELETE/MOVE/DROP/...) against ``/update``.

        Internal write path — NOT the read-only ``/api/sparql`` relay. Used by the
        human-gated promote (MOVE) and the ingest clean-slate / retract (DROP).
        Graph-level updates on a large graph (a multi-million-triple draft DROP or
        a promote MOVE) can take far longer than the default 30s, so this uses the
        generous ``store_timeout_s`` read/write budget (connect stays short — a
        down Oxigraph fails fast). Raises ``httpx.HTTPStatusError`` on non-2xx.
        """
        r = await self._client.post(
            "/update",
            content=update,
            headers={"Content-Type": "application/sparql-update"},
            timeout=httpx.Timeout(
                self.config.timeout_s,
                read=self.config.store_timeout_s,
                write=self.config.store_timeout_s,
            ),
        )
        r.raise_for_status()

    async def graph_triple_count(self, graph_iri: str) -> int:
        """Return number of triples currently stored in ``graph_iri``."""
        query = (
            "SELECT (COUNT(*) AS ?c) WHERE { GRAPH <" + graph_iri + "> { ?s ?p ?o } }"
        )
        data = await self.sparql_select(query)
        results = data.get("results", {}) if isinstance(data, dict) else {}
        bindings = results.get("bindings", []) if isinstance(results, dict) else []
        if not bindings:
            return 0
        return int(bindings[0]["c"]["value"])
