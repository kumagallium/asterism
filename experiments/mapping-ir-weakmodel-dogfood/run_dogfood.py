"""Weak-model dogfood for the Mapping IR contract (ADR mapping-ir-compiler.md §10.3).

Reproduces the production failure case that motivated the ADR — the same
Starrydata CSVs as registry ``dataset-548d5ca3``, proposed by a weak model
(Qwen3.6-35B-A3B / gpt-oss-120b on Sakura AI Engine) — and measures whether the
new §9 mapping-spec contract makes the SYNTAX error classes structurally
disappear and lets the self-correction loop converge.

What it runs (the exact production pipeline, no api server needed):
  run_design_loop (round-0 propose → IR validation → refine rounds)
  → materialize (IR extraction + deterministic compilation)
  → the unchanged hard gates on the compiled RML (assert_rml_safe /
    validate_rml_design / T9 closed set)
  → (optional --materialize) a REAL Morph-KGC materialization of the subset.

What it records (JSON + human summary):
  per-round issue counts and categories; the syntax-class categories that the
  ADR predicts are now IMPOSSIBLE (turtle / function-set / safety / fnml);
  convergence + terminal reason; the gates' verdict on the final design;
  model coordinates + wall-clock + token usage.

Usage (needs only an LLM key — no production access):
  export SAKURA_API_KEY=...   # or any env var, see --api-key-env
  ingest/.venv or api/.venv python with asterism+asterism_step0 installed:

  .venv/bin/python experiments/mapping-ir-weakmodel-dogfood/run_dogfood.py \\
      --model Qwen3.6-35B-A3B \\
      --api-base https://api.ai.sakura.ad.jp/v1 \\
      --papers ../starrydata_dataset/starrydata_papers.csv \\
      --samples ../starrydata_dataset/starrydata_samples.csv \\
      --curves ../starrydata_dataset/starrydata_curves.csv \\
      --rows 40 --out dogfood-qwen.json --materialize

The row subset is cut with the csv module (quoted cells hold JSON with embedded
newlines — a naive ``head`` would split a record).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from pathlib import Path

from asterism import substrate
from asterism_api.design_loop import run_design_loop
from asterism_step0.llm import make_llm
from asterism_step0.materialize import materialize_schema

# The issue categories (design_loop.classify) that the raw-Turtle contract
# produced and the Mapping IR contract makes structurally impossible.
SYNTAX_CLASS_CATEGORIES = {"turtle", "function-set", "safety"}

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, help="e.g. Qwen3.6-35B-A3B / gpt-oss-120b")
    ap.add_argument("--api-base", required=True, help="OpenAI-compatible base URL")
    ap.add_argument(
        "--api-key-env",
        default="SAKURA_API_KEY",
        help="Name of the env var holding the key (default: SAKURA_API_KEY)",
    )
    ap.add_argument("--papers", type=Path, required=True)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--curves", type=Path, required=True)
    ap.add_argument("--rows", type=int, default=40, help="records per CSV (default 40)")
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=None, help="per-generation cap override")
    ap.add_argument("--out", type=Path, default=None, help="write the JSON record here")
    ap.add_argument(
        "--materialize",
        action="store_true",
        help="also run a REAL Morph-KGC materialization of the final design",
    )
    args = ap.parse_args()

    import os

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
        "contract": "mapping-ir (ADR mapping-ir-compiler.md)",
        "rounds": [],
    }

    with tempfile.TemporaryDirectory(prefix="asterism-dogfood-") as tmp:
        src_dir = Path(tmp)
        names = {"papers.csv": args.papers, "samples.csv": args.samples, "curves.csv": args.curves}
        for name, src in names.items():
            n = cut_subset(src, src_dir / name, args.rows)
            print(f"subset {name}: {n} records")

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

        t0 = time.time()
        result = run_design_loop(
            [src_dir / n for n in names],
            DOMAIN_HINT,
            src_dir,
            llm=llm,
            max_rounds=args.max_rounds,
            on_progress=on_progress,
        )
        record["wall_clock_s"] = round(time.time() - t0, 1)
        usage = getattr(llm, "last_usage", None)
        if usage is not None:
            record["last_usage"] = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            }

        record["converged"] = result.converged
        record["terminal_reason"] = result.terminal_reason
        record["initial_issue_count"] = result.initial_issue_count
        record["remaining_issues"] = result.remaining_issues
        record["coverage_dropped"] = result.coverage_dropped

        # The ADR's core claim: syntax-class issues are structurally zero.
        syntax_hits = [
            r for r in record["rounds"]
            if any(c in SYNTAX_CLASS_CATEGORIES for c in (r.get("categories") or {}))
        ]
        record["syntax_class_issue_rounds"] = syntax_hits
        record["syntax_class_zero"] = not syntax_hits

        # Independent gate check on the final (best) design: extract → compile →
        # the unchanged hard gates (what the 422 ingest gate would run).
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

        # Keep the artifacts for the report's evidence trail.
        if args.out:
            keep = args.out.with_suffix("")
            (keep.parent / f"{keep.name}.proposal.md").write_text(
                result.proposal_md, encoding="utf-8"
            )
            if mat.mapping_ir_yaml:
                (keep.parent / f"{keep.name}.mapping.yaml").write_text(
                    mat.mapping_ir_yaml, encoding="utf-8"
                )
            if mat.rml_ttl:
                (keep.parent / f"{keep.name}.mapping.rml.ttl").write_text(
                    mat.rml_ttl, encoding="utf-8"
                )

    print("\n===== dogfood summary =====")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
