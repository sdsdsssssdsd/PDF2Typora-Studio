"""Bundled / external Ollama runtime lifecycle manager."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import psutil

from ai.providers.ollama_api_client import OllamaApiClient, OllamaHealth
from config.config_manager import get_ollama_settings, project_root, save_user_config
from core.exceptions import OllamaRuntimeNotFoundError, OllamaStartupError
from utils.logger import get_logger

logger = get_logger("ollama_manager")


class OllamaMode(str, Enum):
    BUNDLED = "bundled"
    EXTERNAL = "external"
    CUSTOM_SERVER = "custom_server"


class OllamaRuntimeState(str, Enum):
    NOT_FOUND = "not_found"
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class OllamaDirectoryInfo:
    root: Path
    executable: Path | None = None
    executable_found: bool = False
    model_store: Path | None = None
    model_store_valid: bool = False
    version: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class OllamaProcessInfo:
    pid: int
    executable: Path | None
    base_url: str
    port: int
    started_by_app: bool
    started_at: str | None = None


@dataclass
class OllamaRuntimeStatus:
    state: OllamaRuntimeState = OllamaRuntimeState.STOPPED
    running: bool = False
    base_url: str = ""
    managed_by_app: bool = False
    version: str | None = None
    error: str | None = None
    process: OllamaProcessInfo | None = None


@dataclass
class EnsureOllamaResult:
    ok: bool
    base_url: str = ""
    message: str = ""
    model_names: list[str] = field(default_factory=list)


def find_available_port(start: int = 11435, end: int = 11450) -> int:
    """Return first free TCP port on 127.0.0.1 in [start, end]."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OllamaStartupError(f"No free port in range {start}-{end}")


def find_system_ollama_executable() -> Path | None:
    """Locate a system-installed ollama.exe (PATH or common install dirs)."""
    import shutil

    which = shutil.which("ollama")
    if which:
        path = Path(which)
        if path.is_file():
            return path

    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(local) / "Programs" / "Ollama" / "ollama.exe" if local else None,
        Path(r"C:\Program Files\Ollama\ollama.exe"),
        Path(r"D:\Ollama\ollama.exe"),
        Path(r"E:\Ollama\ollama.exe"),
    ]
    for cand in candidates:
        if cand is not None and cand.is_file():
            return cand
    return None


def inspect_ollama_directory(path: Path) -> OllamaDirectoryInfo:
    """Inspect a candidate Ollama install directory without modifying it."""
    root = path.resolve()
    info = OllamaDirectoryInfo(root=root)
    if not root.exists() or not root.is_dir():
        info.warnings.append("Directory does not exist")
        return info

    exe_candidates = [
        root / "ollama.exe",
        root / "bin" / "ollama.exe",
        root / "ollama" / "ollama.exe",
    ]
    for cand in exe_candidates:
        if cand.is_file():
            info.executable = cand
            info.executable_found = True
            break
    if not info.executable_found:
        info.warnings.append("ollama.exe not found")

    model_candidates = [
        root / "models",
        root.parent / "models",
        root / ".ollama" / "models",
    ]
    for cand in model_candidates:
        if cand.is_dir() and (
            (cand / "blobs").exists()
            or (cand / "manifests").exists()
            or any(cand.iterdir())
        ):
            info.model_store = cand
            info.model_store_valid = True
            break
    if not info.model_store_valid:
        # Still note models folder if present but empty
        for cand in model_candidates:
            if cand.is_dir():
                info.model_store = cand
                info.warnings.append("models directory exists but may be empty")
                break
        else:
            info.warnings.append("models directory not found")

    return info


class OllamaRuntimeManager:
    """Owns bundled Ollama process lifecycle; never kills external servers."""

    def __init__(
        self,
        bundled_runtime: str | Path | None = None,
        bundled_models: str | Path | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        cfg = settings or get_ollama_settings()
        self.runtime_path = Path(bundled_runtime or cfg["runtime_path"])
        self.models_path = Path(bundled_models or cfg["models_path"])
        self.port_start = int(cfg.get("port_start", 11435))
        self.port_end = int(cfg.get("port_end", 11450))
        self.no_cloud = bool(cfg.get("no_cloud", True))
        self.connect_seconds = float(cfg.get("connect_seconds", 3))
        self.start_seconds = float(cfg.get("start_seconds", 30))
        self.request_seconds = float(cfg.get("request_seconds", 300))
        self.external_base_url = str(
            cfg.get("external_base_url", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.custom_base_url = str(cfg.get("custom_base_url", "")).rstrip("/")

        self.mode = OllamaMode(cfg.get("mode", "bundled"))
        self._process: subprocess.Popen[bytes] | None = None
        self._process_info: OllamaProcessInfo | None = None
        self._state = OllamaRuntimeState.STOPPED
        self._log_handle: Any = None
        self.state_path = project_root() / "runtime" / "runtime_state.json"
        self._logs_dir = project_root() / "logs" / "ollama"

        self._reconcile_stale_state()

    # ---- discovery ---------------------------------------------------------

    def find_bundled_runtime(self) -> Path | None:
        info = self.inspect_runtime_directory(self.runtime_path)
        return info.executable if info.executable_found else None

    def inspect_runtime_directory(self, path: Path | None = None) -> OllamaDirectoryInfo:
        return inspect_ollama_directory(path or self.runtime_path)

    def detect_external_server(
        self, base_url: str | None = None
    ) -> OllamaHealth:
        url = (base_url or self.external_base_url).rstrip("/")
        client = OllamaApiClient(
            url,
            connect_timeout=min(1.5, self.connect_seconds),
            request_timeout=2.0,
        )
        return client.health_check()

    def auto_configure(self, *, persist: bool = True) -> str:
        """Fix obvious misconfig (bundled mode without bundled exe)."""
        if self.mode != OllamaMode.BUNDLED:
            return ""
        if self.find_bundled_runtime() is not None:
            return ""
        self.mode = OllamaMode.EXTERNAL
        note = (
            "未找到项目内置 Ollama（runtime/ollama/ollama.exe），"
            "已自动切换为外部模式 http://127.0.0.1:11434"
        )
        logger.warning(note)
        if persist:
            try:
                save_user_config({"ollama": {"mode": "external"}})
            except OSError:
                logger.debug("Could not persist ollama mode", exc_info=True)
        return note

    def candidate_base_urls(self) -> list[str]:
        urls: list[str] = []
        if self._process_info and self._process_info.base_url:
            urls.append(self._process_info.base_url.rstrip("/"))
        urls.append(self.resolve_base_url().rstrip("/"))
        urls.append(self.external_base_url.rstrip("/"))
        if self.custom_base_url:
            urls.append(self.custom_base_url.rstrip("/"))
        urls.append("http://127.0.0.1:11434")
        # Only scan bundled port range when explicitly in bundled mode
        if self.mode == OllamaMode.BUNDLED:
            for port in range(
                self.port_start, min(self.port_start + 4, self.port_end + 1)
            ):
                urls.append(f"http://127.0.0.1:{port}")
        seen: set[str] = set()
        ordered: list[str] = []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                ordered.append(u)
        return ordered

    def find_reachable_base_url(self) -> str | None:
        """Return first URL that answers as a healthy Ollama (short timeouts)."""
        for url in self.candidate_base_urls():
            client = OllamaApiClient(
                url, connect_timeout=0.8, request_timeout=1.5
            )
            health = client.health_check()
            if not health.healthy:
                continue
            try:
                client.list_tags()
            except Exception:  # noqa: BLE001
                continue
            return url
        return None

    def _remember_base_url(self, url: str) -> None:
        url = url.rstrip("/")
        if self._process_info and self._process_info.base_url == url:
            return
        # Prefer treating discovered URL as the active external endpoint
        if self.mode == OllamaMode.BUNDLED and self.find_bundled_runtime() is None:
            self.mode = OllamaMode.EXTERNAL
        if self.mode != OllamaMode.BUNDLED:
            self.external_base_url = url

    def start_system_server(self) -> OllamaRuntimeStatus:
        """Start a system-installed ollama.exe without overriding its model store."""
        if self._process is not None and self._process.poll() is None:
            health = self.health_check(
                self._process_info.base_url if self._process_info else None
            )
            if health.healthy:
                self._state = OllamaRuntimeState.READY
                return self.get_runtime_status()

        exe = find_system_ollama_executable()
        if exe is None:
            raise OllamaRuntimeNotFoundError(
                "未找到系统 Ollama。请安装 Ollama，或把 ollama.exe 放到 "
                "runtime/ollama/，或在「Ollama / AI 设置」中配置外部地址。"
            )

        # Prefer classic 11434; skip dead/occupied ports quickly
        try:
            port = find_available_port(11434, max(self.port_end, 11450))
        except OllamaStartupError:
            port = find_available_port(self.port_start, self.port_end)

        host = f"127.0.0.1:{port}"
        base_url = f"http://{host}"
        env = os.environ.copy()
        env["OLLAMA_HOST"] = host
        # Keep the user's normal model directory (do not point at empty project models/)
        env.pop("OLLAMA_MODELS", None)
        if self.no_cloud:
            env["OLLAMA_NO_CLOUD"] = "1"

        self._logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._logs_dir / "ollama_system.log"
        self._log_handle = open(log_path, "ab")  # noqa: SIM115

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        logger.info("Starting system Ollama: exe=%s host=%s", exe, host)
        self._state = OllamaRuntimeState.STARTING
        self.mode = OllamaMode.EXTERNAL
        self.external_base_url = base_url
        try:
            self._process = subprocess.Popen(
                [str(exe), "serve"],
                cwd=str(exe.parent),
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._state = OllamaRuntimeState.ERROR
            self._close_log()
            raise OllamaStartupError(f"无法启动系统 Ollama: {exc}") from exc

        self._process_info = OllamaProcessInfo(
            pid=self._process.pid,
            executable=exe,
            base_url=base_url,
            port=port,
            started_by_app=True,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write_state()

        if not self.wait_until_ready(base_url, timeout=self.start_seconds):
            tail = self._read_log_tail(log_path)
            self.stop_bundled()
            self._state = OllamaRuntimeState.ERROR
            raise OllamaStartupError(
                f"系统 Ollama 在 {self.start_seconds}s 内未就绪。日志尾部:\n{tail}"
            )

        self._state = OllamaRuntimeState.READY
        try:
            save_user_config(
                {
                    "ollama": {
                        "mode": "external",
                        "external": {"base_url": base_url},
                    }
                }
            )
        except OSError:
            logger.debug("Could not persist external base_url", exc_info=True)
        logger.info("System Ollama ready at %s (pid=%s)", base_url, self._process.pid)
        return self.get_runtime_status()

    def ensure_ready_for_models(self, *, start_if_needed: bool = True) -> EnsureOllamaResult:
        """Make sure an Ollama API is reachable and return installed model names."""
        note = self.auto_configure(persist=True)
        url = self.find_reachable_base_url()
        if url is None and start_if_needed:
            try:
                if self.mode == OllamaMode.BUNDLED and self.find_bundled_runtime():
                    self.start_bundled()
                else:
                    self.start_system_server()
                url = self.find_reachable_base_url()
            except Exception as exc:  # noqa: BLE001
                sys_exe = find_system_ollama_executable()
                hint = (
                    f"系统可执行文件: {sys_exe}" if sys_exe else "未检测到系统 ollama.exe"
                )
                return EnsureOllamaResult(
                    ok=False,
                    message=(
                        f"无法启动/连接 Ollama：{exc}\n{hint}\n"
                        "请打开菜单「AI → Ollama / AI 设置」检查，"
                        "或手动运行 ollama serve 后再点「刷新模型」。"
                    ),
                )

        if url is None:
            sys_exe = find_system_ollama_executable()
            return EnsureOllamaResult(
                ok=False,
                message=(
                    "Ollama API 不可用（内置 runtime 为空，11434/11435 均无正常响应）。\n"
                    + (
                        f"已检测到 {sys_exe}，请点「刷新模型」尝试自动启动，"
                        "或先在系统中运行 Ollama。"
                        if sys_exe
                        else "请安装 Ollama，或把 ollama.exe 放到 runtime/ollama/。"
                    )
                    + (f"\n{note}" if note else "")
                ),
            )

        self._remember_base_url(url)
        client = OllamaApiClient(url, connect_timeout=2.0, request_timeout=10.0)
        try:
            tags = client.list_tags()
        except Exception as exc:  # noqa: BLE001
            return EnsureOllamaResult(
                ok=False,
                base_url=url,
                message=f"已连上 {url}，但拉取模型失败：{exc}",
            )

        names = [str(t.get("name") or "") for t in tags if t.get("name")]
        if not names:
            return EnsureOllamaResult(
                ok=True,
                base_url=url,
                message=(
                    f"已连接 {url}，但模型列表为空。"
                    "请用 `ollama pull <vision模型>` 拉取后再刷新。"
                ),
                model_names=[],
            )
        msg = f"已连接 {url}，共 {len(names)} 个模型"
        if note:
            msg = f"{note}；{msg}"
        return EnsureOllamaResult(
            ok=True, base_url=url, message=msg, model_names=names
        )

    # ---- ports / env -------------------------------------------------------

    def find_available_port(
        self, start: int | None = None, end: int | None = None
    ) -> int:
        return find_available_port(
            start if start is not None else self.port_start,
            end if end is not None else self.port_end,
        )

    def build_environment(self, host: str) -> dict[str, str]:
        env = os.environ.copy()
        self.models_path.mkdir(parents=True, exist_ok=True)
        env["OLLAMA_MODELS"] = str(self.models_path.resolve())
        env["OLLAMA_HOST"] = host
        if self.no_cloud:
            env["OLLAMA_NO_CLOUD"] = "1"
        return env

    def resolve_base_url(self, mode: OllamaMode | None = None) -> str:
        m = mode or self.mode
        if m == OllamaMode.BUNDLED:
            if self._process_info and self._process_info.base_url:
                return self._process_info.base_url
            return f"http://127.0.0.1:{self.port_start}"
        if m == OllamaMode.CUSTOM_SERVER:
            return self.custom_base_url or self.external_base_url
        return self.external_base_url

    # ---- lifecycle ---------------------------------------------------------

    def start_bundled(self) -> OllamaRuntimeStatus:
        if self._process is not None and self._process.poll() is None:
            health = self.health_check(self._process_info.base_url if self._process_info else None)
            if health.healthy:
                self._state = OllamaRuntimeState.READY
                return self.get_runtime_status()

        exe = self.find_bundled_runtime()
        if exe is None:
            self._state = OllamaRuntimeState.NOT_FOUND
            raise OllamaRuntimeNotFoundError(
                f"Bundled Ollama not found under {self.runtime_path}. "
                "Place standalone ollama.exe in runtime/ollama/ or use External mode."
            )

        port = self.find_available_port()
        host = f"127.0.0.1:{port}"
        base_url = f"http://{host}"
        env = self.build_environment(host)

        self._logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._logs_dir / "ollama_server.log"
        self._log_handle = open(log_path, "ab")  # noqa: SIM115

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        logger.info(
            "Starting bundled Ollama: exe=%s host=%s models=%s",
            exe,
            host,
            self.models_path,
        )
        self._state = OllamaRuntimeState.STARTING
        try:
            self._process = subprocess.Popen(
                [str(exe), "serve"],
                cwd=str(exe.parent),
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._state = OllamaRuntimeState.ERROR
            self._close_log()
            raise OllamaStartupError(f"Failed to spawn ollama: {exc}") from exc

        self._process_info = OllamaProcessInfo(
            pid=self._process.pid,
            executable=exe,
            base_url=base_url,
            port=port,
            started_by_app=True,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write_state()

        if not self.wait_until_ready(base_url, timeout=self.start_seconds):
            tail = self._read_log_tail(log_path)
            self.stop_bundled()
            self._state = OllamaRuntimeState.ERROR
            raise OllamaStartupError(
                f"Ollama did not become ready within {self.start_seconds}s. "
                f"Log tail:\n{tail}"
            )

        self._state = OllamaRuntimeState.READY
        logger.info("Bundled Ollama ready at %s (pid=%s)", base_url, self._process.pid)
        return self.get_runtime_status()

    def wait_until_ready(
        self, base_url: str, timeout: float = 30.0, interval: float = 0.4
    ) -> bool:
        deadline = time.monotonic() + timeout
        client = OllamaApiClient(
            base_url,
            connect_timeout=min(2.0, self.connect_seconds),
            request_timeout=5.0,
        )
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                return False
            health = client.health_check()
            if health.healthy:
                return True
            time.sleep(interval)
        return False

    def stop_bundled(self) -> OllamaRuntimeStatus:
        """Stop only the process started by this app."""
        if self._process_info is None or not self._process_info.started_by_app:
            logger.info("stop_bundled skipped: not started_by_app")
            return self.get_runtime_status()

        self._state = OllamaRuntimeState.STOPPING
        proc = self._process
        pid = self._process_info.pid

        if proc is not None and proc.poll() is None:
            logger.info("Terminating managed Ollama pid=%s", pid)
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    logger.warning("Ollama pid=%s did not exit; killing", pid)
                    proc.kill()
                    proc.wait(timeout=5)
            except OSError as exc:
                logger.warning("Error stopping Ollama: %s", exc)
        elif pid:
            # Process object lost but we own the PID — verify before kill
            if self._verify_owned_pid(pid, self._process_info.executable):
                try:
                    p = psutil.Process(pid)
                    p.terminate()
                    p.wait(timeout=8)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired, psutil.AccessDenied):
                    try:
                        p = psutil.Process(pid)
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

        self._process = None
        self._process_info = None
        self._clear_state()
        self._close_log()
        self._state = (
            OllamaRuntimeState.NOT_FOUND
            if self.find_bundled_runtime() is None
            else OllamaRuntimeState.STOPPED
        )
        return self.get_runtime_status()

    def restart_bundled(self) -> OllamaRuntimeStatus:
        self.stop_bundled()
        return self.start_bundled()

    def stop_managed(self) -> None:
        """Alias used by app shutdown — only stops app-owned process."""
        self.stop_bundled()

    # ---- status ------------------------------------------------------------

    def is_running(self) -> bool:
        return self.health_check().healthy

    def health_check(self, base_url: str | None = None) -> OllamaHealth:
        url = (base_url or self.resolve_base_url()).rstrip("/")
        if not url:
            return OllamaHealth(healthy=False, error="No base URL configured")
        client = OllamaApiClient(
            url,
            connect_timeout=self.connect_seconds,
            request_timeout=self.request_seconds,
        )
        return client.health_check()

    def get_runtime_status(self) -> OllamaRuntimeStatus:
        if self.mode == OllamaMode.BUNDLED and self.find_bundled_runtime() is None:
            if self._state not in (
                OllamaRuntimeState.STARTING,
                OllamaRuntimeState.READY,
            ):
                self._state = OllamaRuntimeState.NOT_FOUND

        base = self.resolve_base_url()
        health = self.health_check(base) if base else OllamaHealth(healthy=False)
        managed = bool(
            self._process_info and self._process_info.started_by_app
        )
        if health.healthy:
            state = OllamaRuntimeState.READY
        else:
            state = self._state
            if state == OllamaRuntimeState.READY:
                state = OllamaRuntimeState.STOPPED

        return OllamaRuntimeStatus(
            state=state,
            running=health.healthy,
            base_url=base,
            managed_by_app=managed,
            version=health.version,
            error=health.error if not health.healthy else None,
            process=self._process_info,
        )

    def api_client(self, base_url: str | None = None) -> OllamaApiClient:
        return OllamaApiClient(
            (base_url or self.resolve_base_url()).rstrip("/"),
            connect_timeout=self.connect_seconds,
            request_timeout=self.request_seconds,
        )

    # ---- state file --------------------------------------------------------

    def _write_state(self) -> None:
        if not self._process_info:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": self._process_info.pid,
            "executable": str(self._process_info.executable)
            if self._process_info.executable
            else None,
            "base_url": self._process_info.base_url,
            "port": self._process_info.port,
            "started_by_app": self._process_info.started_by_app,
            "started_at": self._process_info.started_at,
        }
        self.state_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _clear_state(self) -> None:
        if self.state_path.exists():
            try:
                self.state_path.unlink()
            except OSError:
                logger.debug("Could not remove runtime_state.json", exc_info=True)

    def _reconcile_stale_state(self) -> None:
        """On startup: never kill based on state file alone."""
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._clear_state()
            return

        pid = data.get("pid")
        exe = Path(data["executable"]) if data.get("executable") else None
        base_url = data.get("base_url", "")
        started_by_app = bool(data.get("started_by_app"))

        if not started_by_app or not pid:
            self._clear_state()
            return

        if not self._verify_owned_pid(int(pid), exe):
            logger.info("Clearing stale runtime state (pid=%s invalid)", pid)
            self._clear_state()
            return

        # Process still looks like ours — reclaim handle without killing
        health = self.health_check(base_url) if base_url else OllamaHealth(healthy=False)
        if not health.healthy:
            logger.info("Stale PID exists but API not Ollama; clearing state")
            self._clear_state()
            return

        self._process_info = OllamaProcessInfo(
            pid=int(pid),
            executable=exe,
            base_url=base_url,
            port=int(data.get("port") or 0),
            started_by_app=True,
            started_at=data.get("started_at"),
        )
        self._state = OllamaRuntimeState.READY
        logger.info("Reclaimed managed Ollama pid=%s at %s", pid, base_url)

    @staticmethod
    def _verify_owned_pid(pid: int, expected_exe: Path | None) -> bool:
        try:
            proc = psutil.Process(pid)
            if not proc.is_running():
                return False
            name = (proc.name() or "").lower()
            if "ollama" not in name:
                return False
            if expected_exe:
                try:
                    actual = Path(proc.exe())
                    if actual.resolve() != expected_exe.resolve():
                        return False
                except (psutil.AccessDenied, psutil.Error):
                    # Fall back to name check only
                    pass
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
            return False

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    @staticmethod
    def _read_log_tail(path: Path, lines: int = 40) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.splitlines()[-lines:])
        except OSError:
            return "(log unavailable)"

    # ---- legacy aliases (Phase 1 stubs) ------------------------------------

    def detect_bundled(self) -> bool:
        return self.find_bundled_runtime() is not None

    def detect_external(self, base_url: str = "http://127.0.0.1:11434") -> bool:
        return self.detect_external_server(base_url).healthy

    def list_models(self, base_url: str | None = None) -> list[str]:
        client = self.api_client(base_url)
        try:
            return [m.get("name", "") for m in client.list_tags() if m.get("name")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_models failed: %s", exc)
            return []
