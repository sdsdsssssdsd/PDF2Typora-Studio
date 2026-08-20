"""Application logging setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from config.config_manager import project_root


def setup_logging(level: str = "INFO") -> logging.Logger:
    logs_dir = project_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "app.log"

    root = logging.getLogger("pdf2typora")
    if root.handlers:
        return root

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(module)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pdf2typora.{name}")
