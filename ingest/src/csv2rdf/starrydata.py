"""Starrydata CSV -> RDF ingester (Phase 1).

Phase 0.5 で書いた experiments/phase05/scripts/csv_to_ttl.py を本実装版に格上げ:

- **blank node 排除**: Phase 0.5 で `schema:Periodical` を bnode にしていた。
  Phase 0.5 oxigraph 素振り §4.4 の通り、bnode は re-ingest 時に重複するため
  csv2rdf-mcp の auto-reindex フローと相性が悪い。Phase 1 では
  `sdr:periodical/{slug}` のような IRI に統一する。
- **PROV-O IngestionActivity**: 設計プラン §4 / option-b-architecture.md §3 に
  従い、各 ingest 実行で 1 つの `sd:IngestionActivity` を発行し、生成する全
  Entity に `prov:wasGeneratedBy` を付ける。
- **構造化エラー出力**: 失敗行は `error_log_path` に jsonl で残す。本体処理は
  止めない。
- **設定可能な base IRI**: design-plan §4.0 通り、Phase 1 は GitHub Pages を
  想定したホスト名を default にするが、上書き可能。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, XSD

from csv2rdf.qudt import quantity_kind_iri, unit_iri

# ----------------------------------------------------------------------------
# Default namespaces (design-plan §4 / §4.0)
# ----------------------------------------------------------------------------

DEFAULT_ONTOLOGY = "https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#"
DEFAULT_RESOURCE = "https://kumagallium.github.io/csv2rdf-mcp/starrydata/resource/"
SOFTWARE_AGENT_IRI = "https://github.com/kumagallium/csv2rdf-mcp"

# Phase 2 #6: starrydata curves are digitized from published figures with
# WebPlotDigitizer. We model that origin as a PROV SoftwareAgent so the
# scientific provenance ("this curve was traced off a figure, not measured
# in-house") is queryable. The homepage doubles as a stable identifier.
WEBPLOTDIGITIZER_IRI = "https://automeris.io/WebPlotDigitizer"

SCHEMA = Namespace("https://schema.org/")
BIBO = Namespace("http://purl.org/ontology/bibo/")


# ----------------------------------------------------------------------------
# Stats / config
# ----------------------------------------------------------------------------


@dataclass
class IngestStats:
    rows_in: int = 0
    rows_ok: int = 0
    rows_err: int = 0
    triples_out: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None


@dataclass
class IngestConfig:
    ontology_iri: str = DEFAULT_ONTOLOGY
    resource_iri: str = DEFAULT_RESOURCE
    software_agent_iri: str = SOFTWARE_AGENT_IRI
    emit_prov: bool = True


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

_SLUG_DROP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_len: int = 80) -> str:
    """IRI segment 用の slug。a-z0-9 + 1 個の `-` のみに正規化。

    starrydata の container_title (journal 名) は引用符付き文字列なので、まず
    json.loads を試みて剥がしてから slug 化する。
    """
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            s = json.loads(s)
        except json.JSONDecodeError:
            s = s.strip('"')
    s = s.lower()
    s = _SLUG_DROP.sub("-", s).strip("-")
    if not s:
        return "unknown"
    return s[:max_len]


def parse_issued(raw: str) -> str | None:
    """{"date_parts": [[YYYY, MM?, DD?]]} -> ISO 8601 date (best effort)."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        parts = data.get("date_parts", [[]])[0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        return None
    if not parts:
        return None
    y = parts[0] if len(parts) >= 1 else None
    m = parts[1] if len(parts) >= 2 else 1
    d = parts[2] if len(parts) >= 3 else 1
    if not isinstance(y, int):
        return None
    try:
        return date(y, m or 1, d or 1).isoformat()
    except ValueError:
        return None


# starrydata's created_at / updated_at are JavaScript Date.toString() output,
# e.g. "Fri Sep 01 2017 18:19:39 GMT+0900 (Japan Standard Time)" — curated in
# Japan, so the offset is usually +0900. We keep the raw local-time string on
# dcterms:created for fidelity, but normalize prov:atTime to a **UTC** instant
# so it is unambiguous and sorts correctly for any reader worldwide (a
# timezone-offset xsd:dateTime is technically unambiguous too, but UTC removes
# any "what is +0900?" doubt and avoids mixed offsets across rows).
_TZ_NAME_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")  # " (Japan Standard Time)"


def parse_curator_timestamp(raw: str) -> str | None:
    """Parse a JS Date.toString() timestamp to a UTC ISO 8601 instant.

    Returns e.g. ``"2017-09-01T09:19:39Z"`` for a ``GMT+0900`` input. Returns
    ``None`` if the string can't be parsed OR carries no timezone — in the
    latter case the instant is genuinely unknown, so we emit no prov:atTime
    rather than fabricate one (the raw local string still lives on
    dcterms:created).
    """
    if not raw:
        return None
    s = _TZ_NAME_SUFFIX.sub("", raw.strip())  # drop trailing "(...)" tz name
    s = s.replace("GMT", "").strip()          # "GMT+0900" -> "+0900" for %z
    try:
        dt = datetime.strptime(s, "%a %b %d %Y %H:%M:%S %z")
    except ValueError:
        return None
    # Normalize to a UTC instant. isoformat() yields the "+00:00" offset form;
    # rdflib canonicalizes xsd:dateTime to that same form anyway (it rewrites a
    # trailing "Z" to "+00:00"), so we emit "+00:00" directly for consistency
    # between this helper and what actually lands in the graph / Oxigraph.
    return dt.astimezone(UTC).isoformat()


def parse_authors(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        given = str(item.get("given", "")).strip()
        family = str(item.get("family", "")).strip()
        if not (given or family):
            continue
        out.append({"given": given, "family": family})
    return out


def parse_project_names(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


def parse_sample_info(raw: str) -> dict[str, dict[str, str]]:
    """sample_info カラム (深くネストした JSON object) を緩くパース。

    形式: { descriptorName: { "category": str?, "comment": str?, "extracted": str? } }
    全フィールド空のエントリは捨てる (S/N 比のため)。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, value in data.items():
        if not isinstance(value, dict):
            continue
        category = str(value.get("category", "")).strip()
        comment = str(value.get("comment", "")).strip()
        extracted = str(value.get("extracted", "")).strip()
        if not (category or comment or extracted):
            continue  # 全部空のエントリは出さない (starrydata のテンプレ残骸)
        out[name.strip()] = {
            "category": category,
            "comment": comment,
            "extracted": extracted,
        }
    return out


def parse_float_array(raw: str) -> list[float]:
    """curves.csv の x / y JSON 配列を Python list[float] にパース。

    失敗した個別要素は除外し (NaN / None / 非数値文字列など)、配列自体が壊れていれば
    空リストを返す。
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[float] = []
    for v in data:
        try:
            if v is None:
                continue
            fv = float(v)
            if fv != fv:  # NaN check
                continue
            out.append(fv)
        except (TypeError, ValueError):
            continue
    return out


def strip_quoted(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v.strip('"')
    return v


# Characters that are illegal in an IRI and would make rdflib emit Turtle that
# Oxigraph rejects. Full-scale starrydata has ~38 legacy Wiley DOI URLs like
# "http://dx.doi.org/10.1002/(SICI)...175:2<683::AID-PSSA683>3.0.CO;2-3" with
# angle brackets. We percent-encode them so the URL stays a valid, dereferenceable
# IRI rather than dropping the triple or breaking serialization.
_IRI_ILLEGAL = {
    " ": "%20", '"': "%22", "<": "%3C", ">": "%3E", "{": "%7B", "}": "%7D",
    "|": "%7C", "\\": "%5C", "^": "%5E", "`": "%60",
}
_IRI_ILLEGAL_RE = re.compile("[" + re.escape("".join(_IRI_ILLEGAL)) + "]")

# A usable schema:url must be an absolute IRI, i.e. start with an RFC 3986
# scheme (``ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":"``). Starrydata has
# placeholder URL values like "unknown" (19 papers at full scale) that would
# otherwise be emitted as the scheme-less IRI ``<unknown>`` — invalid Turtle
# that Oxigraph's bulk loader rejects, taking the whole file down with it.
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def safe_url(value: str) -> str | None:
    """Return an IRI-safe URL (illegal chars percent-encoded), or None.

    Returns None for empty values and for scheme-less placeholders (e.g.
    "unknown") that are not absolute IRIs — we skip the triple rather than emit
    an invalid IRI. Otherwise only the characters that are illegal in an IRI are
    encoded; the URL's structure (scheme, slashes, already-encoded sequences) is
    left intact.
    """
    v = strip_quoted(value)
    if not v or not _URI_SCHEME_RE.match(v):
        return None
    return _IRI_ILLEGAL_RE.sub(lambda m: _IRI_ILLEGAL[m.group()], v)


# ----------------------------------------------------------------------------
# Core: build_graph_for_paper
# ----------------------------------------------------------------------------


def _emit_paper(
    g: Graph,
    row: dict[str, str],
    sd: Namespace,
    sdr: Namespace,
    ingestion_iri: URIRef | None,
) -> bool:
    """1 行 (papers.csv) を Graph に追加。成功で True、SID 不在なら False。"""
    sid = row.get("SID", "").strip()
    if not sid:
        return False
    paper = sdr[f"paper/{sid}"]

    g.add((paper, RDF.type, sd.Paper))
    g.add((paper, RDF.type, SCHEMA.ScholarlyArticle))
    g.add((paper, RDF.type, PROV.Entity))  # design-plan §4: Paper is prov:Entity
    g.add((paper, DCTERMS.identifier, Literal(sid)))

    if ingestion_iri is not None:
        g.add((paper, PROV.wasGeneratedBy, ingestion_iri))

    if doi := row.get("DOI", "").strip():
        g.add((paper, SCHEMA.identifier, Literal(doi)))
    if url := safe_url(row.get("URL", "")):
        g.add((paper, SCHEMA.url, URIRef(url)))
    if title := strip_quoted(row.get("title", "")):
        g.add((paper, SCHEMA.name, Literal(title)))

    if container := strip_quoted(row.get("container_title", "")):
        slug = slugify(container)
        periodical = sdr[f"periodical/{slug}"]
        g.add((paper, SCHEMA.isPartOf, periodical))
        # The periodical is shared across multiple papers; we add the type/name
        # triples idempotently (Oxigraph set semantics deduplicates).
        g.add((periodical, RDF.type, SCHEMA.Periodical))
        g.add((periodical, SCHEMA.name, Literal(container)))
        if container_short := strip_quoted(row.get("container_title_short", "")):
            g.add((periodical, SCHEMA.alternateName, Literal(container_short)))

    if issued_iso := parse_issued(row.get("issued", "")):
        g.add(
            (paper, SCHEMA.datePublished, Literal(issued_iso, datatype=XSD.date))
        )

    for csv_col, ns in (("volume", BIBO.volume), ("issue", BIBO.issue)):
        if v := strip_quoted(row.get(csv_col, "")):
            g.add((paper, ns, Literal(v)))
    if pages := strip_quoted(row.get("page", "")):
        g.add((paper, BIBO.pages, Literal(pages)))

    if publisher := strip_quoted(row.get("publisher", "")):
        g.add((paper, SCHEMA.publisher, Literal(publisher)))

    for project in parse_project_names(row.get("project_names", "")):
        g.add((paper, sd.projectName, Literal(project)))

    for i, author in enumerate(parse_authors(row.get("author", ""))):
        person = sdr[f"person/{sid}/{i}"]
        g.add((paper, SCHEMA.author, person))
        g.add((person, RDF.type, SCHEMA.Person))
        g.add((person, RDF.type, PROV.Agent))
        if author["given"]:
            g.add((person, SCHEMA.givenName, Literal(author["given"])))
        if author["family"]:
            g.add((person, SCHEMA.familyName, Literal(author["family"])))

    # created_at column is the starrydata curator's import timestamp (loosely formatted).
    # We keep it as a literal string rather than xsd:dateTime to avoid parse fragility.
    if created := strip_quoted(row.get("created_at", "")):
        g.add((paper, DCTERMS.created, Literal(created)))

    return True


def _emit_sample(
    g: Graph,
    row: dict[str, str],
    sd: Namespace,
    sdr: Namespace,
    ingestion_iri: URIRef | None,
) -> bool:
    """1 行 (samples.csv) を Graph に追加。成功で True、sample_id / SID 不在なら False。

    NOTE: sample_id は starrydata 内でグローバルユニークではない (実測 9,661 件の
    重複)。Paper SID と組み合わせて初めて一意になるため、複合 IRI を採る:
        sdr:sample/{SID}-{sample_id}
    dcterms:identifier も同じ複合キーを literal で持たせ、`sd:rawSampleId` で
    starrydata 内部の元 sample_id も保持する (CSV と突き合わせ可能にするため)。
    """
    sample_id = row.get("sample_id", "").strip()
    paper_sid = row.get("SID", "").strip()
    if not sample_id or not paper_sid:
        return False
    sample_key = f"{paper_sid}-{sample_id}"
    sample = sdr[f"sample/{sample_key}"]

    g.add((sample, RDF.type, sd.Sample))
    g.add((sample, RDF.type, PROV.Entity))
    g.add((sample, DCTERMS.identifier, Literal(sample_key)))
    g.add((sample, sd.rawSampleId, Literal(sample_id)))

    if ingestion_iri is not None:
        g.add((sample, PROV.wasGeneratedBy, ingestion_iri))

    if name := strip_quoted(row.get("sample_name", "")):
        g.add((sample, SCHEMA.name, Literal(name)))

    if composition := strip_quoted(row.get("composition", "")):
        g.add((sample, sd.compositionString, Literal(composition)))

    if details := strip_quoted(row.get("composition_details", "")):
        g.add((sample, sd.compositionDetails, Literal(details)))

    g.add((sample, sd.fromPaper, sdr[f"paper/{paper_sid}"]))

    if created := strip_quoted(row.get("created_at", "")):
        g.add((sample, DCTERMS.created, Literal(created)))
    if updated := strip_quoted(row.get("updated_at", "")):
        g.add((sample, DCTERMS.modified, Literal(updated)))

    for i, (descriptor_name, body) in enumerate(
        parse_sample_info(row.get("sample_info", "")).items()
    ):
        descriptor = sdr[f"descriptor/{sample_key}/{i}"]
        g.add((sample, sd.hasDescriptor, descriptor))
        g.add((descriptor, RDF.type, sd.Descriptor))
        g.add((descriptor, sd.descriptorName, Literal(descriptor_name)))
        if body["category"]:
            g.add((descriptor, sd.descriptorCategory, Literal(body["category"])))
        if body["comment"]:
            g.add((descriptor, sd.descriptorComment, Literal(body["comment"])))
        if body["extracted"]:
            g.add((descriptor, sd.descriptorExtracted, Literal(body["extracted"])))

    return True


def _emit_curve(
    g: Graph,
    row: dict[str, str],
    sd: Namespace,
    sdr: Namespace,
    ingestion_iri: URIRef | None,
) -> bool:
    """1 行 (curves.csv) を Graph に追加。

    NOTE: starrydata は 1 つの figure (figure_id) に複数 sample の curve を載せる
    ことがあり、また figure_id 自体も paper を跨いで重複しうる (実測 4,466 件)。
    そのため curve IRI は (SID, figure_id, sample_id) の複合キーで作る:
        sdr:curve/{SID}-{figure_id}-{sample_id}
    starrydata 全件で (SID, figure_id, sample_id) は 100% ユニーク (検証済)。

    設計プラン §4「x/y 配列の表現方針」方針 C:
      - sd:xValuesJSON / sd:yValuesJSON で JSON literal 保持 (xsd:string)
      - sd:xMin / sd:xMax / sd:yMin / sd:yMax / sd:pointCount を集約値として出す
      → 2 次元の局所範囲クエリは集約値だけでは答えられない (既知の限界)
    """
    fig_id = row.get("figure_id", "").strip()
    sample_id = row.get("sample_id", "").strip()
    paper_sid = row.get("SID", "").strip()
    if not fig_id or not sample_id or not paper_sid:
        return False
    curve_key = f"{paper_sid}-{fig_id}-{sample_id}"
    curve = sdr[f"curve/{curve_key}"]
    sample_iri = sdr[f"sample/{paper_sid}-{sample_id}"]

    g.add((curve, RDF.type, sd.Curve))
    g.add((curve, RDF.type, PROV.Entity))
    g.add((curve, DCTERMS.identifier, Literal(curve_key)))
    g.add((curve, sd.rawFigureId, Literal(fig_id)))

    if ingestion_iri is not None:
        g.add((curve, PROV.wasGeneratedBy, ingestion_iri))

    if figure_name := strip_quoted(row.get("figure_name", "")):
        g.add((curve, sd.figureName, Literal(figure_name)))

    g.add((curve, sd.ofSample, sample_iri))

    # Phase 1 string predicates are kept as-is for backward compatibility.
    for col_key, prop in (
        ("prop_x", sd.propertyX),
        ("prop_y", sd.propertyY),
        ("unit_x", sd.unitXString),
        ("unit_y", sd.unitYString),
        ("comments", sd.comments),
    ):
        if value := strip_quoted(row.get(col_key, "")):
            g.add((curve, prop, Literal(value)))

    # Phase 2 #2: additively attach canonical QUDT IRIs when a curated mapping
    # exists. The string predicates above stay; these let AI clients pivot on a
    # stable identifier so "Seebeck coefficient" and "thermopower" unify.
    for col_key, prop in (
        ("prop_x", sd.propertyXQuantity),
        ("prop_y", sd.propertyYQuantity),
    ):
        value = strip_quoted(row.get(col_key, ""))
        if value and (iri := quantity_kind_iri(value)):
            g.add((curve, prop, URIRef(iri)))
    for col_key, prop in (
        ("unit_x", sd.unitX),
        ("unit_y", sd.unitY),
    ):
        value = strip_quoted(row.get(col_key, ""))
        if value and (iri := unit_iri(value)):
            g.add((curve, prop, URIRef(iri)))

    # x / y 配列: JSON literal + 集約値
    x_raw = row.get("x", "")
    y_raw = row.get("y", "")
    xs = parse_float_array(x_raw)
    ys = parse_float_array(y_raw)
    if x_raw:
        g.add((curve, sd.xValuesJSON, Literal(x_raw)))
    if y_raw:
        g.add((curve, sd.yValuesJSON, Literal(y_raw)))
    if xs:
        g.add((curve, sd.xMin, Literal(min(xs), datatype=XSD.double)))
        g.add((curve, sd.xMax, Literal(max(xs), datatype=XSD.double)))
    if ys:
        g.add((curve, sd.yMin, Literal(min(ys), datatype=XSD.double)))
        g.add((curve, sd.yMax, Literal(max(ys), datatype=XSD.double)))
    point_count = min(len(xs), len(ys))
    if point_count:
        g.add((curve, sd.pointCount, Literal(point_count, datatype=XSD.integer)))

    for project in parse_project_names(row.get("project_names", "")):
        g.add((curve, sd.projectName, Literal(project)))

    created = strip_quoted(row.get("created_at", ""))
    if created:
        g.add((curve, DCTERMS.created, Literal(created)))
    if updated := strip_quoted(row.get("updated_at", "")):
        g.add((curve, DCTERMS.modified, Literal(updated)))

    # Phase 2 #6: scientific provenance. Each curve was digitized off a
    # published figure with WebPlotDigitizer; model that as a distinct
    # DigitizationActivity (separate from our IngestionActivity, which only
    # records that *our pipeline* loaded the CSV). A curve is thus
    # prov:wasGeneratedBy two activities — the original digitization and our
    # ingestion — which is valid PROV-O.
    digitization = sdr[f"digitization/{curve_key}"]
    g.add((curve, PROV.wasGeneratedBy, digitization))
    g.add((digitization, RDF.type, sd.DigitizationActivity))
    g.add((digitization, RDF.type, PROV.Activity))
    g.add((digitization, PROV.wasAssociatedWith, URIRef(WEBPLOTDIGITIZER_IRI)))
    if created and (at_time := parse_curator_timestamp(created)):
        g.add((digitization, PROV.atTime, Literal(at_time, datatype=XSD.dateTime)))
    # The WebPlotDigitizer agent description is shared across all curves; the
    # triples are IRI-keyed so Oxigraph set-semantics dedupes them on re-ingest.
    agent = URIRef(WEBPLOTDIGITIZER_IRI)
    g.add((agent, RDF.type, PROV.SoftwareAgent))
    g.add((agent, SCHEMA.name, Literal("WebPlotDigitizer")))
    g.add((agent, SCHEMA.url, URIRef(WEBPLOTDIGITIZER_IRI)))

    return True


def _emit_ingestion_activity(
    g: Graph,
    sd: Namespace,
    sdr: Namespace,
    csv_path: Path,
    run_id: str,
    started_at: datetime,
    software_agent_iri: str,
) -> URIRef:
    activity = sdr[f"ingestion/{run_id}"]
    g.add((activity, RDF.type, sd.IngestionActivity))
    g.add((activity, RDF.type, PROV.Activity))
    g.add(
        (activity, PROV.atTime, Literal(started_at.isoformat(), datatype=XSD.dateTime))
    )
    source = sdr[f"source/{csv_path.name}"]
    g.add((activity, PROV.used, source))
    g.add((source, RDF.type, PROV.Entity))
    g.add((source, SCHEMA.name, Literal(csv_path.name)))
    g.add((activity, PROV.wasAssociatedWith, URIRef(software_agent_iri)))
    return activity


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def ingest_papers(
    csv_path: Path | str,
    out_path: Path | str,
    config: IngestConfig | None = None,
    error_log_path: Path | str | None = None,
) -> IngestStats:
    """Convert papers.csv to a Turtle file at `out_path`.

    Args:
        csv_path: Path to starrydata_papers.csv (or any compatible subset).
        out_path: Destination Turtle file. Parent dirs are created on demand.
        config: IngestConfig overrides (defaults to GitHub Pages namespace).
        error_log_path: Optional jsonl file for failed rows.

    Returns:
        IngestStats with row counts and triple count.
    """
    cfg = config or IngestConfig()
    csv_path = Path(csv_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sd = Namespace(cfg.ontology_iri)
    sdr = Namespace(cfg.resource_iri)

    g = Graph()
    g.bind("sd", sd)
    g.bind("sdr", sdr)
    g.bind("schema", SCHEMA)
    g.bind("dcterms", DCTERMS)
    g.bind("bibo", BIBO)
    g.bind("prov", PROV)

    stats = IngestStats()
    err_fh: TextIO | None = None
    if error_log_path is not None:
        err_fh = Path(error_log_path).open("w", encoding="utf-8")  # noqa: SIM115 (closed in finally)

    ingestion_iri: URIRef | None = None
    if cfg.emit_prov:
        run_id = "run-" + stats.started_at.strftime("%Y%m%dT%H%M%SZ")
        ingestion_iri = _emit_ingestion_activity(
            g, sd, sdr, csv_path, run_id, stats.started_at, cfg.software_agent_iri
        )

    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as fi:
            reader = csv.DictReader(fi)
            for row in reader:
                stats.rows_in += 1
                try:
                    if _emit_paper(g, row, sd, sdr, ingestion_iri):
                        stats.rows_ok += 1
                except Exception as exc:
                    stats.rows_err += 1
                    if err_fh is not None:
                        err_fh.write(
                            json.dumps(
                                {
                                    "row": stats.rows_in,
                                    "sid": row.get("SID"),
                                    "error": repr(exc),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
    finally:
        if err_fh is not None:
            err_fh.close()

    if ingestion_iri is not None:
        stats.ended_at = datetime.now(UTC)
        g.add(
            (
                ingestion_iri,
                PROV.endedAtTime,
                Literal(stats.ended_at.isoformat(), datatype=XSD.dateTime),
            )
        )
    else:
        stats.ended_at = datetime.now(UTC)

    g.serialize(destination=str(out_path), format="turtle")
    stats.triples_out = len(g)
    return stats


def ingest_samples(
    csv_path: Path | str,
    out_path: Path | str,
    config: IngestConfig | None = None,
    error_log_path: Path | str | None = None,
) -> IngestStats:
    """Convert samples.csv to a Turtle file at `out_path`."""
    return _ingest_generic(
        csv_path,
        out_path,
        config,
        error_log_path,
        emit_row=_emit_sample,
    )


def ingest_curves(
    csv_path: Path | str,
    out_path: Path | str,
    config: IngestConfig | None = None,
    error_log_path: Path | str | None = None,
) -> IngestStats:
    """Convert curves.csv to a Turtle file at `out_path`."""
    return _ingest_generic(
        csv_path,
        out_path,
        config,
        error_log_path,
        emit_row=_emit_curve,
    )


def _ingest_generic(
    csv_path: Path | str,
    out_path: Path | str,
    config: IngestConfig | None,
    error_log_path: Path | str | None,
    emit_row,
) -> IngestStats:
    """ingest_papers / samples / curves 共通の I/O ハーネス。

    emit_row(g, row, sd, sdr, ingestion_iri) -> bool で 1 行を処理し、True/False を返す。
    """
    cfg = config or IngestConfig()
    csv_path = Path(csv_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sd = Namespace(cfg.ontology_iri)
    sdr = Namespace(cfg.resource_iri)

    g = Graph()
    g.bind("sd", sd)
    g.bind("sdr", sdr)
    g.bind("schema", SCHEMA)
    g.bind("dcterms", DCTERMS)
    g.bind("bibo", BIBO)
    g.bind("prov", PROV)

    stats = IngestStats()
    err_fh: TextIO | None = None
    if error_log_path is not None:
        err_fh = Path(error_log_path).open("w", encoding="utf-8")  # noqa: SIM115 (closed in finally)

    ingestion_iri: URIRef | None = None
    if cfg.emit_prov:
        run_id = "run-" + stats.started_at.strftime("%Y%m%dT%H%M%SZ")
        ingestion_iri = _emit_ingestion_activity(
            g, sd, sdr, csv_path, run_id, stats.started_at, cfg.software_agent_iri
        )

    try:
        # NOTE: starrydata の curves.csv / samples.csv は UTF-8 BOM 付き。
        # utf-8-sig で開けば BOM を黙って剥がし、DictReader が "SID" を見るようになる。
        with csv_path.open(encoding="utf-8-sig", newline="") as fi:
            reader = csv.DictReader(fi)
            for row in reader:
                stats.rows_in += 1
                try:
                    if emit_row(g, row, sd, sdr, ingestion_iri):
                        stats.rows_ok += 1
                except Exception as exc:
                    stats.rows_err += 1
                    if err_fh is not None:
                        err_fh.write(
                            json.dumps(
                                {
                                    "row": stats.rows_in,
                                    "error": repr(exc),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
    finally:
        if err_fh is not None:
            err_fh.close()

    stats.ended_at = datetime.now(UTC)
    if ingestion_iri is not None:
        g.add(
            (
                ingestion_iri,
                PROV.endedAtTime,
                Literal(stats.ended_at.isoformat(), datatype=XSD.dateTime),
            )
        )

    g.serialize(destination=str(out_path), format="turtle")
    stats.triples_out = len(g)
    return stats


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


_INGESTERS = {
    "papers": ingest_papers,
    "samples": ingest_samples,
    "curves": ingest_curves,
}


def _make_arg_parser(kind: str):
    import argparse

    p = argparse.ArgumentParser(
        prog=f"csv2rdf-starrydata-{kind}",
        description=f"Ingest starrydata {kind}.csv into a Turtle file.",
    )
    p.add_argument("csv", type=Path, help=f"Path to starrydata_{kind}.csv")
    p.add_argument("ttl", type=Path, help="Destination Turtle file")
    p.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    p.add_argument("--resource", default=DEFAULT_RESOURCE)
    p.add_argument("--no-prov", action="store_true")
    p.add_argument("--errors", type=Path, default=None)
    return p


def _run(kind: str, argv: list[str] | None = None) -> int:
    import sys

    args = _make_arg_parser(kind).parse_args(argv)
    cfg = IngestConfig(
        ontology_iri=args.ontology,
        resource_iri=args.resource,
        emit_prov=not args.no_prov,
    )
    stats = _INGESTERS[kind](args.csv, args.ttl, cfg, error_log_path=args.errors)
    print(
        f"in={stats.rows_in} ok={stats.rows_ok} err={stats.rows_err} "
        f"triples={stats.triples_out} -> {args.ttl}",
        file=sys.stderr,
    )
    return 0 if stats.rows_err == 0 else 1


def _main(argv: list[str] | None = None) -> int:
    """Default entry point: papers (for backward compat with Phase 1 PR #2)."""
    return _run("papers", argv)


def _main_samples(argv: list[str] | None = None) -> int:
    return _run("samples", argv)


def _main_curves(argv: list[str] | None = None) -> int:
    return _run("curves", argv)


if __name__ == "__main__":
    raise SystemExit(_main())
