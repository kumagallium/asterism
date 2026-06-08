"""Disposable-Oxigraph validation for the memory-bounded (flag-based) promote.

Proves on a throwaway 8 GB-capped Oxigraph that:
  1. streaming ~12 M triples into a canonical graph stays memory-bounded, and
  2. the NEW promote (a control-graph flag write, `mark_graph_promoted`) does NOT
     spike memory — regardless of how many triples the graph holds.
  3. (contrast) the OLD `MOVE GRAPH` of the same graph balloons memory toward the
     8 GB cap / OOM — the very thing this change removed.

Run AFTER starting a disposable container on :7900 (see the orchestration script).
Never point this at the user's :7878 store. Memory is sampled out-of-band by a
`docker stats` logger; this script just drives the workload and prints markers.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

DATASET_ID = "validate"


def gen_nt(path: Path, n: int) -> None:
    """Write ``n`` distinct N-Triples (absolute IRIs) to ``path`` line by line."""
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(
                f"<https://ex/asterism/s/{i}> "
                f"<https://ex/asterism/p/{i % 50}> "
                f'"value-{i}" .\n'
            )


def _marker(label: str) -> None:
    print(f"\n##MARK## {time.time():.1f} {label}", flush=True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7900")
    ap.add_argument("--triples", type=int, default=12_000_000)
    ap.add_argument("--nt", default="")
    ap.add_argument("--chunk", type=int, default=100_000)
    ap.add_argument("--move-contrast", action="store_true")
    args = ap.parse_args()

    nt_path = Path(args.nt) if args.nt else Path(__file__).with_name("_validate.nt")
    canonical_iri = substrate.canonical_graph_iri(DATASET_ID)

    client = OxigraphClient(OxigraphConfig(base_url=args.url))
    try:
        assert await client.ping(), f"oxigraph not reachable at {args.url}"

        if not nt_path.exists() or nt_path.stat().st_size == 0:
            _marker(f"generate {args.triples} triples -> {nt_path}")
            gen_nt(nt_path, args.triples)
        total_lines = substrate.count_nt_lines(nt_path)
        print(f"nt file: {nt_path} ({total_lines} lines, {nt_path.stat().st_size} bytes)")

        # (1) streaming load into the canonical graph (the new ingest target)
        _marker(f"stream_start chunk={args.chunk}")
        t0 = time.time()
        last = [0]

        def on_progress(done: int, tot: int) -> None:
            if done - last[0] >= 1_000_000 or done == tot:
                last[0] = done
                print(f"  upload {done}/{tot} ({100 * done // max(tot, 1)}%)", flush=True)

        loaded = await substrate.stream_nt_file_to_oxigraph(
            nt_path, client, canonical_iri, chunk_lines=args.chunk, on_progress=on_progress
        )
        _marker(f"stream_done loaded={loaded} in {time.time() - t0:.1f}s")

        # (2) the NEW promote = one control-graph flag write. O(1), memory-flat.
        _marker("promote_flag_start")
        tp = time.time()
        await substrate.mark_graph_promoted(client, canonical_iri)
        _marker(f"promote_flag_done in {time.time() - tp:.3f}s")

        # confirm it is now citable + the data is all there
        cgs = await substrate.canonical_graphs(client)
        print(f"canonical_graphs() -> {cgs}")
        assert canonical_iri in cgs, "promoted graph not enumerated as citable"
        data = await client.sparql_select(
            f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{canonical_iri}> {{ ?s ?p ?o }} }}"
        )
        count = data["results"]["bindings"][0]["c"]["value"]
        print(f"COUNT in canonical graph = {count}")
        assert int(count) == total_lines, "triple count mismatch after promote"

        # (3) contrast: the OLD MOVE of the same large graph (memory spike / OOM).
        if args.move_contrast:
            other = substrate.canonical_graph_iri("validate-move-target")
            _marker("OLD_MOVE_start (expect memory spike toward 8 GB / OOM-kill)")
            tm = time.time()
            try:
                await client.sparql_update(f"MOVE GRAPH <{canonical_iri}> TO GRAPH <{other}>")
                _marker(f"OLD_MOVE_done in {time.time() - tm:.1f}s (did NOT OOM)")
            except Exception as exc:  # OOM-kill drops the connection
                _marker(f"OLD_MOVE_failed in {time.time() - tm:.1f}s: {type(exc).__name__}: {exc}")
        print("\nVALIDATION OK")
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
