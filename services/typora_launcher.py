"""Launch exported Markdown in Typora or the OS default app."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LaunchResult:
    success: bool
    method: str
    error: str | None = None


class TyporaLauncher:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        typora = (config or {}).get("typora") or {}
        self.executable = (typora.get("executable_path") or "").strip()

    def launch(self, markdown_path: Path) -> LaunchResult:
        path = Path(markdown_path)
        if not path.exists():
            return LaunchResult(success=False, method="none", error="markdown_missing")

        exe = self.executable
        if exe and Path(exe).exists():
            try:
                subprocess.Popen([exe, str(path)])  # noqa: S603
                return LaunchResult(success=True, method="typora_executable")
            except OSError as exc:
                return LaunchResult(success=False, method="typora_executable", error=str(exc))

        if sys.platform.startswith("win"):
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
                return LaunchResult(success=True, method="os.startfile")
            except OSError as exc:
                return LaunchResult(success=False, method="os.startfile", error=str(exc))

        try:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])  # noqa: S603
            return LaunchResult(success=True, method=opener)
        except OSError as exc:
            return LaunchResult(success=False, method="fallback", error=str(exc))
