"""点の 1 枚（ui-redesign PR 11d・§19）— 点を押したときに出す名前・文・来歴・中身・戻せるか。

``GET /api/chat/point`` の組み立て。読むだけ（ストアにも話にも書かない・AI を呼ばない）。

点の種別（``kind``）は次の順で決める:

* ``draft`` — ``draft:<下書きの id>:<概念の key>``（未確認の札）。文は下書きの関係から。
* ``line`` — ``from`` / ``relation`` / ``to`` の線 1 本。
* ``term`` — 共有の用語（``shared_vocab._read_terms``）。
* ``kind`` — 公開済みの種類（``sky_view`` の ``datasets[].kinds[].iri``）。
* ``std`` — 標準の語（``shared_vocab.known_namespaces`` の名前空間）。
* ``hub`` — 視点（``sky_view`` の ``hubs``）。

文は ``graph_diff.REL_SENTENCE``（``PRED_TO_REL``）で、11b の用語の事実と同じ言い方にする。
来歴の「話・手番」は、線なら ``batch``（``<話>:<手番>``）から、用語なら鋳造した
確認済みの手番から引く。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from asterism import (
    chat_sessions,
    crosswalk_runtime,
    graph_diff,
    shared_vocab,
    sky_view,
    themes,
)
from asterism.grounding import term_display_name

__all__ = ["PointError", "build_point"]

_MAX_SENTENCES = 50


class PointError(Exception):
    """点を引けない（``status`` は HTTP の写像）。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _local(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/#"))[-1] or iri


def _lg(lang: str | None) -> str:
    return "en" if (lang or "").lower().startswith("en") else "ja"


def _revertible(root: Path, loc: dict[str, Any] | None) -> bool:
    """その場所の手番が、確認済みで ``commit`` を持ち、公開したデータの手番でないか。"""
    if not loc:
        return False
    session = chat_sessions.load_session(root, str(loc["session_id"]))
    turn = next(
        (t for t in (session or {}).get("turns") or [] if t.get("id") == loc["turn_id"]), None
    )
    return bool(
        turn is not None
        and turn.get("state") == "confirmed"
        and isinstance(turn.get("commit"), dict)
        and not turn.get("attachment")
        and not turn.get("attach_run")
    )


def _theme_of_slug(root: Path, slug: str) -> str | None:
    for t in (themes.load_themes_file(root) or {}).get("themes") or []:
        if slug in (t.get("minted") or []) or slug in (t.get("uses") or []):
            return str(t.get("id"))
    return None


def _theme_of_line(root: Path, ln: dict[str, Any]) -> str | None:
    key = (ln["source"], ln["relation"], ln["target"])
    for t in (themes.load_themes_file(root) or {}).get("themes") or []:
        for r in t.get("relations") or []:
            if (str(r.get("from")), str(r.get("relation")), str(r.get("to"))) == key:
                return str(t.get("id"))
    return None


class _Ctx:
    """1 回の問い合わせで読んだもの（用語・線・星図）と、名前を引く道具。"""

    def __init__(
        self,
        root: Path,
        lang: str,
        terms: list[Any],
        lines: list[dict[str, Any]],
        sky: dict[str, Any],
    ) -> None:
        self.root = root
        self.lg = _lg(lang)
        self.terms = {t.iri: t for t in terms}
        self.lines = lines
        self.sky = sky
        self.kinds: dict[str, dict[str, Any]] = {}
        self.kind_dataset: dict[str, dict[str, Any]] = {}
        self.datasets = {d["id"]: d for d in sky.get("datasets") or []}
        for d in sky.get("datasets") or []:
            for k in d.get("kinds") or []:
                self.kinds.setdefault(k["iri"], k)
                self.kind_dataset.setdefault(k["iri"], d)
        self.std_ns = shared_vocab.known_namespaces()
        self._batches: dict[str, dict[str, Any] | None] = {}

    def is_standard(self, iri: str) -> bool:
        return any(iri.startswith(ns) for ns in self.std_ns)

    def term_name(self, iri: str) -> str:
        t = self.terms[iri]
        return (t.label_en or t.label) if self.lg == "en" else t.label

    def name(self, iri: str) -> str:
        if iri in self.terms:
            return self.term_name(iri)
        if iri in self.kinds:
            return str(self.kinds[iri]["label"])
        for ln in self.lines:
            if ln["source"] == iri and ln.get("source_label"):
                return str(ln["source_label"])
            if ln["target"] == iri and ln.get("target_label"):
                return str(ln["target_label"])
        return term_display_name(iri) or _local(iri)

    def turn_of_batch(self, batch: str | None) -> dict[str, Any] | None:
        if not batch:
            return None
        if batch not in self._batches:
            self._batches[batch] = chat_sessions.turn_of_batch(self.root, batch)
        return self._batches[batch]

    def sentence(self, ln: dict[str, Any]) -> dict[str, Any] | None:
        rel = graph_diff.PRED_TO_REL.get(str(ln["relation"]))
        if rel is None:
            return None
        loc = self.turn_of_batch(ln.get("batch")) or {}
        return {
            "text": graph_diff.REL_SENTENCE[self.lg][rel].format(
                s=self.name(ln["source"]), o=self.name(ln["target"])
            ),
            "relation": ln["relation"],
            "source": ln["source"],
            "target": ln["target"],
            "drafted_by": ln.get("drafted_by") or "human",
            "at": ln.get("at") or "",
            "batch": ln.get("batch"),
            "reason": ln.get("reason"),
            "session_id": loc.get("session_id"),
            "turn_id": loc.get("turn_id"),
        }

    def sentences_of(self, iri: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for ln in self.lines:
            if iri not in (ln["source"], ln["target"]):
                continue
            key = (ln["source"], ln["relation"], ln["target"])
            s = self.sentence(ln)
            if s is None or key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= _MAX_SENTENCES:
                break
        return out

    def dataset_label(self, dataset_id: str | None) -> str:
        d = self.datasets.get(dataset_id or "")
        return str(d["label"]) if d else ""

    def question_cards(self, iri: str) -> list[dict[str, Any]]:
        """この用語の問い ``[{title, mode, answering, knowledge}]``。星図が空でも（公開が 0 件でも）
        登録簿から引く。"""
        names = sky_view.questions_of_term(self.root, iri)
        by_name = {q["tool_name"]: q for q in self.sky.get("questions") or []}
        wired = shared_vocab.load_wired(self.root)
        known = shared_vocab.load_knowledge(self.root)
        data_tools = {n: tool for n, tool, _needs in sky_view._data_question_tools(self.root)}
        sels = {
            n: sel for _t, n, sel, _target in sky_view._knowledge_question_selections(self.root)
        }
        out: list[dict[str, Any]] = []
        for n in names:
            q = by_name.get(n)
            if q is not None:
                out.append({k: q[k] for k in ("title", "mode", "answering", "knowledge")})
            elif n in data_tools:
                out.append(
                    {
                        "title": str(data_tools[n].get("title") or n),
                        "mode": "data",
                        "answering": wired.get(iri, 0),
                        "knowledge": 0,
                    }
                )
            else:
                out.append(
                    {
                        "title": str((sels.get(n) or {}).get("title") or n),
                        "mode": "knowledge",
                        "answering": wired.get(iri, 0),
                        "knowledge": known.get(iri, 0),
                    }
                )
        return out


def _base(iri: str, kind: str, label: str) -> dict[str, Any]:
    return {
        "iri": iri,
        "kind": kind,
        "label": label,
        "term_kind": None,
        "sentences": [],
        "provenance": None,
        "questions": [],
        "members": [],
        "can_revert": False,
        "revert": None,
    }


async def _term_point(ctx: _Ctx, client: Any, iri: str) -> dict[str, Any]:
    term = ctx.terms[iri]
    out = _base(iri, "term", ctx.term_name(iri))
    out["term_kind"] = term.kind
    out["sentences"] = ctx.sentences_of(iri)
    out["questions"] = ctx.question_cards(iri)
    sky_term = next((t for t in ctx.sky.get("terms") or [] if t["iri"] == iri), None)
    seen: set[str] = set()
    for m in (sky_term or {}).get("members") or []:
        if m["iri"] in seen:
            continue
        seen.add(m["iri"])
        out["members"].append(
            {
                "iri": m["iri"],
                "label": ctx.name(m["iri"]),
                "dataset_label": ctx.dataset_label(m.get("dataset_id")),
                "dataset_id": m.get("dataset_id"),
            }
        )
    prov = await shared_vocab.term_provenance(client, iri)
    found = chat_sessions.turn_of_minted(ctx.root, term.slug)
    if prov is not None:
        out["provenance"] = {
            "drafted_by": prov["drafted_by"],
            "at": prov["at"],
            "theme_id": (found or {}).get("theme_id") or _theme_of_slug(ctx.root, term.slug),
            "session_id": (found or {}).get("session_id"),
            "turn_id": (found or {}).get("turn_id"),
        }
    if found is not None and _revertible(ctx.root, found):
        out["can_revert"] = True
        out["revert"] = {"session_id": found["session_id"], "turn_id": found["turn_id"]}
    return out


def _kind_point(ctx: _Ctx, iri: str) -> dict[str, Any]:
    k = ctx.kinds[iri]
    out = _base(iri, "kind", str(k["label"]))
    out["term_kind"] = "class"
    out["dataset_id"] = ctx.kind_dataset[iri]["id"]
    out["sentences"] = ctx.sentences_of(iri)
    for t in ctx.sky.get("terms") or []:
        if any(m["iri"] == iri for m in t.get("members") or []):
            out["members"].append({"iri": t["iri"], "label": ctx.term_name(t["iri"]),
                                   "dataset_label": ""})  # fmt: skip
    return out


def _std_point(ctx: _Ctx, iri: str) -> dict[str, Any]:
    out = _base(iri, "std", ctx.name(iri))
    out["sentences"] = ctx.sentences_of(iri)
    for t in ctx.sky.get("terms") or []:
        if (t.get("standard") or {}).get("iri") == iri:
            out["members"].append({"iri": t["iri"], "label": ctx.term_name(t["iri"]),
                                   "dataset_label": ""})  # fmt: skip
    return out


def _hub_point(ctx: _Ctx, iri: str, hub: dict[str, Any]) -> dict[str, Any]:
    out = _base(iri, "hub", str(hub["label"]))
    for kind_iri in hub.get("members") or []:
        ds = ctx.kind_dataset.get(kind_iri) or {}
        out["members"].append(
            {
                "iri": kind_iri,
                "label": ctx.name(kind_iri),
                "dataset_label": str(ds.get("label") or ""),
                "dataset_id": ds.get("id"),
            }
        )
    return out


def _line_point(ctx: _Ctx, ln: dict[str, Any]) -> dict[str, Any]:
    sentence = ctx.sentence(ln)
    if sentence is None:
        raise PointError(404, "その関係は図に出していません")
    out = _base(
        f"{ln['source']}|{ln['relation']}|{ln['target']}", "line", sentence["text"]
    )
    out["sentences"] = [sentence]
    loc = ctx.turn_of_batch(ln.get("batch"))
    out["provenance"] = {
        "drafted_by": sentence["drafted_by"],
        "at": sentence["at"],
        "theme_id": _theme_of_line(ctx.root, ln),
        "session_id": (loc or {}).get("session_id"),
        "turn_id": (loc or {}).get("turn_id"),
    }
    if loc is not None and _revertible(ctx.root, loc):
        out["can_revert"] = True
        out["revert"] = {"session_id": loc["session_id"], "turn_id": loc["turn_id"]}
    return out


def _draft_point(root: Path, lg: str, iri: str) -> dict[str, Any]:
    parts = iri.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise PointError(422, "未確認の札の形が合いません")
    _, draft_id, key = parts
    draft = themes.load_draft(root, draft_id) if themes._ID_RE.match(draft_id) else None
    if draft is None:
        raise PointError(404, "その下書きはありません")
    concepts = {str(c.get("key")): c for c in draft.get("concepts") or []}
    concept = concepts.get(key)
    if concept is None:
        raise PointError(404, "その札はありません")

    def label(c: dict[str, Any]) -> str:
        ja, en = str(c.get("label_ja") or ""), str(c.get("label_en") or "")
        return (en or ja) if lg == "en" else (ja or en or str(c.get("key")))

    def name_of(ref: str) -> str:
        c = concepts.get(ref)
        return label(c) if c else (term_display_name(ref) or ref)

    session_id = chat_sessions.session_of_draft(root, draft_id)
    session = chat_sessions.load_session(root, session_id) if session_id else None
    proposed = [
        t
        for t in (session or {}).get("turns") or []
        if t.get("state") == "proposed"
        and not t.get("attachment")
        and not t.get("attach_run")
        and not (t.get("reply") or {}).get("suggestions")
    ]
    mine = next(
        (
            t
            for t in proposed
            if any(
                o.get("op") == "add_concept" and o.get("key") == key
                for o in (t.get("reply") or {}).get("ops") or []
            )
        ),
        proposed[-1] if proposed else None,
    )
    at = str(draft.get("updated_at") or "")
    out = _base(iri, "draft", label(concept))
    out["term_kind"] = concept.get("kind")
    for r in draft.get("relations") or []:
        if key not in (r.get("from"), r.get("to")):
            continue
        rel = str(r.get("rel"))
        template = graph_diff.REL_SENTENCE[lg].get(rel)
        if template is None:
            continue

        def end(ref: str) -> str:
            return f"draft:{draft_id}:{ref}" if ref in concepts else str(ref)

        out["sentences"].append(
            {
                "text": template.format(s=name_of(str(r.get("from"))), o=name_of(str(r.get("to")))),
                "relation": rel,
                "source": end(str(r.get("from"))),
                "target": end(str(r.get("to"))),
                "drafted_by": r.get("by") or "human",
                "at": at,
                "batch": None,
                "reason": r.get("reason") or None,
                "session_id": session_id,
                "turn_id": (mine or {}).get("id"),
            }
        )
    out["provenance"] = {
        "drafted_by": concept.get("by") or "human",
        "at": at,
        "theme_id": draft.get("extend"),
        "session_id": session_id,
        "turn_id": (mine or {}).get("id"),
    }
    if mine is not None and session_id:
        out["can_revert"] = True
        out["revert"] = {"session_id": session_id, "turn_id": mine["id"]}
    return out


async def build_point(
    client: Any,
    root: Path | str,
    *,
    iri: str | None = None,
    source: str | None = None,
    relation: str | None = None,
    target: str | None = None,
    lang: str = "ja",
) -> dict[str, Any]:
    """点 1 つの 1 枚（形は契約 A1）。引けなければ :class:`PointError`（404/422）。"""
    root = Path(root)
    lg = _lg(lang)
    for value in (iri, source, target):
        if value is not None and not (isinstance(value, str) and 0 < len(value) <= 2000):
            raise PointError(422, "IRI の形が合いません")
    if iri is not None and iri.startswith("draft:"):
        return _draft_point(root, lg, iri)
    line_given = [x is not None for x in (source, relation, target)]
    if any(line_given) and not all(line_given):
        raise PointError(422, "from・relation・to を 3 つとも指定してください")
    if iri is None and not all(line_given):
        raise PointError(422, "iri か from・relation・to を指定してください")
    for value in (iri, source, target):
        if value is not None and not value.startswith("hub:"):  # 視点の札は IRI ではない
            try:
                shared_vocab._check_iri(value)
            except ValueError as exc:
                raise PointError(422, "IRI の形が合いません") from exc
    terms = await shared_vocab._read_terms(client)
    lines = await crosswalk_runtime.list_alignments(client, registry_root=root)
    sky = await sky_view.sky_view(client, root)
    ctx = _Ctx(root, lang, terms, lines, sky)
    if all(line_given):
        ln = next(
            (
                x
                for x in lines
                if x["source"] == source and x["relation"] == relation and x["target"] == target
            ),
            None,
        )
        if ln is None:
            raise PointError(404, "その関係はありません")
        return _line_point(ctx, ln)
    assert iri is not None
    if iri in ctx.terms:
        return await _term_point(ctx, client, iri)
    if iri in ctx.kinds:
        return _kind_point(ctx, iri)
    if ctx.is_standard(iri):
        return _std_point(ctx, iri)
    hub = next(
        (
            h
            for h in sky.get("hubs") or []
            if iri in (h["id"], f"hub:{h['id']}") or iri.startswith(f"hub:{h['id']}:")
        ),
        None,
    )
    if hub is not None:
        return _hub_point(ctx, iri, hub)
    raise PointError(404, "その点はありません")
