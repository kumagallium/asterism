# ruff: noqa: RUF001  — 画面に出す日本語の文の全角括弧（chat_attach.py と同じ）
"""✓ の後の送り込み——ローカルの原本を取り込み元の家へ（ADR ``source-switch-mcp-client.md`` §7）。

利用者の裁定（2026-10-23・③）: **✓ の後・表だけ・既定オン**。

* 呼び手は ``chat_routes._finish_attach`` だけ（promote が成功した ``if commit:`` の中）。
  ``attach_source``・``_persist_source_uploads``・ingest・``resync_rows`` からは呼ばない。
  取り込み元プロトコルの ``add``（書き込みの道具）の呼び手は Asterism 全体でここだけ。
* **ローカル原本の定義は 1 つ**: ``staging.is_local_origin``（``home`` が無い、または
  ``home.server == "local"``）。対象はそのうち表（csv/tsv/xlsx/xls。json は送らない）。
* bytes は ``registry/<id>/source/`` の保存ファイルから読む（📎 と ✓ は別の時で、staging は
  attach で消費済み）。**送る前に sha256 が ``source_origins`` と一致する**ことを確かめる。
* 送った後は ``home`` を ``{server, id, name}`` に書き戻す（home 済み＝``server`` が
  ``"local"`` 以外）。相手が ``duplicate: true`` を返しても id は入る（合流）。
* 失敗しても公開は取り消さない。結果は手番の注記 1 行（``sent_home``／``send_failed``）。
* 設定 ``sendOriginalsToHome``（既定 true）で切れる。家（``sourceHome``）か ``add`` の道具名が
  無ければ何もしない。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from asterism_api import registry, staging
from asterism_api.mcp_client import (
    MAX_SOURCE_FILE_BYTES,
    McpConnectError,
    McpTimeout,
    McpToolError,
)

logger = logging.getLogger("asterism.source_push")

#: 送る原本の拡張子（表だけ。json は Graphium の書き出しを 📎 で添えたときに送り返さないため）。
PUSH_EXTS = (".csv", ".tsv", ".xlsx", ".xls")

_T = {
    "ja": {
        "sent": "「{name}」を {server} にも送りました。",
        "existed": "「{name}」は {server} に既にありました（同じ中身）。",
        "too_big": "「{name}」は大きすぎて {server} に送れませんでした（32 MiB まで）。",
        "mismatch": "「{name}」の中身が記録と違うため {server} に送りませんでした。",
        "failed": "「{name}」を {server} に送れませんでした（{reason}）。",
    },
    "en": {
        "sent": '"{name}" was also stored on {server}.',
        "existed": '"{name}" was already on {server} (same content).',
        "too_big": '"{name}" is too large to send to {server} (32 MiB max).',
        "mismatch": '"{name}" differs from its recorded content, so it was not sent to {server}.',
        "failed": '"{name}" could not be sent to {server} ({reason}).',
    },
}


def _text(lang: str, key: str, **kw: Any) -> str:
    return _T["en" if (lang or "").lower().startswith("en") else "ja"][key].format(**kw)


def _pushable(origin: dict) -> bool:
    name = str(origin.get("name") or "")
    return staging.is_local_origin(origin) and name.lower().endswith(PUSH_EXTS)


async def push_originals_home(
    mcp_registry: Any,
    registry_root: Path,
    dataset_id: str,
    *,
    lang: str,
) -> list[dict]:
    """公開したデータセットのローカル原本を家へ送る。返りは手番に足す注記（0 件なら何もしない）。

    ``mcp_registry`` は ``app.state.mcp_registry``（単一ユーザーでなければ None）。
    """
    if mcp_registry is None or not mcp_registry.send_originals():
        return []
    server = mcp_registry.source_home()
    add_tool = mcp_registry.provider().add
    if not server or not add_tool:
        return []
    meta = registry.load_meta(registry_root, dataset_id) or {}
    origins = [o for o in (meta.get("source_origins") or []) if isinstance(o, dict)]
    targets = [o for o in origins if _pushable(o)]
    if not targets:
        return []
    source_dir = registry.source_dir(registry_root, dataset_id)
    notes: list[dict] = []
    changed = False
    for origin in targets:
        name = str(origin["name"])
        path = (source_dir / name) if source_dir is not None else None
        if path is None or not path.is_file():
            continue  # 保存ファイルが無い（置き換え済み）＝送る物が無い
        if staging.sha256_of(path) != origin.get("sha256"):
            notes.append(_note("send_failed", _text(lang, "mismatch", name=name, server=server)))
            continue
        size = path.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            notes.append(_note("send_failed", _text(lang, "too_big", name=name, server=server)))
            continue
        try:
            cfg = mcp_registry.enabled_server_or_raise(server)
            result = await mcp_registry.client.call_tool(
                cfg,
                add_tool,
                {
                    "bytes_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    "name": name,
                    "mimeType": mimetypes.guess_type(name)[0] or "application/octet-stream",
                },
                timeout_s=mcp_registry.client.get_timeout_s,
            )
            data = result.data if isinstance(result.data, dict) else {}
            new_id = data.get("id")
            if not isinstance(new_id, str) or not new_id:
                raise McpToolError("返事に id がありません")
        except HTTPException as exc:
            notes.append(
                _note(
                    "send_failed",
                    _text(lang, "failed", name=name, server=server, reason=exc.detail),
                )
            )
            continue
        except (McpConnectError, McpTimeout, McpToolError) as exc:
            notes.append(
                _note(
                    "send_failed", _text(lang, "failed", name=name, server=server, reason=str(exc))
                )
            )
            continue
        origin["home"] = {"server": server, "id": new_id, "name": name}
        changed = True
        key = "existed" if data.get("duplicate") is True else "sent"
        notes.append(_note("sent_home", _text(lang, key, name=name, server=server)))
    if changed:
        registry.update_meta_atomic(registry_root, dataset_id, {"source_origins": origins})
    return notes


def _note(kind: str, text: str) -> dict:
    return {"kind": kind, "text": text, "choice": None}
