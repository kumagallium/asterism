"""``/api/pdf-runtime`` — ローカルモード（asterism-local）だけに生える 3 経路（ADR D5）。

GET は読むだけなので認証なし。POST・DELETE は ``build_app`` の ``require_write_auth`` と同じ
書き込みゲート（loopback のクライアントには ``LoopbackTokenInjector`` がトークンを足す）。

よそのサイトのページからの POST・DELETE は、ここではなくローカルモードの入口
（``local.LoopbackOriginGuard``）が全経路まとめて断る。経路ごとの確認は持たない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Header, HTTPException

if TYPE_CHECKING:
    from asterism_api.main import Settings
    from asterism_api.pdf_runtime import PdfRuntime


def create_pdf_runtime_router(runtime: PdfRuntime, cfg: Settings) -> APIRouter:
    from asterism_api.main import _write_credential_ok

    def require_write_auth(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        if not cfg.api_token:
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、この操作はできません",
            )
        if not _write_credential_ok(cfg, authorization, x_asterism_token):
            raise HTTPException(401, "利用許可コードが違います")

    def _refuse_if_fixed(status: dict[str, Any]) -> None:
        if status["state"] == "unsupported":
            raise HTTPException(409, "この環境では PDF を読み取る部品を入れられません")
        if status["state"] == "external":
            raise HTTPException(
                409, "PDF の読み取りは外部のサーバ(ASTERISM_DOCLING_URL) を使う設定です"
            )

    router = APIRouter()

    @router.get("/api/pdf-runtime")
    def get_pdf_runtime() -> dict[str, Any]:
        return runtime.status()

    @router.post("/api/pdf-runtime/install", dependencies=[Depends(require_write_auth)])
    def install_pdf_runtime() -> dict[str, Any]:
        _refuse_if_fixed(runtime.status())
        return runtime.install()

    @router.delete("/api/pdf-runtime", dependencies=[Depends(require_write_auth)])
    def delete_pdf_runtime() -> dict[str, Any]:
        _refuse_if_fixed(runtime.status())
        return runtime.remove()

    return router
