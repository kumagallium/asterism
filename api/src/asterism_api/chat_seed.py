"""確認済みのテーマを種にした下書き（ui-redesign PR 11b・§19）。

✓ のあとも話を続けるとき、確認済みのテーマの概念と関係を「種」として下書きに入れる。人が
「粒界散乱は外して」と言えば、種の行が下書きから消える。✓ で、種にあって下書きに無いものを
ストアからも外す（:func:`apply_removals`）。AI に値は渡さない（名前だけ）。

* :func:`seed_from_theme` — テーマ → 種の行（概念・関係・標準の語）と控え（``seed``）。
* :func:`seed_gone` — 種にあって下書きに無いもの。
* :func:`sky_removals` — ``GET /api/sky`` の ``proposed[].removals``（IRI で返す）。
* :func:`apply_removals` — ✓ のあと、外したものをストアから外す。
"""

from __future__ import annotations

import logging
from typing import Any

from asterism import crosswalk_runtime, graph_diff, themes
from asterism import shared_vocab as sv
from asterism.shared_vocab import SharedTerm

__all__ = ["apply_removals", "seed_from_theme", "seed_gone", "sky_removals"]

logger = logging.getLogger(__name__)


def _std_by_iri(iri: str, cache: dict[str, Any]) -> themes.StdRef | None:
    if iri in cache:
        return cache[iri]
    ref: themes.StdRef | None = None
    if iri.startswith(themes._QK_BASE):
        ref = themes.std_by_curie("quantitykind:" + iri[len(themes._QK_BASE) :])
    else:
        from asterism import grounding

        for t in grounding.catalog_terms():
            if t.iri == iri:
                ref = themes.std_by_curie(t.curie)
                break
    cache[iri] = ref
    return ref


def seed_from_theme(root: Any, existing: list[SharedTerm], theme: dict) -> dict:
    """確認済みのテーマ → ``{concepts, relations, standards, seed}``。

    概念 = テーマの関係の両端・問い（``selections``）が指す用語・``uses``（どれも共有の用語だけ）。
    関係 = ``PRED_TO_REL`` で述語を図の関係名にし、両端を key に（標準の語の端は curie）。
    ``seed`` = 話に持つ控え ``{"concepts": [slug], "relations": [[from, rel, to]],
    "kinds": {slug: kind}}``。"""
    by_iri = {t.iri: t for t in existing}
    by_slug = {t.slug: t for t in existing}
    cache: dict[str, Any] = {}
    slugs: dict[str, None] = {}
    rels: list[tuple[str, str, str]] = []
    standards: dict[str, dict] = {}

    def end(iri: str) -> str | None:
        t = by_iri.get(iri)
        if t is not None:
            return t.slug
        ref = _std_by_iri(iri, cache)
        if ref is not None:
            standards[ref.curie] = {"label": ref.label, "iri": ref.iri, "kind": ref.kind}
            return ref.curie
        return None

    for ln in theme.get("relations") or []:
        if not isinstance(ln, dict):
            continue
        rel = graph_diff.PRED_TO_REL.get(str(ln.get("relation")))
        src = by_iri.get(str(ln.get("from")))
        tgt = end(str(ln.get("to")))
        if rel is None or src is None or tgt is None:
            continue
        trip = (src.slug, rel, tgt)
        if trip in rels:
            continue
        rels.append(trip)
        slugs.setdefault(src.slug)
        if tgt in by_slug:
            slugs.setdefault(tgt)
    for sel in (theme.get("selections") or {}).values():
        if isinstance(sel, dict):
            for k, v in sel.items():
                if k.endswith("_slug") and isinstance(v, str) and v in by_slug:
                    slugs.setdefault(v)
    for s in theme.get("uses") or []:
        if isinstance(s, str) and s in by_slug:
            slugs.setdefault(s)

    keys: dict[str, str] = {}
    concepts: list[dict] = []
    for n, slug in enumerate(slugs, start=1):
        t = by_slug[slug]
        keys[slug] = f"c{n}"
        concepts.append(
            {
                "key": f"c{n}",
                "label_ja": t.label,
                "label_en": t.label_en or "",
                "aliases": list(t.aliases),
                "kind": t.kind,
                "use": f"existing:{t.slug}",
                "slug": t.slug,
                "reason": "",
                "match": {
                    "kind": "existing",
                    "ref": t.slug,
                    "label": t.label,
                    "matched_by": "label",
                },
                "by": "human",
            }
        )
    relations = [
        {
            "from": keys[f],
            "rel": r,
            "to": keys.get(t, t),
            "reason": "",
            "problem": None,
            "by": "human",
        }
        for f, r, t in rels
    ]
    return {
        "concepts": concepts,
        "relations": relations,
        "standards": standards,
        "seed": {
            "concepts": list(slugs),
            "relations": [list(t) for t in rels],
            "kinds": {s: by_slug[s].kind for s in slugs},
        },
    }


def seed_gone(seed: dict, draft: dict) -> tuple[list[str], list[tuple[str, str, str]]]:
    """種にあって下書きに無いもの → ``(用語の slug, [(from_slug, rel, to_slug_or_curie)])``。"""
    rows = [c for c in draft.get("concepts") or [] if isinstance(c, dict)]
    slug_of = {str(c.get("key")): str(c.get("slug") or "") for c in rows}
    have_slugs = {s for s in slug_of.values() if s}
    have_rels = {
        (slug_of.get(str(r.get("from")), ""), str(r.get("rel")),
         slug_of.get(str(r.get("to")), str(r.get("to"))))
        for r in draft.get("relations") or []
        if isinstance(r, dict) and r.get("problem") is None
    }  # fmt: skip
    terms = [s for s in seed.get("concepts") or [] if s not in have_slugs]
    lines = [
        (str(f), str(r), str(t))
        for f, r, t in seed.get("relations") or []
        if (str(f), str(r), str(t)) not in have_rels
    ]
    return terms, lines


def _line_iris(seed: dict, trip: tuple[str, str, str]) -> tuple[str, str, str] | None:
    """種の関係 ``(from, rel, to)`` → ``(主語の IRI, 述語, 目的語の IRI)``。"""
    f, rel, t = trip
    kind = (seed.get("kinds") or {}).get(f)
    if kind not in ("class", "property"):
        return None
    try:
        pred = themes.relation_predicate(rel, kind)
        if ":" in t:
            ref = themes.std_by_curie(t)
            if ref is None:
                return None
            return sv.term_iri(f), pred, ref.iri
        return sv.term_iri(f), pred, sv.term_iri(t)
    except (ValueError, themes.ThemeError):
        return None


def sky_removals(seed: dict | None, draft: dict) -> dict:
    """``proposed[].removals`` — ``{"terms": [IRI], "lines": [{from, to, relation}]}``。"""
    out: dict = {"terms": [], "lines": []}
    if not seed:
        return out
    terms, lines = seed_gone(seed, draft)
    out["terms"] = [sv.term_iri(s) for s in terms]
    for trip in lines:
        iris = _line_iris(seed, trip)
        if iris is not None:
            out["lines"].append({"from": iris[0], "to": iris[2], "relation": iris[1]})
    return out


_KEPT = {
    "ja": {
        "question": "問いが使っています",
        "linked": "ほかの関係が残っています",
        "failed": "外せませんでした",
    },
    "en": {
        "question": "a question uses it",
        "linked": "other relations still use it",
        "failed": "could not be removed",
    },
}


def _question_users(root: Any) -> set[str]:
    """問いが使っている用語の slug（テーマの ``selections`` と宣言ツールの ``for_terms``）。"""
    used: set[str] = set()
    data = themes.load_themes_file(root)
    for t in (data or {}).get("themes") or []:
        for sel in (t.get("selections") or {}).values():
            if isinstance(sel, dict):
                for k, v in sel.items():
                    if k.endswith("_slug") and isinstance(v, str):
                        used.add(v)
    try:
        for tool in sv._read_cq_tools(root):
            for iri in tool.get("for_terms") or []:
                slug = sv.slug_of(str(iri))
                if slug:
                    used.add(slug)
    except sv.TermError:
        pass
    return used


async def apply_removals(
    client: Any, root: Any, theme_id: str, seed: dict, draft: dict, *, lang: str, at: str
) -> tuple[dict, list[dict]]:
    """✓ のあと、種にあって下書きに無いものをストアから外す → ``(removed, kept)``。

    ``removed`` = ``{"lines": n, "terms": n}``。問いが使っている用語は用語も関係も残して
    ``kept`` に、ほかの関係が残っている用語も ``kept``（外せた関係は外したまま）。
    テーマの記録も合わせる。"""
    lg = "en" if lang.startswith("en") else "ja"
    msg = _KEPT[lg]
    removed = {"lines": 0, "terms": 0}
    kept: list[dict] = []
    gone_terms, gone_lines = seed_gone(seed, draft)
    if not gone_terms and not gone_lines:
        return removed, kept
    blocked = _question_users(root) & set(gone_terms)
    for slug in gone_terms:
        if slug in blocked:
            kept.append({"slug": slug, "reason": msg["question"]})
    try:
        have = {
            (ln["source"], ln["relation"], ln["target"])
            for ln in await crosswalk_runtime.list_alignments(client)
        }
    except Exception:
        logger.warning("chat seed: alignment read failed", exc_info=True)
        have = set()
    gone_records: set[tuple[str, str, str]] = set()
    for trip in gone_lines:
        if trip[0] in blocked or trip[2] in blocked:
            continue
        iris = _line_iris(seed, trip)
        if iris is None or iris not in have:
            continue
        try:
            await crosswalk_runtime.remove_alignment(client, iris[0], iris[2], iris[1])
        except Exception:
            logger.warning("chat seed: remove_alignment failed", exc_info=True)
            continue
        removed["lines"] += 1
        gone_records.add(iris)
    gone_slugs: set[str] = set()
    for slug in gone_terms:
        if slug in blocked:
            continue
        try:
            await sv.remove_term(client, root, slug)
        except sv.TermNotFound:
            gone_slugs.add(slug)
        except sv.TermHasLines:
            kept.append({"slug": slug, "reason": msg["linked"]})
        except Exception:
            logger.warning("chat seed: remove_term failed", exc_info=True)
            kept.append({"slug": slug, "reason": msg["failed"]})
        else:
            removed["terms"] += 1
            gone_slugs.add(slug)
    if gone_records or gone_slugs:
        data = themes.load_themes_file(root)
        if data is not None:
            for t in data["themes"]:
                if t.get("id") != theme_id:
                    continue
                t["relations"] = [
                    r
                    for r in t.get("relations") or []
                    if (str(r.get("from")), str(r.get("relation")), str(r.get("to")))
                    not in gone_records
                ]
                for k in ("uses", "minted"):
                    if k in t:
                        t[k] = [s for s in t[k] if s not in gone_slugs]
            themes._save_themes(root, data)
    if removed["lines"]:
        try:
            await sv.recompute_wired(client, root, at=at)
        except Exception:
            logger.warning("chat seed: recompute_wired failed", exc_info=True)
    return removed, kept
