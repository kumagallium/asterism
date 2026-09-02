"""Deterministic evidence for the skeleton human gate (Phase 2b follow-up).

Why this module exists
----------------------
The skeleton gate asks a domain expert — NOT an ontology engineer — to confirm
the single costliest design decision: the subject key. "Is
``sdr:point/{2θ (deg)}`` a good key?" is unanswerable from the template string
alone; it is trivially answerable from the DATA ("312 of 1,024 rows would
collapse onto an ID another row already uses"). The inspector already computes
exactly this for the AI (uniqueness statistics ride the prompt); this module
computes it FOR THE HUMAN, per skeleton map, so the gate shows evidence instead
of asking for faith.

Everything here is deterministic and LLM-free: re-read the (dialect-applied)
source, test the AI's chosen key columns for global uniqueness, show real
example IDs (prefix-expanded), name concrete colliding rows, and offer the
inspector's own unique key combinations as one-click fix candidates. The same
function serves the initial skeleton response and the re-validate endpoint the
gate calls after a human edit.

Scope: tabular sources (CSV/TSV and dialect-read instrument text). JSON and
XML/document maps get an honest ``checkable: false`` note instead of a guess —
never a silent pass.
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from .dialect import SourceDialect
from .inspect import (
    SourceInspection,
    _check_uniqueness,
    _dialect_rows,
    _stream_rows,
    inspect_source_set,
)
from .instance_iri import (
    dataset_namespace_info,
    derive_prefix_pair,
    normalize_iri_base,
    placeholder_prefix_issue,
    slugify_dataset_name,
)

__all__ = ["annotate_skeleton", "apply_key_safety_fix"]

# A {column} placeholder inside a subject template (same shape the IR compiler
# expands; an escaped \{ is a literal brace, not a placeholder).
_PLACEHOLDER = re.compile(r"(?<!\\)\{([^{}]+)\}")
# A CURIE head: `sdr:point/…` → prefix `sdr`. A full IRI (http/https) has no
# prefix to resolve.
_CURIE_HEAD = re.compile(r"^([A-Za-z][\w.-]*):")

# How many example IDs / collision groups to surface per map — enough to make
# the failure concrete, few enough to stay a glance.
_PREVIEW_ROWS = 3
_COLLISION_EXAMPLES = 2
_KEY_CANDIDATES = 3
# Near-name suggestions per wrong column — same count/threshold as the mapping-IR
# parser's "Did you mean:" hint, so the gate and the compiler never disagree.
_SUGGEST_N = 3
# The entity card: how many stable property values it shows, and how many
# fighting values per conflicting column (real accident evidence, not a dump).
_CARD_VALUE_COLUMNS = 8
_CONFLICT_VALUES = 3
# Measurement-valued columns make semantically wrong IDs even when accidentally
# unique — candidates made ONLY of these types rank last, never first.
_MEASUREMENT_TYPES = {"xsd:double", "xsd:float", "xsd:decimal"}


def _expand_curie(value: str, prefixes: Mapping[str, str]) -> str:
    """Expand a leading `prefix:` through the skeleton's declared prefixes."""
    m = _CURIE_HEAD.match(value)
    if not m or value.startswith(("http://", "https://")):
        return value
    ns = prefixes.get(m.group(1))
    return ns + value[m.end() :] if ns else value


def _undeclared_prefixes(
    template: str | None, classes: Sequence[str], prefixes: Mapping[str, str]
) -> list[str]:
    """CURIE prefixes used by the subject that ``prefixes`` never declares."""
    heads: set[str] = set()
    for value in [template or "", *classes]:
        if value.startswith(("http://", "https://")):
            continue
        m = _CURIE_HEAD.match(value)
        if m:
            heads.add(m.group(1))
    # xsd is builtin for the IR compiler; never report it.
    return sorted(h for h in heads if h != "xsd" and h not in prefixes)


def _read_rows(path: Path, dialect: SourceDialect | None) -> list[dict[str, str]]:
    """Materialise dict rows the same way the inspector read this source."""
    if dialect is not None:
        return _dialect_rows(path, dialect)
    return list(_stream_rows(path))


def _render_template(template: str, row: Mapping[str, str], prefixes: Mapping[str, str]) -> str:
    """One row's ID exactly as the mapping would mint it (prefix-expanded).

    Values are substituted verbatim — no escaping — because that IS what the
    RML template does; showing a space or unit inside the resulting IRI is a
    feature (the human should see it), not a rendering bug.
    """
    expanded = _expand_curie(template, prefixes)
    return _PLACEHOLDER.sub(lambda m: row.get(m.group(1), ""), expanded)


def _collision_examples(
    rows: list[dict[str, str]], key: tuple[str, ...], first_data_line: int
) -> list[dict[str, Any]]:
    """The largest duplicate-key groups, with 1-based file line numbers.

    ``first_data_line`` counts the preamble (dialect ``skip_rows``) and the
    header, so the numbers match the file as opened in an editor/spreadsheet.
    Rows with an empty key cell are skipped (mirrors ``_check_uniqueness``,
    which only considers rows where every key column is non-empty).
    """
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        values = tuple((row.get(col) or "").strip() for col in key)
        if key and any(v == "" for v in values):
            continue
        groups[values].append(i + first_data_line)
    dupes = sorted(
        ((vals, lines) for vals, lines in groups.items() if len(lines) > 1),
        key=lambda item: (-len(item[1]), item[1][0]),
    )
    out: list[dict[str, Any]] = []
    for values, lines in dupes[:_COLLISION_EXAMPLES]:
        out.append(
            {
                "key_values": dict(zip(key, values, strict=True)) if key else {},
                "row_count": len(lines),
                "line_numbers": lines[:4],
            }
        )
    return out


def _measurement_only(inspection: SourceInspection, key: Sequence[str]) -> bool:
    """True when EVERY key column holds a measured OUTCOME (double/float/decimal).

    Such a key can be unique in today's rows only by accident — two later runs
    measuring the same value collide — so it is never a semantically safe
    identity, even when the current file happens to pass the uniqueness check.

    A column that is an evenly spaced monotonic sequence is excluded
    (``ColumnSummary.sampling_grid``): the rows were sampled ON it, so it is a
    value someone SET rather than one that was measured, and it does not get
    corrected afterwards. Nothing here knows what the column means — the rule is
    the spacing in the data, which sorting a measured column does not produce.

    Why it is worth excluding at all: when every column of a file is numeric,
    every key the gate can offer is "measurement-only" under the plain rule, so
    the warning appears on the correct answer and the person has nothing to
    choose (live 2026-08-19, a 3001-row two-column instrument sweep).
    """
    types = {c.name: c.inferred_type for c in inspection.columns}
    grids = {c.name for c in inspection.columns if getattr(c, "sampling_grid", False)}
    if not key:
        return False
    return all(types.get(col) in _MEASUREMENT_TYPES for col in key) and not all(
        col in grids for col in key
    )


def _class_numeric_key_caution(
    inspection: SourceInspection, key: Sequence[str], classes: Sequence[str]
) -> list[dict[str, str]]:
    """Class names that look like a measured KEY column's name.

    Real dogfood (ZEM): the AI promoted the first numeric column into the row
    identity — key ``{Measurement temp.(C)}`` AND class ``Temperature`` — so
    the design claimed "a temperature has a resistivity". The properties-in-
    the-box diagram makes that visible AFTER the design lands; this flags it
    AT the gate. Only computed when the key is measurement-only (the K7
    caution state — exactly when a value column was promoted to identity, name
    included), which keeps a legitimate row class like ``Measurement`` over a
    mixed key out of scope. Match: the class local name vs each numeric key
    column's word tokens, exact or prefix either way with ≥ 4 shared chars
    (``temp`` ≈ ``temperature``). Deterministic, domain-free."""
    types = {c.name: c.inferred_type for c in inspection.columns}
    out: list[dict[str, str]] = []
    for cls in classes:
        local = re.sub(r"[^a-z0-9]", "", re.split(r"[:#/]", str(cls))[-1].lower())
        if len(local) < 4:
            continue
        for col in key:
            if types.get(col) not in _MEASUREMENT_TYPES:
                continue
            for token in re.split(r"[^a-z0-9]+", col.lower()):
                if len(token) < 4:
                    continue
                if token == local or token.startswith(local) or local.startswith(token):
                    out.append({"class": str(cls), "column": col, "token": token})
                    break
            else:
                continue
            break  # one hit per class is enough for the caution
    return out


def _key_candidates(
    inspection: SourceInspection, current_key: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Unique key combinations the inspector already proved, smallest first.

    A candidate whose EVERY column is a measurement type (double/float/decimal)
    is only accidentally unique — it ranks last and carries
    ``measurement_only: true`` so the UI can caveat it.
    """
    current = tuple(sorted(current_key))
    seen: set[tuple[str, ...]] = set()
    ranked = sorted(
        (r for r in inspection.uniqueness_reports if r.is_unique),
        key=lambda r: (
            _measurement_only(inspection, r.key),
            len(r.key),
            -r.total_rows_considered,
        ),
    )
    out: list[dict[str, Any]] = []
    for report in ranked:
        canonical = tuple(sorted(report.key))
        if canonical == current or canonical in seen:
            continue
        seen.add(canonical)
        out.append(
            {
                "columns": list(report.key),
                "rows_considered": report.total_rows_considered,
                "measurement_only": _measurement_only(inspection, report.key),
            }
        )
        if len(out) >= _KEY_CANDIDATES:
            break
    return out


def _near_column_names(missing: Sequence[str], columns: Sequence[str]) -> list[dict[str, Any]]:
    """For each column the design names but the file lacks, the closest real ones.

    Same edit-distance rule as the mapping-IR parser's "Did you mean:" hint
    (``difflib`` at 0.6), so a fix offered at the gate and a fix suggested by
    the compiler never disagree. Entries with no near name are still returned —
    the UI needs to say "this one has no close match" rather than omit it.
    """
    return [
        {
            "column": str(name),
            "suggestions": difflib.get_close_matches(
                str(name), [str(c) for c in columns], n=_SUGGEST_N, cutoff=0.6
            ),
        }
        for name in missing
    ]


def _collapse_kind(report: Any) -> str:
    """Classify what the key does to the rows — the judgment the gate needs.

    ``unique``    every row keeps its own ID (a row-level entity — normal).
    ``singleton`` ALL rows merge into one ID (a file-scoped entity: the
                  reference card, the run header — merging is the POINT, so
                  this must never be presented as the collision accident).
    ``partial``   some rows merge, some don't — the overwrite accident.
    """
    if report.is_unique:
        return "unique"
    if report.distinct_tuples == 1:
        return "singleton"
    return "partial"


def _entity_preview(
    rows: list[dict[str, str]],
    key: tuple[str, ...],
    template: str,
    prefixes: Mapping[str, str],
    inspection: SourceInspection,
    first_data_line: int,
    collapse_kind: str,
    distinct_ids: int,
) -> dict[str, Any] | None:
    """One representative entity rendered as the CARD the mapping would build.

    The ID previews show URL syntax; this shows the consequence — which rows
    merge onto one subject and what values it ends up carrying. Group choice:
    for a partial collapse the LARGEST colliding group (the accident must be
    visible), otherwise the first group in row order.

    Column verdicts within the representative group:
    - one distinct non-empty value → a property the card carries;
    - several values on a ``partial`` map → a conflict (overwrite fight),
      shown with the real values and file line numbers;
    - several values on a ``singleton`` map → NOT a conflict: the column
      varies per row, so it belongs to a row-level map — named, not shown.
    """
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        values = tuple((row.get(col) or "").strip() for col in key)
        if key and any(v == "" for v in values):
            continue
        groups[values].append(i)
    if not groups:
        return None
    if collapse_kind == "partial":
        rep = max(groups.values(), key=lambda idxs: (len(idxs), -idxs[0]))
    else:
        rep = next(iter(groups.values()))

    props: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    varying: list[str] = []
    for col in (c.name for c in inspection.columns):
        distinct: list[tuple[str, int]] = []
        seen_values: set[str] = set()
        for i in rep:
            v = (rows[i].get(col) or "").strip()
            if v and v not in seen_values:
                seen_values.add(v)
                distinct.append((v, i + first_data_line))
        if not distinct:
            continue
        if len(distinct) == 1:
            props.append({"column": col, "value": distinct[0][0]})
        elif collapse_kind == "partial":
            conflicts.append(
                {
                    "column": col,
                    "conflict": True,
                    "values": [
                        {"value": v, "line": line} for v, line in distinct[:_CONFLICT_VALUES]
                    ],
                    "more_values": max(0, len(distinct) - _CONFLICT_VALUES),
                }
            )
        else:
            varying.append(col)

    # Key columns first (they explain the ID); conflicts always survive the
    # column cap (the accident is the point); stable values fill what's left.
    key_set = set(key)
    key_props = [p for p in props if p["column"] in key_set]
    rest = [p for p in props if p["column"] not in key_set]
    # What survives the cap must be what tells THIS record apart. A column with
    # one value in the whole file is file-scoped metadata (the coarse map's
    # columns, by G1) — real, but it says nothing about which record you are
    # looking at. Observed on the XRD card: the 13 metadata columns come first
    # in file order and filled every slot, so the peak card showed its key and
    # none of its own measurements. Stable sort: file order survives per rank.
    file_wide = {
        p["column"]: len({(row.get(p["column"]) or "").strip() for row in rows}) for p in rest
    }
    rest.sort(key=lambda p: file_wide[p["column"]] <= 1)
    # 1 件しかないカードは、そのカードが**この表そのもの**なので省略しない
    # （2026-08-27 利用者評価「『ほか 10 列』と省略するのはどうですかね？ — ユーザーが
    # これも入れてほしいと言えるように」）。上限は G13 の所見＝**行**のカードが
    # ファイル水準のメタデータで埋まる事故に対する防御で、singleton には当たらない。
    room = (
        len(rest)
        if distinct_ids <= 1
        else max(0, _CARD_VALUE_COLUMNS - len(key_props) - len(conflicts))
    )
    # 行ごとに変わる列のうち、識別子型（測定値でない）のものは「値の種類」に
    # 昇格できる候補。判断材料は列名でなく値なので、実際の値を数個添える
    # （2026-08-27 K33: 種類にできる/できないの境界は列の位置でなく値の性質）。
    types = {c.name: c.inferred_type for c in inspection.columns}
    varying_identity = [c for c in varying if types.get(c) not in _MEASUREMENT_TYPES]
    varying_samples: list[dict[str, Any]] = []
    for col in varying_identity:
        seen: list[str] = []
        for row in rows:
            v = (row.get(col) or "").strip()
            if v and v not in seen:
                seen.append(v)
            if len(seen) >= 3:
                break
        varying_samples.append({"column": col, "values": seen})
    return {
        "id": _render_template(template, rows[rep[0]], prefixes),
        "row_count": len(rep),
        "entity_count": distinct_ids,
        "properties": key_props + conflicts + rest[:room],
        "varying_columns": varying,
        "varying_identity_columns": varying_identity,
        "varying_samples": varying_samples,
        # このカード自身が持つ列のうち、識別子型（測定値でない・キーでない）＝
        # 「値の種類」に昇格できる候補。行データのファイル（1 件のカードが無い）
        # では、ゾーンはこちらを並べる。K32 のゾーンは「ヘッダ付きファイル」の
        # 形だけを見て書かれていて、普通の行データでは丸ごと消えていた
        # （利用者の実データ・2026-08-28）。
        # 候補と、ゾーンが並べる値は**打ち切らない**。カードの上限（G13）は
        # タブ②の読みやすさのためのもので、①「あとでつなぐ値をえらぶ」は
        # 一覧そのものなので、切れると**その先の列でできた種類をチェックで
        # 外せなくなる**（利用者の実データ・2026-08-28: Wikipage や
        # BohrModelImage が 8 列の外にあり 🗑 でしか消せなかった）。
        "identity_columns": [
            p["column"]
            for p in (key_props + conflicts + rest)
            if p["column"] not in key_set
            and types.get(p["column"]) not in _MEASUREMENT_TYPES
        ],
        "all_values": [
            {"column": p["column"], "value": p.get("value", "")}
            for p in (key_props + conflicts + rest)
        ],
        "omitted_columns": max(0, len(rest) - room),
    }


def _scoped_candidates(
    parent_cols: Sequence[str],
    current_key: Sequence[str],
    existing: Sequence[Mapping[str, Any]],
    rows_considered: Any,
    inspection: SourceInspection | None,
) -> list[dict[str, Any]]:
    """Parent-scoped rewrites of the current key and the proven candidates.

    A superset of a unique key stays unique (rows where a parent cell is empty
    simply drop out of consideration), so no re-check is needed. Ranking
    prefers fewer measurement-valued columns, then fewer columns — so
    ``{No}/{(hkl)}`` beats ``{No}/{2theta}`` beats three-column combos.
    """
    types: dict[str, str | None] = (
        {c.name: c.inferred_type for c in inspection.columns} if inspection else {}
    )
    bases: list[Sequence[str]] = [current_key, *(c.get("columns") or [] for c in existing)]
    scored: list[tuple[tuple[bool, int, int], dict[str, Any]]] = []
    seen: set[tuple[str, ...]] = set()
    for base in bases:
        combo = [*parent_cols, *(c for c in base if c not in parent_cols)]
        canonical = tuple(sorted(combo))
        if not combo or canonical in seen:
            continue
        seen.add(canonical)
        n_meas = sum(1 for c in combo if types.get(c) in _MEASUREMENT_TYPES)
        candidate = {
            "columns": combo,
            "rows_considered": rows_considered,
            "measurement_only": n_meas == len(combo),
            "scoped": True,
        }
        scored.append(((candidate["measurement_only"], n_meas, len(combo)), candidate))
    scored.sort(key=lambda item: item[0])
    return [c for _, c in scored[:_KEY_CANDIDATES]]


# Which of a source's file-scoped (singleton) maps is THE parent — the one the
# scope risks, the growth preview and the missing-row-kind repair hang off.
# G7 said "exactly one singleton, else silence". A human split (ADR G15) adds
# a SECOND singleton on the same source on purpose (the shared concept, keyed
# by a column that repeats across files); that map carries `owns` and is never
# the parent — so with one original left, the parent is still unambiguous and
# nothing that used to work goes dark the moment the human splits.
def _parent_singleton(
    names: Sequence[str],
    annotations: Mapping[str, Any],
    human_owns: Mapping[str, Sequence[str]],
) -> str | None:
    singletons = [
        n
        for n in names
        if annotations[n].get("collapse_kind") == "singleton" and annotations[n].get("key_columns")
    ]
    if len(singletons) == 1:
        return singletons[0]
    originals = [n for n in singletons if n not in human_owns]
    if len(originals) == 1:
        return originals[0]
    # G7 の沈黙は「2 つ以上＝どれが親か推測になる」から来ていた。ところが K26
    # (骨格が「外の世界も名前を持つもの」を種類に昇格する) で、1 ファイルから
    # singleton が 3 つ出るのが普通になった。そこで黙ると、成長プレビューも
    # 「別の種類に分ける」(G15) も、行マップの scope risk (K14 C6) も丸ごと
    # 消える — 実データ(XRD 参考カード)で、化学組成を種類に昇格する唯一の
    # 決定論的な導線と、`peak/{2theta}` が次のファイルで衝突する警告が、
    # どちらも同時に沈黙した。
    #
    # ここで選んでいるのは「関係」ではなく「このファイルの名前空間はどれか」で、
    # 同一ソースの singleton はどれも等しくファイル全体を指す = どれを選んでも
    # 主張は真になる。関係の推測は依然しない。並びは骨格の順(設計は主役を先に
    # 書く)。0 個のときだけ黙る。
    return originals[0] if originals else None


def _functional_dependencies(
    rows: Sequence[Mapping[str, str]], key: Sequence[str], columns: Sequence[str]
) -> set[str]:
    """Columns whose value is determined by ``key`` (ADR column-ownership G1).

    Group the rows by the key and keep every column that never shows two
    different non-empty values inside a group: the key decides it, so storing it
    per row of a FINER map would transcribe the same fact many times. Key
    columns themselves are excluded — a map carrying another entity's key is how
    a join is declared, not a duplicate (same exemption as the RML-stage
    advisory).
    """
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        values = tuple((row.get(col) or "").strip() for col in key)
        if key and any(v == "" for v in values):
            continue
        groups[values].append(i)
    if not groups:
        return set()
    key_set = set(key)
    determined: set[str] = set()
    for col in columns:
        if col in key_set:
            continue
        ok = True
        for idxs in groups.values():
            seen = {v for i in idxs if (v := (rows[i].get(col) or "").strip())}
            if len(seen) > 1:
                ok = False
                break
        if ok:
            determined.add(col)
    return determined


def _adjudicate_ownership(
    annotations: dict[str, Any],
    map_sources: Mapping[str, str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
    human_owns: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Which map should carry each column — decided from the data (ADR G1/G2).

    For every source, each checkable map states which columns its own key
    determines. A column determined by several maps belongs to the one minting
    the FEWEST entities (normalisation: a constant-per-card property belongs to
    the card, not to each of its 47 peaks). A tie, or a column no key
    determines, gets no verdict — no claim beats a wrong one, the same posture
    as the RML-stage duplicate-column advisory this mirrors.

    Writes ``borrowed_columns`` on the finer maps and stamps ``owner_map`` onto
    the entity card's properties so the gate can render "this value comes from
    <parent>" instead of leaving the column silently duplicated. The coarse side
    gets the mirror image — ``delegated_columns``, the columns this card cannot
    carry and the map that will — so both cards state the same relation in the
    same shape (G12).
    """
    by_source: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_source[src].append(name)

    for src, names in by_source.items():
        entry = inspections.get(src)
        if entry is None:
            continue
        path, inspection = entry
        if inspection.source_kind != "csv":
            continue
        checkable = [
            n
            for n in names
            if annotations[n].get("checkable") and annotations[n].get("key_columns")
        ]
        if len(checkable) < 2:
            continue
        try:
            rows = _read_rows(path, inspection.dialect)
        except OSError:
            continue
        columns = [c.name for c in inspection.columns]
        # column -> [(entity_count, map_name)] for every map whose key decides it
        claims: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for name in checkable:
            ann = annotations[name]
            key = list(ann["key_columns"])
            count = int(ann.get("distinct_ids") or 0)
            if not count:
                continue
            for col in _functional_dependencies(rows, key, columns):
                claims[col].append((count, name))

        owners: dict[str, str] = {}
        declared = {n: set(cols) for n, cols in (human_owns or {}).items() if n in checkable}
        for col, bids in claims.items():
            # A human split (ADR G15) is world knowledge the rows cannot hold:
            # the map that DECLARES the column owns it outright, and a map that
            # declares a list NOT containing the column steps out of the tie —
            # so "material" vs "substance", both one entity per file, resolves.
            declared_owner = [n for n, cols in declared.items() if col in cols]
            if declared_owner:
                owners[col] = declared_owner[0]
                continue
            live = [b for b in bids if b[1] not in declared]
            live.sort()
            if len(live) == 1 or (len(live) > 1 and live[0][0] < live[1][0]):
                owners[col] = live[0][1]

        # Key columns never appear in `claims`: `_functional_dependencies`
        # exempts them (carrying another entity's key is a join, not a copy).
        # That exemption is right for borrowing and wrong for delegation — the
        # row-level map is usually keyed on exactly the column that varies
        # ({(hkl)} on the peaks), so without this the parent could not say where
        # its most important per-row column goes.
        key_claims: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for name in checkable:
            count = int(annotations[name].get("distinct_ids") or 0)
            if not count:
                continue
            for col in annotations[name]["key_columns"]:
                key_claims[col].append((count, name))

        for name in checkable:
            ann = annotations[name]
            key_set = set(ann["key_columns"])
            # Borrowed = someone else owns it, this map would still carry it,
            # and it is not this map's own key. A PARENT key column lands here
            # too — but it is exempt: carrying it is how the join is declared
            # (it wins ownership on its own map, so `owner != name` filters it).
            borrowed = [
                {"column": col, "owner_map": owner}
                for col, owner in sorted(owners.items())
                if owner != name and col not in key_set
                and any(m == name for _, m in claims[col])
            ]
            if borrowed:
                ann["borrowed_columns"] = borrowed
            card = ann.get("entity_preview")
            if not isinstance(card, Mapping):
                continue
            borrowed_cols = {b["column"] for b in borrowed}
            for prop in card.get("properties") or []:
                if prop.get("column") in borrowed_cols:
                    prop["owner_map"] = owners[prop["column"]]
            # The mirror of `borrowed`, on the coarse side: columns this card
            # CANNOT carry (they vary inside its group) and the map that will.
            # `varying_columns` already names them; only the destination was
            # missing, so the parent could state the fact but not point at the
            # kind that answers it. Columns nobody owns stay out — the card
            # still lists them as varying, without a destination claim.
            delegated: list[dict[str, str]] = []
            for col in card.get("varying_columns") or []:
                dest = owners.get(col)
                if dest == name:
                    dest = None
                if dest is None:
                    # Same adjudication as `owners`: fewest entities wins, a tie
                    # stays silent.
                    bids = sorted(b for b in key_claims.get(col, []) if b[1] != name)
                    if bids and (len(bids) == 1 or bids[0][0] < bids[1][0]):
                        dest = bids[0][1]
                if dest:
                    delegated.append({"column": col, "owner_map": dest})
            if delegated:
                ann["delegated_columns"] = delegated


def _missing_row_kind(
    annotations: dict[str, Any],
    map_sources: Mapping[str, str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
    templates: Mapping[str, str],
    prefixes: Mapping[str, str],
    human_owns: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """A source whose per-row values have NO map to live in (ADR §5).

    The singleton card already says its per-row columns "belong to the row-level
    kind" — but when the skeleton has no row-level map for that source, the kind
    it points at does not exist and those values are silently dropped. Observed
    live: round-0 returned ONE map for a reference card + 47 peaks, so the
    peaks had nowhere to go and the gate said nothing about it.

    Everything the fix needs is already computed: the varying columns, the
    parent key that scopes them, and the inspector's proven-unique combinations.
    So this states the gap AND the one-click repair (name, template, count)
    instead of asking the human to notice an absence.
    """
    by_source: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_source[src].append(name)
    taken = set(map_sources)
    for src, names in by_source.items():
        parent = _parent_singleton(names, annotations, human_owns or {})
        if parent is None:
            continue
        # A row-level map already exists for this source: nothing is homeless.
        # 値のカタログ（K33）は数えない: その map は自分の値 1 列しか持たず、
        # 行の測定値の置き場にはならない。
        if any(
            annotations[n].get("collapse_kind") in ("unique", "partial")
            for n in names
            if n != parent and not annotations[n].get("value_catalog")
        ):
            continue
        ann = annotations[parent]
        card = ann.get("entity_preview")
        parent_key = list(ann.get("key_columns") or [])
        if not isinstance(card, Mapping) or not parent_key:
            continue
        varying = list(card.get("varying_columns") or [])
        entry = inspections.get(src)
        if not varying or entry is None:
            continue
        path, inspection = entry
        # Parent-scoped candidates: a unique key stays unique with the parent's
        # columns prepended, and scoping is what keeps the ID safe once a second
        # file is appended (same rule as the scope-missing repair).
        scoped = [
            c
            for c in _scoped_candidates(
                parent_key,
                tuple(),
                _key_candidates(inspection, tuple(parent_key)),
                None,
                inspection,
            )
            # The parent key ALONE is the singleton we already have — a row-level
            # map needs at least one column that varies within the file.
            if len(c["columns"]) > len(parent_key)
        ]
        best = next((c for c in scoped if not c.get("measurement_only")), None) or (
            scoped[0] if scoped else None
        )
        if not best:
            continue
        try:
            rows = _read_rows(path, inspection.dialect)
        except OSError:
            continue
        key = tuple(best["columns"])
        report = _check_uniqueness(rows, key)
        name = _free_map_name(parent, taken)
        taken.add(name)
        # The parent's template AS WRITTEN (a CURIE) — the suggestion goes back
        # into the skeleton, where prefixes are still symbolic.
        template = _sibling_template(templates.get(parent, ""), parent, name, key)
        parent_classes = [c["curie"] for c in ann.get("expanded_classes") or []]
        ann["missing_row_kind"] = {
            "columns": varying,
            "suggested_name": name,
            "suggested_key": list(key),
            "suggested_template": template,
            "suggested_classes": _sibling_classes(parent_classes, prefixes, name),
            "entity_count": report.distinct_tuples,
        }


def _missing_card_kind(
    annotations: dict[str, Any],
    map_sources: Mapping[str, str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
    templates: Mapping[str, str],
    prefixes: Mapping[str, str],
    human_owns: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """``_missing_row_kind`` の逆: 行の種類はあるが、ファイル全体のカードが無い。

    実測 [本番 v0.28.0・2026-08-31]: round-0 が XRD 参考カードに **行ごとの
    種類 1 つだけ** [``{(hkl)}`` キー・47 件] を返した。ヘッダの 17 列は全行で
    同じ値のまま各記録に写り、画面はそれを黙って表として並べるだけ — 人は
    「行ごとのデータが読まれていない」と読み違えた [実際は逆で、無いのは
    カードの方]。欠けているものは沈黙ではなく、修理つきで言う [K7 と同じ
    姿勢・``missing_row_kind`` と対称]。

    判定は決定論: ファイル全体で値が 1 つしかない列 [ヘッダ由来の放送列] の
    うち識別子型のものが 1 つでもあれば、その先頭列をキーに 1 件のカードを
    提案する。素朴な行テーブル [放送列なし、または測定値だけ] は黙る。
    """
    by_source: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_source[src].append(name)
    taken = set(map_sources)
    for src, names in by_source.items():
        # カード（singleton）が 1 つでもあれば、この修理の出る幕はない。
        if any(annotations[n].get("collapse_kind") == "singleton" for n in names):
            continue
        hosts = [
            n
            for n in names
            if annotations[n].get("collapse_kind") in ("unique", "partial")
            and not annotations[n].get("value_catalog")
            and annotations[n].get("key_columns")
        ]
        if not hosts:
            continue
        host = max(hosts, key=lambda n: int(annotations[n].get("distinct_ids") or 0))
        ann = annotations[host]
        entry = inspections.get(src)
        if entry is None:
            continue
        path, inspection = entry
        if inspection.source_kind != "csv":
            continue
        try:
            rows = _read_rows(path, inspection.dialect)
        except OSError:
            continue
        if not rows:
            continue
        key_set = set(ann.get("key_columns") or [])
        types = {c.name: c.inferred_type for c in inspection.columns}
        file_wide = [
            c.name
            for c in inspection.columns
            if c.name not in key_set
            and len({v for row in rows if (v := (row.get(c.name) or "").strip())}) == 1
        ]
        identity_wide = [c for c in file_wide if types.get(c) not in _MEASUREMENT_TYPES]
        # 放送列が識別子型を 1 つも含まないなら黙る — 単位や定数の測定条件が
        # 1 列あるだけの普通の行テーブルにまで「カードが無い」と言わない。
        if not identity_wide:
            continue
        key = identity_wide[0]
        name = "card" if "card" not in taken else _free_map_name("card", taken)
        taken.add(name)
        template = _sibling_template(templates.get(host, ""), host, name, [key])
        host_classes = [c["curie"] for c in ann.get("expanded_classes") or []]
        ann["missing_card_kind"] = {
            "columns": file_wide,
            "suggested_name": name,
            "suggested_key": [key],
            "suggested_template": template,
            "suggested_classes": _sibling_classes(host_classes, prefixes, name),
            "entity_count": 1,
        }


def _free_map_name(parent: str, taken: set[str]) -> str:
    """A machine-safe name for the missing map; the human names the CLASS."""
    base = f"{parent}_detail"
    if base not in taken:
        return base
    i = 2
    while f"{base}{i}" in taken:
        i += 1
    return f"{base}{i}"


def _sibling_template(parent_template: str, parent: str, name: str, key: Sequence[str]) -> str:
    """The new map's subject, minted in the SAME namespace as the parent's.

    ``xr:material/{No}`` + key (No, (hkl)) -> ``xr:material_detail/{No}/{(hkl)}``
    — the head up to the parent's own name segment is reused verbatim, so the
    suggestion never invents a namespace.
    """
    head = parent_template
    idx = head.find(parent)
    if idx >= 0:
        head = head[:idx]
    elif "{" in head:
        head = head[: head.index("{")]
    return head + name + "".join(f"/{{{c}}}" for c in key)


def _sibling_classes(
    parent_classes: Sequence[str], prefixes: Mapping[str, str], name: str
) -> list[str]:
    """A starter class for the suggested map, in the dataset's own vocabulary.

    Named from the map name (``material_detail`` -> ``MaterialDetail``) under the
    parent's prefix, or the declared ontology (``#``-terminated) namespace. The
    human renames it — a placeholder they can edit beats an empty "what is this
    row?" column, which is what a machine-added map would otherwise carry. No
    prefix to borrow -> no suggestion (never invent a namespace).
    """
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", name) if p]
    pascal = "".join(p[:1].upper() + p[1:] for p in parts)
    if not pascal:
        return []
    for cls in parent_classes:
        m = _CURIE_HEAD.match(cls)
        if m:
            return [f"{m.group(1)}:{pascal}"]
    ontology = [p for p, iri in prefixes.items() if iri.endswith("#")]
    return [f"{ontology[0]}:{pascal}"] if ontology else []


def _growth_preview(
    annotations: dict[str, Any],
    map_sources: Mapping[str, str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
    human_owns: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """What the NEXT file does to this design (ADR column-ownership G3/G4).

    A singleton map is by definition "one entity per source file", so adding
    sources multiplies it — and every non-key column it carries is recorded
    independently per file. When two files happen to share a value there
    (the same substance described by two cards), those are the columns worth
    splitting into their own class so they MERGE instead of forking.

    With one file this is a forecast derived from the structure alone; with
    several it is measured (``shared_values``) — the crosswalk-hub principle
    (overlapping values are the bridge) applied before ingest.
    """
    by_source: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_source[src].append(name)
    # Sources that look alike (same columns) are the ones a growing dataset
    # appends; comparing values across them is what makes the forecast concrete.
    signatures: dict[str, frozenset[str]] = {}
    for src, entry in inspections.items():
        _path, ins = entry
        if ins.source_kind == "csv":
            signatures[src] = frozenset(c.name for c in ins.columns)

    for src, names in by_source.items():
        # EVERY file-scoped map on this source, not just "the" parent
        # (2026-08-27). The offer to split a column into its own kind (G15) is
        # the only deterministic path a human has to promote one, and hanging
        # it on ONE map put it on a different tab from the card the column is
        # visible on — live: the reader was looking at `CrystalStructure`
        # (whose card shows `Chemical Formula: Al3 V`) while the offer sat
        # under `Sample`. Every singleton here is "one entity per file" and its
        # key determines the same file-level columns, so the statement is true
        # of each; the gate's tabs mean only one is ever on screen at a time.
        # (`_parent_singleton` still picks ONE for the scope risk and the
        # missing-row-kind repair — those DO name a specific other map.)
        singletons = [
            n
            for n in names
            if annotations[n].get("collapse_kind") == "singleton"
            and annotations[n].get("key_columns")
            # A map the human already split out (G15) is the RESULT of a split,
            # not a card to split from — offering to break it up again reads as
            # the machine second-guessing a decision that was just made.
            and n not in (human_owns or {})
        ]
        entry = inspections.get(src)
        if not singletons or entry is None:
            continue
        path, inspection = entry
        if inspection.source_kind != "csv":
            continue
        try:
            rows = _read_rows(path, inspection.dialect)
        except OSError:
            continue
        columns = [c.name for c in inspection.columns]
        siblings = [
            other
            for other in signatures
            if other != src and signatures.get(other) == signatures.get(src)
        ]
        types = {c.name: c.inferred_type for c in inspection.columns}
        row_maps = [
            n for n in names if annotations[n].get("collapse_kind") != "singleton"
        ]
        for parent in singletons:
            ann = annotations[parent]
            # Everything this file-scoped entity DESCRIBES: the non-key columns
            # its key determines and that actually hold a value. Not the card's
            # list — the card caps at _CARD_VALUE_COLUMNS, the forecast is about
            # the whole entity (the real card has 18 such columns, the drawing
            # shows 8).
            determined = _functional_dependencies(rows, list(ann["key_columns"]), columns)
            # Columns another map already owns (a human split, or the machine's
            # verdict) have LEFT this entity — the forecast is about what is
            # still recorded per file here.
            elsewhere = {b["column"] for b in ann.get("borrowed_columns") or []}
            described = [
                col
                for col in columns
                if col in determined
                and col not in elsewhere
                and any((r.get(col) or "").strip() for r in rows)
            ]
            # 列名だけでは「これは外のデータにも出てくる名前か」を判断できない
            # （`Chemical Formula` ではなく `Al3 V` を見て人は決める）。この 1 件を
            # 説明する値そのものを添える（2026-08-27）。
            first = rows[0] if rows else {}
            preview: dict[str, Any] = {
                "per_source_entities": 1,
                "source_count": 1 + len(siblings),
                "row_maps": row_maps,
                "described_columns": described,
                "described_values": [
                    {"column": col, "value": (first.get(col) or "").strip()[:80]}
                    for col in described
                ],
            }
            shared: list[dict[str, Any]] = []
            if siblings:
                shared = _shared_column_values(src, siblings, described, inspections)
                preview["shared_values"] = shared
            # The one-click "split these into their own kind" (ADR G15): the
            # human decides WHICH columns name one shared thing (world
            # knowledge), the machine pre-fills what the files already agree on
            # and picks the identity-like column as the key (a name/formula over
            # a measured number). One file → no measured overlap → nothing
            # pre-checked, only the offer.
            shared_cols = [x["column"] for x in shared]
            key = next(
                (c for c in shared_cols if types.get(c) not in _MEASUREMENT_TYPES), None
            )
            if key is None and shared_cols:
                key = shared_cols[0]
            preview["split_default"] = {"columns": shared_cols, "key": key}
            ann["growth_preview"] = preview


def _shared_column_values(
    src: str,
    siblings: Sequence[str],
    columns: Sequence[str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
) -> list[dict[str, Any]]:
    """Measured overlap: which of ``columns`` already repeat across the files.

    A column holding the same value in another file is the concrete evidence
    that those two files describe one shared thing — split it out and they
    merge; leave it and each file mints its own copy.
    """
    def _values(name: str) -> dict[str, str]:
        entry = inspections.get(name)
        if entry is None:
            return {}
        path, ins = entry
        try:
            rows = _read_rows(path, ins.dialect)
        except OSError:
            return {}
        out: dict[str, str] = {}
        for col in columns:
            seen = {v for r in rows if (v := (r.get(col) or "").strip())}
            if len(seen) == 1:
                out[col] = next(iter(seen))
        return out

    mine = _values(src)
    theirs = [_values(name) for name in siblings]
    shared: list[dict[str, Any]] = []
    for col in columns:
        value = mine.get(col)
        if not value:
            continue
        hits = sum(1 for other in theirs if other.get(col) == value)
        if hits:
            shared.append({"column": col, "value": value, "files": hits + 1})
    return shared


def _inject_scope_risks(
    annotations: dict[str, Any],
    map_sources: Mapping[str, str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
    human_owns: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Cross-map pass: append-safety of row-level IDs (the citation question).

    When exactly one map of a source collapses to a SINGLETON (the file-scoped
    metadata entity — one reference card, one run header), its key columns are
    that file's namespace. A row-level map on the same source whose key does
    not include them is unique only within THIS file: appending the next file
    can mint the same ID for a different parent's row. Flag it as a reference
    risk and prepend the parent key to every proven candidate. Several
    singletons on one source are all equally that file's namespace, so
    ``_parent_singleton`` picks one by skeleton order rather than going silent
    (staying silent hid a real cross-file ID collision — see its comment).
    """
    by_source: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_source[src].append(name)
    for src, names in by_source.items():
        parent = _parent_singleton(names, annotations, human_owns or {})
        if parent is None:
            continue
        parent_cols = list(annotations[parent]["key_columns"])
        parent_classes = [c["curie"] for c in annotations[parent].get("expanded_classes") or []]
        entry = inspections.get(src)
        inspection = entry[1] if entry else None
        for name in names:
            if name == parent:
                continue
            ann = annotations[name]
            if not ann.get("checkable") or ann.get("collapse_kind") != "unique":
                continue
            # 値のカタログ（K33）は除外: その ID は値そのもので、ファイルを跨いで
            # 同じ値＝同じ 1 件になるのが目的。親キーを入れたら合流できなくなる。
            if ann.get("value_catalog"):
                continue
            key_cols = list(ann.get("key_columns") or [])
            if not key_cols or set(parent_cols) <= set(key_cols):
                continue
            ann.setdefault("reference_risks", []).append(
                {
                    "kind": "scope-missing",
                    "parent_map": parent,
                    "parent_columns": parent_cols,
                    "parent_classes": parent_classes,
                }
            )
            ann["key_candidates"] = _scoped_candidates(
                parent_cols,
                key_cols,
                ann.get("key_candidates") or [],
                ann.get("rows_considered"),
                inspection,
            )


def _annotate_map(
    map_entry: Mapping[str, Any],
    prefixes: Mapping[str, str],
    source_path: Path | None,
    inspection: SourceInspection | None,
) -> dict[str, Any]:
    """Evidence for ONE skeleton map. Never raises: unreadable → checkable:false."""
    subject = map_entry.get("subject") or {}
    template = subject.get("template") if isinstance(subject, Mapping) else None
    constant = subject.get("constant") if isinstance(subject, Mapping) else None
    classes = list(subject.get("classes") or []) if isinstance(subject, Mapping) else []

    ann: dict[str, Any] = {
        "checkable": False,
        "undeclared_prefixes": _undeclared_prefixes(template, classes, prefixes),
        "expanded_classes": [{"curie": c, "iri": _expand_curie(c, prefixes)} for c in classes],
    }

    if constant is not None and template is None:
        # Document-style map: one subject per source document — uniqueness over
        # rows does not apply. Say so instead of silently passing.
        ann["reason"] = "constant"
        ann["expanded_template"] = _expand_curie(str(constant), prefixes)
        return ann

    if not template:
        # 「ID の作り方が決まっていない」— 潰れでも衝突でもなく、まだ何も決まって
        # いない。ここまで候補は空で返していたので、画面は「決められませんでした」
        # とだけ言って、行き止まりになっていた(K11「行き止まりを作らない」)。
        # 候補は inspection がすでに証明済みなので、同じ one-tap チップを出す。
        ann["reason"] = "no-template"
        if inspection is not None:
            ann["key_candidates"] = _key_candidates(inspection, ())
        return ann

    ann["expanded_template"] = _expand_curie(str(template), prefixes)
    key = tuple(_PLACEHOLDER.findall(str(template)))
    ann["key_columns"] = list(key)

    if inspection is None or source_path is None:
        ann["reason"] = "source-not-found"
        return ann
    if inspection.source_kind != "csv":
        # JSON dot-path rows / XML iterators need their own reader to check
        # honestly; report "not checked" rather than pretend. (Follow-up.)
        ann["reason"] = f"unsupported-source-kind:{inspection.source_kind}"
        return ann

    columns = {c.name for c in inspection.columns}
    missing = [c for c in key if c not in columns]
    ann["missing_columns"] = missing
    if missing:
        ann["reason"] = "missing-columns"
        # A weak model misspells a key column (observed live), and the gate used
        # to answer with one line naming the bad column — leaving a domain
        # expert to retype an English header into an ID template by hand, which
        # is the single least kantan thing in the wizard. The machine holds the
        # real header AND the column sets it proved unique, so it offers both:
        # the near-name for each wrong column, and the proven keys as the same
        # one-tap chips every other uncertain key state already shows.
        ann["column_suggestions"] = _near_column_names(
            missing, [c.name for c in inspection.columns]
        )
        ann["key_candidates"] = _key_candidates(inspection, key)
        return ann

    try:
        rows = _read_rows(source_path, inspection.dialect)
    except OSError as exc:
        ann["reason"] = f"read-error:{exc}"
        return ann

    report = _check_uniqueness(rows, key)
    # Line numbers as the human sees the FILE: preamble lines (dialect
    # skip_rows) + the header line + 1-based counting.
    skip = inspection.dialect.skip_rows if inspection.dialect is not None else 0
    first_data_line = skip + 2
    # K7 (kantan-mode ADR): a key built ONLY from measurement-valued columns can
    # pass the uniqueness check by accident (real dogfood: 13 rows of
    # 3.636740E+1-style values happened to be distinct). The green band alone
    # would let a semantically wrong ID through — flag it so the gate shows an
    # amber caution AND, unlike the plain-unique case, still offers the proven
    # safer key candidates as one-click fixes.
    caution = bool(report.is_unique) and _measurement_only(inspection, key)
    # ZEM-shape naming trap: a measurement-only key whose CLASS is named after
    # one of those numeric columns (the row identity mislabeled as one of its
    # measurements). Computed for any measurement-only key, unique or not.
    class_caution = (
        _class_numeric_key_caution(inspection, key, classes)
        if _measurement_only(inspection, key)
        else []
    )
    kind = _collapse_kind(report)
    ann.update(
        {
            "checkable": True,
            "class_numeric_key_caution": class_caution,
            "rows_considered": report.total_rows_considered,
            "total_rows": len(rows),
            "distinct_ids": report.distinct_tuples,
            "colliding_rows": report.total_rows_considered - report.distinct_tuples,
            "is_unique": report.is_unique,
            "collapse_kind": kind,
            "key_measurement_caution": caution,
            # Citation-consequence risks (machine-readable; copy lives in the
            # UI): a measured-value ID stops pointing at its peak the moment
            # the value is corrected. The cross-map ``scope-missing`` risk is
            # appended by _inject_scope_risks after all maps are annotated.
            "reference_risks": (
                [{"kind": "measurement-id", "columns": list(key)}] if caution else []
            ),
            "collision_examples": []
            if report.is_unique
            else _collision_examples(rows, key, first_data_line),
            "id_previews": [
                _render_template(str(template), row, prefixes) for row in rows[:_PREVIEW_ROWS]
            ],
            "entity_preview": _entity_preview(
                rows,
                key,
                str(template),
                prefixes,
                inspection,
                first_data_line,
                kind,
                report.distinct_tuples,
            ),
            "key_candidates": []
            if (report.is_unique and not caution)
            else _key_candidates(inspection, key),
        }
    )
    return ann


def annotate_skeleton(
    skeleton: Mapping[str, Any],
    paths: Sequence[Path | str],
    *,
    dialects: Mapping[str, Any] | None = None,
    record_path: str | None = None,
    iri_base: str | None = None,
) -> dict[str, Any]:
    """Deterministic per-map evidence for the skeleton gate.

    Returns ``{"maps": {map_name: annotation}}``. Re-inspects the sources with
    the SAME dialect overrides the skeleton run used, so column names match what
    the AI saw. Designed to be best-effort at the call site: raise nothing the
    caller can avoid by construction; per-map problems degrade to
    ``checkable: false`` with a machine-readable ``reason``.
    """
    resolved = [Path(p) for p in paths]
    inspections, _fks = inspect_source_set(resolved, record_path=record_path, dialects=dialects)
    by_name: dict[str, tuple[Path, SourceInspection]] = {}
    for path, ins in zip(resolved, inspections, strict=True):
        by_name[path.name] = (path, ins)
        if ins.source_kind == "json":
            # 設計は JSON を表化名 [<stem>.csv] で参照する [規約]。骨格の source が
            # その名前でも、証拠 [annotation] を物理ファイルへ解決する。実在の
            # 同名 CSV があるときはそちらが勝つ [setdefault]。
            by_name.setdefault(f"{path.stem}.csv", (path, ins))

    prefixes_raw = skeleton.get("prefixes")
    prefixes: dict[str, str] = (
        {str(k): str(v) for k, v in prefixes_raw.items()}
        if isinstance(prefixes_raw, Mapping)
        else {}
    )

    annotations: dict[str, Any] = {}
    map_sources: dict[str, str] = {}
    raw_templates: dict[str, str] = {}
    human_owns: dict[str, list[str]] = {}
    for map_entry in skeleton.get("maps") or []:
        if not isinstance(map_entry, Mapping):
            continue
        name = str(map_entry.get("name") or f"map-{len(annotations) + 1}")
        source = str(map_entry.get("source") or "")
        path, inspection = by_name.get(Path(source).name, (None, None))
        map_sources[name] = Path(source).name
        subject = map_entry.get("subject")
        if isinstance(subject, Mapping) and subject.get("template"):
            raw_templates[name] = str(subject["template"])
        # `owns` (ADR G15): the columns a human assigned to this map when they
        # split a shared concept out at the gate. World knowledge the rows
        # cannot hold — it wins over the machine's ownership verdict.
        owns = map_entry.get("owns")
        if isinstance(owns, list) and owns:
            human_owns[name] = [str(c) for c in owns]
        annotations[name] = _annotate_map(map_entry, prefixes, path, inspection)
    # Second pass — needs every map's collapse verdict: the singleton map's
    # key is the file's namespace, and row-level keys missing it are unique
    # only until the next file is appended (see _inject_scope_risks).
    # 値のカタログ（K33）: 人が「この列そのものを種類にする」と宣言した map は
    # owns == キー列 ちょうどになる。その ID は値そのもの＝**同じ値の行が 1 件に
    # まとまるのは意図**なので、①ID 重複を事故として言わない ②親キーの入れ子
    # （scope）を要求しない ③「行の置き場」としては数えない、の 3 点で扱いが変わる。
    # 押印は scope 検査より先（scope の除外がこの旗を見る）。
    for name, ann in annotations.items():
        owns_cols = set(human_owns.get(name) or [])
        key_cols = set(ann.get("key_columns") or [])
        if owns_cols and owns_cols == key_cols:
            ann["value_catalog"] = True
    # 1 ファイルにつき 1 件の種類が同じソースに 2 つ以上あるとき、**どの列がどれに
    # 属するか**はデータからは決まらない: ヘッダ部の列は値の種類数が 1 なので、
    # どのキーからでも関数従属が成立する（実測 2026-08-28: Name / Chemical Formula /
    # Subfile / Space Group / Crystal System は No・CSD・Reference・Radiation の
    # どれからでも「決まる」）。ここを AI に決めさせていたのが、誤った帰属と
    # 重複列 advisory（S5 で永久に解けないループ）の発生源だった。
    #
    # モデルを 1 つに正す: **カードは 1 つ、ほかの 1 件の種類は「その値の種類」**。
    # 昇格した種類は自分のキー列だけを持ち（`owns` = キー列）、残りの列は全部
    # カードに残る。`_adjudicate_ownership` の「宣言リストに無い列のタイからは
    # 宣言者が退く」（G15）で、タイは決定論的にカードへ落ちる。**推測が要る場面
    # そのものを無くす。**
    inferred_owns: dict[str, list[str]] = {}
    by_src_names: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_src_names[src].append(name)
    for _src, names in by_src_names.items():
        card = _parent_singleton(names, annotations, human_owns)
        if card is None:
            continue
        for name in names:
            ann = annotations[name]
            if name == card or ann.get("collapse_kind") != "singleton":
                continue
            if name in human_owns:
                continue  # 人が宣言済み — 機械は上書きしない
            key_cols = [str(c) for c in (ann.get("key_columns") or [])]
            if key_cols:
                inferred_owns[name] = key_cols
                ann["value_catalog"] = True
                ann["owns_inferred"] = True
    human_owns = {**human_owns, **inferred_owns}
    _inject_scope_risks(annotations, map_sources, by_name, human_owns)
    # Third pass (ADR column-ownership-and-growth): who owns each column, and
    # what the next file does to this design. Both need every map's key and
    # entity count, so they run after the per-map pass.
    _adjudicate_ownership(annotations, map_sources, by_name, human_owns)
    _growth_preview(annotations, map_sources, by_name, human_owns)
    # A source whose per-row values have no map at all — the gap round-0 can
    # leave, stated with its one-click repair.
    _missing_row_kind(annotations, map_sources, by_name, raw_templates, prefixes, human_owns)
    # …and the mirror image: a row-level map with NO file-scoped card, so the
    # header block silently broadcasts onto every record (observed live).
    _missing_card_kind(annotations, map_sources, by_name, raw_templates, prefixes, human_owns)
    # Skeleton-level (not per-map): namespaces minted on a placeholder domain
    # (ADR instance-iri-base.md). The design loop would catch this after the
    # (paid, minutes-long) continue run — the gate shows it in milliseconds,
    # and an edit of the prefix clears it via /skeleton/validate like any key fix.
    placeholder = [
        {"prefix": name, "iri": iri}
        for name, iri in prefixes.items()
        if placeholder_prefix_issue(name, iri)
    ]
    # Which prefixes are THIS dataset's minted pair (vs reused vocabularies),
    # under which base, operator-configured or not — the gate renders "dataset
    # name" as the one editable naming judgment from this (kantan ADR K13).
    return {
        "maps": annotations,
        "placeholder_prefixes": placeholder,
        "dataset_namespace": dataset_namespace_info(prefixes, iri_base),
    }


def _rewrite_key_template(template: str, columns: Sequence[str]) -> str:
    """Rebuild a subject template's key segment from a new column list.

    Same rule the gate's own one-click "apply candidate" button uses
    (``ui/src/SkeletonGate.tsx``, ``applyCandidate``): keep everything before the
    first ``{`` as the head (a template with no placeholder at all gets a
    trailing ``/`` instead, so the new key segment never fuses onto the head),
    then append one ``{column}`` per new key column. Both places must agree —
    a machine rewrite and a human one-click fix are the same operation.
    """
    idx = template.find("{")
    head = template[:idx] if idx >= 0 else template + "/"
    return head + "/".join(f"{{{c}}}" for c in columns)


def _ascii_map_name(raw: str, taken: set[str], fallback: str) -> str:
    """骨格スキーマの map 名 [^A-Za-z][\\w-]* に収まる機械名。"""
    ascii_ = re.sub(r"[^0-9a-z]+", "_", raw.lower()).strip("_")
    ascii_ = re.sub(r"^[^a-z]+", "", ascii_)
    base = ascii_ or fallback
    name = base
    i = 2
    while name in taken:
        name = f"{base}{i}"
        i += 1
    taken.add(name)
    return name


def _pascal(name: str) -> str:
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", name) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _order_key_coarse_first(
    key: Sequence[str], parent: Sequence[str], unique_counts: Mapping[str, int]
) -> list[str]:
    """複合キーの並びを「粗いもの → 細かいもの」にする [決定論]。

    識別子の同一性は列の**集合**で決まり [入れ子判定 embedsKey も集合比較]、
    並びは可読性だけの問題 — だが逆順の IRI [record/{sample}/{figure}/{paper}]
    は引用時に人を迷わせる [利用者報告 2026-09-02: Starrydata]。粗さは検査の
    実測 [異なり数の昇順 = 論文 < 図 < サンプル]。親 [カードのキー] は常に先頭、
    未計測 [unique_count 0 = 高カーディナリティ] は最も細かいとみなして最後尾、
    同数は入力順のまま [安定・同じ入力は同じ絵]。
    """
    rest = [c for c in key if c not in parent]
    order = {c: i for i, c in enumerate(rest)}
    rest.sort(key=lambda c: (unique_counts.get(c) or float("inf"), order[c]))
    return [*parent, *rest]


def assemble_skeleton_from_judgments(
    paths: Sequence[Path | str],
    *,
    linkable: Sequence[Mapping[str, str]] = (),
    card_keys: Mapping[str, str] | None = None,
    excluded: Sequence[Mapping[str, str]] = (),
    dataset_name: str | None = None,
    dialects: Mapping[str, Any] | None = None,
    record_path: str | None = None,
    iri_base: str | None = None,
) -> dict[str, Any]:
    """③④の答えとファイルの検査から骨格を**組み立てる** [決定論・LLM 0]。

    ADR ``skeleton-from-easy-judgments.md`` D5。round-0 の LLM 提案を、かんたん
    モードでは置き換える。入力は人の判断 2 つだけ:

    - ``linkable``: ④「他のデータにも出てくる?」で ☑ した (source, column)
    - ``card_keys``: ④末尾の名指し {source: column}。無ければ機械が仮置きし、
      metadata の ``provisional_card_keys`` で明示する [利用者裁定 2026-08-31]

    導出はすべて検査の事実から: 放送列 [全行同値] = カードの項目、変動列 =
    行の種類、キーは一意性の実測から。☑ した値がその種類の ID になったときは
    受け口の種類を作らない — 種類自身が受け口。

    Returns ``{"skeleton": ..., "metadata": {...}}``。annotation は呼び出し側が
    :func:`annotate_skeleton` でいつもどおり計算する [式を二重に持たない]。
    """
    resolved = [Path(p) for p in paths]
    inspections, _fks = inspect_source_set(resolved, record_path=record_path, dialects=dialects)
    by_name = {path.name: (path, ins) for path, ins in zip(resolved, inspections, strict=True)}

    slug = slugify_dataset_name(dataset_name or (resolved[0].stem if resolved else None))
    onto, res = derive_prefix_pair(slug)
    base = normalize_iri_base(iri_base)
    prefixes = {
        onto: f"{base}/datasets/{slug}/ontology#",
        res: f"{base}/datasets/{slug}/resource/",
    }

    linkable_by_source: dict[str, list[str]] = defaultdict(list)
    for entry in linkable:
        src = Path(str(entry.get("source") or "")).name
        col = str(entry.get("column") or "")
        if col and col not in linkable_by_source[src]:
            linkable_by_source[src].append(col)
    excluded_by_source: dict[str, set[str]] = defaultdict(set)
    for entry in excluded:
        excluded_by_source[Path(str(entry.get("source") or "")).name].add(
            str(entry.get("column") or "")
        )

    maps: list[dict[str, Any]] = []
    taken: set[str] = set()
    provisional: dict[str, str] = {}

    def template(name: str, key: Sequence[str]) -> str:
        return f"{res}:{name}/" + "/".join("{" + c + "}" for c in key)

    def add_map(
        name: str, source: str, key: Sequence[str], cls: str, owns: Sequence[str] = ()
    ) -> None:
        m: dict[str, Any] = {
            "name": name,
            "source": source,
            "subject": {"template": template(name, key), "classes": [f"{onto}:{cls}"]},
        }
        if owns:
            m["owns"] = list(owns)
        maps.append(m)

    for path in resolved:
        src = path.name
        _p, ins = by_name[src]
        # JSON は取り込み時に表化される [asterism.tabularize] ため、設計が参照する
        # 名前は物理名でなく「<stem>.csv」[検査プロンプト・rml_compile と同じ規約]。
        # 物理名 .json を書くと、コンパイラ [「表形式のみ」] と存在検証 [「実在名
        # のみ」] が互いに矛盾する要求を出し、修理ループが収束しない
        # [実測 2026-09-01: 元素表 JSON が 8 ラウンド全敗]。
        design_src = f"{path.stem}.csv" if ins.source_kind == "json" else src
        drop = excluded_by_source.get(src, set())
        links = [c for c in linkable_by_source.get(src, []) if c not in drop]
        types = {c.name: c.inferred_type for c in ins.columns}
        columns = [c.name for c in ins.columns if c.name not in drop]

        broadcast: list[str] = []
        varying: list[str] = []
        if ins.source_kind == "csv":
            try:
                rows = _read_rows(path, ins.dialect)
            except OSError:
                rows = []
            for c in columns:
                distinct = {v for row in rows if (v := (row.get(c) or "").strip())}
                (broadcast if len(distinct) <= 1 else varying).append(c)
        else:
            varying = list(columns)

        # ---- カード（ファイル全体で 1 件）----
        card_key: str | None = None
        if broadcast:
            wanted = (card_keys or {}).get(src)
            identity_bc = [c for c in broadcast if types.get(c) not in _MEASUREMENT_TYPES]
            link_bc = [c for c in links if c in broadcast]
            if wanted and wanted in broadcast:
                card_key = wanted
            elif len(link_bc) == 1:
                # ④で ☑ したファイル単位の値が 1 つ → それが名指し（人の選択）。
                card_key = link_bc[0]
            else:
                card_key = (identity_bc or broadcast)[0]
                provisional[src] = card_key
            name = _ascii_map_name("card", taken, "card")
            add_map(name, design_src, [card_key], _pascal(name) or "Card")

        # ---- 行の種類 ----
        if varying:
            parent = [card_key] if card_key else []
            candidates = _key_candidates(ins, tuple(parent))
            key: list[str] | None = None
            if parent:
                scoped = [
                    c
                    for c in _scoped_candidates(parent, (), candidates, None, ins)
                    if len(c["columns"]) > len(parent)
                ]
                best = next((c for c in scoped if not c.get("measurement_only")), None) or (
                    scoped[0] if scoped else None
                )
                if best:
                    key = list(best["columns"])
            if key is None:
                best = next((c for c in candidates if not c.get("measurement_only")), None) or (
                    candidates[0] if candidates else None
                )
                if best:
                    cols = list(best["columns"])
                    key = [*parent, *(c for c in cols if c not in parent)] if parent else cols
            if key is None:
                # 一意の証明が無い — 最有力の識別子列で仮組みし、ゲートの ⚠ に任せる。
                first_identity = next(
                    (c for c in varying if types.get(c) not in _MEASUREMENT_TYPES),
                    varying[0],
                )
                key = [*parent, first_identity]
            key = _order_key_coarse_first(
                key, parent, {c.name: c.unique_count for c in ins.columns}
            )
            name = _ascii_map_name("record", taken, "record")
            # 行の種類は自分の持ち物 [キー以外の変動列。☑ の受け口列は受け口の
            # 持ち物] を**宣言**する。宣言が無いと、キーでも受け口でもない列は
            # 誰の持ち物でもなく、設計の続き [LLM] が他の種類へ自由に転記できて
            # しまう [実測 2026-09-01: 測定値が受け口のカタログに乗り得る]。
            # record は singleton ではないので、owns を宣言しても
            # _parent_singleton の親判定 [originals] には影響しない。
            record_owns = [c for c in varying if c not in key and c not in links]
            add_map(name, design_src, key, _pascal(name) or "Record", owns=record_owns)

        # ---- つながる受け口（値のカタログ）----
        record_key = set(key or []) if varying else set()
        for col in links:
            if col == card_key or col in record_key:
                # ☑ した値がその種類の ID になったときは受け口を作らない —
                # 種類自身が受け口 [ADR D3]。カードだけでなく行の種類のキーも
                # 同じ [実測 2026-09-01: record/{name} と name/{name} の二重]。
                continue
            if types.get(col) in _MEASUREMENT_TYPES:
                continue  # 測定値は ID にしない（K7/K33）
            if col not in columns:
                continue
            name = _ascii_map_name(col, taken, "value")
            add_map(name, design_src, [col], _pascal(name) or "Value", owns=[col])

    skeleton = {"version": 1, "prefixes": prefixes, "maps": maps}
    return {
        "skeleton": skeleton,
        "metadata": {"provisional_card_keys": provisional, "dataset_slug": slug},
    }


def fold_twin_kinds(skeleton: Mapping[str, Any]) -> tuple[dict, list[str]]:
    """機械の下書きから、**まったく同じ受け口**を畳む [利用者問い 2026-08-31
    「AI の最初の提案時点で、そもそも重複させないのはだめなのか」への答え]。

    同じソース・同じイテレータ・同じ鍵集合で、どのマップも ``owns`` を持たない
    組は、名前が違うだけの同じ受け口 — D1 の双子警告がそのまま当たる形を、人に
    見せる前に先頭の 1 つへ畳む。骨格の順は「主役が先」[``_parent_singleton``
    と同じ読み] なので、残すのは先頭。

    G18 [どちらを残すかは人の裁定] に反しない理由: ここで消えるのは**人がまだ
    見ていない機械の下書き**の中の、持ち物の割り当てが一切ない完全な写しだけ。
    ``owns`` を 1 つでも持つマップが混ざる組 [人の分割・D4 の兄弟・値のカタログ]
    には触らない。ゲートの警告は人が作った双子のために残る。

    Returns (folded_skeleton, dropped_map_names)。畳むものが無ければ入力を
    そのまま [コピーして] 返す。
    """
    maps = [dict(m) for m in (skeleton.get("maps") or []) if isinstance(m, Mapping)]
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, m in enumerate(maps):
        subject = m.get("subject") if isinstance(m.get("subject"), Mapping) else {}
        template = subject.get("template")
        key = tuple(sorted(re.findall(r"\{([^{}]+)\}", str(template or ""))))
        sig = (str(m.get("source") or ""), str(m.get("iterator") or ""), key, template is None)
        groups[sig].append(i)
    dropped: set[int] = set()
    dropped_names: list[str] = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        if any(maps[i].get("owns") for i in idxs):
            continue  # 持ち物の宣言がある＝写しではない。人の領分。
        for i in idxs[1:]:
            dropped.add(i)
            dropped_names.append(str(maps[i].get("name") or f"map-{i + 1}"))
    if not dropped:
        return {**dict(skeleton), "maps": maps}, []
    kept = [m for i, m in enumerate(maps) if i not in dropped]
    return {**dict(skeleton), "maps": kept}, dropped_names


def apply_key_safety_fix(
    skeleton: Mapping[str, Any],
    annotations: Mapping[str, Any],
    *,
    keep: Collection[str] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Swap an AI-chosen measurement-only key for the machine's own safe pick.

    ``annotate_skeleton`` already proves, per map, whether the adopted key is
    measurement-only (K7) AND whether a non-measurement-only alternative is
    among the SAME proven-unique candidates the gate would offer as one-click
    chips. When both are true there is no judgment left to make — the AI's own
    evidence already picked the safer ID, so a human should never have to see
    the caution before the machine has tried the fix it can already prove.

    Fires on TWO risks (2026-08-27):

    - ``key_measurement_caution`` (K7) — the AI keyed rows on a measured value;
    - ``scope-missing`` (K14 C6) — the key does not embed the file-scoped
      parent's key, so the ID is unique in THIS file only and the next file
      can mint the same IRI for a different parent's row.

    C7-C11 originally left ``scope-missing`` for the human ("whether to prepend
    the parent key is a call about how the dataset is expected to grow"). Live
    use showed that framing is wrong: the growth question is what the SCREEN
    asks about (this dataset gets more files — that is what "add data" means),
    and the fix is not a guess. Prepending a proven parent key never breaks a
    unique key (a superset of a unique key stays unique) and it is the only way
    the row can stay addressable once a second file arrives. Leaving it on a
    button meant the risk survived every run where nobody pressed it, and the
    reader could not tell the button apart from the ones that are real choices.
    The escape stays: ``applied_key_fix`` renders the swap and a revert.

    Deliberately does nothing when:
    - no safe candidate exists (e.g. a numeric instrument sweep where every
      column is a measurement) — the caution is shown as-is, unresolved;
    - the pick would not change the key — idempotence, and a no-op record
      would claim a fix that did not happen;
    - the candidate's ``columns`` is empty — defensive only (today's candidate
      generator never proves an empty key unique), but an empty key rewrites
      the template to a CONSTANT subject, collapsing every row onto one ID —
      the single worst outcome this function could cause, so it is refused
      outright rather than trusted to "never happen".

    Pure and non-mutating: returns a NEW skeleton (unaffected maps are shared,
    never mutated, so the caller's original object stays intact) and a
    ``{map_name: record}`` dict of what changed, for the caller to re-stamp
    onto the annotations after a required re-annotate pass (the evidence —
    uniqueness, id previews, candidates — must reflect the NEW key, and
    ``annotate_skeleton`` has no way to know a swap happened).

    Idempotent by construction: once the key is the safe candidate, it is no
    longer measurement-only (the candidate was proven non-measurement-only),
    so a second pass finds nothing to fix — proven by re-annotating and
    re-applying in the tests, not assumed.

    ``keep`` names maps whose ID a PERSON wrote (S4 「AI にもう一度考えさせる」
    hands the edited design back to the model, so this can run on a design that
    has already been through a human). The reasoning above — "there is no
    judgment left to make" — holds only while nobody has made one. Once somebody
    has, the machine's own evidence is the weaker claim, and swapping their key
    without asking is the silent overwrite ADR data-facts-invariant N6 forbids.
    """
    maps_ann = annotations.get("maps") if isinstance(annotations, Mapping) else None
    new_skeleton = dict(skeleton)
    fixes: dict[str, dict[str, Any]] = {}
    if not isinstance(maps_ann, Mapping):
        return new_skeleton, fixes

    new_maps: list[Any] = []
    for map_entry in skeleton.get("maps") or []:
        if not isinstance(map_entry, Mapping):
            new_maps.append(map_entry)
            continue
        name = str(map_entry.get("name") or "")
        if keep and name in keep:
            new_maps.append(map_entry)
            continue
        ann = maps_ann.get(name)
        subject = map_entry.get("subject")
        fixed_entry = None
        measurement_risk = isinstance(ann, Mapping) and ann.get("key_measurement_caution") is True
        scope_risk = isinstance(ann, Mapping) and any(
            isinstance(r, Mapping) and r.get("kind") == "scope-missing"
            for r in (ann.get("reference_risks") or [])
        )
        if (
            isinstance(ann, Mapping)
            and (measurement_risk or scope_risk)
            and isinstance(subject, Mapping)
            and subject.get("template")
        ):
            safe = next(
                (
                    c
                    for c in ann.get("key_candidates") or []
                    if isinstance(c, Mapping)
                    and c.get("measurement_only") is False
                    # A scope-only fix must actually gain the parent's columns;
                    # an unscoped candidate would rewrite the key for nothing.
                    and (measurement_risk or c.get("scoped") is True)
                ),
                None,
            )
            new_key = [str(c) for c in safe.get("columns") or []] if safe is not None else []
            old_key_now = list(ann.get("key_columns") or [])
            if new_key == old_key_now:
                safe, new_key = None, []
            if safe is not None and new_key:
                old_key = list(ann.get("key_columns") or [])
                old_template = str(subject["template"])
                new_template = _rewrite_key_template(old_template, new_key)
                new_subject = dict(subject)
                new_subject["template"] = new_template
                fixed_entry = dict(map_entry)
                fixed_entry["subject"] = new_subject
                fixes[name] = {
                    "from": old_key,
                    "to": new_key,
                    "reason": "measurement-id" if measurement_risk else "scope-missing",
                    "template_from": old_template,
                    "template_to": new_template,
                }
        new_maps.append(fixed_entry if fixed_entry is not None else map_entry)

    new_skeleton["maps"] = new_maps
    return new_skeleton, fixes
