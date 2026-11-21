"""知識の写しの「語の組」に、AI が選んだ関係を検査して台帳に残す、決定論の前後処理。

何をするか（すべて決まった規則だけ）:

1. 写し（:func:`asterism.knowledge_bundle.read_copy`）から、同じ知見に出る語の組を作り、
   AI に渡す入力（語の名前・種別・役割と番号つきの知見の題）と指紋を付ける（:func:`build_tasks`）
2. AI の答え（閉じた集合から関係を 1 つ、または ``none``）を検査する（:func:`check_answer`）
3. ``sub``・``part`` の輪を落とす（:func:`drop_cycles`）
4. 文脈の語・確からしさ・確認状況を付ける（:func:`annotate`）
5. 取り込み元が知っている関係を同じ検査に通す（:func:`origin_relations`）
6. 取り込み元ごとの台帳（``<root>/vocab-shared/knowledge/<名前>.json``）に保存し、
   読み直しの計画（聞く組・外す組）を立てる（:func:`plan_run` ほか）

何をしないか: AI は呼ばない・ストア（SPARQL）は読まない・書かない。取り込み元の名前では
分岐しない（値の語彙は :data:`TENTATIVE` の表だけ）。
"""

from __future__ import annotations

import contextlib
import copy as _copy
import hashlib
import json
import math
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from asterism import knowledge_bundle, relation_catalog
from asterism.knowledge import LINE_OPS, MISSING_MIN_OTHERS
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.shared_vocab import describe_selection
from asterism.term_match import merge_key
from asterism.themes import (
    RELATION_KINDS,
    experiment_relation_problem,
    knowledge_target_kind_ok,
)

__all__ = [
    "CONTEXT_MAX",
    "LEDGER_VERSION",
    "PAIR_RELATIONS",
    "STATES",
    "SYMMETRIC_PAIR_RELATIONS",
    "TENTATIVE",
    "LedgerError",
    "annotate",
    "assign",
    "build_tasks",
    "check_answer",
    "confirm",
    "counts_by_state",
    "drop_cycles",
    "empty_ledger",
    "failed_entry",
    "finalize",
    "ledger_path",
    "load_ledger",
    "merge_entry",
    "origin_check",
    "origin_key",
    "origin_relations",
    "plan_run",
    "questions",
    "reask_keys",
    "reask_targets",
    "reject",
    "relation_problem",
    "remove_ledger",
    "restore",
    "save_ledger",
    "shown_relations",
    "strip_unconfirmed",
    "summarize",
    "unconfirm",
]

#: 語の組で選べる関係（``quantity`` の相手は標準の量の種類で、語の組にならない）。
PAIR_RELATIONS = tuple(r for r in RELATION_KINDS if r != "quantity")
#: 向きの無い関係（語の id の順に並べる）。
SYMMETRIC_PAIR_RELATIONS = relation_catalog.symmetric_pair_relations()


def _readable_relations() -> tuple[str, ...]:
    """読める関係（組み込み＋人が足した関係。やめたものも含む）。呼ぶたびに目録から引く。"""
    return tuple(r for r in relation_catalog.relation_kinds() if r != "quantity")


def _assignable_relations() -> tuple[str, ...]:
    """新しく付けられる関係（やめた関係を除く）。"""
    return tuple(r for r in relation_catalog.assignable_relation_kinds() if r != "quantity")
#: 「見込み」の語彙（束の約束）。根拠の知見が全部これに当たるときだけ ``tentative``。
TENTATIVE: dict[str, set[str]] = {
    "epistemic_status": {"speculation"},
    "modal_qualifier": {"possibly", "rarely"},
}
CONTEXT_MAX = 3
LEDGER_VERSION = 1
STATES = ("relation", "none", "dropped", "failed")

_NONE = "none"
_MAX_EVIDENCE = 3
# 検査と問いの規則は目録から導く（``asterism.relation_catalog``）
_DIRECTED_TO_PROPERTY = relation_catalog.to_property_only_relations()
_SAME_KIND = relation_catalog.same_kind_relations()
_CYCLE_RELS = relation_catalog.acyclic_relations()
_QUESTION_OP = relation_catalog.question_ops()
_NAME_RE = re.compile(r"[a-z0-9_-]{1,64}")
_ANNOTATED = ("context", "tentative", "review")


# ---- 組を作る -----------------------------------------------------------------------------------


def _title(claim: Mapping[str, Any]) -> str:
    return str(claim.get("title") or "").strip()


def _term_brief(t: Mapping[str, Any]) -> dict:
    return {"name": t.get("name"), "kind": t.get("kind"), "role": t.get("role")}


def _fingerprint(inp: dict, claim_ids: list[str]) -> str:
    raw = json.dumps(
        {"input": inp, "claim_ids": claim_ids}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_tasks(copy: KnowledgeCopy, *, max_claims: int = 8) -> list[dict]:
    """写しの語の組ごとに、AI への入力つきの仕事を作る。

    知見は ``verified`` を先・次に id の順で ``max_claims`` 件。語か知見が写しに無い組・題が空の
    知見は作らない（知見が 1 件も残らなければ組も作らない）。並びは 知見の多い組 → ``key``。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    claims = {str(c["id"]): c for c in copy.claims if "id" in c}
    tasks: list[dict] = []
    for (a, b), cids in knowledge_bundle.term_pairs(copy).items():
        ta, tb = terms.get(a), terms.get(b)
        if ta is None or tb is None:
            continue
        usable = [c for c in sorted(set(cids)) if c in claims and _title(claims[c])]
        if not usable:
            continue
        # verified を先に（sorted は安定なので id の順は保たれる）
        usable.sort(key=lambda c: 0 if claims[c].get("review_state") == "verified" else 1)
        chosen = usable[: max(1, max_claims)]
        inp = {
            "a": _term_brief(ta),
            "b": _term_brief(tb),
            "claims": [_title(claims[c]) for c in chosen],
        }
        tasks.append(
            {
                "key": f"{a}|{b}",
                "a": a,
                "b": b,
                "claim_ids": chosen,
                "claims_total": len(usable),
                "input": inp,
                "fingerprint": _fingerprint(inp, chosen),
            }
        )
    tasks.sort(key=lambda t: (-t["claims_total"], t["key"]))
    return tasks


# ---- 検査 ---------------------------------------------------------------------------------------


def relation_problem(
    rel: str, from_term: Mapping[str, Any], to_term: Mapping[str, Any]
) -> str | None:
    """関係の両端の語が規則に合うかを調べる純関数。合えば ``None``。

    ``same``（同じ語）／``mixed``（``sub``・``eq``・``part`` で種別が違う）／
    ``target_not_property``（``lowers``・``raises``・``affects`` の相手が項目でない）／
    新しい 5 つの関係は :func:`asterism.themes.experiment_relation_problem`
    （``mixed``・``role_mismatch``）。"""
    if from_term.get("id") is not None and from_term.get("id") == to_term.get("id"):
        return "same"
    fk, tk = str(from_term.get("kind")), str(to_term.get("kind"))
    if rel in relation_catalog.to_property_only_relations():
        return None if tk == "property" else "target_not_property"
    if rel in _SAME_KIND:
        return None if fk == tk else "mixed"
    if rel in _readable_relations():
        return experiment_relation_problem(
            rel, fk, tk, from_term.get("role"), to_term.get("role")
        )
    return "relation"


def _order(rel: str, frm: str, to: str) -> tuple[str, str]:
    """向きの無い関係は語の id の順に並べる。"""
    if rel in relation_catalog.symmetric_pair_relations() and to < frm:
        return to, frm
    return frm, to


def _terms_by_id(terms: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    if isinstance(terms, Mapping):
        return {str(k): v for k, v in terms.items()}
    return {str(t["id"]): t for t in terms if "id" in t}


def _core(task: Mapping[str, Any], state: str, **kw: Any) -> dict:
    out: dict[str, Any] = {
        "key": task["key"],
        "a": task["a"],
        "b": task["b"],
        "fingerprint": task.get("fingerprint"),
        "claims": list(task.get("claim_ids") or []),
        "claims_total": task.get("claims_total", len(task.get("claim_ids") or [])),
        "state": state,
        "rel": None,
        "from": None,
        "to": None,
        "evidence": [],
        "note": None,
        "reason": None,
    }
    out.update(kw)
    return out


_LEN_UNSTATED_KIND = 20

_KIND_NONE_WORDS = frozenset(
    {
        "none", "null", "n/a", "na", "unknown", "nothing", "no relation",
        "なし", "無し", "不明", "該当なし", "関係なし", "分からない", "わからない",
    }
)  # fmt: skip
_KIND_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_KIND_NUMBER = re.compile(r"^c\d+(?:,\s*c\d+)*\Z", re.IGNORECASE)
_KIND_CODE = re.compile(
    r"https?://|[<>{}`]|\b(?:SELECT|INSERT|DELETE|WHERE|PREFIX)\b", re.IGNORECASE
)


def _unstated_kind(raw: Any) -> str | None:
    """``unstated_kind`` の正規化（step0 と ingest に同じ規則を 2 か所書き、見張りのテストで
    同じ答えを縛る）。通れば整えた文字列・捨てるなら ``None``。

    NFKC にそろえ・空白を詰め・制御と書式の文字（Cc・Cf）を取り除く。捨てる物: 空・20 文字超
    （切らない）・文字が無い（記号・数字だけ）・1 文字の英字・「無い／分からない」の言い換え・
    関係の短い名前と ``none``・知見の番号の形・スキームや URL・Markdown のリンク・
    コードの記号と SQL 語。"""
    if not isinstance(raw, str):
        return None
    kind = unicodedata.normalize("NFKC", raw)
    kind = " ".join(kind.split())
    kind = "".join(ch for ch in kind if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
    if not kind or len(kind) > _LEN_UNSTATED_KIND:
        return None
    if not any(ch.isalpha() for ch in kind):
        return None
    low = kind.casefold()
    if len(kind) == 1 and kind.isascii():
        return None
    if low in _KIND_NONE_WORDS or low in {*PAIR_RELATIONS, "none"} or _KIND_NUMBER.match(low):
        return None
    if _KIND_SCHEME.match(kind) or "//" in kind or low.startswith("www."):
        return None
    if "[" in kind or "]" in kind or _KIND_CODE.search(kind):
        return None
    return kind


def check_answer(
    task: Mapping[str, Any],
    answer: Mapping[str, Any],
    terms: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    rejected: Iterable[Mapping[str, Any]] = (),
) -> dict:
    """AI の答え 1 つを検査して、台帳の 1 件の核にする。

    ``state`` は ``relation``・``none``・``dropped``。

    ``terms`` は語の行（id → 行の辞書、または行の並び。辞書ならそのまま索引に使う）。``rejected`` は
    ``[{rel, from, to, at}]``（語の id）で、同じ 3 つ組（向きの無い関係は向きを問わない）は
    ``dropped``・理由 ``rejected``。落とす理由: ``relation``（集合の外）・``direction``・
    ``term``（語が無い）・``evidence``・``same``／``mixed``／``target_not_property``／
    ``role_mismatch``（:func:`relation_problem`）・``rejected``。"""
    if not isinstance(answer, Mapping):
        return _core(task, "dropped", reason="relation")
    rel = answer.get("rel")
    note = str(answer.get("note") or "").strip() or None
    if rel == _NONE:
        kind = _unstated_kind(answer.get("unstated_kind"))
        extra = {"unstated_kind": kind} if kind else {}  # 無いときは欄ごと出さない
        return _core(task, "none", note=note, **extra)
    if rel not in _assignable_relations():
        return _core(task, "dropped", reason="relation", note=note)
    rel = str(rel)

    side = answer.get("from")
    if rel in relation_catalog.symmetric_pair_relations():
        side = side if side in ("a", "b") else "a"
    if side not in ("a", "b"):
        return _core(task, "dropped", reason="direction", rel=rel, note=note)
    frm, to = (task["a"], task["b"]) if side == "a" else (task["b"], task["a"])
    frm, to = _order(rel, frm, to)

    ids = list(task.get("claim_ids") or [])
    ev: list[str] = []
    raw_ev = answer.get("evidence")
    if raw_ev is None:
        raw_ev = []
    if not isinstance(raw_ev, (list, tuple)):  # 番号の並びでない答えは根拠なし
        return _core(
            task, "dropped", reason="evidence",
            **{"rel": rel, "from": frm, "to": to, "note": note},
        )
    for e in raw_ev:
        m = re.fullmatch(r"c([0-9]{1,2})", e) if isinstance(e, str) else None
        if not m or not 1 <= int(m.group(1)) <= len(ids):
            return _core(
                task,
                "dropped",
                reason="evidence",
                **{"rel": rel, "from": frm, "to": to, "note": note},
            )
        cid = ids[int(m.group(1)) - 1]
        if cid not in ev:
            ev.append(cid)
    ev = ev[:_MAX_EVIDENCE]
    shared = {"rel": rel, "from": frm, "to": to, "evidence": ev, "note": note}
    if not ev:
        return _core(task, "dropped", reason="evidence", **shared)

    by_id = terms if isinstance(terms, dict) else _terms_by_id(terms)  # dict は id → 行（索引済み）
    tf, tt = by_id.get(frm), by_id.get(to)
    if tf is None or tt is None:
        return _core(task, "dropped", reason="term", **shared)
    problem = relation_problem(rel, tf, tt)
    if problem:
        return _core(task, "dropped", reason=problem, **shared)
    here = (frm, rel, to)
    for r in rejected:
        rf, rr, rt = str(r.get("from")), str(r.get("rel")), str(r.get("to"))
        rf, rt = _order(rr, rf, rt)
        if (rf, rr, rt) == here:
            return _core(task, "dropped", reason="rejected", **shared)
    return _core(task, "relation", **shared)


def failed_entry(task: Mapping[str, Any], reason: str) -> dict:
    """AI の呼び出しが失敗した組（``state: "failed"``）。"""
    return _core(task, "failed", reason=str(reason))


# ---- 輪・文脈・確からしさ -----------------------------------------------------------------------


def _reaches(edges: Mapping[str, set[str]], start: str, goal: str) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n == goal:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(sorted(edges.get(n, ())))
    return False


def drop_cycles(
    entries: Iterable[Mapping[str, Any]], origin: Iterable[Mapping[str, Any]] = ()
) -> list[dict]:
    """``sub``・``part`` の輪になる AI の関係を、``key`` の順に見て後のものを落とす。

    落とした件は ``dropped``・理由 ``cycle``。``sub`` と ``part`` は別々に見る。

    - ``confirmed`` のある件は落とさない（先に辺として入れ、そのあと残りを ``key`` の順に見る）。
    - ``origin`` の辺は数えない（引数は互換のために残す）。取り込み元の言い分で AI の関係を
      消さない。
      ``origin`` 側の輪は :func:`finalize` が、``origin`` の行を ``disputes`` にして扱う。

    入力は変えず、入力と同じ並びの新しいリストを返す。落とした件も ``rel``・``from``・``to`` は残す
    （:func:`finalize` が読み直しで元に戻せる）。"""
    out = [dict(e) for e in entries]
    edges: dict[str, dict[str, set[str]]] = {r: {} for r in _CYCLE_RELS}
    live = [
        e
        for e in out
        if e.get("state") == "relation"
        and not e.get("orphan")  # 写しから消えた組は記録として残すだけ（辺に数えない・変えない）
        and e.get("rel") in edges
        and e.get("from")
        and e.get("to")
    ]
    for e in live:  # 確認済みは先に辺として入れる（落とさない）
        if e.get("confirmed"):
            edges[e["rel"]].setdefault(str(e["from"]), set()).add(str(e["to"]))
    for e in sorted((x for x in live if not x.get("confirmed")), key=lambda x: str(x["key"])):
        g = edges[e["rel"]]
        if _reaches(g, str(e["to"]), str(e["from"])):
            e["state"] = "dropped"
            e["reason"] = "cycle"
            for k in _ANNOTATED:
                e.pop(k, None)
        else:
            g.setdefault(str(e["from"]), set()).add(str(e["to"]))
    return out


def _is_tentative(claim: Mapping[str, Any]) -> bool:
    return any(claim.get(f) in vals for f, vals in TENTATIVE.items())


class _Index:
    """写しの索引（組ごとに舐め直さない）: 知見 id → 行・知見 id → 語の id の集合・語 id → 名前。"""

    def __init__(self, copy: KnowledgeCopy) -> None:
        self.claims = {str(c["id"]): c for c in copy.claims if "id" in c}
        self.names = {str(t["id"]): str(t.get("name") or "") for t in copy.terms if "id" in t}
        self.claim_terms: dict[str, set[str]] = {}
        for r in copy.term_claims:
            self.claim_terms.setdefault(str(r["claim_id"]), set()).add(str(r["term_id"]))


def _annotate(entry: Mapping[str, Any], idx: _Index) -> dict:
    out = dict(entry)
    if out.get("state") != "relation":
        return out
    ev = list(dict.fromkeys(str(c) for c in out.get("evidence") or []))
    ends = {out.get("from"), out.get("to")}
    counts: dict[str, int] = {}
    for cid in ev:
        for tid in idx.claim_terms.get(cid, ()):
            if tid not in ends:
                counts[tid] = counts.get(tid, 0) + 1
    need = max(1, math.ceil(len(ev) / 2))
    ctx = sorted(
        (t for t, n in counts.items() if n >= need),
        key=lambda t: (-counts[t], idx.names.get(t, ""), t),
    )
    present = [idx.claims[c] for c in ev if c in idx.claims]
    out["context"] = ctx[:CONTEXT_MAX]
    known = len(present) == len(ev)
    out["tentative"] = bool(ev) and known and all(_is_tentative(c) for c in present)
    out["review"] = {
        "verified": sum(1 for c in present if c.get("review_state") == "verified"),
        "candidate": sum(1 for c in present if c.get("review_state") == "candidate"),
    }
    return out


def annotate(entry: Mapping[str, Any], copy: KnowledgeCopy) -> dict:
    """``state == "relation"`` の件に ``context``・``tentative``・``review`` を付けて返す。

    ほかの件はそのまま返す。

    - ``context``: 根拠の知見の ``ceil(n / 2)`` 件以上に出る、両端以外の語の id（最大
      :data:`CONTEXT_MAX`・出る件数の多い順 → 名前 → id）
    - ``tentative``: 根拠の知見が全部「見込み」（:data:`TENTATIVE`）のときだけ ``True``
    - ``review``: 根拠の知見の ``{"verified": n, "candidate": n}``

    1 件ごとに索引を作る。多数の件は :func:`finalize` が索引を 1 回だけ作って使い回す。"""
    return _annotate(entry, _Index(copy))


def origin_relations(copy: KnowledgeCopy) -> tuple[list[dict], dict[str, int]]:
    """取り込み元が知っている関係（``copy.term_relations``）を同じ検査に通す。

    返りは（行の一覧・落とした数の理由別）。行は ``{from, rel, to, origin_iri, by: "origin"}``
    （向きの無い関係は語の id の順・重複なし・``(from, rel, to)`` の順）。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    rows: dict[tuple[str, str, str], dict] = {}
    dropped: dict[str, int] = {}

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    readable = _readable_relations()
    for r in copy.term_relations:
        rel = str(r.get("rel"))
        if rel not in readable:
            drop("relation")
            continue
        frm, to = _order(rel, str(r.get("from_term")), str(r.get("to_term")))
        tf, tt = terms.get(frm), terms.get(to)
        if tf is None or tt is None:
            drop("term")
            continue
        problem = relation_problem(rel, tf, tt)
        if problem:
            drop(problem)
            continue
        rows.setdefault(
            (frm, rel, to),
            {"from": frm, "rel": rel, "to": to, "origin_iri": r.get("origin_iri"), "by": "origin"},
        )
    return [rows[k] for k in sorted(rows)], dict(sorted(dropped.items()))


def origin_check(row: Mapping[str, Any], pairs: Mapping[str, Any]) -> str:
    """取り込み元の関係 1 行を、AI が同じ 2 語の組について読んだ結果と突き合わせる。

    - ``"agrees"``: 組の件が ``relation`` で、``rel``・向きが同じ（向きの無い関係は向きを問わない）
    - ``"disputes"``: 組の件があり、``none``・``dropped``、または別の関係・逆向きの ``relation``
    - ``"unseen"``: 組の件が無い・``failed``・``orphan``（記録だけの件）。AI は読んでいない

    ``pairs`` は台帳の ``pairs``（``key`` は 2 語の id を文字列順に ``|`` でつないだもの）。"""
    frm, to = str(row.get("from")), str(row.get("to"))
    entry = pairs.get("|".join(sorted((frm, to))))
    if entry and _is_rejected(entry, str(row.get("rel")), frm, to, origin_only=True):
        # 取り込み元の行を人が「違う」とした（組が orphan でも・件が変わっても出さない）
        return "rejected"
    if not entry or entry.get("orphan") or entry.get("state") in (None, "failed"):
        return "unseen"  # state の無い件（外したあとに縮めた「違う」の記録）も、まだ答えの無い組
    if entry.get("state") != "relation" or entry.get("rel") != row.get("rel"):
        return "disputes"
    if row.get("rel") in relation_catalog.symmetric_pair_relations():
        same = {str(entry.get("from")), str(entry.get("to"))} == {frm, to}
    else:
        same = (str(entry.get("from")), str(entry.get("to"))) == (frm, to)
    return "agrees" if same else "disputes"


# ---- 台帳 ---------------------------------------------------------------------------------------


class LedgerError(RuntimeError):
    """台帳のファイルはあるのに読めない（権限など）。空として読んで上書きしないために上げる。"""


def empty_ledger(source: str) -> dict:
    return {
        "version": LEDGER_VERSION,
        "source": source,
        "dataset_id": None,
        "updated_at": None,
        "pairs": {},
        "origin": [],
    }


def ledger_path(root: str | Path, source: str) -> Path:
    """``<root>/vocab-shared/knowledge/<名前>.json``。名前が ``[a-z0-9_-]{1,64}``（小文字だけ）に
    合わなければ ``s-`` ＋ sha1 の先頭 16 桁（パスの外へ出ない・大文字小文字だけ違う名前を
    潰さない）。"""
    name = source if _NAME_RE.fullmatch(source) else "s-" + hashlib.sha1(
        source.encode("utf-8", "surrogatepass")
    ).hexdigest()[:16]
    # 名前は上で安全な形にしてあるが、念のため台帳のフォルダの外を指さないことを確かめる
    base = os.path.normpath(os.path.join(os.fspath(root), "vocab-shared", "knowledge"))
    full = os.path.normpath(os.path.join(base, f"{name}.json"))
    if not full.startswith(base + os.sep):
        raise ValueError(f"台帳のパスが台帳のフォルダの外を指しています: {source!r}")
    return Path(full)


def _set_aside(path: Path) -> None:
    """読めない台帳を ``<名前>.json.bad`` に退避する。既にあれば ``.bad.1``・``.2``… の空いた名前へ
    （上書きしない）。失敗しても落ちない。"""
    base = path.with_name(path.name + ".bad")
    target = base
    n = 0
    while target.exists() and n < 1000:
        n += 1
        target = base.with_name(f"{base.name}.{n}")
    with contextlib.suppress(OSError):
        os.replace(path, target)


def load_ledger(root: str | Path, source: str) -> dict:
    """台帳を読む。ファイルが無ければ空の台帳。UTF-8・JSON として読めない・版が違うときは、
    そのファイルを ``<名前>.json.bad`` に退避してから空の台帳を返す。ファイルはあるが読めない
    （権限など）ときは :class:`LedgerError`（黙って空にして次の保存で上書きしない）。
    ``pairs`` の値が辞書でない件・``key`` の無い件は読み飛ばす。"""
    path = ledger_path(root, source)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_ledger(source)
    except UnicodeDecodeError:
        _set_aside(path)
        return empty_ledger(source)
    except OSError as exc:
        raise LedgerError(f"台帳が読めません: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if (
        not isinstance(data, dict)
        or data.get("version") != LEDGER_VERSION
        or not isinstance(data.get("pairs"), dict)
        or not isinstance(data.get("origin", []), list)
    ):
        _set_aside(path)
        return empty_ledger(source)
    data["pairs"] = {
        k: v for k, v in data["pairs"].items() if isinstance(v, dict) and "key" in v
    }
    for v in data["pairs"].values():  # R1 の最後の砦: 規則に通らない種類は、欄を無いものとして扱う
        if "unstated_kind" in v:
            kind = _unstated_kind(v["unstated_kind"]) if v.get("state") == "none" else None
            if kind:
                v["unstated_kind"] = kind
            else:
                del v["unstated_kind"]
    for v in data["pairs"].values():  # R4: 判定し直しの記録。文字列の並びでなければ欄ごと外す
        if "reasked" in v and not _valid_reasked(v["reasked"]):
            del v["reasked"]
    data["source"] = source
    data.setdefault("dataset_id", None)
    data.setdefault("origin", [])
    if "stored_terms" in data and not isinstance(data["stored_terms"], dict):
        del data["stored_terms"]  # 14e-1b: 語の対応の控え。形が違えば捨てる（次の回で決め直す）
    return data


def _valid_reasked(raw: Any) -> bool:
    return isinstance(raw, list) and all(isinstance(x, str) and x for x in raw)


def reask_keys(rel: Any) -> frozenset[str]:
    """足した関係に「言えなかった種類」を結ぶ鍵（R4）: ``unstated_key`` と表示名（ja・en）の
    ``merge_key``。空の鍵は入れない。``rel`` は :class:`relation_catalog.Relation`。"""
    out: set[str] = set()
    uk = getattr(rel, "unstated_key", None)
    if isinstance(uk, str) and uk:
        out.add(uk)
    for lang in ("ja", "en"):
        text = (getattr(rel, "label", None) or {}).get(lang)
        if isinstance(text, str) and text:
            out.add(merge_key(text))
    out.discard("")
    return frozenset(out)


def reask_targets(
    ledger: Mapping[str, Any], *, relation_name: str, keys: set[str]
) -> list[str]:
    """足した関係 ``relation_name`` で判定し直す組の鍵（R4・決定論の並び）。

    ``state == "none"`` で ``merge_key(unstated_kind)`` が ``keys`` にある組。
    入れない: ``unstated_kind`` の無い組・取り残し（``orphan``）・
    ``confirmed``・``rejected`` のある組・
    人／取り込み元が付けた組（``by`` が ``user``・``origin``）・
    同じ関係の名前が ``reasked`` に入っている組。"""
    out: list[str] = []
    for k in sorted(ledger.get("pairs") or {}):
        e = ledger["pairs"][k]
        if not isinstance(e, Mapping) or e.get("state") != "none":
            continue
        if e.get("orphan") or e.get("confirmed") or e.get("rejected"):
            continue
        if e.get("by") in ("user", "origin"):
            continue
        kind = _unstated_kind(e.get("unstated_kind"))
        if not kind or merge_key(kind) not in keys:
            continue
        done = e.get("reasked")
        if _valid_reasked(done) and relation_name in done:
            continue
        out.append(str(k))
    return out


def save_ledger(root: str | Path, ledger: dict, *, at: str) -> Path:
    """台帳を一時ファイルに書いて ``os.replace`` で置き換える。``updated_at`` は ``at``。"""
    ledger["updated_at"] = at
    path = ledger_path(root, str(ledger["source"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(
            json.dumps(ledger, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def plan_run(
    ledger: Mapping[str, Any],
    tasks: Iterable[Mapping[str, Any]],
    *,
    force: Iterable[str] = frozenset(),
    only_force: bool = False,
) -> tuple[list[dict], dict]:
    """聞く仕事の一覧と、次の台帳の土台を返す。

    聞くのは 台帳に無い組・指紋が変わった組・``failed`` の組（``confirmed`` のある組は聞かない）。
    土台は今の台帳から、写しに無くなった組を外し（``confirmed`` か ``rejected`` があれば
    ``orphan: True`` で残す。戻ってきたら ``orphan`` を外す）、
    聞き直す組は今の件を残したもの（答えが来たら :func:`merge_entry` で置き換える）。

    ``force``（R4）の組は指紋が同じでも聞く（``confirmed`` は今のまま聞かない）。空なら今と同じ。
    ``only_force`` が真なら、聞くのは ``force`` の組だけ（台帳に無い組・指紋が変わった組・
    ``failed`` の組は聞かない）。土台（``base``）の作り方は普段と同じ。"""
    tasks = list(tasks)
    forced = set(force)
    base = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = base.setdefault("pairs", {})
    keys = {t["key"] for t in tasks}
    for k in sorted(pairs):
        if k not in keys:
            if pairs[k].get("by") == "origin" and pairs[k].get("confirmed"):
                # 取り込み元の行を確認した件は、組に知見が無いのが普通（残す）
                pairs[k].pop("orphan", None)
            elif pairs[k].get("confirmed") or pairs[k].get("rejected"):
                pairs[k]["orphan"] = True
            else:
                del pairs[k]
        else:
            pairs[k].pop("orphan", None)
    todo = []
    for t in tasks:
        cur = pairs.get(t["key"])
        if cur is not None and cur.get("confirmed"):
            continue
        if only_force:
            if t["key"] in forced and cur is not None:
                todo.append(dict(t))
            continue
        stale = cur is None or cur.get("fingerprint") != t["fingerprint"]
        if stale or cur.get("state") == "failed" or t["key"] in forced:
            todo.append(dict(t))
    return todo, base


def merge_entry(
    ledger: dict,
    entry: Mapping[str, Any],
    *,
    model: str,
    at: str,
    reasked_as: str | Iterable[str] | None = None,
    forced: bool = False,
) -> None:
    """台帳の 1 件を置き換える（``by: "ai"``・``model``・``proposed_at`` を付け、前の件の
    ``rejected`` を引き継ぐ）。前の件に ``confirmed`` があれば置き換えない。新しい件が ``failed`` で
    前の件が ``failed`` 以外なら、前の件を残す。

    ``reasked``（R4）: 前の件の記録を引き継ぎ、``reasked_as``（その走りで AI に渡した足した関係の
    名前。1 つか並び）を足す（答えが「言えない」でも関係でも残る。失敗の件には足さない）。

    ``forced``（判定し直しの組）: 答えが ``dropped`` なら前の件を残し
    ``reasked`` だけ足す。答えが「言えない」で種類が欠けていれば、
    前の件の ``unstated_kind`` を残す。"""
    pairs = ledger.setdefault("pairs", {})
    prev = pairs.get(entry["key"]) or {}
    if prev.get("confirmed"):
        return
    if entry.get("state") == "failed" and prev and prev.get("state") != "failed":
        return  # AI が落ちている間に、既存の件を消さない（指紋は古いまま → 次の回にまた聞く）
    if forced and prev and entry.get("state") == "dropped":
        # 使えない答え: 前の件を残す（種類の一覧から消さない）。reasked だけ足す（何度も聞かない）
        done = list(prev["reasked"]) if _valid_reasked(prev.get("reasked")) else []
        names = [reasked_as] if isinstance(reasked_as, str) else list(reasked_as or [])
        done += [n for n in names if n and n not in done]
        if done:
            prev["reasked"] = done
        return
    new = dict(entry)
    if forced and entry.get("state") == "none" and "unstated_kind" not in entry:
        kind = _unstated_kind(prev.get("unstated_kind")) if prev.get("state") == "none" else None
        if kind:
            new["unstated_kind"] = kind
    for k in _ANNOTATED:
        new.pop(k, None)
    new.update(
        by="ai",
        model=model,
        proposed_at=at,
        confirmed=None,
        rejected=_copy.deepcopy(prev.get("rejected") or []),
    )
    done = list(prev["reasked"]) if _valid_reasked(prev.get("reasked")) else []
    names = [reasked_as] if isinstance(reasked_as, str) else list(reasked_as or [])
    if entry.get("state") != "failed":
        done += [n for n in names if n and n not in done]
    if done:
        new["reasked"] = done
    pairs[entry["key"]] = new


def finalize(ledger: Mapping[str, Any], copy: KnowledgeCopy, *, dataset_id: str | None) -> dict:
    """``drop_cycles`` → ``annotate`` → ``origin_relations`` → ``origin_check`` を当てて台帳を
    仕上げて返す（冪等）。``origin`` の各行には AI の判定との突き合わせの印 ``ai``
    （``agrees``／``disputes``／``unseen``）が付く。

    ``origin`` の ``sub``・``part`` の行が、既定で見せる AI の関係（``relation``）と、すでに通した
    ``origin`` の行とで輪を作るなら、その行は ``disputes``（``origin`` の行は並びの順に見る。
    ``agrees`` の行は AI の同じ関係が見えているので輪の検査に通さない）。

    ``orphan`` の件（写しから消えた組）はそのまま残し、輪・注釈・``origin_check`` の対象にしない。
    ``relation`` の件の根拠の知見が写しから消えていれば、残った根拠だけにする。1 件も残らなければ
    ``dropped``・理由 ``evidence``（``confirmed`` のある件は変えない）。

    前に輪で落とした件（理由 ``cycle``）は一度戻してから輪を見直す。入力は変えない。"""
    out = _copy.deepcopy(dict(ledger))
    origin, _ = origin_relations(copy)
    idx = _Index(copy)
    entries = []
    for k in sorted(out.get("pairs") or {}):
        e = dict(out["pairs"][k])
        if e.get("orphan"):  # 写しから消えた組: 記録として残すだけ（輪・注釈・数の対象にしない）
            entries.append(e)
            continue
        if e.get("state") == "dropped" and e.get("reason") == "cycle" and e.get("rel"):
            e["state"], e["reason"] = "relation", None
        if e.get("state") == "relation" and not e.get("confirmed"):
            # 根拠の知見が写しから消えていれば、残った根拠だけにする（全部消えたら落とす）
            kept = [c for c in e.get("evidence") or [] if str(c) in idx.claims]
            if kept:
                e["evidence"] = kept
            else:
                e["state"], e["reason"] = "dropped", "evidence"
                for f in _ANNOTATED:
                    e.pop(f, None)
        entries.append(e)
    entries = drop_cycles(entries, ())
    out["pairs"] = {e["key"]: e if e.get("orphan") else _annotate(e, idx) for e in entries}
    edges: dict[str, dict[str, set[str]]] = {r: {} for r in _CYCLE_RELS}
    for e in out["pairs"].values():
        if e.get("state") == "relation" and not e.get("orphan") and e.get("rel") in edges:
            edges[e["rel"]].setdefault(str(e["from"]), set()).add(str(e["to"]))
    marked = []
    for o in origin:
        mark = origin_check(o, out["pairs"])
        g = edges.get(o["rel"])
        # agrees は輪の検査に通さない
        if g is not None and mark not in ("disputes", "agrees", "rejected"):
            if _reaches(g, str(o["to"]), str(o["from"])):
                mark = "disputes"
            else:
                g.setdefault(str(o["from"]), set()).add(str(o["to"]))
        marked.append({**o, "ai": mark})
    out["origin"] = marked
    out["dataset_id"] = dataset_id
    return out


def summarize(ledger: Mapping[str, Any]) -> dict:
    """台帳の件数。

    - ``orphans``: 写しから消えた組（記録として残すだけ）。``pairs`` も含め、ほかの数に入れない。
      ``state`` の無い件（外したあとに縮めた ``rejected`` だけの件。次に聞く回まで答えが無い）も
      どの数にも入れない
    - ``origin``: 取り込み元の関係の件数（「違う」とした行は数えない）。
      ``origin_check``: その ``ai`` の印ごとの数（``ai`` が無い行は ``unseen``）
    - ``shown``: 既定で見せる関係の数 ＝ AI の関係（``relation``）＋ ``origin`` の ``unseen``
      （``agrees`` は AI の関係と同じ 1 本・``disputes`` は候補なので数えない）
    - ``terms``: ``shown`` の関係に出る語の数
    - ``candidates``: 候補の数 ＝ ``none`` の組 ＋ ``origin`` の ``disputes``"""
    every = list((ledger.get("pairs") or {}).values())
    # orphan と state の無い件（外したあとに縮めた「違う」の記録）は、記録として残すだけ（数えない）
    pairs = [p for p in every if not p.get("orphan") and p.get("state")]
    rel_entries = [p for p in pairs if p.get("state") == "relation"]
    dropped: dict[str, int] = {}
    for p in pairs:
        if p.get("state") == "dropped":
            why = str(p.get("reason") or "")
            dropped[why] = dropped.get(why, 0) + 1
    by_rel: dict[str, int] = {}
    for p in rel_entries:
        by_rel[str(p.get("rel"))] = by_rel.get(str(p.get("rel")), 0) + 1
    origin = list(ledger.get("origin") or [])
    check = {"agrees": 0, "disputes": 0, "unseen": 0}
    for o in origin:
        if o.get("ai") == "rejected":  # 人が「違う」とした行は数に入れない
            continue
        mark = o.get("ai") if o.get("ai") in check else "unseen"
        check[mark] += 1
    shown = shown_relations(ledger)
    ends: set[str] = set()
    for r in shown:
        ends.update((str(r["from"]), str(r["to"])))
    n_none = sum(1 for p in pairs if p.get("state") == "none")
    return {
        "pairs": len(pairs),
        "orphans": sum(1 for p in every if p.get("orphan")),
        "relations": len(rel_entries),
        "none": n_none,
        "dropped": dict(sorted(dropped.items())),
        "failed": sum(1 for p in pairs if p.get("state") == "failed"),
        "origin": len([o for o in origin if o.get("ai") != "rejected"]),
        "origin_check": check,
        "shown": len(shown),
        "terms": len(ends),
        "candidates": n_none + check["disputes"],
        "by_rel": dict(sorted(by_rel.items())),
        "tentative": sum(1 for p in rel_entries if p.get("tentative")),
    }


# ---- 見せる関係・答えられる問い・外す（14d-2a） -----------------------------------------------


def shown_relations(ledger: Mapping[str, Any]) -> list[dict]:
    """既定で見せる関係 ＝ AI の関係（``relation``・``orphan`` でない）＋ ``origin`` の ``unseen``
    （``ai`` の印が無い行も ``unseen`` として数える）。

    行は ``{from, rel, to, by, key?}``。AI の関係は ``key``（組の鍵）と ``by: "ai"``、取り込み元の
    行は ``by: "origin"``。並びは AI の関係（``key`` の順）→ 取り込み元（台帳の並び）。
    :func:`summarize` の ``shown``・``terms`` はこの一覧から数える。"""
    out: list[dict] = []
    pairs = ledger.get("pairs") or {}
    for key in sorted(pairs):
        p = pairs[key]
        if p.get("orphan") or p.get("state") != "relation":
            continue
        out.append(
            {
                "from": str(p["from"]),
                "rel": p.get("rel"),
                "to": str(p["to"]),
                "by": "ai",
                "key": key,
            }
        )
    for o in ledger.get("origin") or []:
        if o.get("ai") in ("agrees", "disputes", "rejected"):
            continue
        row = {"from": str(o["from"]), "rel": o.get("rel"), "to": str(o["to"]), "by": "origin"}
        out.append(row)
    return out


def _confirmed_names() -> dict[str, frozenset[str]]:
    """下書きの関係の名前 → 確定側の述語の名前（``graph_diff.PRED_TO_REL`` の逆引き）。
    ``measures`` は相手の種別で割れるので、``measuresMaterial``・``measuresProperty`` の
    両方に当たるものとして持つ。"""
    acc: dict[str, set[str]] = {}
    for pred, rel in relation_catalog.pred_to_rel().items():
        acc.setdefault(rel, set()).add(pred)
    return {rel: frozenset(v) for rel, v in acc.items()}


#: 呼ぶたびに目録から導く（足した関係の述語も入る）。
_CONFIRMED_NAMES = relation_catalog.LiveMap(_confirmed_names)
#: 下書きの関係 → 数える ``factors`` の向き（``knowledge.factors`` と同じく ``affects`` の問いは
#: 3 つの関係全部を数える）。
_FACTOR_DIRECTIONS = relation_catalog.factor_directions()


def questions(
    ledger: Mapping[str, Any], copy: KnowledgeCopy, *, lang: str = "ja", limit: int = 20
) -> tuple[list[dict], int]:
    """台帳の「既定で見せる関係」から、答えられる問いを機械で数える（AI・ストアは使わない）。

    返りは ``(多い順に最大 limit 件, 全数)``。1 件は ``{op, term: {id, name}, text, count}``
    （``factors`` だけ ``direction`` を足す）。型は ``knowledge.LINE_OPS``（確定側の名前で引く表）と
    既存の ``kinds``・``parts``・``factors``・``missing``。台帳の関係は下書きの名前なので
    :data:`PRED_TO_REL` の逆引きで確定側の名前に写して当てる（``measures`` は両方に当たる）。
    向きの無い関係（``competitors``）はどちらの端にも数える。語の種別が型に合わなければ
    （``themes.knowledge_target_kind_ok``）その型の問いは作らない。同じ（from, rel, to）は
    1 回だけ数える。
    ``factors`` は ``knowledge.factors`` と同じく、X への直接の線に加えて X の一部
    （見せる ``part`` で X に付く語）への線も 1 段だけ数える。
    ``missing`` は 14a-2 と同じ定義（語 X が持たず、X 以外の ``MISSING_MIN_OTHERS`` 語以上が
    ``hasProperty`` で持つ項目の数）を台帳の上で数え、X は見せる関係に出る種類の語に限る
    （関係が 1 本も無い語には問いを作らない）。
    並びは 件数の多い順 → 文。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    counts: dict[tuple[str, str, str | None], int] = {}

    def add(op: str, tid: str, direction: str | None = None) -> None:
        t = terms.get(tid)
        if t is None or not knowledge_target_kind_ok(op, t.get("kind")):
            return
        k = (op, tid, direction)
        counts[k] = counts.get(k, 0) + 1

    seen: set[tuple[str, str, str]] = set()
    factor_rows: list[tuple[str, str]] = []  # （下げる等の関係, その to）
    parents: dict[str, set[str]] = {}  # 語 → それが一部になっている語（part の to）
    ends: set[str] = set()
    owners: dict[str, set[str]] = {}
    for r in shown_relations(ledger):
        frm, rel, to = r["from"], str(r["rel"]), r["to"]
        if (frm, rel, to) in seen or frm not in terms or to not in terms:
            continue
        seen.add((frm, rel, to))
        ends.update((frm, to))
        op_of_rel = _QUESTION_OP.get(rel)
        if op_of_rel == "bearers":
            owners.setdefault(to, set()).add(frm)
        if op_of_rel == "kinds":
            add("kinds", to)
        elif op_of_rel == "parts":
            add("parts", to)
            parents.setdefault(frm, set()).add(to)
        elif rel in _FACTOR_DIRECTIONS:
            factor_rows.append((rel, to))
        names = _CONFIRMED_NAMES.get(rel, frozenset())
        for op, (op_names, side) in LINE_OPS.items():
            if not names & set(op_names):
                continue
            for tid in {"to": [to], "from": [frm], "either": [frm, to]}[side]:
                add(op, tid)

    # factors は X への直接の線に加えて、X の一部（part で X に付く語）への線も 1 段だけ数える
    # （``knowledge.factors`` の閉包と同じ）
    for rel, to in factor_rows:
        for d in _FACTOR_DIRECTIONS[rel]:
            add("factors", to, d)
            for parent in parents.get(to, ()):
                add("factors", parent, d)

    for tid in ends:
        if terms[tid].get("kind") != "class":
            continue
        n = sum(
            1
            for froms in owners.values()
            if tid not in froms and len(froms - {tid}) >= MISSING_MIN_OTHERS
        )
        if n:
            counts[("missing", tid, None)] = n

    items: list[dict] = []
    for (op, tid, direction), n in counts.items():
        name = str(terms[tid].get("name") or "")
        sel: dict[str, Any] = {"op": op, "target": name}
        if direction:
            sel["direction"] = direction
        text = describe_selection(sel, lang)
        if not text:
            continue
        item: dict[str, Any] = {
            "op": op,
            "term": {"id": tid, "name": terms[tid].get("name")},
            "text": text,
            "count": n,
        }
        if direction:
            item["direction"] = direction
        items.append(item)
    items.sort(key=lambda i: (-i["count"], i["text"], i["term"]["id"]))
    return items[: max(0, limit)], len(items)


def counts_by_state(ledger: Mapping[str, Any]) -> dict[str, int]:
    """一覧用の数。``confirmed`` ＝ 人が確認した件（``confirmed`` のある組）・``unconfirmed`` ＝
    見せる関係のうち、確認されていないもの（取り込み元の ``unseen`` の行は確認されない）。"""
    pairs = ledger.get("pairs") or {}
    confirmed = sum(1 for p in pairs.values() if p.get("confirmed"))
    shown_confirmed = sum(
        1
        for r in shown_relations(ledger)
        if r["by"] == "ai" and (pairs.get(r["key"]) or {}).get("confirmed")
    )
    return {
        "unconfirmed": len(shown_relations(ledger)) - shown_confirmed,
        "confirmed": confirmed,
    }


def strip_unconfirmed(ledger: Mapping[str, Any]) -> tuple[dict, dict]:
    """外すときの台帳: ``confirmed`` のある組と ``rejected``（「違う」の記録）が空でない組だけを
    残し、ほか（未確認・候補・落ちた件・失敗・``origin``）を消す。``rejected`` だけの組は、関係の
    中身を消して ``orphan: True`` と ``rejected`` だけにする（入れ直したとき同じ 3 つ組を出さない・
    14c の「``confirmed`` か ``rejected`` があれば残す」）。何度かけても同じ結果。

    返りは ``(残す台帳, {relations, kept_confirmed, kept_rejected})``。
    ``relations`` は消した未確認の関係の数（見せていた関係のうち、確認済みでないもの）・
    ``kept_confirmed`` は残した確認済みの組の数・``kept_rejected`` は ``rejected`` だけで残した
    組の数。入力は変えない。"""
    out = _copy.deepcopy(dict(ledger))
    kept: dict[str, dict] = {}
    n_rejected = 0
    for k, v in (out.get("pairs") or {}).items():
        if v.get("confirmed"):
            kept[k] = v
        elif v.get("rejected"):
            kept[k] = {
                "key": v["key"],
                "a": v.get("a"),
                "b": v.get("b"),
                "orphan": True,
                "rejected": v["rejected"],
            }
            n_rejected += 1
    state = counts_by_state(ledger)
    out["pairs"] = kept
    out["origin"] = []
    return out, {
        "relations": state["unconfirmed"],
        "kept_confirmed": len(kept) - n_rejected,
        "kept_rejected": n_rejected,
    }


def remove_ledger(root: str | Path, source: str, *, at: str) -> dict:
    """取り込み元の台帳から未確認を消す（:func:`strip_unconfirmed`）。残る組が無ければファイルごと
    消す。返りは ``{relations, kept_confirmed, kept_rejected}``。台帳が無ければ 0。読めなければ
    :class:`LedgerError`。"""
    ledger = load_ledger(root, source)
    kept, removed = strip_unconfirmed(ledger)
    if kept["pairs"]:
        save_ledger(root, kept, at=at)
    else:
        ledger_path(root, source).unlink(missing_ok=True)
    return removed


# ---- 確認・違う・人が付ける（14e-1） ----------------------------------------------------------
#
# どれも ``(新しい台帳, {changed: [key], skipped: [{key, reason}]})`` を返す純関数
# （入力は変えない）。``at`` は呼び手が渡す時刻。
#
# 指す鍵は 2 種類。**組の鍵**（2 語の id を文字列順に ``|`` でつないだもの）は組の件
# （AI・人が付けた関係・取り込み元の行から確認した関係）だけを指す。**取り込み元の行の鍵**
# ``o:<from>|<rel>|<to>``（:func:`origin_key`・向きの無い関係は id の順）は、その 3 つ組の行だけを
# 指す。

_ORIGIN_PREFIX = "o:"


def origin_key(row: Mapping[str, Any]) -> str:
    """取り込み元の関係 1 行の鍵 ``o:<from>|<rel>|<to>``（向きの無い関係は語の id の順）。"""
    rel = str(row.get("rel"))
    frm, to = _order(rel, str(row.get("from")), str(row.get("to")))
    return f"{_ORIGIN_PREFIX}{frm}|{rel}|{to}"


def _parse_origin_key(key: str) -> tuple[str, str, str] | None:
    if not key.startswith(_ORIGIN_PREFIX):
        return None
    parts = key[len(_ORIGIN_PREFIX):].split("|")
    if len(parts) != 3 or not all(parts):
        return None
    frm, rel, to = parts
    frm, to = _order(rel, frm, to)
    return frm, rel, to


def _pair_key(tri: tuple[str, str, str]) -> str:
    return "|".join(sorted((tri[0], tri[2])))


def _same_triple(rel_a: str, a: tuple[str, str], rel_b: str, b: tuple[str, str]) -> bool:
    return rel_a == rel_b and _order(rel_a, *a) == _order(rel_b, *b)


def _rec_matches(r: Mapping[str, Any], tri: tuple[str, str, str]) -> bool:
    return _same_triple(
        str(r.get("rel")), (str(r.get("from")), str(r.get("to"))), tri[1], (tri[0], tri[2])
    )


def _is_rejected(
    entry: Mapping[str, Any], rel: str, frm: str, to: str, *, origin_only: bool = False
) -> bool:
    """組の件の ``rejected`` に、この 3 つ組（向きの無い関係は向きを問わない）があるか。
    ``origin_only`` は、取り込み元の行を「違う」とした記録（``origin: True``）だけを見る
    （AI の関係の「違う」は、取り込み元の同じ関係を「まだ読んでいない」のままにする）。"""
    for r in entry.get("rejected") or []:
        if origin_only and not r.get("origin"):
            continue
        if _rec_matches(r, (frm, rel, to)):
            return True
    return False


def _outcome(changed: list[str], skipped: list[dict]) -> dict:
    return {"changed": changed, "skipped": skipped}


def _live(entry: Mapping[str, Any] | None) -> bool:
    """見せている AI の（または人の）関係か。"""
    return bool(entry) and not entry.get("orphan") and entry.get("state") == "relation"  # type: ignore[union-attr]


def _origin_row(ledger: Mapping[str, Any], key: str) -> tuple[dict | None, str | None]:
    """取り込み元の行（鍵で指す）とその印。台帳の ``origin`` に無ければ ``(None, None)``。"""
    pairs = ledger.get("pairs") or {}
    for o in ledger.get("origin") or []:
        if origin_key(o) == key:
            return o, (o.get("ai") or origin_check(o, pairs))
    return None, None


def _confirmation(rel: Any, frm: Any, to: Any, evidence: Any, at: str) -> dict:
    return {
        "rel": rel,
        "from": frm,
        "to": to,
        "at": at,
        "by": "user",
        "evidence": [str(c) for c in evidence or []],
    }


def _strip_annotations(e: dict) -> None:
    for k in _ANNOTATED:
        e.pop(k, None)
    e.pop("unstated_kind", None)  # R1: 「言えない」の種類は、関係・確認・「違う」の件には付けない


def _would_cycle(ledger: Mapping[str, Any], key: str, rel: str, frm: str, to: str) -> bool:
    """この関係を ``key`` の組に置くと、見せている関係と「一種」「一部」の輪になるか。"""
    if rel not in _CYCLE_RELS:
        return False
    edges: dict[str, set[str]] = {}
    for r in shown_relations(ledger):
        if r["rel"] == rel and r.get("key") != key:
            edges.setdefault(r["from"], set()).add(r["to"])
    return _reaches(edges, to, frm)


def _entry_is(e: Mapping[str, Any], tri: tuple[str, str, str]) -> bool:
    return _same_triple(
        str(e.get("rel")), (str(e.get("from")), str(e.get("to"))), tri[1], (tri[0], tri[2])
    )


def confirm(ledger: Mapping[str, Any], keys: Iterable[str], *, at: str) -> tuple[dict, dict]:
    """見せている関係に ``confirmed = {rel, from, to, at, by: "user", evidence}`` を書く。

    ``evidence`` は確認した時の根拠の知見の id の控え（題は読む口が写しから引く。写しから知見が
    消えても、確認した事実と根拠の id は残る）。確認済みは :func:`plan_run`・:func:`merge_entry`・
    :func:`finalize` で上書きされない。

    - 組の鍵: 組の件（``relation``）を確認する。
    - 取り込み元の行の鍵（``o:…``）: AI と同じ関係（``agrees``）なら、その組の件を確認する。
      AI がまだ読んでいない行（``unseen``）は、その 3 つ組そのものを ``by: "origin"``・根拠なしの
      件として組に置いて確認する（:func:`finalize` が AI の関係と同じ 1 本として数える）。組に
      すでに別の関係（AI・確認済み・人が付けた）があるときは書かない。

    skipped の理由: ``unknown``（無い鍵・見せていない）・``already_confirmed``・
    ``pair_has_relation``（組に別の関係がある）。"""
    out = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = out.setdefault("pairs", {})
    changed: list[str] = []
    skipped: list[dict] = []

    def confirm_live(key: str, e: dict) -> None:
        if e.get("confirmed"):
            skipped.append({"key": key, "reason": "already_confirmed"})
            return
        e["confirmed"] = _confirmation(
            e.get("rel"), e.get("from"), e.get("to"), e.get("evidence"), at
        )
        changed.append(key)

    for key in dict.fromkeys(str(k) for k in keys):
        tri = _parse_origin_key(key)
        if tri is None:
            e = pairs.get(key)
            if _live(e):
                confirm_live(key, e)  # type: ignore[arg-type]
            else:
                skipped.append({"key": key, "reason": "unknown"})
            continue
        pk = _pair_key(tri)
        e = pairs.get(pk)
        row, mark = _origin_row(out, key)
        if row is None:
            skipped.append({"key": key, "reason": "unknown"})
        elif mark == "agrees" and _live(e) and _entry_is(e, tri):  # type: ignore[arg-type]
            confirm_live(key, e)  # type: ignore[arg-type]
        elif mark not in ("unseen", "disputes"):
            skipped.append({"key": key, "reason": "unknown"})
        elif _live(e) or (e and e.get("confirmed")):
            skipped.append({"key": key, "reason": "pair_has_relation"})
        elif mark != "unseen":  # AI が「言えない」とした組の行は、候補のまま（ここでは置かない）
            skipped.append({"key": key, "reason": "unknown"})
        else:
            a, _, b = pk.partition("|")
            base = dict(e or {})
            base.update(
                key=pk, a=a, b=b, state="relation", rel=tri[1], reason=None, note=None,
                evidence=[], by="origin",
                confirmed=_confirmation(tri[1], tri[0], tri[2], [], at),
            )
            base["from"], base["to"] = tri[0], tri[2]
            base.setdefault("fingerprint", None)
            base.setdefault("claims", [])
            base.setdefault("claims_total", 0)
            base.setdefault("rejected", [])
            base.pop("orphan", None)
            _strip_annotations(base)
            pairs[pk] = base
            changed.append(key)
    return out, _outcome(changed, skipped)


def unconfirm(ledger: Mapping[str, Any], keys: Iterable[str]) -> tuple[dict, dict]:
    """``confirmed`` を消す。skipped の理由: ``unknown``・``not_confirmed``。

    - AI の関係は未確認に戻る。
    - 人が付けた関係（``by: "user"``）は、根拠の有無によらず候補（``none``）に戻る（未確認の
      人の関係は残さない。AI の聞き直しとの境目を無くす）。
    - 取り込み元の行から確認した件は台帳から外れ、取り込み元の行（未確認）に戻る（「違う」の
      記録があれば記録だけ残す）。

    取り込み元の行の鍵（``o:…``）は、その 3 つ組を確認した件にだけ効く。"""
    out = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = out.setdefault("pairs", {})
    changed: list[str] = []
    skipped: list[dict] = []
    for key in dict.fromkeys(str(k) for k in keys):
        tri = _parse_origin_key(key)
        pk = key if tri is None else _pair_key(tri)
        e = pairs.get(pk)
        if e is None:
            skipped.append({"key": key, "reason": "unknown"})
            continue
        wrong_triple = tri is not None and not (e.get("by") == "origin" and _entry_is(e, tri))
        if not e.get("confirmed") or wrong_triple:
            skipped.append({"key": key, "reason": "not_confirmed"})
            continue
        e["confirmed"] = None
        if e.get("by") == "origin":
            if e.get("rejected"):
                pairs[pk] = {"key": pk, "a": e.get("a"), "b": e.get("b"), "orphan": True,
                             "rejected": e["rejected"]}
            else:
                del pairs[pk]
        elif e.get("by") == "user":
            for f in ("rel", "from", "to", "note", "reason", "by", "model", "proposed_at"):
                e[f] = None
            e["state"] = _NONE
            _strip_annotations(e)
        changed.append(key)
    return out, _outcome(changed, skipped)


def reject(ledger: Mapping[str, Any], keys: Iterable[str], *, at: str) -> tuple[dict, dict]:
    """その 3 つ組を ``rejected`` に足し、関係を見せない。

    - 組の鍵: 見せている組の件を ``dropped``（理由 ``rejected``）にする。``confirmed`` があれば
      消す（消す前の確認は記録の ``was_confirmed`` に残し、:func:`restore` が使う）。取り込み元の
      行から確認した件（``by: "origin"``）の記録には ``origin: True`` を付ける（取り込み元の行は
      読み直し・外す → 入れ直しでも ``unseen``／``disputes`` に戻らない）。
    - 取り込み元の行の鍵（``o:…``）: その 3 つ組だけを ``origin: True`` の記録で「違う」にする
      （組の件が無ければ ``orphan`` と ``rejected`` だけの件を作る）。AI と同じ関係
      （``agrees``）の行なら、その組の件を落とす。ほかの取り込み元の行には効かない。

    次の読み直しでも同じ 3 つ組は出ない（``check_answer`` の ``rejected``・``origin_check`` の
    ``rejected``）。skipped の理由: ``unknown``・``already_rejected``。"""
    out = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = out.setdefault("pairs", {})
    changed: list[str] = []
    skipped: list[dict] = []

    def reject_live(e: dict, *, origin: bool) -> None:
        rec = {"rel": e.get("rel"), "from": e.get("from"), "to": e.get("to"), "at": at}
        if origin or e.get("by") == "origin":
            rec["origin"] = True
        if e.get("confirmed"):
            rec["was_confirmed"] = e["confirmed"]
        e["rejected"] = [*(e.get("rejected") or []), rec]
        e["state"], e["reason"], e["confirmed"] = "dropped", "rejected", None
        _strip_annotations(e)

    for key in dict.fromkeys(str(k) for k in keys):
        tri = _parse_origin_key(key)
        if tri is None:
            e = pairs.get(key)
            if _live(e):
                reject_live(e, origin=False)  # type: ignore[arg-type]
                changed.append(key)
            else:
                who = "already_rejected" if e and e.get("rejected") else "unknown"
                skipped.append({"key": key, "reason": who})
            continue
        pk = _pair_key(tri)
        e = pairs.get(pk)
        if e and any(r.get("origin") and _rec_matches(r, tri) for r in e.get("rejected") or []):
            skipped.append({"key": key, "reason": "already_rejected"})
            continue
        row, mark = _origin_row(out, key)
        if row is None:
            skipped.append({"key": key, "reason": "unknown"})
        elif mark == "agrees" and _live(e) and _entry_is(e, tri):  # type: ignore[arg-type]
            reject_live(e, origin=True)  # type: ignore[arg-type]
            changed.append(key)
        elif mark in ("unseen", "disputes"):
            a, _, b = pk.partition("|")
            if e is None:
                e = pairs[pk] = {"key": pk, "a": a, "b": b, "orphan": True, "rejected": []}
            e["rejected"] = [
                *(e.get("rejected") or []),
                {"rel": tri[1], "from": tri[0], "to": tri[2], "at": at, "origin": True},
            ]
            changed.append(key)
        else:
            skipped.append({"key": key, "reason": "unknown"})
    return out, _outcome(changed, skipped)


def restore(ledger: Mapping[str, Any], keys: Iterable[str]) -> tuple[dict, dict]:
    """「違う」の記録を 1 つ外し、関係を未確認で戻す。skipped の理由: ``unknown``・
    ``not_rejected``・``cycle``。

    - 組の鍵: 「違う」で落とした件（``dropped``・理由 ``rejected``）なら、その 3 つ組の記録を
      外して関係（``relation``）に戻す。それ以外の件は、取り込み元の行の記録（``origin: True``）
      を除いた最後の記録を外す。
    - 取り込み元の行の鍵（``o:…``）: その 3 つ組の ``origin: True`` の記録だけを外す。
    - 根拠の無い件（人が付けた・取り込み元の行から確認した）は未確認で残れない（:func:`finalize`
      が落とす）ので、:func:`reject` が記録に残した ``was_confirmed`` で確認も戻す。このとき
      「一種」「一部」の輪になるなら戻さず ``cycle``。
    - 関係が台帳に残っていない件（``orphan``・``state`` が無い・別の関係に変わった件）は記録だけ
      外す。``orphan``・``state`` が無く、``confirmed`` も ``rejected`` も空になった件は台帳から
      消す（次の読み直しで聞き直される。取り込み元の行はまた出る）。"""
    out = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = out.setdefault("pairs", {})
    changed: list[str] = []
    skipped: list[dict] = []
    for key in dict.fromkeys(str(k) for k in keys):
        tri = _parse_origin_key(key)
        pk = key if tri is None else _pair_key(tri)
        e = pairs.get(pk)
        if e is None:
            skipped.append({"key": key, "reason": "unknown" if tri is None else "not_rejected"})
            continue
        rej = list(e.get("rejected") or [])
        own = (str(e.get("from")), str(e.get("rel")), str(e.get("to")))
        dropped = (
            not e.get("orphan") and e.get("state") == "dropped"
            and e.get("reason") == "rejected" and e.get("rel")
        )
        idx = None
        for i in range(len(rej) - 1, -1, -1):
            r = rej[i]
            if tri is not None:
                ok = bool(r.get("origin")) and _rec_matches(r, tri)
            elif dropped:
                ok = _rec_matches(r, own)
            else:
                ok = not r.get("origin")
            if ok:
                idx = i
                break
        if idx is None and tri is None and dropped and rej:
            idx = len(rej) - 1  # 件の 3 つ組に当たる記録が無ければ、最後の記録
        if idx is None:
            skipped.append({"key": key, "reason": "not_rejected"})
            continue
        last = rej[idx]
        revive = bool(dropped) and _rec_matches(last, own)
        reconfirm = None
        by_hand = e.get("by") in ("user", "origin") or not e.get("evidence")
        if revive and last.get("was_confirmed") and by_hand:
            reconfirm = last["was_confirmed"]
            if _would_cycle(out, pk, str(e.get("rel")), str(e.get("from")), str(e.get("to"))):
                skipped.append({"key": key, "reason": "cycle"})
                continue
        del rej[idx]
        e["rejected"] = rej
        if revive:
            e["state"], e["reason"] = "relation", None
            if reconfirm:
                e["confirmed"] = reconfirm
        if not rej and not e.get("confirmed") and (e.get("orphan") or not e.get("state")):
            del pairs[pk]
        changed.append(key)
    return out, _outcome(changed, skipped)


def assign(
    ledger: Mapping[str, Any],
    terms: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    key: str,
    rel: str,
    subject: str,
    *,
    at: str,
) -> tuple[dict, dict]:
    """候補（``none``）や落ちた組（``dropped``・``failed``）に、人が関係を付ける。

    ``subject``（``"a"``／``"b"``）は組のどちらが主語（``from``）か（向きの無い関係では語の id の
    順に並べ直すので無視）。機械の検査（:func:`relation_problem`・自分自身・``sub``／``part`` の
    輪）を通ったものだけ。``by: "user"``・確認済みで置く。``evidence`` は組の知見（``claims``）の
    先頭 :data:`_MAX_EVIDENCE` 件。AI の関係（``relation``）が付いている組には付けない
    （先に「違う」）。
    skipped の理由: ``unknown``・``already_related``・``relation``（集合の外）・``direction``・
    ``term``・``same``／``mixed``／``target_not_property``／``role_mismatch``・``cycle``・
    ``rejected``（同じ 3 つ組を「違う」とした記録がある。先に戻す）。"""
    out = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = out.setdefault("pairs", {})
    key = str(key)
    e = pairs.get(key)

    def no(reason: str) -> tuple[dict, dict]:
        return out, _outcome([], [{"key": key, "reason": reason}])

    if e is None or e.get("orphan") or not e.get("a") or not e.get("b"):
        return no("unknown")
    if e.get("state") == "relation":
        return no("already_related")
    if relation_catalog.is_retired(rel):
        return no("retired")  # 使うのをやめた関係は、新しくは付けられない
    if rel not in _assignable_relations():
        return no("relation")
    if subject not in ("a", "b"):
        return no("direction")
    frm, to = (str(e["a"]), str(e["b"])) if subject == "a" else (str(e["b"]), str(e["a"]))
    frm, to = _order(rel, frm, to)
    by_id = _terms_by_id(terms)
    tf, tt = by_id.get(frm), by_id.get(to)
    if tf is None or tt is None:
        return no("term")
    problem = relation_problem(rel, tf, tt)
    if problem:
        return no(problem)
    if _is_rejected(e, rel, frm, to):
        return no("rejected")
    if _would_cycle(out, key, rel, frm, to):
        return no("cycle")
    ev = [str(c) for c in (e.get("claims") or [])][:_MAX_EVIDENCE]
    e.update(
        state="relation", rel=rel, reason=None, note=None, by="user", evidence=ev,
        confirmed=_confirmation(rel, frm, to, ev, at),
    )
    e["from"], e["to"] = frm, to
    for f in ("model", "proposed_at"):
        e.pop(f, None)
    _strip_annotations(e)
    return out, _outcome([key], [])
