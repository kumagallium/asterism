#!/usr/bin/env python3
"""Regenerate `ingest/src/asterism/grounding/qudt_quantitykinds.yaml` from QUDT.

A QuantityKind answers "WHAT is this column measuring" — temperature, thermal
conductivity, Seebeck coefficient. `build_qudt_units.py` mirrored the units that answer
"in what". Without this half a materials dataset could say its numbers are in `V/K` but
not that they ARE a Seebeck coefficient, which is the half other people search on.

Run it only when moving to a new QUDT release:

    python scripts/build_qudt_quantitykinds.py --version 3.1.0

Requires pyoxigraph (already an ingest dependency) so the Turtle is parsed by a real
RDF parser rather than a regex over machine-generated text.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

import yaml
from _qudt_mirror import (
    QK_NS,
    QUDT,
    UNIT_NS,
    download,
    en_label,
    header,
    is_deprecated,
    load_turtle,
    local,
    objects,
    subjects_of_type,
)

OUT = Path(__file__).resolve().parents[1] / "ingest/src/asterism/grounding/qudt_quantitykinds.yaml"

#: A one-line gloss helps a person tell near-neighbours apart (Resistivity vs
#: ElectricResistance). QUDT's descriptions run to paragraphs, so keep the first
#: sentence and cap it — the full text is one dereference away.
_GLOSS_CHARS = 180


#: QUDT descriptions carry LaTeX inline ("$k$ (also denoted as $\\lambda$)"). Cutting the
#: math out leaves its scaffolding behind — "conductivity, (also denoted as ), is the
#: property" — and no amount of bracket-sweeping fixes an English sentence built around
#: a symbol. So a description containing math is SKIPPED, not repaired: the next
#: candidate may be clean, and a quantity with no gloss still reads fine from its label.
_HAS_MATH = re.compile(r"\$|\\\\[a-zA-Z]+")


def _gloss(store, subj) -> str:
    for pred in (QUDT + "plainTextDescription", "http://purl.org/dc/terms/description"):
        for o in objects(store, subj, pred):
            text = " ".join(str(o.value).split())
            if not text or _HAS_MATH.search(text):
                continue
            # First sentence, unless that is already longer than the cap.
            head = text.split(". ")[0].rstrip(".")
            if len(head) > _GLOSS_CHARS:
                head = head[:_GLOSS_CHARS].rsplit(" ", 1)[0] + "…"
            return head
    return ""


def build(ttl: Path, version: str, retrieved: str) -> dict:
    store = load_turtle(ttl)
    kinds: dict[str, dict] = {}
    skipped = 0
    for s in subjects_of_type(store, QUDT + "QuantityKind"):
        name = local(s.value, QK_NS)
        if not name:
            continue
        if is_deprecated(store, s):
            skipped += 1
            continue
        entry: dict = {}
        if label := en_label(store, s):
            entry["label"] = label
        if gloss := _gloss(store, s):
            entry["gloss"] = gloss
        symbols = [
            o.value for o in objects(store, s, QUDT + "symbol") if hasattr(o, "value")
        ]
        if symbols:
            entry["symbol"] = symbols[0]
        # Which units this quantity may be measured in. Kept because it is the check a
        # person actually makes ("my column is in W/(m·K) — is this the right kind?"),
        # and because it lets the unit a column already carries rank the candidates.
        units = sorted(
            {
                lo
                for o in objects(store, s, QUDT + "applicableUnit")
                if (lo := local(getattr(o, "value", ""), UNIT_NS))
            }
        )
        if units:
            entry["units"] = units
        kinds[name] = entry

    print(f"quantity kinds: {len(kinds)} (skipped deprecated: {skipped})", file=sys.stderr)
    return {
        "source": f"https://qudt.org/{version}/vocab/quantitykind",
        "version": version,
        "retrieved": retrieved,
        "license": "CC-BY-4.0",
        "namespace": QK_NS,
        "unit_namespace": UNIT_NS,
        "quantity_kinds": dict(sorted(kinds.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="3.1.0", help="QUDT release to mirror")
    ap.add_argument("--ttl", type=Path, default=None, help="local quantitykind.ttl")
    ap.add_argument("--retrieved", default=None, help="YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    ttl = args.ttl
    if ttl is None:
        url = f"https://qudt.org/{args.version}/vocab/quantitykind"
        print(f"downloading {url}", file=sys.stderr)
        ttl = download(url, Path(f"/tmp/qudt-quantitykind-{args.version}.ttl"))

    retrieved = args.retrieved or _dt.datetime.now(_dt.UTC).date().isoformat()
    data = build(ttl, args.version, retrieved)
    args.out.write_text(
        header(
            "build_qudt_quantitykinds.py",
            data["version"],
            "A MIRROR of the QUDT quantity-kind vocabulary (CC-BY 4.0), unlike\n"
            "# known_vocabs.yaml which is hand-curated.",
        )
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
