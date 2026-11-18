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
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from asterism import knowledge_bundle
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.themes import RELATION_KINDS, experiment_relation_problem

__all__ = [
    "CONTEXT_MAX",
    "LEDGER_VERSION",
    "PAIR_RELATIONS",
    "STATES",
    "SYMMETRIC_PAIR_RELATIONS",
    "TENTATIVE",
    "LedgerError",
    "annotate",
    "build_tasks",
    "check_answer",
    "drop_cycles",
    "empty_ledger",
    "failed_entry",
    "finalize",
    "ledger_path",
    "load_ledger",
    "merge_entry",
    "origin_check",
    "origin_relations",
    "plan_run",
    "relation_problem",
    "save_ledger",
    "summarize",
]

#: 語の組で選べる関係（``quantity`` の相手は標準の量の種類で、語の組にならない）。
PAIR_RELATIONS = tuple(r for r in RELATION_KINDS if r != "quantity")
#: 向きの無い関係（語の id の順に並べる）。
SYMMETRIC_PAIR_RELATIONS = ("eq", "competes")
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
_DIRECTED_TO_PROPERTY = ("lowers", "raises", "affects")
_SAME_KIND = ("sub", "eq", "part")
_CYCLE_RELS = ("sub", "part")
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
    if rel in _DIRECTED_TO_PROPERTY:
        return None if tk == "property" else "target_not_property"
    if rel in _SAME_KIND:
        return None if fk == tk else "mixed"
    if rel in PAIR_RELATIONS:
        return experiment_relation_problem(
            rel, fk, tk, from_term.get("role"), to_term.get("role")
        )
    return "relation"


def _order(rel: str, frm: str, to: str) -> tuple[str, str]:
    """向きの無い関係は語の id の順に並べる。"""
    if rel in SYMMETRIC_PAIR_RELATIONS and to < frm:
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
        return _core(task, "none", note=note)
    if rel not in PAIR_RELATIONS:
        return _core(task, "dropped", reason="relation", note=note)
    rel = str(rel)

    side = answer.get("from")
    if rel in SYMMETRIC_PAIR_RELATIONS:
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

    for r in copy.term_relations:
        rel = str(r.get("rel"))
        if rel not in PAIR_RELATIONS:
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
    if not entry or entry.get("orphan") or entry.get("state") == "failed":
        return "unseen"
    if entry.get("state") != "relation" or entry.get("rel") != row.get("rel"):
        return "disputes"
    if row.get("rel") in SYMMETRIC_PAIR_RELATIONS:
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
    return Path(root) / "vocab-shared" / "knowledge" / f"{name}.json"


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
    data["source"] = source
    data.setdefault("dataset_id", None)
    data.setdefault("origin", [])
    return data


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
    ledger: Mapping[str, Any], tasks: Iterable[Mapping[str, Any]]
) -> tuple[list[dict], dict]:
    """聞く仕事の一覧と、次の台帳の土台を返す。

    聞くのは 台帳に無い組・指紋が変わった組・``failed`` の組（``confirmed`` のある組は聞かない）。
    土台は今の台帳から、写しに無くなった組を外し（``confirmed`` か ``rejected`` があれば
    ``orphan: True`` で残す。戻ってきたら ``orphan`` を外す）、
    聞き直す組は今の件を残したもの（答えが来たら :func:`merge_entry` で置き換える）。"""
    tasks = list(tasks)
    base = _copy.deepcopy(dict(ledger))
    pairs: dict[str, dict] = base.setdefault("pairs", {})
    keys = {t["key"] for t in tasks}
    for k in sorted(pairs):
        if k not in keys:
            if pairs[k].get("confirmed") or pairs[k].get("rejected"):
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
        stale = cur is None or cur.get("fingerprint") != t["fingerprint"]
        if stale or cur.get("state") == "failed":
            todo.append(dict(t))
    return todo, base


def merge_entry(ledger: dict, entry: Mapping[str, Any], *, model: str, at: str) -> None:
    """台帳の 1 件を置き換える（``by: "ai"``・``model``・``proposed_at`` を付け、前の件の
    ``rejected`` を引き継ぐ）。前の件に ``confirmed`` があれば置き換えない。新しい件が ``failed`` で
    前の件が ``failed`` 以外なら、前の件を残す。"""
    pairs = ledger.setdefault("pairs", {})
    prev = pairs.get(entry["key"]) or {}
    if prev.get("confirmed"):
        return
    if entry.get("state") == "failed" and prev and prev.get("state") != "failed":
        return  # AI が落ちている間に、既存の件を消さない（指紋は古いまま → 次の回にまた聞く）
    new = dict(entry)
    for k in _ANNOTATED:
        new.pop(k, None)
    new.update(
        by="ai",
        model=model,
        proposed_at=at,
        confirmed=None,
        rejected=_copy.deepcopy(prev.get("rejected") or []),
    )
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
        if g is not None and mark not in ("disputes", "agrees"):  # agrees は輪の検査に通さない
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

    - ``orphans``: 写しから消えた組（記録として残すだけ）。``pairs`` も含め、ほかの数に入れない
    - ``origin``: 取り込み元の関係の件数。``origin_check``: その ``ai`` の印ごとの数
      （``ai`` が無い行は ``unseen``）
    - ``shown``: 既定で見せる関係の数 ＝ AI の関係（``relation``）＋ ``origin`` の ``unseen``
      （``agrees`` は AI の関係と同じ 1 本・``disputes`` は候補なので数えない）
    - ``terms``: ``shown`` の関係に出る語の数
    - ``candidates``: 候補の数 ＝ ``none`` の組 ＋ ``origin`` の ``disputes``"""
    every = list((ledger.get("pairs") or {}).values())
    pairs = [p for p in every if not p.get("orphan")]  # orphan は記録として残すだけ（数えない）
    rel_entries = [p for p in pairs if p.get("state") == "relation"]
    dropped: dict[str, int] = {}
    for p in pairs:
        if p.get("state") == "dropped":
            why = str(p.get("reason") or "")
            dropped[why] = dropped.get(why, 0) + 1
    by_rel: dict[str, int] = {}
    ends: set[str] = set()
    for p in rel_entries:
        by_rel[str(p.get("rel"))] = by_rel.get(str(p.get("rel")), 0) + 1
        ends.update((str(p["from"]), str(p["to"])))
    origin = list(ledger.get("origin") or [])
    check = {"agrees": 0, "disputes": 0, "unseen": 0}
    for o in origin:
        mark = o.get("ai") if o.get("ai") in check else "unseen"
        check[mark] += 1
        if mark == "unseen":
            ends.update((str(o["from"]), str(o["to"])))
    n_none = sum(1 for p in pairs if p.get("state") == "none")
    return {
        "pairs": len(pairs),
        "orphans": len(every) - len(pairs),
        "relations": len(rel_entries),
        "none": n_none,
        "dropped": dict(sorted(dropped.items())),
        "failed": sum(1 for p in pairs if p.get("state") == "failed"),
        "origin": len(origin),
        "origin_check": check,
        "shown": len(rel_entries) + check["unseen"],
        "terms": len(ends),
        "candidates": n_none + check["disputes"],
        "by_rel": dict(sorted(by_rel.items())),
        "tentative": sum(1 for p in rel_entries if p.get("tentative")),
    }
