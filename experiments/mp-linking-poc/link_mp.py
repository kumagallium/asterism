"""Starrydata sample -> Materials Project 結晶構造リンクの PoC.

docs/ontology/README.md の Phase 2 ロードマップ
「EMMO や Materials Project などの上位 ontology に subclassOf を張る」の試作。

狙い (PSPP の "structure" 穴埋め):
  実験サンプル (starrydata) は process / property / performance は持つが、
  結晶構造 (structure) 情報が無いことが多い。そこを Materials Project の
  「母相 (host) の理想結晶構造」で補完する。

設計上のキモ:
  - 実サンプルはドープ・非化学量論。MP のエントリは純粋・規則構造。両者は別物。
    → owl:sameAs は使わない。sd:hasHostStructure + sd:idealizedFrom で
      「母相を参照しているだけ」という弱いリンクに留める。
  - ドープは「理想母相からのズレ」= 点欠陥として sd:PointDefect で表現
    (PODO: POint Defect Ontology と整合させる前提)。
  - リンク自体を sd:StructureMatchActivity (prov:Activity) として由来づけ
    (突き合わせ方法・一致度・MP 汎関数・取得日時)。既存の IngestionActivity /
    DigitizationActivity と同じ PROV パターン。
  - 既存 ABox と **同じ** sdr:sample/{SID}-{sample_id} IRI に追加 triple を足すので、
    本番グラフにそのままマージできる。

モード:
  demo  : MP API キー不要。母相式候補を内蔵テーブルで照合 (mp-id はプレースホルダ)。
  live  : MP REST API で母相式候補を順に問い合わせ、実 mp-id・空間群を解決 (要 MP_API_KEY)。

使い方:
  python link_mp.py --csv ../../../starrydata_dataset/starrydata_samples.csv \
                    --out out/sample_mp_links.demo.ttl --limit 40 --mode demo
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import reduce as _freduce
from math import gcd
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, RDFS, XSD

# ---------------------------------------------------------------------------
# Namespaces — 既存 ingester (ingest/src/csv2rdf/starrydata.py) と一致させる
# ---------------------------------------------------------------------------
SD = Namespace("https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#")
SDR = Namespace("https://kumagallium.github.io/csv2rdf-mcp/starrydata/resource/")
SCHEMA = Namespace("https://schema.org/")
# Materials Project の material ページ (dereferenceable IRI として使う)
MP = Namespace("https://next-gen.materialsproject.org/materials/")
# このPoC を表す PROV SoftwareAgent
POC_AGENT = "https://github.com/kumagallium/csv2rdf-mcp#mp-linking-poc"

DOPANT_THRESHOLD = 0.06  # 原子分率がこれ未満の「余り元素」は点欠陥(ドーパント)とみなす


# ---------------------------------------------------------------------------
# 結晶構造記述子
# ---------------------------------------------------------------------------
@dataclass
class StructureInfo:
    space_group_symbol: str
    space_group_number: int
    crystal_system: str
    prototype: str
    mp_id: str | None          # demo はプレースホルダ / live は実 mp-id
    functional: str            # 出所 (demo / GGA / r2SCAN 等)
    source: str                # "demo-table" / "materials-project-api"


# demo モードで確証のある母相のみ (MaterialFamily ラベル / 還元式の両方をキーに)。
# mp-id はプレースホルダ。--live で実際の mp-id に解決する。
KNOWN_HOSTS: dict[str, StructureInfo] = {
    "Bi2Te3": StructureInfo("R-3m", 166, "trigonal",
                            "tetradymite (Bi2Te3-type)", None, "demo", "demo-table"),
    "PbTe": StructureInfo("Fm-3m", 225, "cubic",
                          "rock salt (NaCl-type)", None, "demo", "demo-table"),
    "ZnO": StructureInfo("P6_3mc", 186, "hexagonal",
                         "wurtzite (ZnO-type)", None, "demo", "demo-table"),
    "PbSe": StructureInfo("Fm-3m", 225, "cubic",
                          "rock salt (NaCl-type)", None, "demo", "demo-table"),
    "SnSe": StructureInfo("Pnma", 62, "orthorhombic",
                          "GeS-type (alpha-SnSe)", None, "demo", "demo-table"),
}
# 同族元素グループ (固溶の主成分寄せに使う)。少数派を多数派に合算して母相式を作る。
CHEM_GROUPS: list[set[str]] = [
    {"O", "S", "Se", "Te", "Po"},          # カルコゲン
    {"N", "P", "As", "Sb", "Bi"},          # ニクトゲン
    {"F", "Cl", "Br", "I"},                # ハロゲン
    {"Ti", "Zr", "Hf"}, {"V", "Nb", "Ta"}, {"Cr", "Mo", "W"},  # 同族遷移金属
    {"Li", "Na", "K", "Rb", "Cs"}, {"Be", "Mg", "Ca", "Sr", "Ba"},
]


def _group_of(el: str) -> frozenset[str] | None:
    for grp in CHEM_GROUPS:
        if el in grp:
            return frozenset(grp)
    return None

_FORMULA_RE = re.compile(r"([A-Z][a-z]?)(\d+\.\d+|\d+|\.\d+)?")
_WORDISH_RE = re.compile(r"[a-z]{2,}")  # "with", "doping" 等の単語混入を検出


@dataclass
class Candidate:
    """母相式の候補 (どう導いたか・確からしさ付き)。"""
    formula: str
    method: str
    confidence: str          # high / medium / low


@dataclass
class HostResult:
    candidates: list[Candidate]         # 試す母相式 (優先順)
    dopants: list[tuple[str, float]]    # [(元素, 式中の量)] 希薄=点欠陥
    substituents: list[str]             # 固溶の少数派元素 (注記用)

    @property
    def primary(self) -> str:
        return self.candidates[0].formula if self.candidates else "-"


def parse_formula(comp: str) -> dict[str, float] | None:
    """組成文字列を {元素: 量} に。式に見えなければ None。"""
    s = (comp or "").strip()
    if not s or " " in s or "(" in s or _WORDISH_RE.search(s):
        return None
    out: dict[str, float] = {}
    consumed = 0
    for m in _FORMULA_RE.finditer(s):
        el = m.group(1)
        amt = float(m.group(2)) if m.group(2) else 1.0
        out[el] = out.get(el, 0.0) + amt
        consumed += len(m.group(0))
    # 文字列を概ね全部消費できていなければ式ではないと判断
    if not out or consumed < len(s) * 0.9:
        return None
    return out


def _formula_str(counts: dict[str, int]) -> str:
    """{Pb:1, Te:1} -> 'PbTe' (入力順を保持・1 は省略)。"""
    return "".join(f"{el}{'' if n == 1 else n}" for el, n in counts.items() if n > 0)


def _ints_from(amounts: dict[str, float], round_mode: bool) -> dict[str, int] | None:
    """量を整数化して GCD 約分。round_mode=False は (ほぼ) 整数のみ、True は最近接へ丸め。"""
    if not amounts:
        return None
    ints: dict[str, int] = {}
    for el, a in amounts.items():
        if not round_mode and abs(a - round(a)) > 0.02:
            return None
        n = int(round(a))
        if n > 0:
            ints[el] = n
    if not ints:
        return None
    g = _freduce(gcd, ints.values())
    if g > 1:
        ints = {el: n // g for el, n in ints.items()}
    return ints


def _collapse(amounts: dict[str, float]) -> dict[str, float] | None:
    """同族元素を多数派に合算 (Hf0.75Zr0.25->Hf1, Bi2Te2.7Se0.3->Bi2Te3)。無ければ None。"""
    groups: dict[frozenset[str], list[str]] = {}
    for el in amounts:
        g = _group_of(el)
        if g:
            groups.setdefault(g, []).append(el)
    if not any(len(v) > 1 for v in groups.values()):
        return None
    out = dict(amounts)
    for els in groups.values():
        if len(els) <= 1:
            continue
        major = max(els, key=lambda e: amounts[e])
        total = sum(amounts[e] for e in els)
        for e in els:
            if e != major:
                out.pop(e, None)
        out[major] = total
    return out


def _iso_substituents(amounts: dict[str, float]) -> list[str]:
    """同族で共存する少数派元素 (固溶置換の注記用)。"""
    out: list[str] = []
    groups: dict[frozenset[str], list[str]] = {}
    for el in amounts:
        g = _group_of(el)
        if g:
            groups.setdefault(g, []).append(el)
    for els in groups.values():
        if len(els) > 1:
            major = max(els, key=lambda e: amounts[e])
            out += [e for e in els if e != major]
    return out


def _host_candidates(host_amounts: dict[str, float], family: str) -> list[Candidate]:
    """母相式の候補を優先順に生成 (MaterialFamily / 整数化 / 丸め / 固溶寄せ)。"""
    cands: list[Candidate] = []
    seen: set[str] = set()

    def add(formula: str, method: str, conf: str) -> None:
        if formula and formula not in seen:
            seen.add(formula)
            cands.append(Candidate(formula, method, conf))

    # 1) MaterialFamily が式ならそのまま (curator ラベル・最優先)
    fam = (family or "").strip()
    if parse_formula(fam):
        add(fam, "MaterialFamily(curator)", "high")
    # 2) 厳密整数化 (もともと化学量論的)
    ex = _ints_from(host_amounts, round_mode=False)
    if ex:
        add(_formula_str(ex), "exact integer formula", "medium")
    # 3) 非化学量論を丸め
    rd = _ints_from(host_amounts, round_mode=True)
    if rd:
        add(_formula_str(rd), "rounded stoichiometry", "low")
    # 4) 固溶の主成分寄せ -> 丸め
    col = _collapse(host_amounts)
    if col:
        rc = _ints_from(col, round_mode=True)
        if rc:
            add(_formula_str(rc), "solid-solution collapsed", "low")
    return cands


def normalize_host(comp: str, material_family: str) -> HostResult | None:
    """composition と MaterialFamily から母相式の候補列を作る (テーブル非依存)。

    希薄元素 (原子分率 < DOPANT_THRESHOLD) は点欠陥(ドープ)として分離し、残りの主要元素から
    複数の母相式候補を生成する。live はこれを順に MP に問い合わせ、最初にヒットした式を
    採用する (MP が母相存在の最終判定者)。式も家系も使えなければ None。
    """
    amounts = parse_formula(comp)
    fam = (material_family or "").strip()
    dopants: list[tuple[str, float]] = []
    host_amounts: dict[str, float] = {}
    if amounts:
        total = sum(amounts.values()) or 1.0
        for el, a in amounts.items():
            if a / total < DOPANT_THRESHOLD:
                dopants.append((el, a))
            else:
                host_amounts[el] = a
        if not host_amounts:        # 全部希薄なら丸ごと host 扱い
            host_amounts, dopants = dict(amounts), []

    cands = _host_candidates(host_amounts, fam)
    if not cands:
        return None                 # Polymer/Organic 等 -> unresolved
    return HostResult(cands, dopants, _iso_substituents(host_amounts))


def resolve_structure_demo(host: HostResult) -> tuple[StructureInfo | None, Candidate | None]:
    """候補を順に内蔵テーブルで照合 (オフライン)。最初にヒットした候補を採用。"""
    for cand in host.candidates:
        info = KNOWN_HOSTS.get(cand.formula)
        if info is not None:
            si = StructureInfo(info.space_group_symbol, info.space_group_number,
                               info.crystal_system, info.prototype,
                               mp_id=f"mp-DEMO-{cand.formula}", functional="demo",
                               source="demo-table")
            return si, cand
    return None, None


# MP の新 API (REST)。mp-api クライアントの重い依存 (emmet-core/pymatgen) を避け、
# 標準ライブラリ urllib だけで summary を引く。pymatgen 不要・ビルド失敗の心配なし。
MP_API_BASE = "https://api.materialsproject.org"


def _mp_summary(formula: str, api_key: str, limit: int = 50) -> list[dict]:
    """MP summary を formula で引いて JSON の data 配列を返す。

    失敗時は HTTP ステータスと本文先頭を載せて SystemExit (原因が一目で分かるように)。
    """
    params = {
        "formula": formula,
        "_fields": "material_id,formula_pretty,symmetry,energy_above_hull",
        "_limit": str(limit),
    }
    url = f"{MP_API_BASE}/materials/summary/?" + urllib.parse.urlencode(params)
    # urllib 既定の UA (Python-urllib/x) は Cloudflare の error 1010
    # (browser_signature_banned) で 403 になる。認可済みキーでの正当なアクセスなので
    # ブラウザ風 UA を明示して通す。
    req = urllib.request.Request(url, headers={
        "X-API-KEY": api_key,
        "Accept": "application/json",
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(
            f"MP API HTTP {e.code} (formula={formula!r})。キーやクエリを確認:\n{body}") from e
    except urllib.error.URLError as e:  # ネット到達不可など
        raise SystemExit(f"MP API へ接続できません: {e.reason}") from e
    return payload.get("data") or []


_LIVE_CACHE: dict[str, "StructureInfo | None"] = {}


def _resolve_formula_live(formula: str, api_key: str) -> StructureInfo | None:
    """1 つの母相式を MP に問い合わせ、最安定相の構造を返す (式単位でキャッシュ)。"""
    if formula in _LIVE_CACHE:
        return _LIVE_CACHE[formula]
    docs = _mp_summary(formula, api_key)
    si: StructureInfo | None = None
    if docs:
        def _eah(d: dict) -> float:  # e_above_hull 最小 = 最安定相
            v = d.get("energy_above_hull")
            return v if isinstance(v, (int, float)) else 1e9

        best = min(docs, key=_eah)
        sym = best.get("symmetry") or {}
        si = StructureInfo(
            space_group_symbol=sym.get("symbol", "") or "",
            space_group_number=int(sym.get("number") or 0),
            crystal_system=str(sym.get("crystal_system", "") or ""),
            prototype="",  # REST だけでは prototype 不明 (pymatgen で補える)
            mp_id=str(best.get("material_id", "") or ""),
            functional="MP summary (GGA/GGA+U or r2SCAN)",
            source="materials-project-rest",
        )
    _LIVE_CACHE[formula] = si
    return si


def resolve_structure_live(host: HostResult, api_key: str
                           ) -> tuple[StructureInfo | None, Candidate | None]:
    """候補を順に MP REST API へ問い合わせ、最初にヒットした候補を採用 (要 MP_API_KEY)。"""
    if not api_key:
        raise SystemExit("MP_API_KEY が未設定です (環境変数で渡してください)")
    for cand in host.candidates:
        si = _resolve_formula_live(cand.formula, api_key)
        if si is not None:
            return si, cand
    return None, None


# ---------------------------------------------------------------------------
# RDF 出力
# ---------------------------------------------------------------------------
@dataclass
class RunStats:
    rows_in: int = 0
    resolved: int = 0
    unresolved: int = 0
    dopants_modeled: int = 0
    triples: int = 0
    rows: list[dict] = field(default_factory=list)


def read_material_family(row: dict[str, str]) -> str:
    try:
        info = json.loads(row.get("sample_info") or "{}")
        return (info.get("MaterialFamily") or {}).get("category", "") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return ""


def bind_prefixes(g: Graph) -> None:
    g.bind("sd", SD)
    g.bind("sdr", SDR)
    g.bind("schema", SCHEMA)
    g.bind("mp", MP)
    g.bind("prov", PROV)
    g.bind("dcterms", DCTERMS)


def emit_link(g: Graph, sample_iri: URIRef, sample_key: str,
              host: HostResult | None, struct: StructureInfo | None,
              cand: Candidate | None, activity: URIRef, stats: RunStats) -> None:
    """1 サンプル分の MP リンク triple を追加。"""
    # 突き合わせ Activity (resolved/unresolved 問わず必ず残す = 試行が queryable)
    g.add((activity, RDF.type, SD.StructureMatchActivity))
    g.add((activity, RDF.type, PROV.Activity))
    g.add((activity, PROV.used, sample_iri))
    g.add((activity, PROV.wasAssociatedWith, URIRef(POC_AGENT)))
    g.add((activity, SD.matchMethod,
           Literal(cand.method if cand else "no host resolvable")))
    g.add((activity, SD.matchConfidence,
           Literal(cand.confidence if cand else "unresolved")))
    g.add((activity, PROV.endedAtTime,
           Literal(datetime.now(timezone.utc).isoformat(), datatype=XSD.dateTime)))

    if struct is None or cand is None:
        stats.unresolved += 1
        return

    # 母相結晶構造ノード (同一母相式はサンプル間で共有 = dedupe)
    struct_iri = SDR[f"structure/{cand.formula}"]
    g.add((sample_iri, SD.hasHostStructure, struct_iri))
    g.add((struct_iri, RDF.type, SD.CrystalStructure))
    g.add((struct_iri, RDF.type, PROV.Entity))
    g.add((struct_iri, SD.reducedHostFormula, Literal(cand.formula)))
    if struct.space_group_symbol:
        g.add((struct_iri, SD.spaceGroupSymbol, Literal(struct.space_group_symbol)))
    if struct.space_group_number:
        g.add((struct_iri, SD.spaceGroupNumber,
               Literal(struct.space_group_number, datatype=XSD.integer)))
    if struct.crystal_system:
        g.add((struct_iri, SD.crystalSystem, Literal(struct.crystal_system)))
    if struct.prototype:
        g.add((struct_iri, SD.structurePrototype, Literal(struct.prototype)))
    g.add((struct_iri, SD.mpFunctional, Literal(struct.functional)))
    g.add((struct_iri, PROV.wasGeneratedBy, activity))
    g.add((activity, PROV.generated, struct_iri))

    # Materials Project エントリへの参照 (owl:sameAs ではない!)
    if struct.mp_id:
        mp_iri = MP[struct.mp_id]
        g.add((struct_iri, SD.idealizedFrom, mp_iri))
        g.add((mp_iri, RDF.type, PROV.Entity))
        g.add((mp_iri, SCHEMA.identifier, Literal(struct.mp_id)))
        g.add((mp_iri, SCHEMA.url, mp_iri))

    # 固溶置換の注記
    for el in (host.substituents if host else []):
        g.add((struct_iri, SD.solidSolutionElement, Literal(el)))

    # ドープ = 理想母相からのズレ = 点欠陥 (PODO 整合)
    for el, amt in (host.dopants if host else []):
        defect_iri = SDR[f"defect/{sample_key}/{el}"]
        g.add((sample_iri, SD.hasPointDefect, defect_iri))
        g.add((defect_iri, RDF.type, SD.PointDefect))
        g.add((defect_iri, SD.dopantElement, Literal(el)))
        g.add((defect_iri, SD.siteAmount, Literal(amt, datatype=XSD.double)))
        stats.dopants_modeled += 1

    stats.resolved += 1


def run(csv_path: Path, out_path: Path, limit: int, mode: str) -> RunStats:
    g = Graph()
    bind_prefixes(g)
    # PoC エージェントの最小記述
    g.add((URIRef(POC_AGENT), RDF.type, PROV.SoftwareAgent))
    g.add((URIRef(POC_AGENT), SCHEMA.name, Literal("csv2rdf-mcp mp-linking PoC")))

    api_key = os.environ.get("MP_API_KEY", "") if mode == "live" else ""
    _LIVE_CACHE.clear()  # run ごとに MP 問い合わせキャッシュをリセット
    stats = RunStats()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open(encoding="utf-8-sig", newline="") as fi:
        reader = csv.DictReader(fi)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            stats.rows_in += 1
            sid = (row.get("SID") or "").strip()
            sample_id = (row.get("sample_id") or "").strip()
            if not sid or not sample_id:
                continue
            sample_key = f"{sid}-{sample_id}"
            sample_iri = SDR[f"sample/{sample_key}"]
            comp = (row.get("composition") or "").strip()
            fam = read_material_family(row)

            host = normalize_host(comp, fam)
            struct: StructureInfo | None = None
            cand: Candidate | None = None
            if host is not None and host.candidates:
                struct, cand = (resolve_structure_live(host, api_key) if mode == "live"
                                else resolve_structure_demo(host))

            activity = SDR[f"structurematch/{sample_key}"]
            emit_link(g, sample_iri, sample_key, host, struct, cand, activity, stats)

            stats.rows.append({
                "sample": sample_key, "composition": comp, "family": fam,
                "host": cand.formula if cand else (host.primary if host else "-"),
                "space_group": struct.space_group_symbol if struct else "-",
                "prototype": struct.prototype if struct else "-",
                "mp_id": (struct.mp_id if struct and struct.mp_id else "-"),
                "dopants": ",".join(e for e, _ in host.dopants) if host else "",
                "confidence": cand.confidence if cand else "unresolved",
            })

    stats.triples = len(g)
    g.serialize(destination=str(out_path), format="turtle")
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Starrydata -> Materials Project 構造リンク PoC")
    p.add_argument("--csv", type=Path, required=True, help="starrydata_samples.csv")
    p.add_argument("--out", type=Path, required=True, help="出力 Turtle パス")
    p.add_argument("--limit", type=int, default=40, help="先頭から処理する行数")
    p.add_argument("--mode", choices=["demo", "live"], default="demo")
    args = p.parse_args(argv)

    stats = run(args.csv, args.out, args.limit, args.mode)

    # サマリ表 (stderr ではなく stdout に出して見やすく)
    cols = ["sample", "host", "space_group", "prototype", "mp_id", "dopants", "confidence"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in stats.rows)) for c in cols} if stats.rows else {}
    print("  ".join(c.ljust(widths.get(c, len(c))) for c in cols))
    print("  ".join("-" * widths.get(c, len(c)) for c in cols))
    for r in stats.rows:
        print("  ".join(str(r[c]).ljust(widths.get(c, len(c))) for c in cols))
    print(f"\nrows={stats.rows_in} resolved={stats.resolved} "
          f"unresolved={stats.unresolved} dopants_modeled={stats.dopants_modeled} "
          f"triples={stats.triples} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
