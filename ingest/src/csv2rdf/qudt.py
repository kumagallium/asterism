"""QUDT quantity-kind / unit normalization for starrydata (Phase 2 #2).

Phase 1 stores curve properties as free-text strings (``sd:propertyY``
"Seebeck coefficient", ``sd:unitYString`` "V*K^(-1)"). That makes synonym
matching brittle: "Seebeck coefficient", "thermopower" and "熱起電力" are the
same physical quantity but three different strings.

This module maps those strings to canonical QUDT IRIs
(`http://qudt.org/vocab/quantitykind/...` and `.../unit/...`) using a curated
table in ``qudt_map.yaml``. The ingester then emits *additional* IRI-valued
triples alongside the existing string ones, so the change is backward
compatible — existing queries keep working, and new queries can pivot on the
stable IRI.

Lookups:

- ``quantity_kind_iri`` is case-insensitive (property names are English words;
  "Seebeck Coefficient" == "seebeck coefficient").
- ``unit_iri`` is case-sensitive (unit symbols carry case meaning: V≠v, K≠k,
  S≠s, T≠t), so the raw string is matched verbatim after stripping.

Unmapped inputs return ``None`` (the caller then emits no QUDT triple).
"""
from __future__ import annotations

import functools
import importlib.resources
from typing import Final

import yaml

QUANTITY_KIND_BASE: Final[str] = "http://qudt.org/vocab/quantitykind/"
UNIT_BASE: Final[str] = "http://qudt.org/vocab/unit/"

_MAP_RESOURCE: Final[str] = "qudt_map.yaml"


@functools.lru_cache(maxsize=1)
def _load_map() -> dict[str, dict[str, str]]:
    text = (
        importlib.resources.files("csv2rdf")
        .joinpath(_MAP_RESOURCE)
        .read_text(encoding="utf-8")
    )
    data = yaml.safe_load(text) or {}
    quantity_kinds = data.get("quantity_kinds") or {}
    units = data.get("units") or {}
    # Normalize quantity-kind keys to lowercase once at load time so lookups
    # are O(1) and case-insensitive without re-lowercasing the whole table.
    quantity_kinds = {str(k).strip().lower(): str(v) for k, v in quantity_kinds.items()}
    units = {str(k).strip(): str(v) for k, v in units.items()}
    return {"quantity_kinds": quantity_kinds, "units": units}


def quantity_kind_iri(property_name: str | None) -> str | None:
    """Return the QUDT QuantityKind IRI for a starrydata property name, or None."""
    if not property_name:
        return None
    local = _load_map()["quantity_kinds"].get(property_name.strip().lower())
    return QUANTITY_KIND_BASE + local if local else None


def unit_iri(unit_string: str | None) -> str | None:
    """Return the QUDT Unit IRI for a starrydata unit string, or None.

    Matching is case-sensitive: unit symbols differ by case (e.g. ``S`` siemens
    vs ``s`` second, ``T`` tesla vs ``t`` tonne).
    """
    if not unit_string:
        return None
    local = _load_map()["units"].get(unit_string.strip())
    return UNIT_BASE + local if local else None
