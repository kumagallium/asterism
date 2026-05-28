"""Thin async HTTP client for Oxigraph SPARQL endpoint.

Used by the Phase 2 watcher / upload API:

- ``POST /store?graph=<URI>`` with ``Content-Type: text/turtle`` puts a Turtle
  payload into a named graph (SPARQL 1.1 Graph Store Protocol). Existing
  IRI-keyed triples are deduplicated by Oxigraph's set semantics so re-running
  the same ingest is safe (the only delta is a new ``sd:IngestionActivity``).
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
DEFAULT_RETRIES: Final[int] = 3
DEFAULT_BACKOFF_S: Final[float] = 0.2


@dataclass(frozen=True)
class OxigraphConfig:
    base_url: str = "http://localhost:7878"
    timeout_s: float = DEFAULT_TIMEOUT_S
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

    async def post_turtle(self, turtle_path: Path | str, graph_iri: str) -> int:
        """POST a Turtle file to ``graph_iri`` and return bytes uploaded.

        Raises ``httpx.HTTPError`` after exhausting retries.
        """
        path = Path(turtle_path)
        payload = path.read_bytes()
        return await self.post_turtle_bytes(payload, graph_iri)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str) -> int:
        params = {"graph": graph_iri}
        last_exc: Exception | None = None
        for attempt in range(self.config.retries):
            try:
                r = await self._client.post(
                    "/store",
                    params=params,
                    content=payload,
                    headers={"Content-Type": "text/turtle; charset=utf-8"},
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

    async def graph_triple_count(self, graph_iri: str) -> int:
        """Return number of triples currently stored in ``graph_iri``."""
        query = (
            "SELECT (COUNT(*) AS ?c) WHERE { GRAPH <" + graph_iri + "> { ?s ?p ?o } }"
        )
        r = await self._client.post(
            "/query",
            content=query,
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
        )
        r.raise_for_status()
        data = r.json()
        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            return 0
        return int(bindings[0]["c"]["value"])
