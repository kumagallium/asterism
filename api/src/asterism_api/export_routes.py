"""``register_export(app, cfg)`` — the api half of エージェント束の生成
（契約メモ contract_pr_d.md §3、担当 D1-export）。

Mirrors ``cards_routes.py``'s own wiring convention exactly (§0.1): this
module owns its own ``@app.post`` decorator, never touches ``main.py``, and
reads ``app.state.client`` / ``cfg.registry_root`` at request time. The
**integration** step — calling ``register_export(app, cfg)`` inside
``build_app`` — is left to the integrator, per contract.

Same deviation as ``cards_routes.py`` (see its module docstring): the
contract says to import ``main.py``'s ``require_write_auth``, but that
function is a closure defined *inside* ``build_app`` — not importable, and
importing anything *from* ``main.py`` here would create a circular import
once ``main.py`` imports ``register_export`` from this module. Instead,
:func:`_require_write_auth` below reproduces the exact same fail-closed check
(same messages, same constant-time comparison), copied from
``cards_routes._require_write_auth`` per the contract's explicit instruction
to "write the same style".

``POST /api/subjects/export`` is read-only (it runs the same
``run_subject_tool`` dispatch ``POST /api/cards/run`` already exposes
unauthenticated) but gated behind the write token anyway, per contract §3:
building a bundle re-runs every card and walks a 1-hop describe over
possibly many IRIs, so it is throttled like a write instead of left open
like the cheap reads.

契約メモ contract_pr_f4.md §1-6 (このファイルの追記分): 束を作るとき、
呼び出し側が渡した ``cards`` に加えて、appdata の ``cards`` namespace に
保存済みの「足したカード」のうちこの ``subject`` のもの
（``subject_key`` 一致）も自動で合流させる — フロントが列挙し忘れても
束から漏れないようにするための下支え（重複 ``card_id`` は呼び出し側の
ものを優先）。単一ユーザーでない/appdata 未設定なら何も足さない
（hosted は appdata 自体が無い）。
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Any

from asterism import query_tools as query_tools_mod
from asterism import subject_tools
from asterism import subjects as subjects_mod
from asterism.agent_bundle import ExportError, NotShareableError, build_export_bundle
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from asterism_api import appdata
from asterism_api.appdata_cards_routes import CARDS_NAMESPACE

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

#: A card list is small JSON (card_id/tool/params triples) — guard against an
#: oversized body the same way ``appdata``/``cards_routes`` pre-check
#: ``Content-Length`` before buffering it.
_MAX_EXPORT_BODY_BYTES = 200_000
#: A page reasonably has on the order of a handful of cards (§5.3/§5); this
#: is a generous ceiling against a malformed or abusive request, not a UX
#: limit any real page would hit.
_MAX_CARDS = 100
_SHARE_MODES = ("full", "shareable")
_LANGS = ("ja", "en")


class SubjectsExportBody(BaseModel):
    """Body for ``POST /api/subjects/export`` (契約メモ §3)."""

    subject: dict[str, Any]
    cards: list[dict[str, Any]]
    share: str = "full"
    lang: str = "ja"


def _require_write_auth(cfg: Settings):
    """Verbatim copy of ``cards_routes._require_write_auth`` (see the module
    docstring for why this cannot be a literal import of that function)."""

    def _dep(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        token = cfg.api_token
        if not token:
            logger.warning(
                "write route refused: ASTERISM_API_TOKEN is unset "
                "(fail-closed against anonymous writes)"
            )
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、この操作はできません",
            )
        presented: str | None = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer ") :].strip()
        elif x_asterism_token:
            presented = x_asterism_token.strip()
        if not (presented and hmac.compare_digest(presented, token)):
            raise HTTPException(401, "利用許可コードが違います")

    return _dep


def _reject_if_content_length_exceeds(request: Request, limit: int) -> None:
    """Mirrors ``cards_routes``/``main.py``'s appdata pre-check: refuse early
    on an oversized declared ``Content-Length`` instead of buffering it."""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > limit:
        raise HTTPException(413, f"body is {declared} bytes, over the {limit} limit")


_VIEW_LANGS = ("vega-lite", "table", "mermaid")


def _view_field(raw: Any) -> dict[str, Any] | None:
    """CardSpec.view（AI が書いた見せ方・F13）を束まで運ぶ。形が違えば黙って落とす
    （束に壊れた view を入れるより、元のカードだけ入れる方が安全側）。"""
    if not isinstance(raw, dict):
        return None
    lang = raw.get("lang")
    source = raw.get("source_card_id")
    if lang not in _VIEW_LANGS or not isinstance(source, str) or not source:
        return None
    body_key = "text" if lang == "mermaid" else "spec"
    body = raw.get(body_key)
    if body is None:
        return None
    return {"lang": lang, body_key: body, "source_card_id": source, "custom": True}


def _normalize_cards(raw_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not raw_cards:
        raise HTTPException(400, "cards must be a non-empty list")
    if len(raw_cards) > _MAX_CARDS:
        raise HTTPException(400, f"too many cards (max {_MAX_CARDS})")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_cards):
        if not isinstance(raw, dict):
            raise HTTPException(400, f"cards[{i}] must be an object")
        card_id = raw.get("card_id")
        tool = raw.get("tool")
        if not isinstance(card_id, str) or not card_id:
            raise HTTPException(400, f"cards[{i}].card_id is required")
        if not isinstance(tool, str) or not tool:
            raise HTTPException(400, f"cards[{i}].tool is required")
        params = raw.get("params")
        if params is not None and not isinstance(params, dict):
            raise HTTPException(400, f"cards[{i}].params must be an object")
        entry: dict[str, Any] = {"card_id": card_id, "tool": tool, "params": dict(params or {})}
        title = raw.get("title")
        if isinstance(title, str) and title.strip():
            entry["title"] = title.strip()
        view = _view_field(raw.get("view"))
        if view is not None:
            entry["view"] = view
        out.append(entry)
    return out


def _appdata_cards_for_subject(cfg: Settings, subject_key: str) -> list[dict[str, Any]]:
    """appdata の ``cards`` namespace のうち ``subject_key`` が一致する
    「足したカード」を ``{card_id, tool, params}`` の形で返す（契約メモ
    contract_pr_f4.md §1-6）。単一ユーザーでない/appdata 未設定/壊れた
    エントリは黙って除く（材料が壊れているより 1 枚欠けるほうが安全側）。
    """
    if not cfg.single_user or cfg.appdata_root is None:
        return []
    out: list[dict[str, Any]] = []
    for raw in appdata.read_threads(cfg.appdata_root, namespace=CARDS_NAMESPACE):
        if not isinstance(raw, dict) or raw.get("subject_key") != subject_key:
            continue
        card_id = raw.get("card_id")
        tool = raw.get("tool")
        if not isinstance(card_id, str) or not card_id or not isinstance(tool, str) or not tool:
            continue
        params = raw.get("params")
        entry: dict[str, Any] = {"card_id": card_id, "tool": tool, "params": dict(params or {})}
        title = raw.get("title")
        if isinstance(title, str) and title.strip():
            entry["title"] = title.strip()  # 凍結ツールと AGENT.md の見出しに使う
        view = _view_field(raw.get("view"))
        if view is not None:
            entry["view"] = view  # AI が書いた見せ方（F13）も束へ
        out.append(entry)
    return out


def _merge_cards(
    body_cards: list[dict[str, Any]], appdata_cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """``body_cards`` を優先し（同じ ``card_id`` はそちらを残す）、appdata の
    足したカードのうち未出のものだけ末尾に足す。"""
    merged = list(body_cards)
    seen = {c["card_id"] for c in merged}
    for card in appdata_cards:
        if card["card_id"] in seen:
            continue
        merged.append(card)
        seen.add(card["card_id"])
    return merged


def register_export(app: FastAPI, cfg: Settings) -> None:
    """Register ``POST /api/subjects/export`` on ``app``.

    Called once by the integrator, inside ``build_app`` (§0.1) — never
    imported/called anywhere else.
    """

    write_auth = [Depends(_require_write_auth(cfg))]

    @app.post("/api/subjects/export", dependencies=write_auth)
    async def subjects_export(body: SubjectsExportBody, request: Request) -> StreamingResponse:
        _reject_if_content_length_exceeds(request, _MAX_EXPORT_BODY_BYTES)
        client: OxigraphClient = app.state.client
        try:
            subject = subjects_mod.validate_subject_key(body.subject)
        except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if body.share not in _SHARE_MODES:
            raise HTTPException(400, f"share must be one of {_SHARE_MODES}, got {body.share!r}")
        if body.lang not in _LANGS:
            raise HTTPException(400, f"lang must be one of {_LANGS}, got {body.lang!r}")
        cards = _normalize_cards(body.cards)
        subject_key = subjects_mod.subject_key_string(subject)
        cards = _merge_cards(cards, _appdata_cards_for_subject(cfg, subject_key))
        if len(cards) > _MAX_CARDS:
            raise HTTPException(400, f"too many cards (max {_MAX_CARDS})")

        try:
            bundle = await build_export_bundle(
                client,
                cfg.registry_root,
                subject=subject,
                cards=cards,
                share=body.share,
                lang=body.lang,
            )
        except NotShareableError as exc:
            raise HTTPException(409, {"reasons": exc.reasons}) from exc
        except subject_tools.UnknownSubjectToolError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (
            subject_tools.SubjectKindMismatchError,
            subject_tools.SubjectToolError,
            subjects_mod.SetSpecError,
            query_tools_mod.QueryToolError,
            ValueError,
        ) as exc:
            raise HTTPException(400, str(exc)) from exc
        except ExportError as exc:  # pragma: no cover - defensive catch-all
            raise HTTPException(400, str(exc)) from exc

        async def _body_iter() -> Any:
            yield bundle.zip_bytes

        return StreamingResponse(
            _body_iter(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{bundle.filename}"'},
        )
