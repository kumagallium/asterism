"""Spike — close the 3 irreducible coverage `…Raw` fallbacks by *tabularizing*
native-JSON sources into JSON-string cells, then reusing the EXISTING vetted
Tier 0 exploders (json_pluck / json_array / split).

The coverage report (`docs/reports/tier0-coverage-sufficiency.md`) leaves three
columns as irreducible raw — `crossref-works.author` (array of objects),
`openlibrary-books.subject`, `github-repos.topics` (arrays of scalars). Each is a
nested array in a *native JSON* source. Morph-KGC cannot (a) pass a native JSON
array into a function, nor (b) reference a parent key from a nested iterator, so a
nested array in a JSON source stays orphaned — hence the raw fallback.

But Tier 0 ALREADY explodes the same shapes when they arrive as a JSON *string* in
a tabular cell (the starrydata author / project_names shape — see
`test_materialize_json_array_and_pluck_from_string_cells`). So the gap is narrow:
a single, generic, dataset-independent transform that flattens a native-JSON
source into a CSV where nested objects become dotted columns and arrays become
JSON-string cells. No new function; the T9 closed set is untouched.

This spike PROVES that transform + the existing RML pattern materializes the three
arrays *linked to their parent row* on the real corpus data. Run:

    PYTHONPATH=../../ingest/src \
      ../../../csv2rdf-mcp/api/.venv/bin/python spike.py

(any Python whose env has morph-kgc + an `asterism` with json_pluck/json_array).
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

from asterism.substrate import materialize_to_graph

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parents[0] / "coverage-corpus" / "datasets"


# Morph-KGC builds its term-map intermediate dataframe with reserved columns named
# `subject` and `predicate` (case-sensitive). A *source* column with either name
# collides: a function input `rml:reference "subject"` then reads the generated
# subject IRI instead of the CSV cell, silently yielding 0 triples. (`object` and
# `graph` are NOT reserved — they pass through.) OpenLibrary literally ships a
# `subject` column, so the tabularizer MUST rename these. This is a substrate-level
# hazard for *any* tabular source, not just nested JSON.
_RESERVED_COLS = {"subject", "predicate"}


def safe_col(name: str) -> str:
    """Rename a column that would collide with Morph-KGC's reserved term columns."""
    return f"{name}_" if name in _RESERVED_COLS else name


# --- the proposed substrate transform: native JSON -> flat JSON-string cells ---
def tabularize(record: dict, prefix: str = "") -> dict[str, str]:
    """Flatten one JSON record into a flat {column: cell} dict, the shape Tier 0
    already explodes from. Rule (generic, no per-dataset logic):

      - scalar leaf        -> column = str(value)
      - nested object      -> recurse with a dotted prefix (owner.login, ...)
      - array (any kind)   -> a SINGLE column holding the array as a JSON string,
                              so json_pluck / json_array / split can explode it.

    Reserved-name collisions (`subject` / `predicate`) are renamed via ``safe_col``.
    """
    out: dict[str, str] = {}
    for key, value in record.items():
        col = safe_col(f"{prefix}{key}")
        if isinstance(value, dict):
            out.update(tabularize(value, prefix=f"{col}."))
        elif isinstance(value, list):
            out[col] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            out[col] = ""
        else:
            out[col] = str(value)
    return out


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["rid", *cols], extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow({"rid": str(i), **r})


# --- RML reused verbatim from the working string-cell test pattern -------------
PREFIXES = """
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix rmlf: <http://w3id.org/rml/> .
@prefix fn:   <https://kumagallium.github.io/asterism/fn/> .
@prefix ex:   <https://ex/> .
"""


def pluck_pom(predicate: str, ref: str, field: str) -> str:
    return f"""  rr:predicateObjectMap [ rr:predicate {predicate} ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_pluck ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "{ref}" ] ] ;
      rmlf:input [ rmlf:parameter fn:p_field ;
        rmlf:inputValueMap [ rmlf:constant "{field}" ] ] ] ] ] ;"""


def array_pom(predicate: str, ref: str) -> str:
    return f"""  rr:predicateObjectMap [ rr:predicate {predicate} ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_array ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "{ref}" ] ] ] ] ] ;"""


def run_case(name: str, json_file: Path, source_csv: str, body_poms: list[str]) -> bool:
    records = json.loads(json_file.read_text(encoding="utf-8"))
    rows = [tabularize(r) for r in records]

    work = Path(tempfile.mkdtemp(prefix=f"spike_{name}_"))
    csv_path = work / source_csv
    write_csv(rows, csv_path)

    poms = "\n".join(body_poms)
    rml = (
        PREFIXES
        + '\n<#M> a rr:TriplesMap ;\n'
        + f'  rml:logicalSource [ rml:source "{source_csv}" ; rml:referenceFormulation ql:CSV ] ;\n'
        + '  rr:subjectMap [ rr:template "https://ex/r/{rid}" ] ;\n'
        + poms.rstrip(";\n ") + " .\n"
    )
    graph = materialize_to_graph(rml, work)
    triples = {(str(s), str(p), str(o)) for s, p, o in graph}

    # Every exploded value must hang off an ex:/r/<rid> subject = linked to parent.
    by_pred: dict[str, set[tuple[str, str]]] = {}
    for s, p, o in triples:
        by_pred.setdefault(p, set()).add((s, o))

    print(f"\n=== {name} ===  rows={len(rows)}  triples={len(triples)}")
    ok = bool(by_pred)  # no predicate produced any triple => FAIL (caught a false pass)
    for p in sorted(by_pred):
        pairs = by_pred[p]
        subj_ok = all(s.startswith("https://ex/r/") for s, _ in pairs)
        distinct_subj = len({s for s, _ in pairs})
        sample = sorted(o for _, o in pairs)[:4]
        print(f"  {p.split('/')[-1]:14s} {len(pairs):3d} triples  "
              f"over {distinct_subj:2d} parents  linked={subj_ok}  e.g. {sample}")
        ok = ok and subj_ok and len(pairs) > 0
    return ok


def main() -> int:
    results: dict[str, bool] = {}

    # crossref: author = array of objects -> json_pluck family/given (the hard one)
    results["crossref-works.author"] = run_case(
        "crossref-works",
        CORPUS / "crossref-works" / "source" / "crossref-works.json",
        "crossref.csv",
        [
            pluck_pom("ex:authorFamily", "author", "family"),
            pluck_pom("ex:authorGiven", "author", "given"),
        ],
    )

    # openlibrary: subject = array of strings -> json_array. NB the source column is
    # literally "subject" → tabularize renames it to "subject_" to dodge Morph-KGC's
    # reserved term column (otherwise 0 triples — the collision this spike uncovered).
    results["openlibrary-books.subject"] = run_case(
        "openlibrary-books",
        CORPUS / "openlibrary-books" / "source" / "openlibrary-books.json",
        "openlibrary.csv",
        [array_pom("ex:subject", "subject_")],
    )

    # github: topics = array of strings -> json_array
    results["github-repos.topics"] = run_case(
        "github-repos",
        CORPUS / "github-repos" / "source" / "github-repos.json",
        "github.csv",
        [array_pom("ex:topic", "topics")],
    )

    print("\n================ VERDICT ================")
    all_ok = True
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
        all_ok = all_ok and v
    print("========================================")
    print("ALL PASS — the 3 irreducible raws close via tabularize + existing Tier 0"
          if all_ok else "SOME FAIL — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
