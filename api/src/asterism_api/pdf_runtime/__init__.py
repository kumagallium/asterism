"""デスクトップ版の「PDF を読み取る部品」を、あとから入れて動かす管理クラス。

設計は ``docs/architecture/desktop-pdf-runtime.md``（ADR）。要点:

* 部品 = Docling の実行環境（venv）+ モデル。データの保存先とは別の場所（D1）に置く。
* 入れ方は、アプリの python で venv を作り、版とハッシュを固定した一覧を
  ``pip install --require-hashes --only-binary :all:`` で入れる（D2）。
* モデルはコミットで固定して取得し、``HF_HUB_OFFLINE=1`` で初期化できることを確かめる（D3）。
* 動かすのは既存の ``infra/docling-sidecar/app.py``（無改修）を子プロセスで（D4）。
* ``asterism-local`` だけが使う。サーバ版（``asterism-api``）は import もしない（D5）。

実際の子プロセスの起動は ``spawn`` で差し替えられる（単体テストは取得をしない）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
# PyPI に wheel が無い依存（antlr4）を、ハッシュを確かめた sdist から作って同梱している。
WHEELS_DIR = PACKAGE_DIR / "wheels"

# 入れるのに要る空き容量（ADR D2）。これ未満なら始めずに failed。
MIN_FREE_BYTES = 3 * 1024**3
# bytes_total は目安（実測: 実行環境 1.2GB + モデル 0.5GB = 約 1.7GB）。
BYTES_TOTAL_ESTIMATE = 1_700_000_000
# bytes_done（入れている最中のフォルダの大きさ）を測る間隔。API の呼び出しごとには測らない。
MEASURE_INTERVAL_S = 3.0
# 目印(自分が作ったものだけを消す・使う。runtime.json の kind と、.partial の目印ファイル)。
MARKER_KIND = "asterism-pdf-runtime"
ROOT_MARKER = "runtime.json"
PARTIAL_MARKER = ".asterism-pdf-runtime-partial"
# サイドカーのログがこれを超えていたら、起こすときに空にしてから追記する。
SIDECAR_LOG_MAX_BYTES = 5 * 1024 * 1024
# 子プロセスに渡す環境(許可リスト)。親の ASTERISM_*・HF_TOKEN・PIP_*・各種キーは渡さない。
ENV_ALLOW = frozenset(
    {
        "PATH", "HOME", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy",
    }
)
# 起動時／入れ終わった直後のサイドカーの /health を待つ上限。
SIDECAR_READY_TIMEOUT_S = 60.0

STATES = ("unsupported", "external", "absent", "installing", "starting", "ready", "failed")
PHASES = ("packages", "models", "starting")

# spawn(argv, env, log_path) -> Popen 互換（poll/wait/terminate/kill/pid）
Spawn = Callable[[list[str], dict[str, str], Path], Any]


class _Cancelled(Exception):
    """利用者が中止した(失敗ではない)。"""


class _Failed(Exception):
    """人が読める短い文つきの失敗。"""

    def __init__(self, message: str, log: Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.log = log  # failed のとき見せるログ。None なら呼んだ側の既定


def platform_key(system: str | None = None, machine: str | None = None) -> str | None:
    """依存一覧があるプラットフォームの鍵。無ければ None(= unsupported)。"""
    system = system if system is not None else sys.platform
    machine = machine if machine is not None else platform.machine()
    if system == "darwin" and machine.lower() in ("arm64", "aarch64"):
        return "macos-arm64"
    return None


def default_runtime_root(env: dict[str, str] | None = None) -> Path:
    """ADR D1: env ASTERISM_PDF_RUNTIME_DIR > OS 既定。"""
    env = os.environ if env is None else env
    override = (env.get("ASTERISM_PDF_RUNTIME_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "com.kumagallium.asterism"
            / "pdf-runtime"
        )
    xdg = (env.get("XDG_DATA_HOME") or "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "asterism-pdf-runtime"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _default_spawn(argv: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_file:
        return subprocess.Popen(
            argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )


def _terminate(process: Any) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _dir_size(path: Path) -> int:
    """フォルダの大きさ(symlink は辿らない＝モデルの blobs を二重に数えない)。"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            with contextlib.suppress(OSError):
                st = os.lstat(os.path.join(dirpath, name))
                if not stat.S_ISLNK(st.st_mode):
                    total += st.st_size
    return total


def _wait_health(
    url: str, process: Any, timeout_s: float, cancelled: Callable[[], bool] | None = None
) -> bool:
    """子の ``/health`` を待つ(local.wait_demo_agent_ready と同じ形)。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled():
            return False
        if process.poll() is not None:
            return False
        try:
            if httpx.get(url + "/health", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False



def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _read_marker_json(path: Path) -> bool:
    try:
        data = json.loads((path / ROOT_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("kind") == MARKER_KIND


def _remove_marked(path: Path, markers: tuple[str, ...]) -> None:
    """中身を消してから、目印を最後に消す(途中で失敗しても目印が残り、次に消し直せる)。"""
    errors: list[str] = []
    for entry in os.scandir(path):
        if entry.name in markers:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        except OSError as exc:
            errors.append(f"{entry.path}: {exc}")
    if errors:
        raise OSError("; ".join(errors[:3]))
    for name in markers:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path / name)
    os.rmdir(path)


class _Job:
    """1 回の操作(入れる／起動する／掃除する)。中止の合図と子プロセスを自分で持つ。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind  # install | start | cleanup
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.child: Any = None

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class PdfRuntime:
    """部品の状態・取得・サイドカーの起動を持つ。

    操作(入れる・消す・起動時の処理)は ``_op_lock`` で直列。状態は ``_lock`` で守る。
    自分が作ったもの(目印つき)だけを使い・消す。目印の無い置き場所には触らない。
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        sidecar_dir: Path | None = None,
        base_python: str | None = None,
        log_dir: Path | None = None,
        external_url: str | None = None,
        on_url: Callable[[str | None], None] | None = None,
        spawn: Spawn | None = None,
        env: dict[str, str] | None = None,
        requirements: Path | None = None,
        models_json: Path | None = None,
        disk_free: Callable[[Path], int] | None = None,
        wait_health: Callable[..., bool] | None = None,
        port_factory: Callable[[], int] | None = None,
        ready_timeout_s: float = SIDECAR_READY_TIMEOUT_S,
        measure_interval_s: float = MEASURE_INTERVAL_S,
        cleanup_wait_s: float = 60.0,
    ) -> None:
        self._env = dict(os.environ if env is None else env)
        # `.` や `/` のように名前を持たない値でも、生成は例外を投げない(unsupported になる)。
        self.root: Path | None
        self.partial: Path | None
        try:
            self.root = Path(root) if root is not None else default_runtime_root(self._env)
            self.partial = self.root.with_name(self.root.name + ".partial")
        except (ValueError, RuntimeError, OSError):
            self.root = self.partial = None
        self.sidecar_dir = sidecar_dir
        self.base_python = base_python or sys.executable
        if log_dir is not None:
            self.log_dir = Path(log_dir)
        elif self.root is not None:
            self.log_dir = self.root.parent / "pdf-runtime-logs"
        else:
            self.log_dir = Path(tempfile.gettempdir()) / "asterism-pdf-runtime-logs"
        self.install_log = self.log_dir / "pdf-runtime-install.log"
        self.sidecar_log = self.log_dir / "pdf-runtime-sidecar.log"
        self.external_url = external_url
        self._on_url = on_url
        self._spawn = spawn or _default_spawn
        key = platform_key()
        if requirements is None and key is not None:
            requirements = PACKAGE_DIR / f"requirements-{key}.txt"
        self.requirements = requirements if requirements and requirements.is_file() else None
        self.models_json = models_json or PACKAGE_DIR / "models.json"
        self._disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)
        self._wait_health = wait_health or _wait_health
        self._port_factory = port_factory or _free_port
        self._ready_timeout_s = ready_timeout_s
        self._measure_interval_s = measure_interval_s
        self._cleanup_wait_s = cleanup_wait_s

        self._op_lock = threading.Lock()  # 入れる・消す・起動時の処理を直列にする
        self._lock = threading.RLock()  # 状態
        self._state = "absent"
        self._phase: str | None = None
        self._error: str | None = None
        self._log_path: Path | None = None
        self._bytes_done = 0
        self._job: _Job | None = None
        self._sidecar: Any = None
        self._url: str | None = None

    # ------------------------------------------------------------------ 状態

    def _static_state(self) -> str | None:
        if self.external_url:
            return "external"
        if self.root is None or self.partial is None:
            return "unsupported"
        if self.requirements is None or self.sidecar_dir is None:
            return "unsupported"
        if not (self.sidecar_dir / "app.py").is_file():
            return "unsupported"
        return None

    def venv_python(self) -> Path:
        assert self.root is not None
        return self.root / "venv" / "bin" / "python"

    def _root_is_ours(self) -> bool:
        return (
            self.root is not None and _is_real_dir(self.root) and _read_marker_json(self.root)
        )

    def _partial_is_ours(self) -> bool:
        p = self.partial
        if p is None or not _is_real_dir(p):
            return False
        if (p / PARTIAL_MARKER).is_file() or _read_marker_json(p):
            return True
        return not any(p.iterdir())  # 空のフォルダ(目印を書く前に落ちた)は消してよい

    def _foreign(self) -> Path | None:
        """目印の無い root／.partial が在れば、そのパス。"""
        assert self.root is not None and self.partial is not None
        if _lexists(self.root) and not self._root_is_ours():
            return self.root
        if _lexists(self.partial) and not self._partial_is_ours():
            return self.partial
        return None

    def status(self) -> dict[str, Any]:
        with self._lock:
            static = self._static_state()
            if static is not None:
                state, phase, error, log = static, None, None, None
            else:
                state, phase, error = self._state, self._phase, self._error
                log = self._log_path
            return {
                "state": state,
                "phase": phase if state == "installing" else None,
                "bytes_done": self._bytes_done if state == "installing" else 0,
                "bytes_total": BYTES_TOTAL_ESTIMATE,
                "error": error if state == "failed" else None,
                "log_path": str(log) if state == "failed" and log is not None else None,
                "installed": static is None and self._root_is_ours(),
            }

    def _set(self, **kw: Any) -> None:
        with self._lock:
            for key, value in kw.items():
                setattr(self, "_" + key, value)

    def _jset(self, job: _Job, **kw: Any) -> None:
        """この操作がまだ現役のときだけ状態を書く(古い操作が新しい状態を壊さない)。"""
        with self._lock:
            if self._job is job:
                for key, value in kw.items():
                    setattr(self, "_" + key, value)

    def _set_url(self, url: str | None) -> None:
        self._url = url
        if self._on_url is not None:
            try:
                self._on_url(url)
            except Exception:
                logger.warning("pdf-runtime: on_url callback failed", exc_info=True)

    def join(self, timeout: float | None = None) -> None:
        job = self._job
        if job is not None and job.thread is not None:
            job.thread.join(timeout)

    def _fail_foreign(self, path: Path) -> None:
        self._set(
            state="failed",
            phase=None,
            error=f"置き場所に別のものがあります: {path}",
            log_path=None,
        )

    # ------------------------------------------------------------------ 操作

    def install(self) -> dict[str, Any]:
        """入れ始める。入れている最中なら同じ状態を返すだけ。"""
        with self._op_lock:
            if self._static_state() is not None:
                return self.status()
            job = self._job
            if job is not None and job.alive():
                if job.kind != "cleanup":
                    return self.status()
                assert job.thread is not None
                job.thread.join(self._cleanup_wait_s)  # 掃除の終わりを待ってから入れ始める
                if job.alive():
                    return self.status()
            if self._state in ("ready", "starting", "installing"):
                return self.status()
            foreign = self._foreign()
            if foreign is not None:
                self._fail_foreign(foreign)
                return self.status()
            # 入っているのに起動できなかった(failed)なら、取得し直さず起動だけやり直す。
            self._begin("start" if self._root_is_ours() else "install")
            return self.status()

    def startup(self) -> None:
        """起動時の処理。すぐ返る(残骸の掃除とサイドカーの起動は別スレッド)。"""
        with self._op_lock:
            if self._static_state() is not None:
                return
            job = self._job
            if job is not None and job.alive():
                return
            foreign = self._foreign()
            if foreign is not None:
                self._fail_foreign(foreign)
                return
            if self._root_is_ours():
                self._begin("start")
            elif self.partial is not None and _lexists(self.partial):
                self._begin("cleanup")

    def remove(self) -> dict[str, Any]:
        """入れている最中なら中止、入っていれば止めて消す。最後は absent。"""
        with self._op_lock:
            if self._static_state() is not None:
                return self.status()
            assert self.root is not None and self.partial is not None
            job = self._job
            if job is not None:
                job.cancel.set()
                if job.child is not None:
                    _terminate(job.child)
                if job.thread is not None:
                    job.thread.join(30)
            self._stop_sidecar()
            with self._lock:
                self._job = None
            foreign = self._foreign()
            if foreign is not None:
                self._fail_foreign(foreign)
                return self.status()
            try:
                if _lexists(self.partial):
                    _remove_marked(self.partial, (PARTIAL_MARKER, ROOT_MARKER))
                if _lexists(self.root):
                    _remove_marked(self.root, (PARTIAL_MARKER, ROOT_MARKER))
            except OSError as exc:
                logger.warning("pdf-runtime: remove failed: %s", exc)
                self._set(
                    state="failed",
                    phase=None,
                    error=f"部品を消せませんでした。もう一度消してみてください: {self.root}",
                    log_path=None,
                )
                return self.status()
            self._set(state="absent", phase=None, error=None, log_path=None, bytes_done=0)
            return self.status()

    def shutdown(self) -> None:
        """終了時: 子(pip・モデル取得・サイドカー)にすぐ terminate を送り、2 秒ほどで切り上げる。

        ``.partial`` は次回起動で消える。操作のロックは取らない(終了を待たせない)。
        """
        job = self._job
        procs: list[Any] = []
        if job is not None:
            job.cancel.set()
            if job.child is not None:
                procs.append(job.child)
        with self._lock:
            sidecar, self._sidecar = self._sidecar, None
            if self._url is not None:
                self._set_url(None)
        if sidecar is not None:
            procs.append(sidecar)
        for p in procs:
            with contextlib.suppress(Exception):
                p.terminate()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(p.poll() is None for p in procs):
            time.sleep(0.05)
        for p in procs:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
        if job is not None and job.thread is not None:
            job.thread.join(0.3)

    def _stop_sidecar(self) -> None:
        with self._lock:
            sidecar, self._sidecar = self._sidecar, None
            had_url = self._url is not None
            if had_url:
                self._set_url(None)
        if sidecar is not None:
            _terminate(sidecar)

    # ------------------------------------------------------------------ ジョブ

    def _begin(self, kind: str) -> None:
        job = _Job(kind)
        with self._lock:
            self._job = job
            self._error = None
            self._log_path = None
            self._bytes_done = 0
            if kind == "install":
                self._state, self._phase = "installing", "packages"
            elif kind == "start":
                self._state, self._phase = "starting", None
            else:
                self._state, self._phase = "absent", None
        target = {
            "install": self._job_install,
            "start": self._job_start,
            "cleanup": self._job_cleanup,
        }[kind]
        job.thread = threading.Thread(
            target=target, args=(job,), name=f"pdf-runtime-{kind}", daemon=True
        )
        job.thread.start()

    @staticmethod
    def _check_cancel(job: _Job) -> None:
        if job.cancel.is_set():
            raise _Cancelled

    def _remove_partial_quiet(self) -> None:
        """自分の .partial だけを消す(失敗は握りつぶす=次回起動で消し直す)。"""
        if self.partial is not None and _lexists(self.partial) and self._partial_is_ours():
            try:
                _remove_marked(self.partial, (PARTIAL_MARKER, ROOT_MARKER))
            except OSError:
                logger.warning("pdf-runtime: could not remove %s", self.partial)

    def _job_cleanup(self, job: _Job) -> None:
        self._remove_partial_quiet()

    def _job_start(self, job: _Job) -> None:
        try:
            self._remove_partial_quiet()
            if not self._root_is_ours():
                self._jset(job, state="absent", phase=None)
                return
            self._jset(job, state="starting", phase=None)
            self._ensure_venv(job)
            self._start_sidecar(job)
        except _Cancelled:
            pass
        except _Failed as exc:
            self._jset(
                job,
                state="failed",
                phase=None,
                error=exc.message,
                log_path=exc.log or self.sidecar_log,
            )
        except Exception as exc:
            logger.warning("pdf-runtime: startup failed", exc_info=True)
            self._jset(
                job,
                state="failed",
                phase=None,
                error=f"読み取りの部品を起動できませんでした({exc})。",
                log_path=self.sidecar_log,
            )

    def _ensure_venv(self, job: _Job) -> None:
        """venv の python が動くか確かめ、動かなければ ``venv --upgrade`` で付け直す。

        venv の python はアプリの python への symlink。アプリの更新で同梱の CPython の
        場所が変わる（パスに版が入っている）と、リンクの先が無くなる。``venv --upgrade`` は
        **切れたリンクが残っていると失敗する**（実物で確認: ``[Errno 2] ... venv/bin/python3``）
        ので、切れたものを先に外す。入れたパッケージ（site-packages）はそのまま残る。
        """
        assert self.root is not None
        if self._python_runs():
            return
        bin_dir = self.root / "venv" / "bin"
        for name in ("python", "python3", f"python3.{sys.version_info.minor}"):
            link = bin_dir / name
            if link.is_symlink() and not link.exists():  # 切れたリンクだけを外す
                link.unlink()
        self._run_step(
            job,
            [self.base_python, "-m", "venv", "--upgrade", str(self.root / "venv")],
            "入れた部品の実行環境を付け直せませんでした。",
            env=self._child_env(self.root / "models", offline=False),
            log=self.sidecar_log,
        )
        if not self._python_runs():
            raise _Failed(
                "入れた部品の実行環境が動きません。いったん消して入れ直してください。",
                self.sidecar_log,
            )

    def _python_runs(self) -> bool:
        python = self.venv_python()
        try:
            return (
                python.exists()
                and subprocess.run(
                    [str(python), "-c", "pass"],
                    capture_output=True,
                    timeout=30,
                    check=False,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _job_install(self, job: _Job) -> None:
        try:
            self._install(job)
        except _Cancelled:
            self._remove_partial_quiet()
            self._jset(job, state="absent", phase=None)
        except _Failed as exc:
            self._remove_partial_quiet()
            self._jset(
                job,
                state="failed",
                phase=None,
                error=exc.message,
                log_path=exc.log or self.install_log,
            )
        except Exception as exc:
            logger.warning("pdf-runtime: install failed", exc_info=True)
            self._remove_partial_quiet()
            self._jset(
                job,
                state="failed",
                phase=None,
                error=f"入れる途中で失敗しました({exc})。",
                log_path=self.install_log,
            )

    def _install(self, job: _Job) -> None:
        assert self.requirements is not None and self.sidecar_dir is not None
        assert self.root is not None and self.partial is not None
        foreign = self._foreign()
        if foreign is not None:
            raise _Failed(f"置き場所に別のものがあります: {foreign}", None)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        # symlink を解決した先(実際に置かれるボリューム)で見る。
        free = self._disk_free(self.root.parent.resolve())
        if free < MIN_FREE_BYTES:
            raise _Failed(
                f"空き容量が足りません({MIN_FREE_BYTES // 1024**3}GB 以上の空きが必要です。"
                f"いまは約 {free / 1024**3:.1f}GB)。"
            )
        self.install_log.parent.mkdir(parents=True, exist_ok=True)
        self.install_log.write_text("", encoding="utf-8")  # 入れるたびに作り直す
        self._remove_partial_quiet()
        if _lexists(self.partial):
            raise _Failed(f"置き場所に別のものがあります: {self.partial}", None)
        self.partial.mkdir()
        (self.partial / PARTIAL_MARKER).write_text(MARKER_KIND + "\n", encoding="utf-8")
        models_dir = self.partial / "models"
        # pip・取得の一時ファイルも同じボリュームへ(片付けで一緒に消える)
        tmp_dir = self.partial / "tmp"
        models_dir.mkdir()
        tmp_dir.mkdir()
        venv = self.partial / "venv"
        python = venv / "bin" / "python"

        self._jset(job, state="installing", phase="packages")
        self._run_step(
            job, [self.base_python, "-m", "venv", str(venv)], "実行環境を作れませんでした。"
        )
        self._run_step(
            job,
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary",
                ":all:",
                "--no-input",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--find-links",
                str(WHEELS_DIR),
                "-r",
                str(self.requirements),
            ],
            "部品のダウンロード・インストールに失敗しました。ネットワークを確かめてください。",
        )

        self._jset(job, phase="models")
        # 取得はネットワークあり。オフライン指定は持ち込まない。
        self._run_step(
            job,
            [
                str(python),
                str(PACKAGE_DIR / "fetch_models.py"),
                str(self.models_json),
                str(models_dir),
            ],
            "モデルのダウンロードに失敗しました。ネットワークを確かめてください。",
        )
        # 変換時と同じ条件(オフライン)でパイプラインを初期化できるか。足りなければ失敗にする。
        self._run_step(
            job,
            [str(python), str(PACKAGE_DIR / "check_pipeline.py"), str(self.sidecar_dir)],
            "入れたモデルを読み込めませんでした。",
            env=self._child_env(models_dir, offline=True, tmp=tmp_dir),
        )

        self._check_cancel(job)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        self._write_runtime_json()
        self._jset(job, bytes_done=_dir_size(self.partial))
        self._check_cancel(job)
        os.rename(self.partial, self.root)

        self._jset(job, phase="starting")
        self._start_sidecar(job)

    def _write_runtime_json(self) -> None:
        assert self.requirements is not None and self.partial is not None
        requirements_text = self.requirements.read_bytes()
        docling = None
        for line in requirements_text.decode("utf-8").splitlines():
            if line.startswith("docling=="):
                docling = line.split("==", 1)[1].split()[0]
                break
        models = json.loads(self.models_json.read_text(encoding="utf-8"))
        info = {
            "kind": MARKER_KIND,
            "requirements_sha256": hashlib.sha256(requirements_text).hexdigest(),
            "docling": docling,
            "models": [
                {"repo_id": m["repo_id"], "ref": m["ref"], "commit": m["commit"]} for m in models
            ],
            "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        (self.partial / ROOT_MARKER).write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------ 子プロセス

    def _child_env(
        self, models_dir: Path, *, offline: bool, tmp: Path | None = None
    ) -> dict[str, str]:
        """許可リスト方式: 親の ASTERISM_*・HF_TOKEN・HF_HUB_CACHE・PIP_*・各種キーは渡さない。"""
        env = {
            k: v
            for k, v in self._env.items()
            if k in ENV_ALLOW or k.startswith("LC_") or (k == "TMPDIR" and tmp is None)
        }
        if tmp is not None:
            env["TMPDIR"] = str(tmp)
        env["HF_HOME"] = str(models_dir)
        env["PYTHONNOUSERSITE"] = "1"
        env["PIP_NO_INPUT"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        if offline:
            env["HF_HUB_OFFLINE"] = "1"
        return env

    def _run_step(
        self,
        job: _Job,
        argv: list[str],
        failure: str,
        *,
        env: dict[str, str] | None = None,
        log: Path | None = None,
    ) -> None:
        """子を起こして終わるまで待つ。待つあいだ中止を見て、bytes_done を測る。"""
        assert self.partial is not None
        self._check_cancel(job)
        if env is None:
            env = self._child_env(
                self.partial / "models", offline=False, tmp=self.partial / "tmp"
            )
        process = self._spawn(argv, env, log or self.install_log)
        job.child = process
        last_measure = 0.0
        try:
            while process.poll() is None:
                if job.cancel.is_set():
                    _terminate(process)
                    raise _Cancelled
                now = time.monotonic()
                if now - last_measure >= self._measure_interval_s:
                    last_measure = now
                    if self.partial.exists():
                        self._jset(job, bytes_done=_dir_size(self.partial))
                time.sleep(0.1)
        finally:
            job.child = None
        self._check_cancel(job)
        if process.poll() != 0:
            raise _Failed(failure, log)

    def _rotate_sidecar_log(self) -> None:
        with contextlib.suppress(OSError):
            if self.sidecar_log.stat().st_size > SIDECAR_LOG_MAX_BYTES:
                self.sidecar_log.write_text("", encoding="utf-8")

    def _start_sidecar(self, job: _Job) -> None:
        assert self.sidecar_dir is not None and self.root is not None
        port = self._port_factory()
        url = f"http://127.0.0.1:{port}"
        argv = [
            str(self.venv_python()),
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            str(self.sidecar_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        env = self._child_env(self.root / "models", offline=True)
        self._rotate_sidecar_log()
        process = self._spawn(argv, env, self.sidecar_log)
        job.child = process
        try:
            ok = self._wait_health(url, process, self._ready_timeout_s, job.cancel.is_set)
        finally:
            job.child = None
        if job.cancel.is_set():
            _terminate(process)
            raise _Cancelled
        if not ok:
            _terminate(process)
            raise _Failed("読み取りの部品を起動できませんでした。", self.sidecar_log)
        with self._lock:
            if self._job is not job:  # 追い越された
                superseded = True
            else:
                superseded = False
                self._sidecar = process
                self._state, self._phase, self._error = "ready", None, None
                self._set_url(url)
        if superseded:
            _terminate(process)
            raise _Cancelled
        threading.Thread(
            target=self._watch_sidecar, args=(process,), name="pdf-runtime-watch", daemon=True
        ).start()
        logger.info("pdf-runtime: docling sidecar %s (pid %s)", url, getattr(process, "pid", "?"))

    def _watch_sidecar(self, process: Any) -> None:
        """サイドカーが勝手に落ちたら、その場で failed にして URL を外す(自動再起動はしない)。"""
        with contextlib.suppress(Exception):
            process.wait()
        with self._lock:
            if self._sidecar is not process:  # こちらが止めた
                return
            self._sidecar = None
            self._state, self._phase = "failed", None
            self._error = "読み取りの部品が止まりました。"
            self._log_path = self.sidecar_log
            self._set_url(None)
        logger.warning("pdf-runtime: docling sidecar exited unexpectedly (%s)", self.sidecar_log)


__all__ = [
    "BYTES_TOTAL_ESTIMATE",
    "MIN_FREE_BYTES",
    "PdfRuntime",
    "default_runtime_root",
    "platform_key",
]
