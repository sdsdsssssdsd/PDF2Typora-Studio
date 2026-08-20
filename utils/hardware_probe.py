"""Windows hardware probe (CPU / RAM / NVIDIA GPU)."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger("hardware_probe")


@dataclass
class HardwareInfo:
    cpu_name: str | None = None
    ram_total_gb: float | None = None
    gpu_name: str | None = None
    gpu_vram_total_mb: int | None = None
    gpu_vram_free_mb: int | None = None


def probe_hardware() -> HardwareInfo:
    """Detect local hardware. Never raises — returns partial info on failure."""
    info = HardwareInfo()
    try:
        info.cpu_name = platform.processor() or platform.machine() or None
    except Exception:  # noqa: BLE001 — probe must never crash app
        logger.debug("CPU probe failed", exc_info=True)

    try:
        import psutil

        info.ram_total_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:  # noqa: BLE001
        logger.debug("RAM probe failed", exc_info=True)

    _probe_nvidia(info)
    return info


def _probe_nvidia(info: HardwareInfo) -> None:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        logger.debug("nvidia-smi not found")
        return
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_no_window_flags(),
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return
        line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            info.gpu_name = parts[0]
            info.gpu_vram_total_mb = int(float(parts[1]))
            info.gpu_vram_free_mb = int(float(parts[2]))
    except Exception:  # noqa: BLE001
        logger.debug("nvidia-smi probe failed", exc_info=True)


def _no_window_flags() -> int:
    if platform.system() == "Windows":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def format_hardware_summary(info: HardwareInfo) -> str:
    lines = [
        f"CPU: {info.cpu_name or '未知'}",
        f"RAM: {f'{info.ram_total_gb} GB' if info.ram_total_gb else '未知'}",
        f"GPU: {info.gpu_name or '未检测到 NVIDIA GPU'}",
    ]
    if info.gpu_vram_total_mb is not None:
        total_gb = info.gpu_vram_total_mb / 1024
        free = (
            f"{info.gpu_vram_free_mb / 1024:.1f} GB free"
            if info.gpu_vram_free_mb is not None
            else ""
        )
        lines.append(f"VRAM: {total_gb:.1f} GB" + (f" ({free})" if free else ""))
    return "\n".join(lines)
