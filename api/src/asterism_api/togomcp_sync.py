"""Best-effort togomcp publication of promoted datasets (ADR togomcp-auto-publish.md).

promote copies the dataset's vetted MIE into a togomcp ``TOGOMCP_DIR`` layout
(``mie/<id>.yaml`` + a ``resources/endpoints.csv`` row), so promoted asterism
datasets appear in the DBCLS togomcp catalog next to the public RDF databases
(ChEMBL & co). The FILE layout is the ONLY coupling: ``TOGOMCP_DIR`` is
togomcp's documented content interface, togomcp itself is never imported or
patched, and unsetting ``ASTERISM_TOGOMCP_DIR`` disables the feature entirely
(loose coupling by construction — contrast MateReason, which vendors togomcp
and writes into its package-internal data directory).

Projection, not a byte copy: the registry MIE is authored against the api's
canonical FROM-merge (GRAPH-less example queries), but togomcp talks to the RAW
store endpoint where the default graph is empty — republished verbatim, every
example would return zero rows, which is exactly the stale-example failure trap
T10 exists to catch. So each (re-)promote pins ``schema_info.endpoint`` /
``schema_info.graphs`` and injects ``FROM <live-graph>`` into every example
that declares no dataset of its own (:func:`asterism.substrate.
scope_query_to_graph`); the published document always names the CURRENT live
version graph. retract / delete unlist the dataset again (the raw endpoint is a
second, ungated read surface — see the compose loopback notes — so unlisting is
best-effort hygiene, not an access revocation).

Everything here is best-effort by contract: failures are logged and surfaced in
the endpoint response, and never fail the promote (the ontology-projection /
crosswalk-rebuild precedent). Only PROMOTED data is ever published — drafts
never reach the togomcp catalog.
"""
from __future__ import annotations

import csv
import logging
import re
import threading
from pathlib import Path

import yaml
from asterism.substrate import scope_query_to_graph

logger = logging.getLogger(__name__)

# Same charset registry.mark_promoted enforces for on-disk dataset dirs — the id
# becomes a filename and a CSV cell, so anything else is refused outright.
_SAFE_ID = re.compile(r"[a-z0-9-]{1,128}")
_CSV_FIELDS = ["database", "endpoint_url", "endpoint_name", "keyword_search_api"]
# asterism MIEs use `query:`; togomcp-style documents use `sparql:` — scope both.
_QUERY_KEYS = ("query", "sparql")
# endpoints.csv read-modify-write must not interleave across concurrent promotes.
_LOCK = threading.Lock()


def _mie_path(togomcp_dir: Path, dataset_id: str) -> Path:
    return togomcp_dir / "mie" / f"{dataset_id}.yaml"


def _endpoints_csv(togomcp_dir: Path) -> Path:
    return togomcp_dir / "resources" / "endpoints.csv"


def project_mie(mie_text: str, *, endpoint_url: str, live_graph: str) -> str:
    """The registry MIE, re-scoped for togomcp's raw-store endpoint.

    Raises ``ValueError`` when the MIE is not a YAML mapping — an unparseable
    document must never be published (togomcp clients would choke on it).
    """
    document = yaml.safe_load(mie_text)
    if not isinstance(document, dict):
        raise ValueError("MIE is not a YAML mapping")
    info = document.get("schema_info")
    if not isinstance(info, dict):
        info = {}
        document["schema_info"] = info
    info["endpoint"] = endpoint_url
    info["graphs"] = [live_graph]
    examples = document.get("sparql_query_examples")
    if isinstance(examples, list):
        for item in examples:
            if not isinstance(item, dict):
                continue
            for key in _QUERY_KEYS:
                text = item.get(key)
                if isinstance(text, str) and text.strip():
                    item[key] = scope_query_to_graph(text, live_graph)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=1000)


def _rewrite_rows(togomcp_dir: Path, keep: list[dict[str, str]]) -> None:
    """Atomically replace endpoints.csv with ``keep`` (caller holds ``_LOCK``)."""
    path = _endpoints_csv(togomcp_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(keep)
    tmp.replace(path)


def _read_rows(togomcp_dir: Path) -> list[dict[str, str]]:
    path = _endpoints_csv(togomcp_dir)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def publish_dataset(
    togomcp_dir: Path,
    dataset_id: str,
    mie_text: str,
    live_graph: str,
    *,
    endpoint_url: str,
    endpoint_name: str,
) -> dict[str, object]:
    """Project + write ``mie/<id>.yaml`` and upsert the endpoints.csv row.

    Never raises: promote must not fail on a publication problem. Returns a
    small status dict the endpoint response discloses. Idempotent — a
    re-promote overwrites the projection with the new live graph.
    """
    try:
        if not _SAFE_ID.fullmatch(dataset_id):
            return {"published": False, "reason": "unsafe dataset id"}
        if not (mie_text or "").strip():
            return {"published": False, "reason": "dataset has no MIE artifact"}
        try:
            projected = project_mie(
                mie_text, endpoint_url=endpoint_url, live_graph=live_graph
            )
        except Exception as exc:
            logger.warning(
                "togomcp publish skipped for %s: MIE not projectable: %s",
                dataset_id,
                exc,
            )
            return {"published": False, "reason": "MIE is not parseable YAML"}
        target = _mie_path(togomcp_dir, dataset_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(projected, encoding="utf-8")
        with _LOCK:
            rows = [r for r in _read_rows(togomcp_dir) if r.get("database") != dataset_id]
            rows.append(
                {
                    "database": dataset_id,
                    "endpoint_url": endpoint_url,
                    "endpoint_name": endpoint_name,
                    "keyword_search_api": "sparql",
                }
            )
            _rewrite_rows(togomcp_dir, rows)
        logger.info("togomcp publish: %s -> %s", dataset_id, target)
        return {"published": True, "database": dataset_id}
    except Exception:  # never fail a promote on the side-effect
        logger.exception("togomcp publish failed for %s (continuing)", dataset_id)
        return {"published": False, "reason": "publish error (see logs)"}


def unpublish_dataset(togomcp_dir: Path, dataset_id: str) -> dict[str, object]:
    """Remove ``mie/<id>.yaml`` and the endpoints.csv row (retract / delete).

    Never raises; idempotent — unlisting an unlisted dataset reports
    ``removed: False``.
    """
    try:
        if not _SAFE_ID.fullmatch(dataset_id):
            return {"published": False, "reason": "unsafe dataset id"}
        removed = False
        target = _mie_path(togomcp_dir, dataset_id)
        if target.is_file():
            target.unlink()
            removed = True
        with _LOCK:
            rows = _read_rows(togomcp_dir)
            keep = [r for r in rows if r.get("database") != dataset_id]
            if len(keep) != len(rows):
                _rewrite_rows(togomcp_dir, keep)
                removed = True
        return {"published": False, "removed": removed}
    except Exception:
        logger.exception("togomcp unpublish failed for %s (continuing)", dataset_id)
        return {"published": False, "reason": "unpublish error (see logs)"}
