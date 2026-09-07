"""``asterism-metadata migrate``: one-time deterministic migration of hand-authored
``mie.yaml`` files into the description graph (ADR
``dataset-description-in-the-store.md`` §8).

For each registry dataset with a non-empty ``mie.yaml``: parse it, build the
description graph (:func:`asterism.metadata.build_metadata_graph`), project it
back (:func:`asterism.metadata.project_mie_document`), and refuse to touch
anything unless the round trip is semantically lossless
(:func:`asterism.metadata.normalize_document`) — a migration must never hide a
modeling gap by silently dropping what it cannot represent. Only ``--apply``
writes; the default is dry-run. No LLM calls anywhere (ADR §5, §11):
everything here is a pure, deterministic function of what is already on disk.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from asterism import metadata as m
from asterism.metadata import SupportsMetadataStore
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

logger = logging.getLogger(__name__)

#: Same convention as asterism.catalog._SAFE_ID / api.togomcp_sync._SAFE_ID —
#: a registry dataset directory name. Leading-underscore siblings (``_staging``,
#: ``_usage``) never match this, and a dataset's own ``history/`` snapshots
#: live one level *below* it — since we only ever list --registry's immediate
#: children, neither is ever visited (spec §A: "history/ は触らない").
_SAFE_ID = re.compile(r"[a-z0-9-]{1,128}")

_META_FILE = "meta.json"
_MIE_FILE = "mie.yaml"
_MIE_AUTHORED_FILE = "mie.yaml.authored"
_METADATA_TTL_FILE = "metadata.ttl"
_RML_FILE = "mapping.rml.ttl"


@dataclass
class _Outcome:
    dataset_id: str
    status: str
    triples: int = 0
    sections: list[str] = field(default_factory=list)
    is_error: bool = False


def _iter_dataset_dirs(registry: Path, only: set[str] | None) -> list[Path]:
    """One entry per top-level ``registry/{id}/`` with a ``meta.json``."""
    dirs = []
    for child in sorted(registry.iterdir()):
        if not child.is_dir() or not _SAFE_ID.fullmatch(child.name):
            continue
        if not (child / _META_FILE).is_file():
            continue
        if only is not None and child.name not in only:
            continue
        dirs.append(child)
    return dirs


def _read_meta(dataset_dir: Path) -> dict[str, object]:
    try:
        data = json.loads((dataset_dir / _META_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("%s: unreadable meta.json (%s)", dataset_dir.name, exc)
        return {}


def _dump_for_diff(doc: object) -> list[str]:
    return yaml.safe_dump(doc, sort_keys=True, allow_unicode=True).splitlines()


def _diff(before: object, after: object) -> str:
    """Human-readable diff between the original and round-tripped document,
    normalized form on both sides (spec §A.4: show what did not survive)."""
    return "\n".join(
        difflib.unified_diff(
            _dump_for_diff(before),
            _dump_for_diff(after),
            fromfile="original",
            tofile="projected",
            lineterm="",
        )
    )


async def _migrate_one(
    dataset_dir: Path,
    *,
    apply: bool,
    client: SupportsMetadataStore | None,
) -> _Outcome:
    """Migrate one dataset directory. ``client`` is only ever consulted when
    ``apply`` is also true (dry-run never touches the store, spec §A.7) and the
    dataset's own ``meta.json`` says ``promoted`` (ADR §4: unpublished data is
    never written to the store)."""
    dataset_id = dataset_dir.name
    mie_path = dataset_dir / _MIE_FILE
    if not mie_path.is_file() or not mie_path.read_text(encoding="utf-8").strip():
        return _Outcome(dataset_id, "skip (no mie.yaml)")

    original_text = mie_path.read_text(encoding="utf-8")
    try:
        document = m.parse_mie_yaml(original_text)
    except (yaml.YAMLError, ValueError) as exc:
        logger.warning("%s: unparseable mie.yaml (%s)", dataset_id, exc)
        return _Outcome(dataset_id, "error (unparseable)", is_error=True)

    rml_path = dataset_dir / _RML_FILE
    rml_ttl = rml_path.read_text(encoding="utf-8") if rml_path.is_file() else None

    graph = m.build_metadata_graph(document, dataset_id, rml_ttl=rml_ttl)
    projected = m.project_mie_document(graph, dataset_id)
    sections = list(projected.keys())
    triples = len(graph)

    before = m.normalize_document(document)
    after = m.normalize_document(projected)
    if before != after:
        logger.warning("%s: round trip is not lossless\n%s", dataset_id, _diff(before, after))
        return _Outcome(
            dataset_id, "error (not lossless)", triples=triples, sections=sections, is_error=True
        )

    if not apply:
        return _Outcome(dataset_id, "ok", triples=triples, sections=sections)

    # 1) Preserve the hand-authored original once — never overwritten after
    #    that (spec §A.6 / ADR §9: this is the only place the "#" comments
    #    lost in the yaml round trip still live).
    authored_path = dataset_dir / _MIE_AUTHORED_FILE
    if not authored_path.is_file():
        authored_path.write_text(original_text, encoding="utf-8")

    # 2) metadata.ttl and 3) mie.yaml (now a generated artifact, ADR §8): only
    #    rewrite a file (and only report "written") if its bytes actually
    #    differ — makes a second --apply run visibly idempotent (no mtime churn
    #    on either file, not just no content change).
    ttl_text = m.metadata_turtle(graph)
    ttl_path = dataset_dir / _METADATA_TTL_FILE
    ttl_changed = not ttl_path.is_file() or ttl_path.read_text(encoding="utf-8") != ttl_text
    if ttl_changed:
        ttl_path.write_text(ttl_text, encoding="utf-8")

    mie_text = m.project_mie_yaml(graph, dataset_id)
    mie_changed = mie_path.read_text(encoding="utf-8") != mie_text
    if mie_changed:
        mie_path.write_text(mie_text, encoding="utf-8")

    changed = ttl_changed or mie_changed
    status = "written" if changed else "unchanged"

    if client is not None:
        meta = _read_meta(dataset_dir)
        if bool(meta.get("promoted")):
            # A store failure (unreachable host, non-2xx, timeout, ...) must
            # mark only *this* dataset as an error, not crash the whole run —
            # the local files above are already written, and every other
            # dataset (store-bound or not) must still get its own chance
            # (spec §A: one output line per dataset, none silently dropped).
            try:
                stored = await m.write_metadata_graph(client, dataset_id, graph)
            except m.MetadataGraphWriteError as exc:
                # DROP already succeeded before the POST failed: the store's
                # meta/{id} graph is now empty, not merely stale — a plain
                # "store write failed" would read as "nothing happened".
                logger.warning("%s", exc)
                note = "graph now empty in store; re-run --apply --store to repair"
                return _Outcome(
                    dataset_id,
                    f"{status} +store-error={exc} ({note})",
                    triples=triples,
                    sections=sections,
                    is_error=True,
                )
            except Exception as exc:  # any other store failure, not just httpx's
                logger.warning("%s: store write failed (%s)", dataset_id, exc)
                return _Outcome(
                    dataset_id,
                    f"{status} +store-error={exc}",
                    triples=triples,
                    sections=sections,
                    is_error=True,
                )
            status = f"{status} +stored={stored}"

    return _Outcome(dataset_id, status, triples=triples, sections=sections)


async def _migrate_all(
    dataset_dirs: list[Path],
    *,
    apply: bool,
    client: SupportsMetadataStore | None,
) -> tuple[int, int, int]:
    """Migrate each dataset in turn, printing its result line immediately
    (spec §A: one line per dataset). ``_migrate_one`` never raises for a store
    failure — it reports that dataset as an error and returns — so one bad
    dataset can never swallow the result lines, or the local-file migration,
    of every dataset that comes after it."""
    n_ok = n_skip = n_error = 0
    for d in dataset_dirs:
        o = await _migrate_one(d, apply=apply, client=client)
        sections_str = "[" + ", ".join(o.sections) + "]"
        print(f"{o.dataset_id}  triples={o.triples}  sections={sections_str}  {o.status}")
        if o.is_error:
            n_error += 1
        elif o.status.startswith("skip"):
            n_skip += 1
        else:
            n_ok += 1
    return n_ok, n_skip, n_error


async def _run_async(
    registry: Path,
    *,
    apply: bool,
    store_url: str | None,
    only: set[str] | None,
    client: SupportsMetadataStore | None = None,
) -> int:
    dataset_dirs = _iter_dataset_dirs(registry, only)

    # --dataset names an id to retry/repair; a typo'd or already-removed id
    # must not look like a clean, empty, successful run (0 ok, 0 skip, 0
    # error, exit 0) — that reads as "nothing needed doing" rather than
    # "nothing was found".
    unmatched = sorted(only - {d.name for d in dataset_dirs}) if only is not None else []
    if unmatched:
        logger.warning(
            "--dataset id(s) not found under --registry %s: %s",
            registry,
            ", ".join(unmatched),
        )

    if apply and store_url and client is None:
        async with OxigraphClient(OxigraphConfig(base_url=store_url)) as owned_client:
            n_ok, n_skip, n_error = await _migrate_all(
                dataset_dirs, apply=apply, client=owned_client
            )
    else:
        active_client = client if apply else None
        n_ok, n_skip, n_error = await _migrate_all(dataset_dirs, apply=apply, client=active_client)

    mode = "apply" if apply else "dry-run"
    print(f"{len(dataset_dirs)} dataset(s) [{mode}]: {n_ok} ok, {n_skip} skip, {n_error} error")

    return 1 if n_error or unmatched else 0


def _run(
    registry: Path,
    *,
    apply: bool,
    store_url: str | None,
    only: set[str] | None,
    client: SupportsMetadataStore | None = None,
) -> int:
    """Sync entry point used both by :func:`_main` and directly by tests
    (passing ``client`` — a fake recording post/drop calls — instead of
    ``store_url``, to exercise the store-write path with no real Oxigraph)."""
    return asyncio.run(
        _run_async(registry, apply=apply, store_url=store_url, only=only, client=client)
    )


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="asterism-metadata",
        description="Migrate hand-authored mie.yaml files into the description graph.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="Migrate a registry's mie.yaml files.")
    migrate.add_argument("--registry", type=Path, required=True, help="Registry root directory.")
    migrate.add_argument(
        "--apply",
        action="store_true",
        help="Write metadata.ttl / mie.yaml.authored / mie.yaml (default: dry-run only).",
    )
    migrate.add_argument(
        "--store",
        default=None,
        metavar="URL",
        help="Oxigraph base URL; load promoted datasets' graphs (has no effect without --apply).",
    )
    migrate.add_argument(
        "--dataset",
        action="append",
        default=None,
        metavar="ID",
        help="Restrict to this dataset id (repeatable). Default: every dataset in --registry.",
    )

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.registry.is_dir():
        print(f"error: --registry {args.registry} is not a directory", file=sys.stderr)
        return 1

    only = set(args.dataset) if args.dataset else None
    if args.store and not args.apply:
        logger.warning("--store has no effect without --apply (dry-run never writes to the store)")

    return _run(args.registry, apply=args.apply, store_url=args.store, only=only)


if __name__ == "__main__":
    raise SystemExit(_main())
