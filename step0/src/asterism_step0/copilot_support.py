"""GitHub Copilot（公式 SDK ``github-copilot-sdk``）まわりの共通部品。

LLM クライアント（:class:`asterism_step0.llm.CopilotLLMClient`）と demo-agent の Ask
ループの両方が使う。置き場所を step0 にしたのは、デスクトップ同梱では api / demo-agent /
step0 が同じ site-packages に入るため。

方針（ADR llm-multi-provider の「Copilot サブスク」節）:

* 利用者の手元の ``copilot`` CLI のログイン（``gh`` のログインも可）を使う公式経路。
* **SDK の CLI 自動ダウンロードは使わない**（``COPILOT_SKIP_CLI_DOWNLOAD=1``）。
  見つからなければ ``copilot_cli_missing`` で止める。
* Copilot 内蔵のツール（シェル・ファイル操作）は一切使わせない。呼び出し側は
  ``available_tools`` に自前ツール名だけを渡し、権限要求は全拒否する（:func:`deny_all`）。
* SDK は遅延 import。このモジュール自体は SDK 無しで import できる。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from asterism_step0.llm import (
    CopilotCliMissingError,
    CopilotNotSignedInError,
    CopilotTimeoutError,
    LLMCancelledError,
    LLMUsage,
)

T = TypeVar("T")

PROVIDER_ID = "copilot-subscription"
DEFAULT_COPILOT_MODEL = "auto"

# CLI の起動＋認証確認 / モデル一覧の上限（秒）。環境変数で上書きできる。
START_TIMEOUT_ENV = "ASTERISM_COPILOT_START_TIMEOUT_S"
LIST_TIMEOUT_ENV = "ASTERISM_COPILOT_LIST_TIMEOUT_S"
DEFAULT_START_TIMEOUT_S = 60.0
DEFAULT_LIST_TIMEOUT_S = 30.0
_STOP_TIMEOUT_S = 10.0
_NO_RESPONSE = (
    "Copilot CLI が応答しません。{what}が {sec:g} 秒以内に終わりませんでした。"
    "CLI を更新・再ログインするか、しばらくしてやり直してください。"
)


def _env_seconds(name: str, default: float) -> float:
    try:
        v = float((os.environ.get(name) or "").strip())
    except ValueError:
        return default
    return v if v > 0 else default


def start_timeout_s() -> float:
    return _env_seconds(START_TIMEOUT_ENV, DEFAULT_START_TIMEOUT_S)


def list_timeout_s() -> float:
    return _env_seconds(LIST_TIMEOUT_ENV, DEFAULT_LIST_TIMEOUT_S)


def _extra_bin_dirs() -> list[str]:
    """Finder 起動のアプリは PATH が貧弱なので、CLI / gh が入りがちな場所を足す。"""
    home = Path.home()
    dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(home / ".npm-global" / "bin"),
        str(home / ".local" / "bin"),
        str(home / ".volta" / "bin"),
        str(home / ".bun" / "bin"),
    ]
    npm = shutil.which("npm", path=os.pathsep.join([os.environ.get("PATH", ""), *dirs]))
    if npm:
        try:
            out = subprocess.run(
                [npm, "prefix", "-g"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out:
            dirs.append(os.path.join(out, "bin"))
    return dirs


def search_path() -> str:
    """現在の PATH に追加の探索先を足した PATH 文字列（重複は除く）。"""
    seen: list[str] = []
    for d in [*os.environ.get("PATH", "").split(os.pathsep), *_extra_bin_dirs()]:
        if d and d not in seen:
            seen.append(d)
    return os.pathsep.join(seen)


def find_copilot_cli() -> str | None:
    """``copilot`` CLI の絶対パス。``ASTERISM_COPILOT_CLI`` があればそれを最優先。"""
    explicit = (os.environ.get("ASTERISM_COPILOT_CLI") or "").strip()
    if explicit:
        p = os.path.expanduser(explicit)
        return p if os.path.isfile(p) and os.access(p, os.X_OK) else None
    return shutil.which("copilot", path=search_path())


def resolve_cli() -> str:
    """CLI のパスを返す。無ければ :class:`CopilotCliMissingError`。"""
    path = find_copilot_cli()
    if not path:
        raise CopilotCliMissingError(
            "GitHub Copilot CLI (`copilot`) が見つかりません。インストールしてサインインするか、"
            "ASTERISM_COPILOT_CLI に実行ファイルのパスを指定してください。"
        )
    return path


def child_env(cli_path: str | None = None) -> dict[str, str]:
    """CLI 子プロセスに渡す環境変数（PATH 補強・自動ダウンロード禁止）。"""
    env = dict(os.environ)
    path = search_path()
    if cli_path:
        cli_dir = os.path.dirname(cli_path)
        if cli_dir and cli_dir not in path.split(os.pathsep):
            path = os.pathsep.join([cli_dir, path])
    env["PATH"] = path
    env["COPILOT_SKIP_CLI_DOWNLOAD"] = "1"
    return env


async def _wait_bounded(
    aw: Awaitable[T],
    *,
    timeout: float,
    what: str,
    should_cancel: Callable[[], bool] | None = None,
) -> T:
    """``aw`` を上限つきで待つ。0.5 秒ごとに ``should_cancel`` も見る。"""
    task = asyncio.ensure_future(aw)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.5)
            if done:
                return task.result()
            if should_cancel and should_cancel():
                raise LLMCancelledError("cancelled")
            if loop.time() >= deadline:
                raise CopilotTimeoutError(_NO_RESPONSE.format(what=what, sec=timeout))
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


@asynccontextmanager
async def copilot_client(
    should_cancel: Callable[[], bool] | None = None,
) -> AsyncIterator[Any]:
    """起動・ログイン確認済みの ``CopilotClient``。抜けると停止する。

    起動＋認証確認は :func:`start_timeout_s` 秒で打ち切る（超えたら子プロセスを止めて
    :class:`CopilotTimeoutError`）。待ちの間も ``should_cancel`` が効く。
    """
    from copilot import CopilotClient, RuntimeConnection  # 遅延 import

    path = resolve_cli()
    client = CopilotClient(
        connection=RuntimeConnection.for_stdio(path=path),
        log_level="error",
        env=child_env(path),
    )

    async def boot() -> None:
        await client.start()
        auth = await client.get_auth_status()
        if not getattr(auth, "isAuthenticated", False):
            raise CopilotNotSignedInError(
                "GitHub Copilot にサインインしていません。ターミナルで `copilot` を起動して "
                "/login するか、`gh auth login` を実行してください。"
            )

    try:
        await _wait_bounded(
            boot(),
            timeout=start_timeout_s(),
            what="起動とサインイン確認",
            should_cancel=should_cancel,
        )
        yield client
    finally:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(client.stop(), timeout=_STOP_TIMEOUT_S)


def deny_all(request: Any = None, invocation: Any = None) -> Any:
    """Copilot 内蔵ツールの権限要求をすべて拒否する。

    自前ツール（``Tool`` の handler）は SDK が権限確認をしないので影響しない。
    """
    from copilot.generated.rpc import PermissionDecisionReject

    return PermissionDecisionReject(feedback="Asterism does not allow built-in tools.")


def is_usage_event(event: Any) -> bool:
    t = getattr(event, "type", None)
    return getattr(t, "value", t) == "assistant.usage"


def usage_from_event(event: Any) -> LLMUsage:
    """``assistant.usage`` イベントを :class:`LLMUsage` に写す。"""
    d = getattr(event, "data", None)
    return LLMUsage(
        input_tokens=getattr(d, "input_tokens", 0) or 0,
        output_tokens=getattr(d, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(d, "cache_read_tokens", 0) or 0,
        cache_write_tokens=getattr(d, "cache_write_tokens", 0) or 0,
    )


class UsageCollector:
    """``session.on(collector)`` で渡すとモデル呼び出しごとの使用量を合算する。"""

    def __init__(self) -> None:
        self.total = LLMUsage.zero()
        self.model: str | None = None

    def __call__(self, event: Any) -> None:
        if is_usage_event(event):
            self.total = self.total + usage_from_event(event)
            m = getattr(getattr(event, "data", None), "model", None)
            if m:
                self.model = str(m)


def run_sync(factory: Callable[[], Awaitable[T]], *, timeout: float | None = None) -> T:
    """コルーチンを同期で実行する。

    ``timeout`` を渡すと、その秒数で専用スレッドを待つのを諦めて
    :class:`CopilotTimeoutError` を投げる（スレッドは daemon なので残っても終了を妨げない）。

    呼び出し元に既にイベントループがあっても壊れないよう、常に専用スレッド上の
    新しいループで回す（FastAPI の async ハンドラから同期クライアントを呼ぶ場合など）。
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(factory())  # type: ignore[arg-type]
        except BaseException as exc:  # 呼び出し元スレッドへ運ぶ
            box["error"] = exc

    th = threading.Thread(target=target, name="copilot-sync", daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise CopilotTimeoutError(_NO_RESPONSE.format(what="処理", sec=timeout or 0))
    if "error" in box:
        raise box["error"]
    return box["value"]  # type: ignore[no-any-return]


def list_models_sync() -> list[dict[str, str]]:
    """利用できるモデル一覧 ``[{"id", "display_name"}]``。"""

    async def go() -> list[dict[str, str]]:
        async with copilot_client() as client:
            models = await _wait_bounded(
                client.list_models(), timeout=list_timeout_s(), what="モデル一覧の取得"
            )
            out: list[dict[str, str]] = []
            for m in models:
                mid = getattr(m, "id", None)
                if mid:
                    name = getattr(m, "name", None) or mid
                    out.append({"id": str(mid), "display_name": str(name)})
            return out

    # 内側の上限（起動＋一覧＋停止）に少し余裕を足した外枠。
    return run_sync(go, timeout=start_timeout_s() + list_timeout_s() + 2 * _STOP_TIMEOUT_S)


__all__ = [
    "DEFAULT_COPILOT_MODEL",
    "PROVIDER_ID",
    "UsageCollector",
    "child_env",
    "copilot_client",
    "deny_all",
    "find_copilot_cli",
    "list_models_sync",
    "resolve_cli",
    "run_sync",
    "search_path",
    "usage_from_event",
]
