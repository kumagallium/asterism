"""台帳の走者 — 台帳の「見せる関係」（未確認も確認済みも）で、型の問いに答える（14e-2）。

``knowledge.knowledge_answer`` はストアの確定した線で答える。こちらは台帳（取り込み元ごとの
``vocab-shared/knowledge/<名前>.json``）と写しだけで答える。ストアを読まないので、確認済みを
ストアに置く前でも、AI・人・取り込み元の未確認の関係を使って答えられる。確認済みかどうかは
台帳の ``confirmed`` で見分け、答えの項目ごとに印を付ける（``confirmed``・``uses``）。

* 数え方は :func:`asterism.pair_relations.questions` と同じ（``factors`` の 1 段・
  ``missing`` の定義・向きの無い関係・種別が型に合うか）。1 つの問いの ``count`` ＝
  :func:`answer` の項目数。
* AI は呼ばない・SPARQL は書かない・``pair_relations`` と ``knowledge`` は読むだけ。
* ``asterism_api`` に依らない（demo-agent が import する）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from asterism import knowledge_bundle, relation_catalog
from asterism import pair_relations as pr
from asterism.graph_diff import PRED_TO_REL, REL_SENTENCE
from asterism.knowledge import LINE_OPS, MISSING_MIN_OTHERS, item_sentence
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.shared_vocab import describe_selection
from asterism.themes import knowledge_target_kind_ok

__all__ = [
    "EVIDENCE_MAX",
    "NAME_PREFIX",
    "REPLY_ITEMS_MAX",
    "answer",
    "find_ledgers",
    "find_question",
    "ledger_questions",
    "load_source",
    "reply_block",
    "summarize_use",
]

#: 問いの名前の頭（``mcp/`` には現れない）。
NAME_PREFIX = "lq_"
#: 1 項目に添える根拠の知見の最大件数。
EVIDENCE_MAX = 3
#: ``reply_block`` に入れる項目の最大件数（確認の対象もこの中だけ）。
REPLY_ITEMS_MAX = 50
#: ``missing`` の行に添える「ほかの語」の最大件数（``knowledge.MISSING_OTHERS_MAX`` と同じ）。
_OTHERS_MAX = 5
_FACTOR_DIRECTIONS = relation_catalog.factor_directions()
#: 関係 → 数える問いの型（``kinds``・``parts``・``bearers``・``factors`` …）。目録から導く。
_QUESTION_OP = relation_catalog.question_ops()
#: ``missing`` の「持つ」の関係（``bearers`` を数える関係）。記録が無い行の ``relation``。
_OWNER_RELATION = relation_catalog.relation_for_op("bearers")
_GONE = ("retracted", "deleted")
_SAFE_ID = re.compile(r"[a-z0-9-]{1,128}")


# ---- 台帳の関係を数える（questions と同じ規則） -----------------------------------------------


def _record(ledger: Mapping[str, Any], r: Mapping[str, Any]) -> dict:
    """見せる関係 1 行 → 答えの根拠にする記録 ``{from, rel, to, key, confirmed, entry}``。"""
    pairs = ledger.get("pairs") or {}
    if r.get("by") == "ai":
        entry = pairs.get(r["key"]) or {}
        return {
            "from": r["from"], "rel": str(r["rel"]), "to": r["to"], "key": str(r["key"]),
            "confirmed": bool(entry.get("confirmed")), "entry": entry,
        }
    return {
        "from": r["from"], "rel": str(r["rel"]), "to": r["to"], "key": pr.origin_key(r),
        "confirmed": False, "entry": None,
    }


def _collect(ledger: Mapping[str, Any], copy: KnowledgeCopy) -> dict[tuple, list[dict]]:
    """``(op, 語 id, direction)`` → 当たり（項目になる 1 件ずつ）の一覧。件数は
    :func:`asterism.pair_relations.questions` の ``counts`` と同じ。
    当たり ``{other, via, recs: [記録…], primary: 記録 | None, p?}``。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    hits: dict[tuple, list[dict]] = {}

    def add(op: str, tid: str, direction: str | None, hit: dict) -> None:
        t = terms.get(tid)
        if t is None or not knowledge_target_kind_ok(op, t.get("kind")):
            return
        hits.setdefault((op, tid, direction), []).append(hit)

    seen: set[tuple[str, str, str]] = set()
    every: list[dict] = []
    factor_rows: list[tuple[dict, str, str]] = []  # （記録, 下げる等の関係, その to）
    parents: dict[str, dict[str, dict]] = {}  # 語 → {それが一部になっている語: part の記録}
    ends: set[str] = set()
    owners: dict[str, dict[str, dict]] = {}  # 物性 P → {持つ語: has_property の記録}
    for r in pr.shown_relations(ledger):
        frm, rel, to = r["from"], str(r["rel"]), r["to"]
        if (frm, rel, to) in seen or frm not in terms or to not in terms:
            continue
        seen.add((frm, rel, to))
        rec = _record(ledger, r)
        every.append(rec)
        ends.update((frm, to))
        op_of_rel = _QUESTION_OP.get(rel)
        if op_of_rel == "bearers":
            owners.setdefault(to, {})[frm] = rec
        if op_of_rel == "kinds":
            add("kinds", to, None, {"other": frm, "via": to, "recs": [rec], "primary": rec})
        elif op_of_rel == "parts":
            add("parts", to, None, {"other": frm, "via": to, "recs": [rec], "primary": rec})
            parents.setdefault(frm, {})[to] = rec
        elif rel in _FACTOR_DIRECTIONS:
            factor_rows.append((rec, rel, to))
        names = pr._CONFIRMED_NAMES.get(rel, frozenset())
        for op, (op_names, side) in LINE_OPS.items():
            if not names & set(op_names):
                continue
            for tid in {"to": [to], "from": [frm], "either": [frm, to]}[side]:
                other = frm if tid == to else to  # 向きなしで両端が同じ語のときも 1 件ずつ数える
                add(op, tid, None, {"other": other, "via": tid, "recs": [rec], "primary": rec})

    # 同じ 2 語に未確認の見せる関係が 2 本以上 → 確認の口は 1 組に 1 本しか確認できない
    # （2 本目は ``pair_has_relation``）ので、確認ボタンの対象から外す印を付ける
    per_pair: dict[str, int] = {}
    for rec in every:
        rec["pair"] = "|".join(sorted((str(rec["from"]), str(rec["to"]))))
        if not rec["confirmed"]:
            per_pair[rec["pair"]] = per_pair.get(rec["pair"], 0) + 1
    for rec in every:
        rec["conflict"] = not rec["confirmed"] and per_pair.get(rec["pair"], 0) >= 2

    for rec, rel, to in factor_rows:
        for d in _FACTOR_DIRECTIONS[rel]:
            add("factors", to, d,
                {"other": rec["from"], "via": to, "recs": [rec], "primary": rec})
            for parent, part_rec in parents.get(to, {}).items():
                add("factors", parent, d,
                    {"other": rec["from"], "via": to, "recs": [rec, part_rec], "primary": rec})

    for tid in ends:
        if terms[tid].get("kind") != "class":
            continue
        for p, own in owners.items():
            if tid in own or len(set(own) - {tid}) < MISSING_MIN_OTHERS:
                continue
            recs = [own[f] for f in sorted(own) if f != tid]
            hits.setdefault(("missing", tid, None), []).append(
                {"other": p, "via": tid, "recs": recs, "primary": None,
                 "owners": sorted(set(own) - {tid})}
            )
    return hits


def _slug(tid: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", tid)
    if base != tid or not base:  # 記号を落としたら、別の id と重ならないよう id の短い指紋を付ける
        base += "x" + hashlib.sha1(tid.encode("utf-8", "surrogatepass")).hexdigest()[:6]
    return base


def _name(op: str, tid: str, direction: str | None) -> str:
    return f"{NAME_PREFIX}{op}_{_slug(tid)}" + (f"_{direction}" if direction else "")


# ---- 問い -------------------------------------------------------------------------------------


def ledger_questions(
    ledger: Mapping[str, Any], copy: KnowledgeCopy, *, lang: str = "ja"
) -> list[dict]:
    """答えられる問いの全件（件数の多い順 → 文 → 語 id）。1 件は ``{name, source, op, term:
    {id, name}, direction?, text, count}``。数え方は :func:`asterism.pair_relations.questions` と
    同じ。``name`` は機械が作る ``lq_<op>_<語 id の英数字>[_<direction>]``（同じ取り込み元の中で
    一意。語 id に記号があれば指紋を付ける）。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    out: list[dict] = []
    for (op, tid, direction), hs in _collect(ledger, copy).items():
        sel: dict[str, Any] = {"op": op, "target": str(terms[tid].get("name") or "")}
        if direction:
            sel["direction"] = direction
        text = describe_selection(sel, lang)
        if not text:
            continue
        q: dict[str, Any] = {
            "name": _name(op, tid, direction),
            "source": str(ledger.get("source") or ""),
            "op": op,
            "term": {"id": tid, "name": terms[tid].get("name")},
            "text": text,
            "count": len(hs),
        }
        if direction:
            q["direction"] = direction
        out.append(q)
    out.sort(key=lambda i: (-i["count"], i["text"], i["term"]["id"]))
    return out


def find_question(
    ledger: Mapping[str, Any], copy: KnowledgeCopy, name: str, *, lang: str = "ja"
) -> dict | None:
    """名前 ``lq_…`` の問い。今の台帳に無ければ ``None``（取り込み直しで消えた）。"""
    if not isinstance(name, str) or not name.startswith(NAME_PREFIX):
        return None
    for q in ledger_questions(ledger, copy, lang=lang):
        if q["name"] == name:
            return q
    return None


# ---- 答え -------------------------------------------------------------------------------------


def _evidence(rec: Mapping[str, Any] | None, claims: Mapping[str, Any]) -> tuple[list[dict], int]:
    entry = (rec or {}).get("entry") or {}
    ids = list(dict.fromkeys(str(c) for c in entry.get("evidence") or []))
    out: list[dict] = []
    for cid in ids:
        c = claims.get(cid)
        if c is None:
            continue
        out.append(
            {"id": cid, "iri": c.get("iri"), "title": c.get("title"),
             "source_url": c.get("source_url")}
        )
        if len(out) >= EVIDENCE_MAX:
            break
    total = entry.get("claims_total")
    return out, int(total if isinstance(total, int) else len(ids))


def _sentence(op: str, hit: Mapping[str, Any], names: Mapping[str, str], lang: str) -> str:
    lg = "en" if lang.startswith("en") else "ja"
    if op == "missing":
        ordered = sorted(hit["owners"], key=lambda o: (names.get(o, o), o))
        others = [{"label": names.get(o, o)} for o in ordered]
        return item_sentence(
            "missing",
            {"label": names.get(hit["other"], hit["other"]),
             "others": others[:_OTHERS_MAX], "others_count": len(others)},
            lg,
        ) or ""
    rec = hit["primary"]
    frm, to = names.get(rec["from"], rec["from"]), names.get(rec["to"], rec["to"])
    if op in LINE_OPS:
        pred = sorted(pr._CONFIRMED_NAMES.get(rec["rel"], frozenset()) & set(LINE_OPS[op][0]))
        if pred:
            s = item_sentence(
                op,
                {"relation": pred[0], "label": names.get(hit["other"], hit["other"]),
                 "via_label": names.get(hit["via"], hit["via"])},
                lg,
            )
            if s:
                return s
    tmpl = REL_SENTENCE[lg].get(PRED_TO_REL.get(rec["rel"], rec["rel"]))
    return tmpl.format(s=frm, o=to) if tmpl else f"{frm} - {to}"


def answer(
    ledger: Mapping[str, Any], copy: KnowledgeCopy, question: Mapping[str, Any], *, lang: str = "ja"
) -> dict:
    """問い（:func:`ledger_questions` の 1 件）に答える。``knowledge.knowledge_answer`` と同じ形
    ``{found, question, op, target, direction, count, items}``（＋ ``name``・``source``）。

    項目 ``{label, relation, via_label, sentence, confirmed, tentative, context, evidence,
    evidence_total, uses}``。``uses`` は項目に絡む関係の全部 ``{source, key, confirmed}``
    （``key`` は確認の口がそのまま受ける鍵）。``confirmed`` は絡む関係が全部確認済みのとき。
    問いが今の台帳に無ければ ``found: False``・項目 0 件。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    names = {tid: str(t.get("name") or tid) for tid, t in terms.items()}
    claims = {str(c["id"]): c for c in copy.claims if "id" in c}
    source = str(ledger.get("source") or "")
    op = str(question.get("op"))
    tid = str((question.get("term") or {}).get("id"))
    direction = question.get("direction") or None
    hs = _collect(ledger, copy).get((op, tid, direction), [])
    items: list[dict] = []
    for h in hs:
        rec = h["primary"]
        evidence, total = _evidence(rec, claims)
        entry = (rec or {}).get("entry") or {}
        ctx = [names[str(t)] for t in entry.get("context") or [] if str(t) in names]
        uses = []
        for r in h["recs"]:
            u = {"source": source, "key": r["key"], "confirmed": r["confirmed"]}
            if r["conflict"]:
                u["conflict"] = True
            uses.append(u)
        items.append(
            {
                "label": names.get(h["other"], h["other"]),
                "relation": rec["rel"] if rec else _OWNER_RELATION,
                "via_label": names.get(h["via"], h["via"]),
                "sentence": _sentence(op, h, names, lang),
                "confirmed": all(u["confirmed"] for u in uses),
                "tentative": any(bool((r["entry"] or {}).get("tentative")) for r in h["recs"]),
                "context": ctx,
                "evidence": evidence,
                "evidence_total": total,
                "uses": uses,
            }
        )
    if op == "missing":
        items.sort(key=lambda i: (-len(i["uses"]), i["label"]))
    else:
        items.sort(key=lambda i: (i["label"], i["relation"], i["via_label"]))
    return {
        "found": bool(items),
        "name": question.get("name"),
        "source": source,
        "question": question.get("text"),
        "op": op,
        "target": names.get(tid, tid),
        "direction": direction,
        "count": len(items),
        "items": items,
    }


def summarize_use(answers: Sequence[Mapping[str, Any]]) -> dict:
    """答えに使った関係のうち、確認ボタンで確認できる未確認の鍵（重複なし）。
    ``{unconfirmed: [{source, key}], unconfirmed_count, confirmed_count, conflicts: [{source, key}],
    conflict_count}``。「N 件」＝確認できる鍵の数。同じ 2 語に未確認の関係が複数ある（``uses`` の
    ``conflict``）関係は ``unconfirmed`` に入れず ``conflicts`` に分ける。"""
    unconfirmed: dict[tuple[str, str], None] = {}
    conflicts: dict[tuple[str, str], None] = {}
    confirmed: set[tuple[str, str]] = set()
    for a in answers:
        for item in a.get("items") or []:
            for u in item.get("uses") or []:
                k = (str(u["source"]), str(u["key"]))
                if u.get("confirmed"):
                    confirmed.add(k)
                elif u.get("conflict"):
                    conflicts[k] = None
                else:
                    unconfirmed[k] = None
    confirmed -= set(unconfirmed) | set(conflicts)
    return {
        "unconfirmed": [{"source": s, "key": k} for s, k in unconfirmed],
        "unconfirmed_count": len(unconfirmed),
        "confirmed_count": len(confirmed),
        "conflicts": [{"source": s, "key": k} for s, k in conflicts],
        "conflict_count": len(conflicts),
    }


def reply_block(
    answers: Sequence[Mapping[str, Any]], *, limit: int = REPLY_ITEMS_MAX
) -> dict | None:
    """答えを ``reply.answer.ledger`` の形にする。使った答え（見つかったもの）が無ければ ``None``。

    項目は**問いごとに**先頭 ``limit`` 件（AI に見せた物と同じ）をつなぎ、各項目に ``question``
    （問いの名前）を足す。``unconfirmed``・``conflicts`` と数は、載せた項目（＝AI に見せた全部）から
    数える。``items_total`` は全数・``questions[].shown`` は問いごとに載せた数。"""
    used = [a for a in answers if a.get("found")]
    if not used:
        return None
    items: list[dict] = []
    total = 0
    qs: list[dict] = []
    for a in used:
        total += len(a["items"])
        cut = a["items"][: max(0, limit)]
        qs.append(
            {"source": a["source"], "name": a["name"], "text": a["question"],
             "count": a["count"], "shown": len(cut)}
        )
        for it in cut:
            items.append(
                {
                    "question": a["name"],
                    "label": it["label"],
                    "sentence": it["sentence"],
                    "confirmed": it["confirmed"],
                    "tentative": it["tentative"],
                    "context": it["context"],
                    "evidence_total": it["evidence_total"],
                    "evidence": [
                        {"title": e.get("title"), "source_url": e.get("source_url")}
                        for e in it["evidence"]
                    ],
                    "uses": it["uses"],
                }
            )
    return {
        "questions": qs,
        "items": items,
        "items_total": total,
        **summarize_use([{"items": items}]),
    }


# ---- registry から読む ------------------------------------------------------------------------


def _read_ledger(path: Path) -> dict | None:
    """台帳を読むだけ（壊れていても退避しない）。壊れていれば ``None``。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != pr.LEDGER_VERSION
        or not isinstance(data.get("pairs"), dict)
        or not isinstance(data.get("origin") or [], list)
        or not isinstance(data.get("source"), str)
    ):
        return None
    data["pairs"] = {
        k: _clean_pair(v)
        for k, v in data["pairs"].items()
        if isinstance(v, dict) and "key" in v and _pair_ok(v)
    }
    data["origin"] = [o for o in data.get("origin") or [] if _origin_ok(o)]
    data.setdefault("dataset_id", None)
    return data


def _strs(*vals: Any) -> bool:
    return all(isinstance(v, str) and v for v in vals)


def _pair_ok(e: Mapping[str, Any]) -> bool:
    """走者が読める組の件か。関係を持つ件は ``rel``・``from``・``to`` が文字列であること。"""
    if e.get("state") == "relation" and not e.get("orphan"):
        return _strs(e.get("rel"), e.get("from"), e.get("to"))
    return True


def _clean_pair(e: dict) -> dict:
    for k in ("evidence", "context", "claims"):
        if not isinstance(e.get(k, []), list):
            e[k] = []
    return e


def _origin_ok(o: Any) -> bool:
    return isinstance(o, dict) and _strs(o.get("rel"), o.get("from"), o.get("to"))


def _copy_of(root: Path, ledger: Mapping[str, Any]) -> KnowledgeCopy | None:
    did = ledger.get("dataset_id")
    if not isinstance(did, str) or not _SAFE_ID.fullmatch(did):
        return None
    try:
        meta = json.loads((root / did / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("status") in _GONE:
        return None
    try:
        return knowledge_bundle.read_copy(root / did / "source")
    except (knowledge_bundle.BundleError, OSError):
        return None


def load_source(registry_root: Path | str, source: str) -> tuple[dict, KnowledgeCopy] | None:
    """取り込み元 1 つの ``(台帳, 写し)``。無い・壊れている・取り下げ済みは ``None``。"""
    root = Path(registry_root)
    ledger = _read_ledger(pr.ledger_path(root, source))
    if ledger is None or ledger["source"] != source:
        return None
    copy = _copy_of(root, ledger)
    return (ledger, copy) if copy is not None else None


def find_ledgers(
    registry_root: Path | str | None, *, stats: dict[str, int] | None = None
) -> list[tuple[str, dict, KnowledgeCopy]]:
    """registry の台帳と写し ``[(取り込み元, 台帳, 写し)]``（取り込み元の名前順）。台帳の
    ``dataset_id`` で ``<root>/<dataset_id>/source`` を読む。取り下げ・削除済みの写し・写しが読め
    ない台帳は飛ばす。壊れた台帳（読めない・版が違う）も飛ばして ``stats["broken"]`` に数える
    （``stats["skipped"]`` は写しが無くて飛ばした数）。ファイルは変えない。"""
    root = Path(registry_root) if registry_root is not None else None
    found: list[tuple[str, dict, KnowledgeCopy]] = []
    st = stats if stats is not None else {}
    st.setdefault("broken", 0)
    st.setdefault("skipped", 0)
    if root is None:
        return found
    folder = root / "vocab-shared" / "knowledge"
    try:
        files = sorted(p for p in folder.glob("*.json") if not p.name.startswith("."))
    except OSError:
        return found
    for path in files:
        ledger = _read_ledger(path)
        if ledger is None:
            st["broken"] += 1
            continue
        copy = _copy_of(root, ledger)
        if copy is None:
            st["skipped"] += 1
            continue
        found.append((ledger["source"], ledger, copy))
    found.sort(key=lambda t: t[0])
    return found
