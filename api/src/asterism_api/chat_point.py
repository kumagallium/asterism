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

from asterism_api import chat_seed

__all__ = ["PointError", "build_point"]

_MAX_SENTENCES = 50
#: ``hub:`` の後ろ（空でなく、IRI に使えない字を含まない）
_HUB_REST = re.compile(r'^[^\s<>"{}|\\^`]+$')


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

    def standard_of(self, iri: str | None) -> dict[str, str] | None:
        """標準の語 ``{iri, label, prefix}``（``sky_view`` の ``standards`` と同じ形）。"""
        if not iri:
            return None
        found = next((x for x in self.sky.get("standards") or [] if x.get("iri") == iri), None)
        e = found or sky_view._standard_entry(iri)
        return {
            "iri": str(e["iri"]),
            "label": str(e["label"]),
            "prefix": str(e.get("prefix") or ""),
        }

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
        "standard": None,
        # ストアにある（✓ 済み・公開済み・標準・視点）。未確認の札・提案の線は False
        "confirmed": True,
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
    out["standard"] = ctx.standard_of(((sky_term or {}).get("standard") or {}).get("iri"))
    for m in (sky_term or {}).get("members") or []:
        if m["iri"] in seen or m["iri"] not in ctx.kinds:  # 種類（class）だけ。項目は入れない
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
    out["dataset_label"] = ctx.dataset_label(out["dataset_id"])
    out["standard"] = ctx.standard_of(k.get("standard"))
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
        raise PointError(404, "その関係は知識グラフに出していません")
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


class _DraftView:
    """提案（未確認の下書き）1 つを読んだもの。札と線の両方がこれで名前・来歴を引く。"""

    def __init__(self, root: Path, lg: str, draft_id: str) -> None:
        draft = themes.load_draft(root, draft_id) if themes._ID_RE.match(draft_id) else None
        if draft is None:
            raise PointError(404, "その提案はありません")
        self.lg = lg
        self.draft_id = draft_id
        self.draft = draft
        self.concepts = {str(c.get("key")): c for c in draft.get("concepts") or []}
        self.at = str(draft.get("updated_at") or "")
        self.session_id = chat_sessions.session_of_draft(root, draft_id)
        self.seed_keys, self.seed_rels = chat_seed.seed_marks(
            chat_sessions.draft_seed_map(root).get(draft_id), draft
        )
        session = chat_sessions.load_session(root, self.session_id) if self.session_id else None
        self.proposed = [
            t
            for t in (session or {}).get("turns") or []
            if t.get("state") == "proposed"
            and not t.get("attachment")
            and not t.get("attach_run")
            and not (t.get("reply") or {}).get("suggestions")
        ]

    def label(self, c: dict[str, Any]) -> str:
        ja, en = str(c.get("label_ja") or ""), str(c.get("label_en") or "")
        return (en or ja) if self.lg == "en" else (ja or en or str(c.get("key")))

    def name_of(self, ref: str) -> str:
        c = self.concepts.get(ref)
        return self.label(c) if c else (term_display_name(ref) or ref)

    def end(self, ref: str) -> str:
        return f"draft:{self.draft_id}:{ref}" if ref in self.concepts else str(ref)

    def approved_iris(self, f: str, rel: str, t: str) -> tuple[str, str, str] | None:
        """承認済みの行 → ``(主語の IRI, 述語, 目的語の IRI)``（用語は ``existing:<slug>``、
        標準の語は curie の IRI）。IRI にできなければ ``None``。"""

        def slug(key: str) -> str:
            c = self.concepts.get(key) or {}
            use = str(c.get("use") or "")
            bare = use[len("existing:") :] if use.startswith("existing:") else ""
            return str(c.get("slug") or bare)

        kinds = {slug(k): c.get("kind") for k, c in self.concepts.items() if slug(k)}
        return chat_seed._line_iris({"kinds": kinds}, (slug(f), rel, slug(t) or t))

    def turn_for(self, key: str | None) -> dict[str, Any] | None:
        """この札（key）を足した提案の手番。無ければ最後の提案の手番。"""
        if key is not None:
            for t in self.proposed:
                if any(
                    o.get("op") == "add_concept" and o.get("key") == key
                    for o in (t.get("reply") or {}).get("ops") or []
                ):
                    return t
        return self.proposed[-1] if self.proposed else None

    def sentence(self, r: dict[str, Any], turn: dict[str, Any] | None) -> dict[str, Any] | None:
        rel = str(r.get("rel"))
        template = graph_diff.REL_SENTENCE[self.lg].get(rel)
        if template is None:
            return None
        f, t = str(r.get("from")), str(r.get("to"))
        marked = (f, rel, t) in self.seed_rels
        source, relation, target = self.end(f), rel, self.end(t)
        if marked:  # 承認済みの行は IRI の形（外す・戻すが承認済みの行として扱える）
            iris = self.approved_iris(f, rel, t)
            if iris is not None:
                source, relation, target = iris
        return {
            "text": template.format(s=self.name_of(f), o=self.name_of(t)),
            "relation": relation,
            "source": source,
            "target": target,
            "drafted_by": r.get("by") or "human",
            "at": self.at,
            "batch": None,
            "reason": r.get("reason") or None,
            "session_id": self.session_id,
            "turn_id": (turn or {}).get("id"),
            # 11o: 行を特定する鍵（外す・承認・範囲に使う）。下書きの鍵と短い関係名
            "from": str(r.get("from")),
            "rel": rel,
            "to": str(r.get("to")),
            "draft_id": self.draft_id,
            # 承認済みの種にある行（UI は濃く出す）
            "from_seed": marked,
        }

    def revert_of(self, out: dict[str, Any], turn: dict[str, Any] | None) -> None:
        if turn is not None and self.session_id:
            out["can_revert"] = True
            out["revert"] = {"session_id": self.session_id, "turn_id": turn["id"]}


def _split_draft_ref(ref: str) -> tuple[str, str]:
    parts = ref.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise PointError(422, "承認していない提案の形が合いません")
    return parts[1], parts[2]


def _draft_point(root: Path, lg: str, iri: str) -> dict[str, Any]:
    draft_id, key = _split_draft_ref(iri)
    view = _DraftView(root, lg, draft_id)
    concept = view.concepts.get(key)
    if concept is None:
        raise PointError(404, "その提案はありません")
    mine = view.turn_for(key)
    out = _base(iri, "draft", view.label(concept))
    out["confirmed"] = False
    out["term_kind"] = concept.get("kind")
    for r in view.draft.get("relations") or []:
        if key not in (r.get("from"), r.get("to")):
            continue
        sentence = view.sentence(r, mine)
        if sentence is not None:
            out["sentences"].append(sentence)
    out["provenance"] = {
        "drafted_by": concept.get("by") or "human",
        "at": view.at,
        "theme_id": view.draft.get("extend"),
        "session_id": view.session_id,
        "turn_id": (mine or {}).get("id"),
    }
    view.revert_of(out, mine)
    return out


def _draft_line_point(
    root: Path, lg: str, source: str, relation: str, target: str
) -> dict[str, Any]:
    """提案の線 — ``from`` / ``to`` のどちらかまたは両方が ``draft:<id>:<key>``。"""
    ids = {_split_draft_ref(x)[0] for x in (source, target) if x.startswith("draft:")}
    if len(ids) != 1:
        raise PointError(422, "関係の両端が別の提案にまたがっています")
    view = _DraftView(root, lg, ids.pop())

    def key_of(ref: str) -> str:
        return _split_draft_ref(ref)[1] if ref.startswith("draft:") else ref

    want = (key_of(source), relation, key_of(target))
    found = next(
        (
            r
            for r in view.draft.get("relations") or []
            if (str(r.get("from")), str(r.get("rel")), str(r.get("to"))) == want
        ),
        None,
    )
    if found is None:
        raise PointError(404, "その関係はありません")
    mine = view.turn_for(want[0] if want[0] in view.concepts else None)
    sentence = view.sentence(found, mine)
    if sentence is None:
        raise PointError(404, "その関係は知識グラフに出していません")
    out = _base(f"{source}|{relation}|{target}", "line", sentence["text"])
    out["confirmed"] = False
    out["sentences"] = [sentence]
    out["provenance"] = {
        "drafted_by": sentence["drafted_by"],
        "at": view.at,
        "theme_id": view.draft.get("extend"),
        "session_id": view.session_id,
        "turn_id": (mine or {}).get("id"),
    }
    view.revert_of(out, mine)
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
    if any(x is not None and x.startswith("draft:") for x in (source, target)) and all(line_given):
        return _draft_line_point(root, lg, str(source), str(relation), str(target))
    for value in (iri, source, target):
        if value is None:
            continue
        if value.startswith("hub:"):  # 視点の札は IRI ではない（後ろは空でない・IRI の字だけ）
            if not _HUB_REST.match(value[4:]):
                raise PointError(422, "視点の形が合いません")
            continue
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
