"""Write tools for the app's own ``/mcp`` endpoint (local / single-user mode).

The standalone MCP server (``asterism_mcp.server.build_server``) stays
read-only: it is the security boundary in front of the hosted store. A tool
that grows a dataset lives only here, on the endpoint the app mounts for its
one local user, and calls the same append core as ``POST /api/datasets/{id}/append``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism.oxigraph_client import OxigraphClient


def register_write_tools(
    mcp: Any,
    *,
    registry_root: Path,
    get_client: Callable[[], OxigraphClient],
    get_rebuilder: Callable[[], Any] | None = None,
    log_job: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """Register the write tools on a FastMCP ``mcp``.

    ``get_client`` / ``get_rebuilder`` are called at tool-call time, because the
    app's client only exists after its lifespan has started.
    """

    @mcp.tool(
        name="append_rows",
        description=(
            "Append new rows to a PROMOTED dataset's live graph. Generic: any "
            "dataset whose design reads JSON array files. The dataset's design "
            "is not changed; a row identical to one already sent is not "
            "added again (the batch is idempotent); a changed row with the same "
            "id is added alongside — replacing is not supported. The first design is approved by a "
            "person in the app (attach a file in the chat); this tool only grows it. "
            "``file_name`` is the design's source name (e.g. judgments.json); "
            "``rows`` is a list of flat JSON objects, one per record; "
            "``child_files`` maps other source names (e.g. rule_terms.json) to "
            "their rows, appended in the same call. Re-sending the same rows "
            "is a no-op (idempotent_replay). Failures are returned as "
            "{dataset_id, error, status}, never raised."
        ),
    )
    async def _append_rows(
        dataset_id: str,
        file_name: str,
        rows: list[dict[str, Any]],
        child_files: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        # Imported here: main.py is large and imports this package's siblings.
        from asterism_api.main import AppendError, _append_batch_to_dataset

        def fail(status: int, detail: str) -> dict[str, Any]:
            return {"dataset_id": dataset_id, "error": detail, "status": status}

        files: list[tuple[str, list[dict[str, Any]]]] = [(file_name, rows)]
        files.extend((child_files or {}).items())
        lowered = [n.lower() for n, _ in files]
        if len(set(lowered)) != len(lowered):
            return fail(400, "file_name and child_files must name different files")
        for name, body in files:
            if not name.lower().endswith(".json"):
                return fail(400, f"file name {name!r} must end with .json")
            if not body:
                return fail(400, f"{name!r} has no rows to append")
        batch = [
            (name, json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"))
            for name, body in files
        ]
        names = ", ".join(name for name, _ in batch)
        try:
            result = await _append_batch_to_dataset(
                registry_root,
                get_client(),
                dataset_id,
                batch,
                rebuilder=get_rebuilder() if get_rebuilder else None,
            )
        except AppendError as exc:
            if log_job:
                log_job(
                    {
                        "kind": "append",
                        "dataset_id": dataset_id,
                        "file": names,
                        "status": "error",
                        "error": exc.detail,
                        "ended_at": datetime.now(UTC).isoformat(),
                    }
                )
            return fail(exc.status, exc.detail)
        if log_job:
            log_job(
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": names,
                    "status": "ok",
                    "triples_in_batch": result["triples_in_batch"],
                    "append_seq": result["append_seq"],
                    "ended_at": datetime.now(UTC).isoformat(),
                }
            )
        return {
            k: result.get(k)
            for k in (
                "dataset_id",
                "live_graph",
                "triples_in_batch",
                "append_seq",
                "idempotent_replay",
                "advisories",
            )
        }
