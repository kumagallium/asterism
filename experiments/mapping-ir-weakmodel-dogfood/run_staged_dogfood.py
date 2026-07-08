"""Weak-model dogfood for the Mapping IR Phase 2b STAGED round-0
(ADR mapping-ir-phase2b-skeleton-wizard.md §10.4).

Runs the exact staged pipeline a weak model would drive through the 2-job UI,
with no api server:

  propose_skeleton (job 1: skeleton only — which source → which class, keyed how)
    → record each map's subject KEY (the metric: does the curve map get a
      COMPOSITE key, or the single non-unique `sample_id` the ADR warns about?)
  → run_design_loop(skeleton=…) (job 2: per-map + document + self-correction)
    → the same hard gates on the compiled RML (assert_rml_safe /
      validate_rml_design / T9) → optional REAL Morph-KGC materialization.

The point is NOT to re-measure the syntax-class-zero claim (Phase 1 did that);
it is to measure (a) skeleton subject-key quality at the human gate and (b)
per-map first-pass quality + convergence, and to confirm the staged artifact is
the same §1-9 Markdown the single call produces (materialize contract).

Usage (needs only an LLM key):
  export SAKURA_API_KEY=$(tr -d '[:space:]' < ~/.config/opencode/apikey-sakura-aiengine)
  api/.venv/bin/python experiments/mapping-ir-weakmodel-dogfood/run_staged_dogfood.py \\
      --model preview/Qwen3.6-35B-A3B --api-base https://api.ai.sakura.ad.jp/v1 \\
      --papers ../starrydata_dataset/starrydata_papers.csv \\
      --samples ../starrydata_dataset/starrydata_samples.csv \\
      --curves ../starrydata_dataset/starrydata_curves.csv \\
      --rows 40 --out results/staged-qwen.json --materialize
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from asterism import substrate
from asterism_api.design_loop import run_design_loop
from asterism_step0.llm import make_llm
from asterism_step0.materialize import materialize_schema
from asterism_step0.staged_propose import propose_skeleton

DOMAIN_HINT = """\
Dataset name: starrydata (thermoelectric materials curves)
Purpose: papers, samples and measured property curves digitized from published
figures. Papers have DOIs and bibliographic fields; samples belong to a paper
and carry a composition string; curves belong to a sample and hold x/y value
arrays (JSON in a cell) with property/unit names.
Constraints: reuse standard vocabularies where they exist (schema.org, dcterms,
bibo, prov). Composition strings are free text. x/y arrays should keep the raw
JSON and expose min/max aggregates.
Synonyms: thermoelectric, Seebeck, ZT, 熱電, ゼーベック.
"""

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def cut_subset(src: Path, dst: Path, rows: int) -> int:
    """Copy the header + first ``rows`` records CSV-safely (quoted multiline cells)."""
    with src.open("r", encoding="utf-8-sig", newline="") as fin, dst.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)
        n = 0
        for i, record in enumerate(reader):
            if i > rows:
                break
            writer.writerow(record)
            n = i
        return n


def describe_skeleton(skeleton: dict) -> list[dict]:
    """Per-map key summary — the human-gate metric. ``key_placeholders`` and
    ``composite`` say whether the subject key joins >1 column (the fix for the
    'curve keyed by sample_id alone collapses all curves' failure)."""
    out = []
    for m in skeleton.get("maps", []):
        subj = m.get("subject", {}) or {}
        key = subj.get("template") or subj.get("constant") or ""
        ph = _PLACEHOLDER.findall(key) if subj.get("template") else []
        out.append(
            {
                "name": m.get("name"),
                "source": m.get("source"),
                "subject_key": key,
                "key_placeholders": ph,
                "composite": len(ph) >= 2,
                "classes": subj.get("classes", []),
                "note": m.get("note"),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--api-key-env", default="SAKURA_API_KEY")
    ap.add_argument("--papers", type=Path, required=True)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--curves", type=Path, required=True)
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--materialize", action="store_true")
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        print(f"error: set the LLM key in ${args.api_key_env}", file=sys.stderr)
        return 2

    llm = make_llm(
        "openai-compatible",
        model=args.model,
        api_base=args.api_base,
        api_key=api_key,
        max_tokens=args.max_tokens,
    )

    record: dict = {
        "model": args.model,
        "api_base": args.api_base,
        "rows_per_csv": args.rows,
        "max_rounds": args.max_rounds,
        "contract": "mapping-ir staged round-0 (ADR mapping-ir-phase2b-skeleton-wizard.md)",
        "rounds": [],
    }

    with tempfile.TemporaryDirectory(prefix="asterism-staged-dogfood-") as tmp:
        src_dir = Path(tmp)
        names = {"papers.csv": args.papers, "samples.csv": args.samples, "curves.csv": args.curves}
        for name, src in names.items():
            n = cut_subset(src, src_dir / name, args.rows)
            print(f"subset {name}: {n} records")
        paths = [src_dir / n for n in names]

        # --- Job 1: skeleton (the human-gate artifact) ---
        print(f"[{time.strftime('%H:%M:%S')}] job1: propose_skeleton …")
        t0 = time.time()
        sk = propose_skeleton(paths, DOMAIN_HINT, llm=llm)
        record["skeleton_wall_clock_s"] = round(time.time() - t0, 1)
        record["skeleton"] = sk.skeleton
        record["skeleton_maps"] = describe_skeleton(sk.skeleton)
        record["skeleton_all_keys_composite"] = all(
            m["composite"] for m in record["skeleton_maps"] if m["subject_key"]
        )
        print(json.dumps(record["skeleton_maps"], ensure_ascii=False, indent=2))

        # --- Job 2: continue (per-map + document + self-correction) ---
        def on_progress(data: dict) -> None:
            print(f"[{time.strftime('%H:%M:%S')}] {data}")
            if data.get("phase") == "validated":
                record["rounds"].append(
                    {
                        "round": data.get("round"),
                        "issue_count": data.get("issue_count"),
                        "categories": data.get("categories"),
                    }
                )

        print(f"[{time.strftime('%H:%M:%S')}] job2: run_design_loop(skeleton=…) …")
        t1 = time.time()
        result = run_design_loop(
            paths,
            DOMAIN_HINT,
            src_dir,
            llm=llm,
            max_rounds=args.max_rounds,
            on_progress=on_progress,
            skeleton=sk.skeleton,
        )
        record["continue_wall_clock_s"] = round(time.time() - t1, 1)
        record["converged"] = result.converged
        record["terminal_reason"] = result.terminal_reason
        record["initial_issue_count"] = result.initial_issue_count
        record["remaining_issues"] = result.remaining_issues
        record["coverage_dropped"] = result.coverage_dropped

        # Independent gate check on the final (best) staged design.
        mat = materialize_schema(result.proposal_md, src_dir, "dogfood", write=False)
        record["final_design"] = {
            "has_mapping_spec": mat.mapping_ir_yaml is not None,
            "mapping_ir_issues": mat.mapping_ir_issues,
            "compiled": mat.rml_ttl is not None,
        }
        if mat.rml_ttl:
            prepared = substrate.substitute_run_id(mat.rml_ttl)
            try:
                substrate.assert_rml_safe(prepared, src_dir)
                record["final_design"]["assert_rml_safe"] = "pass"
            except Exception as exc:  # noqa: BLE001
                record["final_design"]["assert_rml_safe"] = f"FAIL: {exc}"
            try:
                substrate.validate_rml_design(prepared, src_dir)
                record["final_design"]["validate_rml_design"] = "pass"
            except Exception as exc:  # noqa: BLE001
                record["final_design"]["validate_rml_design"] = f"issues: {exc}"
            if args.materialize:
                try:
                    g = substrate.materialize_to_graph(mat.rml_ttl, src_dir)
                    record["final_design"]["morph_kgc_triples"] = len(g)
                except Exception as exc:  # noqa: BLE001
                    record["final_design"]["morph_kgc_triples"] = f"FAIL: {exc}"

        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            keep = args.out.with_suffix("")
            (keep.parent / f"{keep.name}.proposal.md").write_text(
                result.proposal_md, encoding="utf-8"
            )
            if mat.mapping_ir_yaml:
                (keep.parent / f"{keep.name}.mapping.yaml").write_text(
                    mat.mapping_ir_yaml, encoding="utf-8"
                )

    print("\n===== staged dogfood summary =====")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
