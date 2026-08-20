"""PDF2Typora Studio entry point."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from config.config_manager import load_config
from gui.main_window import MainWindow
from gui.theme import apply_theme
from utils.logger import setup_logging


def main() -> int:
    config = load_config()
    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logging(log_level)

    app = QApplication(sys.argv)
    app.setApplicationName("PDF2Typora Studio")
    app.setOrganizationName("PDF2Typora")
    apply_theme(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
