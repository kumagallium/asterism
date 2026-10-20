"""``register_sky_suggestions(app, cfg, write_auth=…)`` — 公開のあとの「結べそうな線」
（ui-redesign PR 7c・契約 B1・design.md §15.2「育てる」）。

公開（``promote``・名前だけの公開）の後処理が、そのデータセットの種類・項目と共有の用語の**名前の
完全一致**を機械で数えて ``registry/vocab-shared/suggested_lines.json`` に置く
（``asterism.suggested_lines``。AI は呼ばない）。この api はそれを人に見せて、人が決める口。

- ``GET /api/sky/suggestions`` — いま出してよい候補（すでに線がある・人が外した・公開や用語が
  無くなったものは除く）。文に必要な名前（種類・用語・データセット）つき。
- ``POST /api/sky/suggestions/apply`` — ``{items[{subject, term, relation}], batch?}``。
  **いまの候補にあるものだけ**を ``assert_alignment(drafted_by="machine", batch, reason)`` で
  線にする（クライアントの文字列は信用しない）。「N 件をまとめて確定」も「1 件ずつ」もこの口。
- ``POST /api/sky/suggestions/dismiss`` — 外す。却下を保存して次から出さない。

どれも ``write_auth``。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from asterism import crosswalk_runtime, shared_vocab, suggested_lines
from asterism.class_schema import class_labels
from asterism.sky_view import _published_datasets
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = ["register_sky_suggestions"]

logger = logging.getLogger(__name__)

_MAX_ITEMS = 200
_MAX_BATCH = 100


class SuggestionItem(BaseModel):
    subject: str
    term: str
    relation: str


class ApplyBody(BaseModel):
    items: list[SuggestionItem] = Field(max_length=_MAX_ITEMS)
    #: 「まとめて確定」の ID（来歴）。無ければサーバが付ける
    batch: str | None = None

    @field_validator("batch")
    @classmethod
    def _batch_is_one_short_line(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > _MAX_BATCH or any(
            unicodedata.category(c) == "Cc" or c in "\u2028\u2029" for c in v
        ):
            raise ValueError(f"batch は 1 行 {_MAX_BATCH} 字までで、制御文字を含めません")
        return v


class DismissBody(BaseModel):
    items: list[SuggestionItem] = Field(max_length=_MAX_ITEMS)


def register_sky_suggestions(
    app: FastAPI,
    cfg: Settings,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """Register the ``/api/sky/suggestions*`` routes on ``app`` (called once from ``build_app``)."""
    lock = asyncio.Lock()

    def _client() -> OxigraphClient:
        return app.state.client

    def _root() -> Path:
        return Path(cfg.registry_root)

    async def _pending() -> tuple[list[dict], list[shared_vocab.SharedTerm]]:
        rows = suggested_lines.load_suggestions(_root())
        if not rows:
            return [], []
        try:
            terms = await shared_vocab._read_terms(_client())
            lines = await crosswalk_runtime.list_alignments(_client())
        except Exception as exc:
            raise HTTPException(502, f"store read failed: {exc}") from exc
        live = {d["id"] for d in await asyncio.to_thread(_published_datasets, _root())}
        out = suggested_lines.pending(
            rows,
            lines=lines,
            dismissed=shared_vocab.load_dismissed(_root()),
            terms=terms,
            live_datasets=live,
        )
        return out, terms

    @app.get("/api/sky/suggestions", dependencies=list(write_auth))
    async def list_suggestions() -> JSONResponse:
        """いま出してよい「結べそうな線」。``[{subject, subject_label, term, term_label,
        relation, dataset_id, dataset_label, version, at}]``。"""
        rows, terms = await _pending()
        if not rows:
            return JSONResponse({"suggestions": []})
        from asterism.subjects import resolve_dataset_label

        term_label = {t.iri: t.label for t in terms}
        class_iris = {r["subject"] for r in rows if r["relation"] == "equivalentClass"}
        prop_iris = [r["subject"] for r in rows if r["relation"] != "equivalentClass"]
        labels: dict[str, str] = {}
        try:
            if class_iris:
                labels.update(await class_labels(_client(), _root(), class_iris))
            if prop_iris:
                labels.update(await shared_vocab._display_labels(_client(), prop_iris))
        except Exception:  # 名前が引けなくても候補は出す（IRI の最後の区切りで）
            logger.warning("suggestions: labels unavailable", exc_info=True)
        out = [
            {
                **r,
                "subject_label": labels.get(r["subject"]) or r["subject"].rsplit("/", 1)[-1],
                "term_label": term_label.get(r["term"], r["term"]),
                "dataset_label": resolve_dataset_label(_root(), r["dataset_id"]),
            }
            for r in rows
        ]
        return JSONResponse({"suggestions": out})

    @app.post("/api/sky/suggestions/apply", dependencies=list(write_auth))
    async def apply_suggestions(body: ApplyBody) -> JSONResponse:
        """人の「まとめて確定」「1 件ずつ」。いまの候補にあるものだけを線にする。返し
        ``{applied, dropped[{index, reason}], batch}``（``reason`` = ``not_found``〔候補に無い〕・
        ``cycle`` / ``mixed`` / ``invalid``〔閉じた規則で断られた〕）。"""
        batch = (body.batch or "").strip() or f"suggest-{datetime.now(UTC):%Y%m%dT%H%M%S}"
        async with lock:
            rows, terms = await _pending()
            by_key = {(r["subject"], r["term"], r["relation"]): r for r in rows}
            term_label = {t.iri: t.label for t in terms}
            at = datetime.now(UTC).isoformat()
            applied: list[dict] = []
            dropped: list[dict] = []
            for i, it in enumerate(body.items):
                key = (it.subject, it.term, it.relation)
                row = by_key.get(key)
                if row is None:
                    dropped.append({"index": i, "reason": "not_found"})
                    continue
                reason = f"名前の完全一致（{term_label.get(it.term, '')}）"
                try:
                    await crosswalk_runtime.assert_alignment(
                        _client(),
                        it.subject,
                        it.term,
                        it.relation,
                        at=at,
                        drafted_by="machine",
                        batch=batch,
                        reason=reason,
                    )
                    applied.append(
                        {"subject": it.subject, "term": it.term, "relation": it.relation}
                    )
                except ValueError as exc:  # 閉じた規則で断られた（循環・種別 など）
                    msg = str(exc)
                    why = "invalid"
                    if msg == "cycle":
                        why = "cycle"
                    elif msg.startswith("kind mismatch"):
                        why = "mixed"
                    dropped.append({"index": i, "reason": why})
                except Exception as exc:
                    logger.warning("suggestions: alignment write failed", exc_info=True)
                    raise HTTPException(502, f"alignment write failed: {exc}") from exc
            if applied:
                await asyncio.to_thread(suggested_lines.remove_suggestions, _root(), applied)
                try:  # 語の「答えるデータセット数」を数え直す（線が増えたので）
                    await shared_vocab.recompute_wired(_client(), _root())
                except Exception:
                    logger.warning("suggestions: recompute_wired failed", exc_info=True)
        return JSONResponse({"applied": len(applied), "dropped": dropped, "batch": batch})

    @app.post("/api/sky/suggestions/dismiss", dependencies=list(write_auth))
    async def dismiss_suggestions(body: DismissBody) -> JSONResponse:
        """外す。関係なし（この種類にこの用語は無い）と関係つき（この線は結ばない）の両方を
        保存する — 次の候補にも、AI の提案にも出ない。冪等。返し ``{dismissed: n}``（新しく
        保存した行数）。"""
        at = datetime.now(UTC).isoformat()
        added = 0
        async with lock:
            for it in body.items:
                for rel in (None, it.relation):
                    try:
                        if shared_vocab.dismiss_fit(
                            _root(), it.subject, it.term, at=at, relation=rel
                        ):
                            added += 1
                    except ValueError as exc:
                        raise HTTPException(422, str(exc)) from exc
        return JSONResponse({"dismissed": added})
