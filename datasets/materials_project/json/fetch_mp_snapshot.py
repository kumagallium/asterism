"""Fetch a **JSON snapshot** of Materials Project facts for the host compositions
that actually appear in Starrydata.

Why a snapshot and not a live connector: the reproducible, declarative path is
*API → JSON snapshot → JSON ingest* (ADR ``non-csv-sources.md`` §7). The snapshot
is the persisted, citable source; ingest reads it declaratively with the existing
``mp.rml.ttl`` (no RML change — this script emits the same record shape as
``build_json_snapshot.py``, just with more fields and many more records).

Why only Starrydata's compositions: the point of this dataset is the *structure*
dimension Starrydata lacks (``dataset.toml``), joined on reduced host formula.
Mirroring all ~150k MP materials would be a different project. Measured on
starrydata_samples.csv (90,547 rows): 33,653 raw compositions collapse to about
6,100 unique host formulas — a 1/25 of a full mirror.

Host-formula normalization (stripping dopants and non-stoichiometry) is reused
from the vetted spike ``experiments/mp-linking-poc/link_mp.py`` rather than
re-implemented, so both paths agree on what "the host phase" means. That spike
imports rdflib, so run this with an interpreter that has it::

    ingest/.venv/bin/python datasets/materials_project/json/fetch_mp_snapshot.py --probe

This is a **content-authoring tool**, not runtime code: it is run by a human, its
output is committed, and nothing in the ingest path executes it.

Usage::

    # 1. see what the API actually returns for one material (no field guessing)
    python fetch_mp_snapshot.py --probe

    # 2. small run first — writes mp.sample.json
    python fetch_mp_snapshot.py --limit 20 --out mp.sample.json

    # 3. the real thing (resumable: re-run after an interruption and it continues)
    python fetch_mp_snapshot.py --out mp.json

The API key is read from ``MP_API_KEY`` in the environment or from the repo's
gitignored ``.env``. It is never printed or written to the snapshot.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from importlib import util as _importlib_util
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SPIKE = REPO / "experiments" / "mp-linking-poc" / "link_mp.py"
MP_API_BASE = "https://api.materialsproject.org"
MP_PAGE = "https://next-gen.materialsproject.org/materials/"

# We request ``_all_fields=true`` and drop from the response, rather than listing
# the fields we want. The summary endpoint returns 69 fields today; an allow-list
# would silently miss whatever MP adds next, and the point of this snapshot is to
# carry as much of the material's characterization as is useful. What gets dropped
# is only what does not belong in a knowledge graph:
EXCLUDE_FIELDS = frozenset({
    # Every atomic coordinate. Kilobytes per material, and — as the mp_all.csv
    # exercise showed — not something anyone queries with SPARQL. Coordinates
    # belong in a structure file. (Note this is MP's own "structure" field; the
    # snapshot's own nested "structure" object below is the symmetry block, kept
    # under that name so the existing mp.rml.ttl reads it unchanged.)
    "structure",
    # Pointers into other endpoints or bulk objects, not values.
    "bandstructure", "dos", "dos_energy_up", "dos_energy_down", "xas",
    "phonon_IDs", "grain_boundaries", "has_reconstructed",
    # Builder bookkeeping — facts about MP's pipeline, not about the material.
    # (database_IDs is NOT here: it carries ICSD identifiers, which are a link to
    # an external standard rather than pipeline noise.)
    "builder_meta", "origins", "task_ids", "has_props",
    "warnings", "property_name", "deprecation_reasons",
    # Redundant with formula_pretty + composition_reduced.
    "composition",
    # Handled explicitly in to_record().
    "material_id", "formula_pretty", "symmetry",
})

# A run of capital letters with no lowercase is an acronym, not a formula:
# Starrydata's battery rows carry "LMB" / "RMB" / "SIB" / "ASSLSB" in the
# composition column. ``parse_formula`` happily reads those as L+M+B, so they
# reach the API and come back empty. Filter them here instead of spending a
# request on each.
_ELEMENTS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu "  # noqa: SIM905
    "Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba "
    "La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi "
    "Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds "
    "Rg Cn Nh Fl Mc Lv Ts Og".split()
)
# Which nested objects get flattened into one column per sub-key. Only those whose
# sub-keys are FIXED across materials belong here: bulk_modulus/shear_modulus are
# always {voigt, reuss, vrh}, database_IDs always {icsd, pauling}. A dict whose keys
# vary per material — composition_reduced is {"Bi": 2.0, "Te": 3.0} for one material
# and {"Zn": 4.0, "Sb": 3.0} for the next — would otherwise sprout one sparse column
# per element (84 of them, nearly all empty). Those stay a single JSON-string column
# instead, which fn:json_get reads by path.
FLATTEN_DICTS = frozenset({"bulk_modulus", "shear_modulus", "database_IDs"})

_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)")


def load_api_key() -> str:
    """``MP_API_KEY`` from the environment, falling back to the repo's ``.env``."""
    key = os.environ.get("MP_API_KEY", "").strip()
    if key:
        return key
    env = REPO / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MP_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit(
        "MP_API_KEY が見つかりません。次のどちらかで渡してください:\n"
        "  export MP_API_KEY=...\n"
        f"  echo 'MP_API_KEY=...' >> {env}   (.env は gitignore 済み)"
    )


def load_spike():
    """Import the vetted host-normalization spike as a module."""
    if not SPIKE.is_file():
        raise SystemExit(f"母相正規化のスパイクが見つかりません: {SPIKE}")
    spec = _importlib_util.spec_from_file_location("link_mp", SPIKE)
    assert spec and spec.loader
    mod = _importlib_util.module_from_spec(spec)
    sys.modules["link_mp"] = mod  # dataclass needs the module registered first
    try:
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"スパイクの import に失敗しました ({exc.name} が無い)。rdflib のある "
            "インタプリタで実行してください:\n"
            "  ingest/.venv/bin/python datasets/materials_project/json/fetch_mp_snapshot.py …"
        ) from exc
    return mod


def is_plausible_formula(formula: str) -> bool:
    """True when ``formula`` reads as a chemical formula rather than an acronym.

    Two rules, in order:

    1. Every token must be a real element symbol and the tokens must consume the
       whole string — this rejects ``LMB`` (no element ``L``/``M``) and ``Li|S``
       (leftover punctuation).
    2. An all-caps string with no digits and **three or more** tokens is treated as
       an acronym even when the letters happen to be elements: ``SIB`` reads as
       S+I+B but means "sodium-ion battery". The three-token floor is what keeps
       single elements (``C``, ``B``, ``O`` — graphite, boron, oxygen are real
       Starrydata hosts) and two-element caps out of the acronym bucket.

    The cost of rule 2 is a genuine three-element all-caps formula (``HCN``), which
    does not appear among Starrydata's host phases. A false reject only skips one
    API request; a false accept spends one and gets nothing back.
    """
    if not formula:
        return False
    tokens = _TOKEN.findall(formula)
    if not tokens:
        return False
    if "".join(sym + cnt for sym, cnt in tokens) != formula:
        return False  # leftover punctuation ("Li|S", "Na-ion")
    if not all(sym in _ELEMENTS for sym, _ in tokens):
        return False
    is_bare_caps = formula.isupper() and not any(c.isdigit() for c in formula)
    return not (is_bare_caps and len(tokens) >= 3)


def host_formulas(csv_path: Path, spike) -> list[tuple[str, int]]:
    """Unique host formulas from a Starrydata samples CSV, most frequent first.

    Returns ``(formula, sample_count)`` so the caller can report coverage and so a
    truncated run (``--limit``) covers the compositions that matter most.
    """
    counts: Counter[str] = Counter()
    skipped_acronym: Counter[str] = Counter()
    unparsed = 0
    rows = 0

    def _clean(fh):
        for line in fh:  # some exports carry stray NULs
            yield line.replace("\0", "")

    csv.field_size_limit(sys.maxsize)
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(_clean(fh)):
            rows += 1
            comp = (row.get("composition") or "").strip()
            if not comp:
                continue
            result = spike.normalize_host(comp, spike.read_material_family(row))
            if result is None or not result.candidates:
                unparsed += 1
                continue
            formula = result.candidates[0].formula
            if not is_plausible_formula(formula):
                skipped_acronym[formula] += 1
                continue
            counts[formula] += 1

    print(
        f"starrydata: {rows:,} 行 / 母相不明 {unparsed:,} / "
        f"略号として除外 {len(skipped_acronym):,} 種 ({sum(skipped_acronym.values()):,} 行) / "
        f"ユニーク母相 {len(counts):,}",
        file=sys.stderr,
    )
    if skipped_acronym:
        top = ", ".join(f for f, _ in skipped_acronym.most_common(8))
        print(f"  除外した略号の例: {top}", file=sys.stderr)
    return counts.most_common()


def mp_get(path: str, params: dict[str, str], api_key: str, *, retries: int = 4) -> Any:
    """One GET against the MP API, retrying on 429/5xx with exponential backoff.

    A browser-ish User-Agent is required: urllib's default is rejected by
    Cloudflare with error 1010 even for an authorized key (observed in the spike).
    """
    url = f"{MP_API_BASE}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-API-KEY": api_key,
        "Accept": "application/json",
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
    })
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                print(f"    HTTP {exc.code} — {wait:.0f}s 待って再試行", file=sys.stderr)
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise SystemExit(f"MP API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise SystemExit(f"MP API へ接続できません: {exc.reason}") from exc
    raise SystemExit("MP API: 再試行を使い切りました")


def probe(api_key: str) -> None:
    """Fetch one material with no field filter and print what the API offers."""
    payload = mp_get(
        "/materials/summary/",
        {"formula": "Bi2Te3", "_limit": "1", "_all_fields": "true"},
        api_key,
    )
    data = payload.get("data") or []
    if not data:
        raise SystemExit("probe: データが返りませんでした")
    doc = data[0]
    kept = dropped = 0
    print(f"\n/materials/summary/ が返すフィールド ({len(doc)} 個):\n")
    for key in sorted(doc):
        value = doc[key]
        preview = json.dumps(value, ensure_ascii=False, default=str)
        if len(preview) > 84:
            preview = preview[:81] + "…"
        excluded = key in EXCLUDE_FIELDS and key not in (
            "material_id", "formula_pretty", "symmetry")
        if excluded:
            dropped += 1
        else:
            kept += 1
        mark = "・" if excluded else "  "
        print(f"{mark}{key:<38} {type(value).__name__:<9} {preview}")
    print(f"\n取り込む {kept} / 除外する {dropped}   [・ = EXCLUDE_FIELDS で除外]")


def best_match(formula: str, api_key: str) -> dict[str, Any] | None:
    """The most stable MP entry for ``formula`` (lowest energy_above_hull)."""
    payload = mp_get("/materials/summary/", {
        "formula": formula,
        "_all_fields": "true",
        "_limit": "50",
    }, api_key)
    data = payload.get("data") or []
    if not data:
        return None
    return min(data, key=lambda d: (d.get("energy_above_hull") is None,
                                    d.get("energy_above_hull") or 0.0))


def load_legacy_ids() -> dict[str, str]:
    """``host formula -> legacy mp-id`` from the committed seed CSV.

    MP renumbered its materials (``mp-34202`` -> ``mp-aaaabypm``) with the r2SCAN
    recalculation, and the new API never echoes the old id back. The eleven
    materials this dataset already published carry their old ids in the seed CSV,
    so that file is the only place the mapping survives.

    Why the snapshot has to carry it: the id-move ledger
    (``id-move-after-publish.md``) builds "old IRI -> new IRI" by running ONE
    source through two subject templates. Putting ``mp_id_legacy`` next to
    ``mp_id`` in the same record is what makes that possible here — otherwise the
    two ids live in different files and the ledger cannot be compiled.
    """
    path = HERE.parent / "seed" / "csv" / "materials_project.csv"
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            row["formula"]: row["mp_id"]
            for row in csv.DictReader(fh)
            if row.get("formula") and row.get("mp_id")
        }


def to_record(doc: dict[str, Any], host_formula: str, samples: int,
              legacy: dict[str, str]) -> dict[str, Any]:
    """Shape one API document into the snapshot record.

    The top-level keys and the nested ``structure`` object match what
    ``build_json_snapshot.py`` emits, so ``mp.rml.ttl`` reads this unchanged; the
    extra fields are additive. Absent fields are omitted rather than nulled —
    Morph-KGC drops a row when a referenced field is null, so an omitted key is
    the safer shape for a sparse response.
    """
    sym = doc.get("symmetry") or {}
    record: dict[str, Any] = {
        "mp_id": doc.get("material_id"),
        "formula": doc.get("formula_pretty"),
        "mp_page": MP_PAGE + str(doc.get("material_id")),
        # what this snapshot was resolved FROM (provenance of the join key)
        "host_formula": host_formula,
        "starrydata_samples": samples,
        # only the already-published materials have one; absent elsewhere so the
        # id-move ledger simply has no row for them (Morph-KGC drops null refs)
        "mp_id_legacy": legacy.get(host_formula),
        "structure": {
            "space_group_symbol": sym.get("symbol"),
            "space_group_number": sym.get("number"),
            "crystal_system": sym.get("crystal_system"),
            "point_group": sym.get("point_group"),
        },
    }

    # Everything else the API returned, minus EXCLUDE_FIELDS. Shapes are flattened
    # to what the ingest path can actually read:
    #   dict  -> one key per sub-field ("bulk_modulus" {voigt,reuss,vrh} becomes
    #            bulk_modulus_voigt / _reuss / _vrh), because a nested object under
    #            a *sibling* of "structure" has no place in the existing RML.
    #   list  -> a JSON string, which fn:json_array (scalars, e.g. elements) or
    #            fn:json_pluck (objects, e.g. decomposes_to) explodes into one
    #            triple per element.
    #   null / empty -> omitted entirely. Morph-KGC drops a whole row when a
    #            referenced field is null, so an absent key is the safer shape.
    for key in sorted(doc):
        if key in EXCLUDE_FIELDS:
            continue
        value = doc[key]
        if value is None:
            continue
        if isinstance(value, dict) and key not in FLATTEN_DICTS:
            # keys vary per material -> keep as one JSON-string column
            record[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, dict):
            for sub, sub_value in sorted(value.items()):
                if sub_value is None or isinstance(sub_value, dict):
                    continue
                if isinstance(sub_value, list):
                    if sub_value:  # e.g. database_IDs.icsd -> fn:json_array
                        record[f"{key}_{sub}"] = json.dumps(
                            sub_value, ensure_ascii=False, sort_keys=True)
                else:
                    record[f"{key}_{sub}"] = sub_value
        elif isinstance(value, list):
            if value:
                record[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            record[key] = value

    # drop empty nested keys so a symmetry-less entry stays clean
    record["structure"] = {k: v for k, v in record["structure"].items() if v is not None}
    return {k: v for k, v in record.items() if v is not None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", type=Path,
                    default=Path.home()
                    / "Downloads/starrydata_dataset_260301-0300/starrydata_samples.csv",
                    help="Starrydata samples CSV: composition / sample_info 列を読む")
    ap.add_argument("--out", type=Path, default=HERE / "mp.json", help="出力 JSON")
    ap.add_argument("--limit", type=int, default=0, help="上位 N 母相だけ。0 = 全件")
    ap.add_argument("--sleep", type=float, default=0.1, help="リクエスト間の待ち [s]")
    ap.add_argument("--probe", action="store_true",
                    help="1 件だけ取得しフィールド一覧を表示して終了")
    args = ap.parse_args(argv)

    api_key = load_api_key()
    if args.probe:
        probe(api_key)
        return 0

    if not args.csv.is_file():
        raise SystemExit(f"Starrydata CSV が見つかりません: {args.csv}")

    formulas = host_formulas(args.csv, load_spike())
    if args.limit:
        formulas = formulas[: args.limit]

    # Resume: keep whatever a previous run already resolved.
    cache_path = args.out.with_suffix(".cache.json")
    cache: dict[str, Any] = {}
    if cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"再開: {len(cache):,} 件はキャッシュ済み [{cache_path.name}]", file=sys.stderr)

    total = len(formulas)
    resolved = missed = 0
    try:
        for i, (formula, samples) in enumerate(formulas, 1):
            if formula in cache:
                continue
            doc = best_match(formula, api_key)
            cache[formula] = {"doc": doc, "samples": samples}
            if doc:
                resolved += 1
            else:
                missed += 1
            if i % 25 == 0 or i == total:
                print(f"  {i:,}/{total:,}  解決 {resolved:,} / 該当なし {missed:,}",
                      file=sys.stderr)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\n中断しました。キャッシュを保存して終了します: 再実行で続きから。", file=sys.stderr)
    finally:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    legacy = load_legacy_ids()
    records = [
        to_record(entry["doc"], host, entry["samples"], legacy)
        for host, entry in cache.items()
        if entry.get("doc")
    ]
    records.sort(key=lambda r: (r.get("formula") or "", r.get("mp_id") or ""))
    args.out.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    hit = len(records)
    print(f"\n{args.out} に {hit:,} 材料を書きました "
          f"[問い合わせた母相 {len(cache):,} / 該当なし {len(cache) - hit:,}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
