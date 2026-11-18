"""台帳の「確認済み」の関係を、ストアの正本に置く（PR 14e-1b・``pair-relations.md`` §7）。

人が確認した関係（:func:`asterism.pair_relations.confirm`）は、台帳に書くだけでは MCP・チャットの
答えにならない。ここは、台帳の ``confirmed`` の全件を「あるべき状態」として、ストアを合わせる。

* :func:`plan` — 純関数。台帳・写し・ストアの今（用語・線）から、置く・外す・作る用語を決める
* :func:`apply` — 決めたことをストアに書く（外す → 語を作る → 置く → 要らなくなった語を外す）
* :func:`record` — 結果を台帳に写す（件の ``store``・``stored_terms``）
* :func:`sync` — 上の 3 つ＋ストアの読み取りを 1 つにした入口（api が呼ぶ）

決まり（契約 ``pr14e1b-contract.md`` §1）:

* 語の slug は写しの語の id から機械的に（``t-<12 桁>`` → ``kt_<12 桁>``）。同じ名前・同じ種別の
  用語が既にあれば乗り（既存の用語は書き換えない）、対応は台帳の ``stored_terms`` に固定する。
* 線の来歴の ``batch`` は ``knowledge:<取り込み元>``。外すのは「どの台帳も確認していない
  ``knowledge:`` の線」だけ（話・人が置いた線・ほかの取り込み元が確認している線は外さない）。
* 何度流しても同じ（``assert_alignment`` の冪等・用語は在れば作らない）。失敗した件は ``store`` に
  理由を残し、ほかの件は進める（台帳の確認は消さない）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism import knowledge_sources as ks
from asterism import pair_relations as pr
from asterism import shared_vocab as sv
from asterism import substrate
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import (
    BATCH_MAX,
    REASON_MAX,
    ROLES,
    assert_alignment,
    canonical_ends,
    remove_alignment,
)
from asterism.knowledge_bundle import KN_NS, KnowledgeCopy
from asterism.themes import ThemeError, _match_existing, relation_predicate

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_PREFIX",
    "MAX_PUTS",
    "REASONS",
    "Holders",
    "Plan",
    "Report",
    "apply",
    "batch_name",
    "holders_of_others",
    "plan",
    "record",
    "slug_for",
    "sync",
]

BATCH_PREFIX = "knowledge:"
#: 1 回で置く件数の上限（残りは次の回の「追い付き」）。
MAX_PUTS = 50
#: ``store.reason`` の記号（置けなかった理由）。
REASONS = ("role_mismatch", "kind_mismatch", "cycle", "self", "error")
DEFAULT_REASON = "取り込み元の知見から、人が確認した関係"

_TERM_ID = re.compile(r"^t-([0-9a-f]{12})\Z")
_DRAFTED_BY = {"ai": "ai", "user": "human", "origin": "machine"}

#: 線の鍵 ``(source IRI, 述語の名前, target IRI)``（向きの無い関係は :func:`canonical_ends` の順）
Line = tuple[str, str, str]


def batch_name(source: str) -> str:
    """``knowledge:<取り込み元>``。100 字（``BATCH_MAX``）を超えたら切り詰めてハッシュを付ける。"""
    full = f"{BATCH_PREFIX}{source}"
    if len(full) <= BATCH_MAX and not any(ord(c) < 32 for c in full):
        return full
    digest = hashlib.sha1(source.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    head = "".join(c for c in source if ord(c) >= 32)[: BATCH_MAX - len(BATCH_PREFIX) - 13]
    return f"{BATCH_PREFIX}{head}-{digest}"


def slug_for(term_id: str) -> str:
    """写しの語の id → 用語の slug。``t-<12 桁>`` → ``kt_<12 桁>``（それ以外の id は sha1）。"""
    m = _TERM_ID.match(term_id)
    return "kt_" + (m.group(1) if m else hashlib.sha1(term_id.encode("utf-8")).hexdigest()[:12])


def _line_str(line: Line) -> str:
    return "|".join(line)


def _line_of(text: Any) -> Line | None:
    parts = str(text or "").split("|")
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 and all(parts) else None


# ---- 計画 ---------------------------------------------------------------------------------------


@dataclass
class Holders:
    """ほかの取り込み元の台帳が持っているもの。``complete`` が False（読めない台帳がある）なら、
    ほかの取り込み元の線かもしれない物は外さない。"""

    lines: set[Line] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    complete: bool = True


@dataclass
class Put:
    key: str
    source: str
    target: str
    relation: str
    drafted_by: str
    at: str
    reason: str
    evidence: list[str]
    batch: str

    @property
    def line(self) -> Line:
        return (self.source, self.relation, self.target)


@dataclass
class Mint:
    id: str
    slug: str
    kind: str
    label: str
    aliases: list[str]
    role: str | None
    at: str


@dataclass
class Plan:
    source: str
    batch: str
    mints: list[Mint] = field(default_factory=list)
    puts: list[Put] = field(default_factory=list)
    #: 既にストアにある（置き直さない）件 ``key → 線``
    kept: dict[str, Line] = field(default_factory=dict)
    #: 決めた時点で置けない件 ``key → 理由の記号``
    failed: dict[str, str] = field(default_factory=dict)
    #: 前の結果のまま変えない件 ``key → store``
    unchanged: dict[str, dict] = field(default_factory=dict)
    drops: list[Line] = field(default_factory=list)
    #: 台帳に残す語の対応（``stored_terms``）
    mapping: dict[str, dict] = field(default_factory=dict)
    #: この回に回せなかった件数（上限を超えた）
    remaining: int = 0
    #: 確認が外れた件（``store`` を消す）
    cleared: list[str] = field(default_factory=list)
    #: 回した件の確認の時刻 ``key → confirmed.at``（``store.of``。確認が変わったかの見分け）
    of: dict[str, str] = field(default_factory=dict)
    #: 今の確認で、ストアにあるべき線（置けた件・既にある件）
    wanted: set[Line] = field(default_factory=set)
    at: str = ""


@dataclass
class Report:
    placed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    minted: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    removed_terms: list[str] = field(default_factory=list)
    remaining: int = 0
    #: 件ごとの ``store``（台帳に写す）
    store: dict[str, dict] = field(default_factory=dict)
    cleared: list[str] = field(default_factory=list)
    mapping: dict[str, dict] = field(default_factory=dict)

    def summary(self) -> dict:
        """口の返りに載せる形 ``{placed, removed, failed: [{key, reason}]}``。"""
        return {
            "placed": len(self.placed),
            "removed": len(self.removed),
            "failed": [dict(f) for f in self.failed],
        }


def _stored_terms(ledger: Mapping[str, Any]) -> dict[str, dict]:
    raw = ledger.get("stored_terms")
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict) and isinstance(v.get("slug"), str) and v["slug"]:
                out[str(k)] = {"slug": v["slug"], "minted": bool(v.get("minted"))}
    return out


def plan(
    ledger: Mapping[str, Any],
    copy: KnowledgeCopy,
    *,
    existing_terms: Iterable[sv.SharedTerm],
    other_holders: Holders,
    lines: Mapping[Line, str | None] | None = None,
    claim_iris: Iterable[str] | None = None,
    focus: Iterable[str] = (),
    limit: int = MAX_PUTS,
    now: str | None = None,
) -> Plan:
    """台帳の確認済みの全件に対する、ストアの「あるべき状態」との差を決める（純関数）。

    ``lines`` はストアの今の線 ``{線の鍵: batch}``（無ければ「無い」として扱う）。``claim_iris`` は
    公開中の写しに在る知見の IRI（``None`` なら外さない）。``focus`` の鍵を先に回し、回すのは
    1 回に ``limit`` 件まで（超えた分は ``remaining``）。"""
    source = str(ledger.get("source") or "")
    out = Plan(source=source, batch=batch_name(source), at=now or datetime.now(UTC).isoformat())
    lines = lines or {}
    existing = list(existing_terms)
    by_slug = {t.slug: t for t in existing}
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    claim_iri = {str(c["id"]): str(c["iri"]) for c in copy.claims if "id" in c and c.get("iri")}
    present = None if claim_iris is None else set(claim_iris)
    mapping = _stored_terms(ledger)
    minting: dict[str, Mint] = {}
    pairs = ledger.get("pairs") or {}
    focus_set = set(focus)

    def resolve(tid: str, at: str) -> tuple[str, str] | None:
        """語の id → ``(slug, kind)``。決められなければ ``None``。"""
        t = terms.get(tid)
        cur = mapping.get(tid)
        if cur is not None:
            slug = cur["slug"]
            have = by_slug.get(slug)
            if have is not None:
                return slug, have.kind
            if t is None:
                return None
            mapping[tid] = {"slug": slug, "minted": True}  # 外された物はもう一度作る
            minting.setdefault(slug, _mint(tid, slug, t, at))
            return slug, str(t.get("kind"))
        if t is None:
            return None
        kind = "property" if t.get("kind") == "property" else "class"
        name = str(t.get("name") or "")
        hit = _match_existing(name, "", [str(a) for a in t.get("aliases") or []], kind, existing)
        if hit is not None:
            mapping[tid] = {"slug": hit[0].slug, "minted": False}
            return hit[0].slug, kind
        slug = slug_for(tid)
        mapping[tid] = {"slug": slug, "minted": True}
        if slug not in by_slug:
            minting.setdefault(slug, _mint(tid, slug, t, at))
        return slug, kind

    todo: list[str] = []
    for key in sorted(pairs):
        e = pairs[key]
        c = e.get("confirmed") if isinstance(e, dict) else None
        if not c:
            if isinstance(e, dict) and e.get("store") is not None:
                out.cleared.append(key)
            continue
        st = e.get("store") if isinstance(e.get("store"), dict) else None
        if st and st.get("of") == c.get("at"):
            line = _line_of(st.get("line"))
            if st.get("ok") and line is not None and line in lines:
                out.kept[key] = line
                out.wanted.add(line)
                out.unchanged[key] = dict(st)
                continue
            if not st.get("ok") and st.get("reason") != "error":
                out.unchanged[key] = dict(st)  # 同じ確認の断りは、確認が変わるまで置き直さない
                continue
        todo.append(key)
    todo.sort(key=lambda k: (k not in focus_set, k))
    out.remaining = max(0, len(todo) - max(0, limit))
    for key in todo[: max(0, limit)]:
        e = pairs[key]
        c = e["confirmed"]
        frm, to, rel = str(c.get("from")), str(c.get("to")), str(c.get("rel"))
        at = str(c.get("at") or out.at)
        out.of[key] = c.get("at")
        a, b = resolve(frm, at), resolve(to, at)
        if a is None or b is None:
            out.failed[key] = "error"
            continue
        try:
            pred = relation_predicate(rel, a[1], b[1])
        except ThemeError:
            out.failed[key] = "kind_mismatch"
            continue
        s, t = canonical_ends(pred, sv.term_iri(a[0]), sv.term_iri(b[0]))
        line = (s, pred, t)
        if line in lines:  # 既にある（自分でも・話・ほかの取り込み元でも）。置き直さない
            out.kept[key] = line
            out.wanted.add(line)
            out.unchanged[key] = {"ok": True, "at": out.at, "of": at, "line": _line_str(line)}
            continue
        wanted_claims = [claim_iri[str(i)] for i in c.get("evidence") or [] if str(i) in claim_iri]
        if present is not None:
            wanted_claims = [i for i in wanted_claims if i in present]
        reason = " ".join(str(e.get("reason") or "").split())[:REASON_MAX] or DEFAULT_REASON
        out.puts.append(
            Put(
                key=key,
                source=s,
                target=t,
                relation=pred,
                drafted_by=_DRAFTED_BY.get(str(e.get("by")), "human"),
                at=at,
                reason=reason,
                evidence=wanted_claims,
                batch=out.batch,
            )
        )
    out.mints = [minting[k] for k in sorted(minting)]
    out.mapping = mapping
    # 外す線: ``knowledge:`` の線のうち、どの台帳も確認していないもの
    keep = set(out.wanted) | {p.line for p in out.puts} | other_holders.lines
    # 回せなかった件（上限の外）の線は、前の結果にある物を残す
    for key in todo[max(0, limit) :]:
        st = (pairs[key].get("store") or {}) if isinstance(pairs[key], dict) else {}
        line = _line_of(st.get("line")) if st.get("ok") else None
        if line is not None:
            keep.add(line)
            out.wanted.add(line)
    for line, batch in sorted(lines.items()):
        if line in keep or not isinstance(batch, str) or not batch.startswith(BATCH_PREFIX):
            continue
        if batch == out.batch or other_holders.complete:
            out.drops.append(line)
    return out


def _mint(tid: str, slug: str, t: Mapping[str, Any], at: str) -> Mint:
    kind = "property" if t.get("kind") == "property" else "class"
    role = t.get("role") if kind == "class" and t.get("role") in ROLES else None
    return Mint(
        id=tid,
        slug=slug,
        kind=kind,
        label=str(t.get("name") or slug),
        aliases=[str(a) for a in t.get("aliases") or []],
        role=role,
        at=at,
    )


# ---- 書く ---------------------------------------------------------------------------------------


def _reason_of(exc: ValueError) -> str:
    msg = str(exc)
    if msg == "cycle":
        return "cycle"
    if msg.startswith("kind mismatch"):
        return "kind_mismatch"
    if msg.startswith("role mismatch"):
        return "role_mismatch"
    if msg == "self relation":
        return "self"
    return "error"


async def apply(
    client: Any,
    root: Path | str,
    plan: Plan,
    *,
    source: str,
    other_terms: Iterable[str] = (),
) -> Report:
    """計画をストアに書く。外す → 語を作る → 置く → 要らなくなった（この段で作った）語を外す。
    1 件の失敗は ``Report.failed`` に入れて続ける（何度流しても同じ・次の回でやり直せる）。
    ``other_terms`` はほかの取り込み元が使っている用語の slug（外さない）。"""
    rep = Report(remaining=plan.remaining, cleared=list(plan.cleared))
    at = plan.at
    wanted = set(plan.wanted)

    for line in plan.drops:
        try:
            await remove_alignment(client, line[0], line[2], line[1])
            rep.removed.append(_line_str(line))
        except Exception:
            logger.warning("confirmed_store: could not remove a line", exc_info=True)

    bad_terms: set[str] = set()
    for m in plan.mints:
        try:
            await sv.mint_term(
                client,
                root,
                slug=m.slug,
                kind=m.kind,
                label=m.label,
                cqs=[],
                at=m.at,
                aliases=m.aliases,
                theme_id=f"knowledge:{source}",
                role=m.role,
                role_by="machine" if m.role else None,
            )
            rep.minted.append(m.slug)
        except sv.TermExists:
            pass
        except Exception:
            logger.warning("confirmed_store: could not mint a term", exc_info=True)
            bad_terms.add(m.slug)

    failed: dict[str, str] = dict(plan.failed)
    for p in plan.puts:
        ends = {sv.slug_of(p.source), sv.slug_of(p.target)}
        if ends & bad_terms:
            failed[p.key] = "error"
            continue
        try:
            await assert_alignment(
                client,
                p.source,
                p.target,
                p.relation,
                at=p.at,
                drafted_by=p.drafted_by,
                batch=p.batch,
                reason=p.reason,
                evidence=p.evidence,
            )
            rep.placed.append(p.key)
            wanted.add(p.line)
            rep.store[p.key] = {
                "ok": True, "at": at, "of": p.at, "line": _line_str(p.line)
            }
        except ValueError as exc:
            failed[p.key] = _reason_of(exc)
        except Exception:
            logger.warning("confirmed_store: could not place a line", exc_info=True)
            failed[p.key] = "error"

    for key, st in plan.unchanged.items():
        rep.store.setdefault(key, st)
    for key, reason in failed.items():
        rep.store[key] = {"ok": False, "reason": reason, "at": at, "of": plan.of.get(key)}
        rep.failed.append({"key": key, "reason": reason})

    # 要らなくなった（この段で作った）語を外す。線が触れていれば外せない（残す）
    used_slugs = {s for ln in wanted for s in (sv.slug_of(ln[0]), sv.slug_of(ln[2])) if s}
    mapping = {k: dict(v) for k, v in plan.mapping.items()}
    keep_slugs = set(other_terms)
    for tid, v in sorted(mapping.items()):
        slug = v["slug"]
        if not v.get("minted") or slug in used_slugs or slug in keep_slugs:
            continue
        try:
            await sv.remove_term(client, root, slug)
            rep.removed_terms.append(slug)
            del mapping[tid]
        except sv.TermNotFound:
            del mapping[tid]
        except sv.TermHasLines:
            pass
        except Exception:
            logger.warning("confirmed_store: could not remove a term", exc_info=True)
    rep.used = sorted({v["slug"] for v in mapping.values() if not v.get("minted")} & used_slugs)
    rep.mapping = mapping
    return rep


# ---- 台帳へ写す ---------------------------------------------------------------------------------


def record(ledger: Mapping[str, Any], report: Report) -> dict:
    """結果を台帳に写した新しい台帳を返す（入力は変えない）。件の ``store``（確認が外れた件は
    消す）と ``stored_terms``。"""
    import copy as _copy

    out = _copy.deepcopy(dict(ledger))
    pairs = out.get("pairs") or {}
    for key, st in report.store.items():
        if key in pairs and pairs[key].get("confirmed"):
            pairs[key]["store"] = st
    for key in report.cleared:
        if key in pairs:
            pairs[key].pop("store", None)
    if report.mapping or out.get("stored_terms"):
        out["stored_terms"] = report.mapping
    return out


# ---- ストアの読み取り・ほかの台帳 ---------------------------------------------------------------


async def read_lines(client: Any) -> dict[Line, str | None]:
    """alignment graph の全部の線 ``{(source, 述語の名前, target): batch}``（1 回の問い合わせ）。"""
    res = await client.sparql_select(
        f"SELECT ?s ?r ?t ?b WHERE {{ GRAPH <{substrate.ALIGNMENT_GRAPH}> {{ "
        f"?a a <{XW}Alignment> ; <{XW}alignSource> ?s ; <{XW}alignTarget> ?t ; "
        f"<{XW}alignRelation> ?r . OPTIONAL {{ ?a <{XW}batch> ?b }} }} }}"
    )
    rows = (res.get("results") or {}).get("bindings", []) if isinstance(res, dict) else []
    out: dict[Line, str | None] = {}
    for b in rows:
        s, r, t = (b.get(k, {}).get("value") for k in ("s", "r", "t"))
        if s and r and t:
            out[(s, r, t)] = (b.get("b") or {}).get("value")
    return out


async def present_claims(client: Any, iris: Iterable[str], graph: str | None) -> set[str]:
    """公開中の写しの graph に ``kn:Claim`` として在る IRI（1 回の問い合わせ）。
    graph が分からなければ全 graph から探す。"""
    wanted = sorted({i for i in iris if re.fullmatch(r'https?://[^\s<>"{}|\\^`]+', i)})
    if not wanted:
        return set()
    values = " ".join(f"<{i}>" for i in wanted)
    inner = f"?c a <{KN_NS}Claim>"
    where = f"GRAPH <{graph}> {{ {inner} }}" if graph else f"GRAPH ?g {{ {inner} }}"
    res = await client.sparql_select(
        f"SELECT DISTINCT ?c WHERE {{ VALUES ?c {{ {values} }} {where} }}"
    )
    rows = (res.get("results") or {}).get("bindings", []) if isinstance(res, dict) else []
    return {b["c"]["value"] for b in rows if "c" in b}


def holders_of_others(root: Path | str, source: str) -> Holders:
    """ほかの取り込み元の台帳が置いている線と使っている用語（台帳のファイルだけを読む）。"""
    out = Holders()
    folder = Path(root) / "vocab-shared" / "knowledge"
    mine = pr.ledger_path(root, source)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        if path == mine:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.complete = False
            continue
        if not isinstance(data, dict) or not isinstance(data.get("pairs"), dict):
            continue
        for e in data["pairs"].values():
            if not isinstance(e, dict) or not e.get("confirmed"):
                continue
            st = e.get("store")
            line = _line_of(st.get("line")) if isinstance(st, dict) and st.get("ok") else None
            if line is not None:
                out.lines.add(line)
        out.terms.update(v["slug"] for v in _stored_terms(data).values())
    return out


async def sync(
    client: Any,
    root: Path | str,
    ledger: Mapping[str, Any],
    copy: KnowledgeCopy,
    *,
    claim_graph: str | None = None,
    focus: Iterable[str] = (),
    limit: int = MAX_PUTS,
    now: str | None = None,
    title: str | None = None,
) -> tuple[dict, Report]:
    """ストアを読んで :func:`plan` → :func:`apply` → :func:`record`。返りは ``(新しい台帳, 結果)``。
    台帳は保存しない（呼び手が保存する）。"""
    source = str(ledger.get("source") or "")
    holders = holders_of_others(root, source)
    existing = await sv._read_terms(client)
    lines = await read_lines(client)
    iris = {
        str(c["iri"])
        for c in copy.claims
        if c.get("iri") and c.get("id") is not None
    }
    claims = await present_claims(client, iris, claim_graph) if iris else set()
    p = plan(
        ledger,
        copy,
        existing_terms=existing,
        other_holders=holders,
        lines=lines,
        claim_iris=claims,
        focus=focus,
        limit=limit,
        now=now,
    )
    rep = await apply(client, root, p, source=source, other_terms=holders.terms)
    new = record(ledger, rep)
    try:  # 置けた件から、この取り込み元の知識の問いを作り直す（落ちても置いた結果は残す）
        ks.update_source(
            root, source, new, copy, mapping=rep.mapping, title=title, now=p.at
        )
    except Exception:
        logger.warning("confirmed_store: could not rebuild the knowledge questions", exc_info=True)
    return new, rep
