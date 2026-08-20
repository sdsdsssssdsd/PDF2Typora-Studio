"""Grab a main-window screenshot for README (no interactive loop)."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.config_manager import load_config
from gui.main_window import MainWindow
from gui.theme import apply_theme
from utils.logger import setup_logging


def main() -> int:
    out = ROOT / "docs" / "images" / "gui-main.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    config = load_config()
    setup_logging(config.get("logging", {}).get("level", "INFO"))

    app = QApplication(sys.argv)
    app.setApplicationName("PDF2Typora Studio")
    apply_theme(app)

    window = MainWindow()
    window.resize(1280, 800)
    window.show()
    window.raise_()
    window.activateWindow()

    def capture() -> None:
        pix = window.grab()
        pix.save(str(out), "PNG")
        print(f"wrote {out} ({out.stat().st_size} bytes)")
        app.quit()

    QTimer.singleShot(800, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
