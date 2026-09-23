"""``register_license(app, cfg)`` — ライセンスの書き手 api（契約メモ
contract_pr_d.md §1・担当 D1-license）。

``PUT /api/datasets/{dataset_id}/license`` は ``mie.yaml`` の
``schema_info.license`` を書き換えて既存 ``update_dataset_artifacts``（＝
``_project_description``）を通す — ``metadata.ttl`` の ``dcterms:license``
（正本）は、その投影として一緒に書き直る。公開済み（``promoted``）なら
ストアのメタグラフも書き直す（既存 ``asterism_api.main._project_meta_graph``
を呼ぶ）。読み手は ``asterism.licenses``（正規化・再配布可否の決定論判定）。

§0.1 の型どおり、このモジュール自身が ``@app.put`` を持ち、``main.py`` は
一切書き換えない — 配線（``build_app`` の ``return app`` 直前で
``register_license(app, cfg)`` を呼ぶこと）は統合段の担当が行う（並列段の
テストは ``register_license(app, cfg)`` を自分で呼んでから ``TestClient``
を作る — ``test_class_schema_api.py`` と同じ流儀）。

Deviation from the contract's literal wording (same as ``cards_routes.py`` /
``place_routes.py``): ``main.py`` の ``require_write_auth`` はクロージャで
直接 import できないので、``_require_write_auth(cfg)`` は
``cards_routes.py`` と同じ実装（同じメッセージ・同じ定数時間比較）を写した
もの。一方 ``_project_meta_graph`` は関数（型ではない）なので
``TYPE_CHECKING`` では済まず、モジュール直下で ``asterism_api.main`` を
実 import する — ``main.py`` がこのモジュールを import しない今回は循環
import にならない（統合段で ``build_app`` に配線するときは、
``place_routes.py`` と同じ理由で ``Settings``/``_project_meta_graph`` が
定義された後の遅延 import に切り替えること）。
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Any

import yaml
from asterism import metadata as metadata_mod
from asterism.licenses import normalize_license, redistributable
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from asterism_api import registry
from asterism_api.main import Settings, _project_meta_graph

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

__all__ = ["register_license"]

logger = logging.getLogger(__name__)


class LicenseBody(BaseModel):
    """Body for ``PUT /api/datasets/{dataset_id}/license``."""

    license: str | None = None


def _require_write_auth(cfg: Settings):
    """``cards_routes.py`` の ``_require_write_auth(cfg)`` と同じ実装
    （同じフェイルクローズ・同じメッセージ）— そちらのモジュール docstring
    がこの形にした理由を説明している。"""

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
                "利用許可コード (管理者が設定する API token) が未設定のため、"
                "この操作はできません",
            )
        presented: str | None = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer ") :].strip()
        elif x_asterism_token:
            presented = x_asterism_token.strip()
        if not (presented and hmac.compare_digest(presented, token)):
            raise HTTPException(401, "利用許可コードが違います")

    return _dep


def _mie_document(mie_text: str) -> dict[str, Any]:
    """既存の ``mie.yaml`` を dict として読む。壊れている/未書き込みなら
    空の dict（``_project_description`` が best-effort で扱うのと同じ流儀 —
    書き込み自体を止めない）。"""
    if not mie_text.strip():
        return {}
    try:
        return metadata_mod.parse_mie_yaml(mie_text)
    except (yaml.YAMLError, ValueError):
        logger.warning("license write: existing mie.yaml did not parse (starting fresh)")
        return {}


def register_license(app: FastAPI, cfg: Settings) -> None:
    """Register ``PUT /api/datasets/{dataset_id}/license`` on ``app``.

    Called once by the integrator, inside ``build_app``, right before
    ``return app`` (§0.1) — never imported/called anywhere else.
    """

    write_auth = [Depends(_require_write_auth(cfg))]

    @app.put("/api/datasets/{dataset_id}/license", dependencies=write_auth)
    async def put_dataset_license(dataset_id: str, body: LicenseBody) -> JSONResponse:
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        meta = data["meta"]
        artifacts = dict(data["artifacts"])

        doc = _mie_document(str(artifacts.get("mie.yaml") or ""))
        schema_info = dict(doc.get("schema_info") or {})
        license_value = (body.license or "").strip() or None
        if license_value:
            schema_info["license"] = license_value
        else:
            schema_info.pop("license", None)
        if schema_info:
            doc["schema_info"] = schema_info
        else:
            doc.pop("schema_info", None)
        artifacts["mie.yaml"] = metadata_mod.dump_mie_yaml(doc) if doc else ""

        proposal_md = registry.load_proposal(cfg.registry_root, dataset_id) or ""
        updated = registry.update_dataset_artifacts(
            cfg.registry_root,
            dataset_id,
            artifacts,
            complete=bool(meta.get("complete", True)),
            warnings=list(meta.get("warnings") or []),
            traps=list(meta.get("traps") or []),
            exit_code=int(meta.get("exit_code", 0) or 0),
            proposal_md=proposal_md,
            advisories=list(meta.get("advisories") or []),
        )
        if updated is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")

        if updated.get("promoted"):
            # ADR dataset-description-in-the-store.md §4: 公開中のデータセット
            # は説明の正本がストアなので、書いた metadata.ttl をそこにも反映
            # する。update_dataset_artifacts が書いた「投影後」の内容を
            # ディスクから読み直す（この関数はそれを返さないため）。
            fresh = registry.load_dataset(cfg.registry_root, dataset_id)
            fresh_artifacts = fresh["artifacts"] if fresh else artifacts
            client: OxigraphClient = app.state.client
            await _project_meta_graph(client, dataset_id, fresh_artifacts)

        normalized = normalize_license(license_value)
        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "license": normalized,
                "redistributable": redistributable(normalized),
            }
        )
