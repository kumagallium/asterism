"""PDF を読み取る部品の管理（ADR desktop-pdf-runtime.md）。

実際の取得・サイドカーの起動はしない: 子プロセスの起動を差し替え（``FakeSpawn``）、状態の
移り変わりと後始末だけを確かめる。実機での取得は PR の確認項目。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.local import build_local_app, find_docling_sidecar_dir
from asterism_api.main import Settings, build_app
from asterism_api.pdf_runtime import (
    BYTES_TOTAL_ESTIMATE,
    MARKER_KIND,
    PACKAGE_DIR,
    PARTIAL_MARKER,
    PdfRuntime,
    _dir_size,
    default_runtime_root,
    platform_key,
)

REPO = Path(__file__).resolve().parents[2]
REQUIREMENTS = PACKAGE_DIR / "requirements-macos-arm64.txt"
_TOKEN = "pdf-runtime-token"
GB = 1024**3


# ---------------------------------------------------------------------------
# 差し替えの子プロセス


class FakeProc:
    def __init__(self, rc: int | None, pid: int = 4242) -> None:
        self._rc = rc  # None = 止められるまで走り続ける
        self.pid = pid
        self.terminated = False
        self._done = threading.Event()
        if rc is not None:
            self._done.set()

    def poll(self) -> int | None:
        if self.terminated:
            return -15
        return self._rc

    def terminate(self) -> None:
        self.terminated = True
        self._done.set()

    kill = terminate

    def die(self) -> None:
        """勝手に落ちた(こちらが止めたのではない)。"""
        self._rc = 1
        self._done.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return self.poll() or 0


class FakeSpawn:
    """argv から段を見分けて、必要なファイルを作り、FakeProc を返す。"""

    def __init__(self, runtime_box: list[PdfRuntime]) -> None:
        self.box = runtime_box
        self.calls: list[dict[str, Any]] = []
        self.fail_at: str | None = None
        self.hang_at: str | None = None
        self.procs: dict[str, FakeProc] = {}
        self.pip_writes: int = 0  # pip の段で partial に書くバイト数

    def stage(self, argv: list[str]) -> str:
        if argv[1:3] == ["-m", "venv"]:
            return "venv-upgrade" if "--upgrade" in argv else "venv"
        if "pip" in argv[1:3]:
            return "pip"
        if argv[1].endswith("fetch_models.py"):
            return "fetch"
        if argv[1].endswith("check_pipeline.py"):
            return "check"
        if argv[1:3] == ["-m", "uvicorn"]:
            return "sidecar"
        raise AssertionError(f"unexpected argv {argv}")

    def __call__(self, argv: list[str], env: dict[str, str], log: Path) -> FakeProc:
        stage = self.stage(argv)
        rt = self.box[0]
        self.calls.append(
            {"stage": stage, "argv": argv, "env": env, "phase": rt.status()["phase"]}
        )
        if stage in ("venv", "venv-upgrade"):
            python = Path(argv[-1]) / "bin" / "python"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
        if stage == "pip" and self.pip_writes:
            (rt.partial / "venv" / "blob.bin").write_bytes(b"x" * self.pip_writes)
        if stage == "fetch":
            ref = rt.partial / "models" / "hub" / "models--a--b" / "refs"
            ref.mkdir(parents=True, exist_ok=True)
            (ref / "main").write_text("c" * 40)
        if stage == "sidecar" or stage == self.hang_at:
            rc = None  # サイドカーは止められるまで走り続ける
        else:
            rc = 1 if stage == self.fail_at else 0
        proc = FakeProc(rc)
        self.procs[stage] = proc
        return proc

    def stages(self) -> list[str]:
        return [c["stage"] for c in self.calls]


@pytest.fixture
def sidecar_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sidecar"
    d.mkdir()
    (d / "app.py").write_text("# stub\n")
    return d


def make_runtime(
    tmp_path: Path,
    sidecar_dir: Path | None,
    *,
    urls: list[str | None] | None = None,
    health: bool = True,
    free: int = 10 * GB,
    external_url: str | None = None,
    requirements: Path | None = REQUIREMENTS,
    env: dict[str, str] | None = None,
) -> tuple[PdfRuntime, FakeSpawn]:
    box: list[PdfRuntime] = []
    spawn = FakeSpawn(box)
    rt = PdfRuntime(
        root=tmp_path / "home" / "pdf-runtime",
        sidecar_dir=sidecar_dir,
        base_python="/base/python",
        log_dir=tmp_path / "logs",
        external_url=external_url,
        on_url=(urls.append if urls is not None else None),
        spawn=spawn,
        env=env if env is not None else {"PATH": "/usr/bin", "ASTERISM_API_TOKEN": "secret"},
        requirements=requirements,
        disk_free=lambda _p: free,
        wait_health=lambda _url, _proc, _t, _c: health,
        port_factory=lambda: 18765,
        ready_timeout_s=1.0,
        measure_interval_s=0.0,
    )
    box.append(rt)
    return rt, spawn


def wait_for(cond: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


# ---------------------------------------------------------------------------
# 状態の移り変わり


def test_install_goes_through_phases_and_becomes_ready(tmp_path: Path, sidecar_dir: Path) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls)
    assert rt.status()["state"] == "absent"

    status = rt.install()
    assert status["state"] == "installing"
    rt.join(10)

    status = rt.status()
    assert status["state"] == "ready", status
    assert status["error"] is None
    assert spawn.stages() == ["venv", "pip", "fetch", "check", "sidecar"]
    assert [c["phase"] for c in spawn.calls] == [
        "packages",
        "packages",
        "models",
        "models",
        "starting",
    ]
    assert urls == ["http://127.0.0.1:18765"]
    assert not rt.partial.exists()
    assert (rt.root / "venv" / "bin" / "python").is_file()

    info = json.loads((rt.root / "runtime.json").read_text())
    assert info["docling"] == "2.102.1"
    assert len(info["requirements_sha256"]) == 64
    assert {m["repo_id"] for m in info["models"]} == {
        "docling-project/docling-layout-heron",
        "docling-project/docling-models",
    }
    assert info["installed_at"]


def test_install_commands_and_environments(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(
        tmp_path,
        sidecar_dir,
        env={
            "PATH": "/usr/bin",
            "ASTERISM_API_TOKEN": "secret",
            "PYTHONPATH": "/leak",
            "HF_HUB_OFFLINE": "1",
        },
    )
    rt.install()
    rt.join(10)
    by_stage = {c["stage"]: c for c in spawn.calls}

    assert by_stage["venv"]["argv"][:3] == ["/base/python", "-m", "venv"]
    pip = by_stage["pip"]["argv"]
    for flag in ("--require-hashes", "--only-binary", ":all:", "--no-input", "-r"):
        assert flag in pip
    assert str(REQUIREMENTS) in pip
    assert pip[pip.index("--find-links") + 1] == str(PACKAGE_DIR / "wheels")

    fetch_env = by_stage["fetch"]["env"]
    assert "HF_HUB_OFFLINE" not in fetch_env  # 取得はネットワークあり
    assert fetch_env["HF_HOME"] == str(rt.partial / "models")
    assert by_stage["check"]["env"]["HF_HUB_OFFLINE"] == "1"

    sidecar = by_stage["sidecar"]
    assert sidecar["argv"][1:5] == ["-m", "uvicorn", "app:app", "--app-dir"]
    assert sidecar["argv"][5] == str(sidecar_dir)
    assert "127.0.0.1" in sidecar["argv"] and "18765" in sidecar["argv"]
    assert sidecar["env"]["HF_HUB_OFFLINE"] == "1"
    assert sidecar["env"]["HF_HOME"] == str(rt.root / "models")
    for call in spawn.calls:  # 親の python 設定とシークレットは持ち込まない
        assert "PYTHONPATH" not in call["env"]
        assert "ASTERISM_API_TOKEN" not in call["env"]


@pytest.mark.parametrize("stage", ["venv", "pip", "fetch", "check"])
def test_failure_in_any_step_removes_partial_and_reports(
    tmp_path: Path, sidecar_dir: Path, stage: str
) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls)
    spawn.fail_at = stage
    rt.install()
    rt.join(10)

    status = rt.status()
    assert status["state"] == "failed"
    assert status["error"]
    assert status["log_path"] == str(rt.install_log)
    assert not rt.partial.exists()
    assert not rt.root.exists()
    assert urls == []
    assert "sidecar" not in spawn.stages()


def test_sidecar_that_never_answers_after_install_is_failed_but_kept(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls, health=False)
    rt.install()
    rt.join(10)
    assert rt.status()["state"] == "failed"
    assert rt.root.exists()  # 入ってはいる
    assert spawn.procs["sidecar"].terminated
    assert urls == []

    # もう一度 install すると、取得し直さず起動だけやり直す。
    spawn.calls.clear()
    rt._wait_health = lambda _u, _p, _t, _c: True
    rt.install()
    rt.join(10)
    assert spawn.stages() == ["sidecar"]
    assert rt.status()["state"] == "ready"
    assert urls == ["http://127.0.0.1:18765"]


def test_low_disk_space_fails_without_starting(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir, free=2 * GB)
    rt.install()
    rt.join(10)
    status = rt.status()
    assert status["state"] == "failed"
    assert "空き容量" in status["error"]
    assert spawn.calls == []
    assert not rt.partial.exists()


def test_second_install_while_installing_only_returns_status(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.hang_at = "pip"
    rt.install()
    wait_for(lambda: "pip" in spawn.stages())
    again = rt.install()
    assert again["state"] == "installing"
    assert spawn.stages().count("venv") == 1
    rt.remove()
    assert rt.status()["state"] == "absent"


def test_cancel_while_installing_stops_child_and_removes_partial(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.hang_at = "pip"
    rt.install()
    wait_for(lambda: "pip" in spawn.stages())
    assert rt.partial.exists()

    status = rt.remove()
    assert status["state"] == "absent"
    assert spawn.procs["pip"].terminated
    assert not rt.partial.exists()
    assert not rt.root.exists()
    assert "fetch" not in spawn.stages()


def test_bytes_done_follows_the_partial_folder(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.pip_writes = 5000
    spawn.hang_at = "pip"
    rt.install()
    wait_for(lambda: rt.status()["bytes_done"] >= 5000)
    status = rt.status()
    assert status["state"] == "installing"
    assert status["phase"] == "packages"
    assert status["bytes_total"] == BYTES_TOTAL_ESTIMATE
    rt.remove()
    assert rt.status()["bytes_done"] == 0


def test_dir_size_does_not_follow_symlinks(tmp_path: Path) -> None:
    (tmp_path / "blob").write_bytes(b"x" * 100)
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "link").symlink_to(tmp_path / "blob")
    assert _dir_size(tmp_path) == 100  # symlink は数えない（blobs を二重に数えない）


def test_remove_when_ready_stops_sidecar_and_deletes(tmp_path: Path, sidecar_dir: Path) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls)
    rt.install()
    rt.join(10)
    assert rt.status()["state"] == "ready"

    status = rt.remove()
    assert status["state"] == "absent"
    assert spawn.procs["sidecar"].terminated
    assert not rt.root.exists()
    assert urls == ["http://127.0.0.1:18765", None]


def test_remove_after_failure_clears_it(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.fail_at = "pip"
    rt.install()
    rt.join(10)
    assert rt.status()["state"] == "failed"
    assert rt.remove()["state"] == "absent"


def test_startup_removes_stale_partial_and_stays_absent(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    _mark_partial(rt)
    (rt.partial / "venv").mkdir(parents=True)
    (rt.partial / "venv" / "junk").write_text("x")
    rt.startup()
    rt.join(10)
    assert not rt.partial.exists()
    assert rt.status()["state"] == "absent"
    assert spawn.calls == []


def test_startup_with_nothing_installed_does_nothing(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    rt.startup()
    assert rt.status()["state"] == "absent"
    assert spawn.calls == []


def _mark_partial(rt: PdfRuntime) -> None:
    rt.partial.mkdir(parents=True, exist_ok=True)
    (rt.partial / PARTIAL_MARKER).write_text("x")


def _installed(rt: PdfRuntime, *, python_ok: bool = True) -> None:
    python = rt.root / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n" if python_ok else "#!/bin/sh\nexit 3\n")
    python.chmod(0o755)
    (rt.root / "runtime.json").write_text(json.dumps({"kind": MARKER_KIND}))


def test_startup_with_installed_runtime_starts_sidecar_without_fetching(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls)
    _installed(rt)
    _mark_partial(rt)
    rt.startup()
    rt.join(10)
    assert rt.status()["state"] == "ready"
    assert spawn.stages() == ["sidecar"]
    assert not rt.partial.exists()
    assert urls == ["http://127.0.0.1:18765"]
    rt.shutdown()
    assert spawn.procs["sidecar"].terminated
    assert urls[-1] is None


def test_startup_repairs_a_venv_whose_python_does_not_run(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    _installed(rt, python_ok=False)
    rt.startup()
    rt.join(10)
    assert spawn.stages() == ["venv-upgrade", "sidecar"]
    assert spawn.calls[0]["argv"][:4] == ["/base/python", "-m", "venv", "--upgrade"]
    assert rt.status()["state"] == "ready"


def test_startup_repairs_a_venv_whose_python_link_is_dangling(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    """アプリの更新で同梱の python の場所が変わると(パスに版が入っている)、venv の
    python(symlink)の先が無くなる。``venv --upgrade`` は切れたリンクが残っていると
    失敗する(実物で確認: ``[Errno 2] ... venv/bin/python3``)ので、先に外す。
    差し替えの venv も、切れたリンクが残っていれば同じ理由で書けずに落ちる。"""
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    bin_dir = rt.root / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python3").symlink_to(tmp_path / "old-app" / "bin" / "python3")
    (bin_dir / "python").symlink_to("python3")
    (rt.root / "runtime.json").write_text(json.dumps({"kind": MARKER_KIND}))
    rt.startup()
    rt.join(10)
    assert spawn.stages() == ["venv-upgrade", "sidecar"]
    assert not (bin_dir / "python3").is_symlink()  # 切れたリンクは外された
    assert rt.status()["state"] == "ready"


def test_startup_does_not_block_the_caller(tmp_path: Path, sidecar_dir: Path) -> None:
    """サイドカーの起動待ちは別スレッド: startup() は /health を待たずに返る。"""
    gate = threading.Event()
    rt, _spawn = make_runtime(tmp_path, sidecar_dir)
    rt._wait_health = lambda _u, _p, _t, _c: gate.wait(5)
    _installed(rt)
    started = time.monotonic()
    rt.startup()
    assert time.monotonic() - started < 1.0
    status = rt.status()
    assert status["state"] == "starting"  # 入っていて起動中（入れている最中ではない）
    assert status["phase"] is None
    assert status["bytes_done"] == 0
    gate.set()
    rt.join(10)
    assert rt.status()["state"] == "ready"


def test_startup_with_unresponsive_sidecar_is_failed_not_fatal(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls, health=False)
    _installed(rt)
    rt.startup()
    rt.join(10)
    assert rt.status()["state"] == "failed"
    assert spawn.procs["sidecar"].terminated
    assert urls == []


def test_ready_turns_failed_when_the_sidecar_dies(tmp_path: Path, sidecar_dir: Path) -> None:
    urls: list[str | None] = []
    rt, spawn = make_runtime(tmp_path, sidecar_dir, urls=urls)
    rt.install()
    rt.join(10)
    spawn.procs["sidecar"].die()  # 勝手に落ちた。GET を待たずに気づく
    wait_for(lambda: urls[-1] is None)
    status = rt.status()
    assert status["state"] == "failed"
    assert status["log_path"] == str(rt.sidecar_log)


def test_external_url_means_external_and_nothing_happens(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir, external_url="http://docling:8090")
    _installed(rt)
    assert rt.status()["state"] == "external"
    rt.startup()
    assert rt.install()["state"] == "external"
    assert rt.remove()["state"] == "external"
    assert spawn.calls == []
    assert rt.root.exists()


def test_unsupported_without_requirements_or_sidecar(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir, requirements=None)
    # requirements=None は「このプラットフォーム用の一覧が既定で決まる」ので、無い場合を明示する
    rt.requirements = None
    assert rt.status()["state"] == "unsupported"
    assert rt.install()["state"] == "unsupported"
    rt.startup()
    assert spawn.calls == []

    rt2, _ = make_runtime(tmp_path / "b", None)
    assert rt2.status()["state"] == "unsupported"
    empty = tmp_path / "empty"
    empty.mkdir()
    rt3, _ = make_runtime(tmp_path / "c", empty)
    assert rt3.status()["state"] == "unsupported"


# ---------------------------------------------------------------------------
# 場所・プラットフォーム


def test_runtime_root_env_override_wins(tmp_path: Path) -> None:
    assert default_runtime_root({"ASTERISM_PDF_RUNTIME_DIR": str(tmp_path / "x")}) == (
        tmp_path / "x"
    )
    assert default_runtime_root({}).name in ("pdf-runtime", "asterism-pdf-runtime")


def test_runtime_root_linux_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("asterism_api.pdf_runtime.sys.platform", "linux")
    assert default_runtime_root({"XDG_DATA_HOME": str(tmp_path)}) == (
        tmp_path / "asterism-pdf-runtime"
    )


def test_platform_key_only_macos_arm64() -> None:
    assert platform_key("darwin", "arm64") == "macos-arm64"
    assert platform_key("darwin", "x86_64") is None
    assert platform_key("linux", "x86_64") is None
    assert platform_key("win32", "AMD64") is None


def test_find_docling_sidecar_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ASTERISM_DOCLING_SIDECAR_DIR", raising=False)
    assert find_docling_sidecar_dir() == REPO / "infra" / "docling-sidecar"
    (tmp_path / "app.py").write_text("")
    monkeypatch.setenv("ASTERISM_DOCLING_SIDECAR_DIR", str(tmp_path))
    assert find_docling_sidecar_dir() == tmp_path
    # env が指す先に app.py が無ければ、checkout には落ちずに None（find_demo_agent_dir と同じ）
    monkeypatch.setenv("ASTERISM_DOCLING_SIDECAR_DIR", str(tmp_path / "nope"))
    assert find_docling_sidecar_dir() is None


# ---------------------------------------------------------------------------
# 固定ファイル


def test_locked_docling_matches_the_server_sidecar() -> None:
    server = (REPO / "infra" / "docling-sidecar" / "requirements.txt").read_text()
    server_ver = re.search(r"^docling==([\w.]+)", server, re.M)
    locked_ver = re.search(r"^docling==([\w.]+)", REQUIREMENTS.read_text(), re.M)
    assert server_ver and locked_ver
    assert locked_ver.group(1) == server_ver.group(1)


def test_every_requirement_is_pinned_with_hashes() -> None:
    text = REQUIREMENTS.read_text().replace("\\\n", " ")
    entries = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert len(entries) > 50
    for entry in entries:
        assert re.match(r"^[A-Za-z0-9_.\-\[\]]+==[^\s;]+", entry), entry
        assert "--hash=sha256:" in entry, entry


def test_antlr4_hash_matches_the_bundled_wheel() -> None:
    wheel = PACKAGE_DIR / "wheels" / "antlr4_python3_runtime-4.9.3-py3-none-any.whl"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    text = REQUIREMENTS.read_text()
    m = re.search(r"^antlr4-python3-runtime==4\.9\.3 \\\n((?:\s+--hash=.*\n?)+)", text, re.M)
    assert m
    assert re.findall(r"sha256:([0-9a-f]{64})", m.group(1)) == [digest]


def test_models_json_shape() -> None:
    models = json.loads((PACKAGE_DIR / "models.json").read_text())
    assert {m["repo_id"] for m in models} == {
        "docling-project/docling-layout-heron",
        "docling-project/docling-models",
    }
    for m in models:
        assert set(m) == {"repo_id", "ref", "commit"}
        assert re.fullmatch(r"[0-9a-f]{40}", m["commit"])
        assert m["ref"]


def test_packaged_data_files_exist() -> None:
    for name in (
        "requirements-macos-arm64.txt",
        "requirements.in",
        "models.json",
        "README.md",
        "fetch_models.py",
        "check_pipeline.py",
    ):
        assert (PACKAGE_DIR / name).is_file(), name


# ---------------------------------------------------------------------------
# API 3 経路


def _settings(tmp: Path) -> Settings:
    s = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": "http://test",
            "CSV2RDF_SETTLE_S": "0.0",
        }
    )
    s.api_token = _TOKEN
    return s


def _fake_oxigraph() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            body = {"head": {"vars": []}, "results": {"bindings": []}}
            return httpx.Response(
                200, content=json.dumps(body), headers={"Content-Type": "application/json"}
            )
        return httpx.Response(204)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _local_app(tmp_path: Path, rt: PdfRuntime) -> Any:
    return build_local_app(
        token=_TOKEN,
        ui_dist=None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
        mcp=False,
        pdf_runtime=rt,
    )


def test_api_get_is_open_and_reports_state(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    with TestClient(_local_app(tmp_path, rt)) as client:
        res = client.get("/api/pdf-runtime")  # 認証なし
        assert res.status_code == 200
        body = res.json()
        assert body["state"] == "absent"
        assert set(body) == {
            "state",
            "phase",
            "bytes_done",
            "bytes_total",
            "error",
            "log_path",
            "installed",
        }
        assert body["bytes_total"] == BYTES_TOTAL_ESTIMATE


def test_api_install_and_delete_go_through_the_write_gate(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    with TestClient(_local_app(tmp_path, rt)) as client:
        assert client.post("/api/pdf-runtime/install").status_code == 401
        assert (
            client.post(
                "/api/pdf-runtime/install", headers={"X-Asterism-Token": "wrong"}
            ).status_code
            == 401
        )
        assert client.delete("/api/pdf-runtime").status_code == 401
        assert spawn.calls == []

        headers = {"X-Asterism-Token": _TOKEN}
        res = client.post("/api/pdf-runtime/install", headers=headers)
        assert res.status_code == 200
        assert res.json()["state"] == "installing"
        rt.join(10)
        assert client.get("/api/pdf-runtime").json()["state"] == "ready"

        res = client.delete("/api/pdf-runtime", headers=headers)
        assert res.status_code == 200
        assert res.json()["state"] == "absent"
        assert client.get("/api/pdf-runtime").json()["state"] == "absent"


def test_api_unsupported_and_external_refuse_changes(tmp_path: Path, sidecar_dir: Path) -> None:
    headers = {"X-Asterism-Token": _TOKEN}
    rt, _ = make_runtime(tmp_path / "a", None)
    with TestClient(_local_app(tmp_path, rt)) as client:
        assert client.get("/api/pdf-runtime").json()["state"] == "unsupported"
        assert client.post("/api/pdf-runtime/install", headers=headers).status_code == 409
        assert client.delete("/api/pdf-runtime", headers=headers).status_code == 409

    rt2, _ = make_runtime(tmp_path / "b", sidecar_dir, external_url="http://docling:8090")
    with TestClient(_local_app(tmp_path, rt2)) as client:
        assert client.get("/api/pdf-runtime").json()["state"] == "external"
        assert client.post("/api/pdf-runtime/install", headers=headers).status_code == 409


def test_server_app_has_no_pdf_runtime_routes(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_fake_oxigraph(), start_watcher=False)
    with TestClient(app) as client:
        assert client.get("/api/pdf-runtime").status_code == 404
        headers = {"X-Asterism-Token": _TOKEN}
        assert client.post("/api/pdf-runtime/install", headers=headers).status_code == 404
        assert client.delete("/api/pdf-runtime", headers=headers).status_code == 404


def test_ready_runtime_makes_instance_report_can_convert_pdf(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    """サイドカーが応答したら settings.docling_url に入り、止めると外れる。"""
    cfg = _settings(tmp_path)
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    rt._on_url = lambda url: setattr(cfg, "docling_url", url)
    app = build_local_app(
        token=_TOKEN,
        ui_dist=None,
        settings=cfg,
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
        mcp=False,
        pdf_runtime=rt,
    )
    with TestClient(app) as client:
        assert client.get("/api/instance").json()["can_convert_pdf"] is False
        client.post("/api/pdf-runtime/install", headers={"X-Asterism-Token": _TOKEN})
        rt.join(10)
        assert client.get("/api/instance").json()["can_convert_pdf"] is True
        client.delete("/api/pdf-runtime", headers={"X-Asterism-Token": _TOKEN})
        assert client.get("/api/instance").json()["can_convert_pdf"] is False


def test_disk_check_uses_the_resolved_location(tmp_path: Path, sidecar_dir: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    seen: list[Path] = []
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    rt.root = tmp_path / "link" / "pdf-runtime"
    rt.partial = rt.root.with_name("pdf-runtime.partial")
    rt._disk_free = lambda p: (seen.append(p), 10 * GB)[1]
    rt.install()
    rt.join(10)
    assert seen == [real.resolve()]


# ---------------------------------------------------------------------------
# 自分が作ったものだけを消す・使う


def _snapshot(path: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for p in sorted(path.rglob("*")):
        out[str(p.relative_to(path))] = None if p.is_dir() else p.read_bytes()
    return out


def _foreign_dir(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "thesis.docx").write_bytes(b"precious")
    (path / "sub").mkdir()
    (path / "sub" / "data.csv").write_bytes(b"a,b\n1,2\n")


@pytest.mark.parametrize("where", ["root", "partial", "root_json_without_kind"])
def test_foreign_folder_is_never_touched(tmp_path: Path, sidecar_dir: Path, where: str) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    target = rt.partial if where == "partial" else rt.root
    _foreign_dir(target)
    if where == "root_json_without_kind":
        (target / "runtime.json").write_text(json.dumps({"hello": 1}))
    before = _snapshot(target)

    rt.startup()
    rt.join(10)
    status = rt.status()
    assert status["state"] == "failed"
    assert str(target) in status["error"]
    assert status["installed"] is False

    rt._set(state="absent", error=None)
    assert rt.install()["state"] == "failed"
    assert str(target) in rt.status()["error"]
    rt.join(10)

    assert rt.remove()["state"] == "failed"
    assert str(target) in rt.status()["error"]

    assert _snapshot(target) == before  # 1 バイトも変わらない
    assert spawn.calls == []  # venv --upgrade も何もしない


def test_symlinked_root_is_not_followed(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    outside = tmp_path / "outside"
    _foreign_dir(outside)
    (outside / "runtime.json").write_text(json.dumps({"kind": MARKER_KIND}))  # 目印つきでも
    rt.root.parent.mkdir(parents=True)
    rt.root.symlink_to(outside)
    before = _snapshot(outside)
    rt.startup()
    assert rt.status()["state"] == "failed"
    assert rt.remove()["state"] == "failed"
    assert _snapshot(outside) == before
    assert spawn.calls == []


def test_marked_root_counts_as_installed_and_can_be_removed(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    assert rt.status()["installed"] is False
    _installed(rt)
    assert rt.status()["installed"] is True
    assert rt.remove()["state"] == "absent"
    assert not rt.root.exists()
    assert rt.status()["installed"] is False


def test_failed_but_installed_can_be_deleted_to_absent(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, _spawn = make_runtime(tmp_path, sidecar_dir, health=False)
    rt.install()
    rt.join(10)
    status = rt.status()
    assert status["state"] == "failed"
    assert status["installed"] is True
    assert status["log_path"] == str(rt.sidecar_log)  # 起動できない failed はサイドカーのログ
    # install を押しても起動だけやり直して同じ失敗 = 行き止まり。DELETE で抜けられる。
    assert rt.remove()["state"] == "absent"
    assert rt.status()["installed"] is False
    assert not rt.root.exists()


def test_install_failure_points_at_the_install_log(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.fail_at = "pip"
    rt.install()
    rt.join(10)
    assert rt.status()["log_path"] == str(rt.install_log)


def test_half_deleted_root_can_be_deleted_again(
    tmp_path: Path, sidecar_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    _installed(rt)
    (rt.root / "models" / "hub").mkdir(parents=True)
    (rt.root / "models" / "hub" / "blob").write_text("x")
    real_rmtree = shutil.rmtree

    def flaky(path: Any, *a: Any, **k: Any) -> None:
        if str(path).endswith("models"):
            raise OSError("busy")
        real_rmtree(path, *a, **k)

    monkeypatch.setattr("asterism_api.pdf_runtime.shutil.rmtree", flaky)
    assert rt.remove()["state"] == "failed"
    assert (rt.root / "runtime.json").exists()  # 目印は最後に消す = まだ残っている
    assert rt.status()["installed"] is True

    monkeypatch.setattr("asterism_api.pdf_runtime.shutil.rmtree", real_rmtree)
    assert rt.remove()["state"] == "absent"
    assert not rt.root.exists()


# ---------------------------------------------------------------------------
# 並行性


def test_install_during_startup_cleanup_waits_instead_of_being_dropped(
    tmp_path: Path, sidecar_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    _mark_partial(rt)
    gate = threading.Event()
    real = PdfRuntime._remove_partial_quiet

    def slow(self: PdfRuntime) -> None:
        gate.wait(5)
        real(self)

    monkeypatch.setattr(PdfRuntime, "_remove_partial_quiet", slow)
    rt.startup()
    results: list[dict[str, Any]] = []
    t = threading.Thread(target=lambda: results.append(rt.install()))
    t.start()
    time.sleep(0.3)
    assert t.is_alive()  # 掃除の終わりを待っている(捨てていない)
    assert spawn.calls == []
    gate.set()
    t.join(10)
    rt.join(10)
    assert results[0]["state"] == "installing"
    assert rt.status()["state"] == "ready"
    assert "venv" in spawn.stages()


def test_install_right_after_remove_is_not_undone_by_the_old_remove(
    tmp_path: Path, sidecar_dir: Path
) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.hang_at = "pip"
    rt.install()
    wait_for(lambda: "pip" in spawn.stages())
    assert rt.remove()["state"] == "absent"
    spawn.hang_at = None
    spawn.calls.clear()
    rt.install()
    rt.join(10)
    time.sleep(0.2)
    assert rt.status()["state"] == "ready"
    assert (rt.root / "runtime.json").exists()
    assert spawn.stages() == ["venv", "pip", "fetch", "check", "sidecar"]


def test_shutdown_is_quick_and_stops_children(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(tmp_path, sidecar_dir)
    spawn.hang_at = "pip"
    rt.install()
    wait_for(lambda: "pip" in spawn.stages())
    started = time.monotonic()
    rt.shutdown()
    assert time.monotonic() - started < 3.0
    assert spawn.procs["pip"].terminated


# ---------------------------------------------------------------------------
# 子プロセスの環境


def test_child_env_is_an_allowlist(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, spawn = make_runtime(
        tmp_path,
        sidecar_dir,
        env={
            "PATH": "/usr/bin",
            "HOME": "/home/x",
            "LC_ALL": "C.UTF-8",
            "HTTPS_PROXY": "http://proxy:3128",
            "https_proxy": "http://proxy:3128",
            "SSL_CERT_FILE": "/etc/ca.pem",
            "TMPDIR": "/var/parent-tmp",
            "ASTERISM_API_TOKEN": "secret",
            "ASTERISM_LLM_KEY_X": "sk-secret",
            "HF_TOKEN": "hf_secret",
            "HF_HUB_CACHE": "/elsewhere",
            "PIP_INDEX_URL": "http://evil/simple",
            "ANTHROPIC_API_KEY": "sk-ant",
        },
    )
    rt.install()
    rt.join(10)
    assert len(spawn.calls) == 5
    for call in spawn.calls:
        env = call["env"]
        for banned in (
            "ASTERISM_API_TOKEN",
            "ASTERISM_LLM_KEY_X",
            "HF_TOKEN",
            "HF_HUB_CACHE",
            "PIP_INDEX_URL",
            "ANTHROPIC_API_KEY",
        ):
            assert banned not in env, (call["stage"], banned)
        assert env["HTTPS_PROXY"] == "http://proxy:3128"
        assert env["https_proxy"] == "http://proxy:3128"
        assert env["SSL_CERT_FILE"] == "/etc/ca.pem"
        assert env["LC_ALL"] == "C.UTF-8"
        assert env["HOME"] == "/home/x"
        assert env["PYTHONNOUSERSITE"] == "1"
        assert env["PIP_NO_INPUT"] == "1"
    by_stage = {c["stage"]: c for c in spawn.calls}
    for stage in ("venv", "pip", "fetch", "check"):  # 一時ファイルは .partial の下
        assert by_stage[stage]["env"]["TMPDIR"] == str(rt.partial / "tmp")
    assert by_stage["sidecar"]["env"]["TMPDIR"] == "/var/parent-tmp"
    assert not (rt.root / "tmp").exists()  # 片付いている


def test_sidecar_log_is_truncated_when_over_5mb(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    rt.log_dir.mkdir(parents=True)
    rt.sidecar_log.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    _installed(rt)
    rt.startup()
    rt.join(10)
    assert rt.sidecar_log.stat().st_size == 0
    rt.sidecar_log.write_bytes(b"x" * 100)
    rt.shutdown()
    rt2, _ = make_runtime(tmp_path, sidecar_dir)
    rt2.startup()
    rt2.join(10)
    assert rt2.sidecar_log.stat().st_size == 100  # 小さければ残して追記


# ---------------------------------------------------------------------------
# Origin の確認


def test_origin_must_match_host_for_post_and_delete(tmp_path: Path, sidecar_dir: Path) -> None:
    rt, _ = make_runtime(tmp_path, sidecar_dir)
    auth = {"X-Asterism-Token": _TOKEN}
    with TestClient(_local_app(tmp_path, rt)) as client:
        evil = {**auth, "Origin": "https://evil.example"}
        assert client.post("/api/pdf-runtime/install", headers=evil).status_code == 403
        assert client.delete("/api/pdf-runtime", headers=evil).status_code == 403
        assert (
            client.post(
                "/api/pdf-runtime/install", headers={**auth, "Origin": "null"}
            ).status_code
            == 403
        )
        same = {**auth, "Origin": "http://testserver"}
        assert client.post("/api/pdf-runtime/install", headers=same).status_code == 200
        rt.join(10)
        assert client.delete("/api/pdf-runtime", headers=same).status_code == 200
        # Origin が無い(curl など)ときは書き込みゲートだけ
        assert client.post("/api/pdf-runtime/install", headers=auth).status_code == 200
        rt.join(10)
        assert client.delete("/api/pdf-runtime", headers=auth).status_code == 200
        evil_get = client.get("/api/pdf-runtime", headers={"Origin": "https://evil.example"})
        assert evil_get.status_code == 200  # 読むだけの GET は対象外


# ---------------------------------------------------------------------------
# 壊れた置き場所でも例外を投げない


@pytest.mark.parametrize("value", [".", "/", ""])
def test_unusable_runtime_dir_does_not_raise(
    value: str, monkeypatch: pytest.MonkeyPatch, sidecar_dir: Path
) -> None:
    monkeypatch.setenv("ASTERISM_PDF_RUNTIME_DIR", value)
    rt = PdfRuntime(sidecar_dir=sidecar_dir, requirements=REQUIREMENTS)
    status = rt.status()
    assert status["state"] in ("unsupported", "absent")
    rt.startup()
    rt.shutdown()
    rt2 = PdfRuntime(root=Path(value or "."), sidecar_dir=sidecar_dir, requirements=REQUIREMENTS)
    assert rt2.status()["state"] == "unsupported"
    assert rt2.install()["state"] == "unsupported"
    assert rt2.remove()["state"] == "unsupported"


def _run_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, extra: list[str]) -> list[Any]:
    """asterism-local の main() を、サーバを起こさずに通す。"""
    import asterism_api.local as local

    served: list[Any] = []
    monkeypatch.setattr(local, "_serve", lambda app, **kw: served.append(app))

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(local, "run_startup_sample", _noop)
    monkeypatch.setenv("CSV2RDF_OXIGRAPH_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("ASTERISM_API_TOKEN", raising=False)
    saved = dict(os.environ)
    try:
        rc = local.main(
            ["--data-dir", str(tmp_path / "home"), "--no-browser", "--no-ask", "--no-mcp", *extra]
        )
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert rc == 0
    return served


@pytest.mark.parametrize("value", [".", "/"])
def test_main_survives_a_broken_runtime_dir(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASTERISM_PDF_RUNTIME_DIR", value)
    served = _run_main(tmp_path, monkeypatch, extra=[])
    assert len(served) == 1


def test_main_survives_the_runtime_blowing_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boom:
        def __init__(self, **_kw: Any) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr("asterism_api.pdf_runtime.PdfRuntime", Boom)
    served = _run_main(tmp_path, monkeypatch, extra=[])
    assert len(served) == 1
    client = TestClient(served[0])  # lifespan なし(Oxigraph は居ない)
    res = client.get("/api/pdf-runtime")  # 部品の経路は無し(SPA があればその index.html が返る)
    assert "json" not in res.headers.get("content-type", "")
