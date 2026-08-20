"""Global visual theme for PDF2Typora Studio."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

APP_QSS = """
/* PDF2Typora — ink + sand studio theme */
* {
    font-family: "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog {
    background: #f3efe6;
    color: #1c2421;
}

/* Force readable body text (Windows dark-mode otherwise uses white labels) */
QLabel, QCheckBox, QRadioButton, QGroupBox {
    color: #1c2421;
}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    color: #1c2421;
    background: #fffdf8;
    border: 1px solid #cbbfaa;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #3d5c4f;
    selection-color: #f7f2e8;
}

/* Keep QComboBox styling minimal — heavy radius/padding breaks dropdown on Win+Fusion */
QComboBox {
    color: #1c2421;
    background: #fffdf8;
    border: 1px solid #cbbfaa;
    border-radius: 4px;
    padding: 4px 28px 4px 8px;
    min-height: 28px;
}
QComboBox:disabled {
    color: #8a8378;
    background: #ebe6dc;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border: none;
    border-left: 1px solid #d0c7b6;
    background: #f0ebe1;
}
QComboBox QAbstractItemView {
    color: #1c2421;
    background: #fffdf8;
    border: 1px solid #cbbfaa;
    selection-background-color: #3d5c4f;
    selection-color: #f7f2e8;
    outline: 0;
}

QListWidget, QTreeWidget, QTableWidget {
    color: #1c2421;
    background: #fffdf8;
    border: 1px solid #cbbfaa;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: #3d5c4f;
    selection-color: #f7f2e8;
}

QMenuBar {
    background: #1c2421;
    color: #f3efe6;
    padding: 4px 8px;
}
QMenuBar::item {
    color: #f3efe6;
}
QMenuBar::item:selected {
    background: #2f3d38;
}
QMenu {
    background: #24302c;
    color: #f3efe6;
    border: 1px solid #3d4f48;
}
QMenu::item {
    color: #f3efe6;
}
QMenu::item:selected {
    background: #3d5c4f;
}

QStatusBar {
    background: #1c2421;
    color: #c9d4ce;
}
QStatusBar QLabel {
    color: #c9d4ce;
    background: transparent;
}

QToolTip {
    background: #1c2421;
    color: #f3efe6;
    border: 1px solid #5a7268;
    padding: 6px;
}

/* Brand header — light text on dark only here */
#BrandBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #1c2421, stop:0.55 #24302c, stop:1 #2f463c);
    border-bottom: 1px solid #3d5c4f;
    min-height: 72px;
    max-height: 88px;
}
#BrandBar QLabel {
    background: transparent;
}
#BrandTitle {
    color: #f7f2e8;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
#BrandSubtitle {
    color: #b7c7be;
    font-size: 12px;
}

/* Pipeline rail */
#PipelineRail {
    background: #e8e2d6;
    border-right: 1px solid #d0c7b6;
    min-width: 360px;
    max-width: 520px;
}
#PipelineRail QLabel {
    color: #1c2421;
    background: transparent;
}
#PipelineHint {
    color: #4a5751;
    font-size: 12px;
    padding: 4px 2px 10px 2px;
}
QPushButton#PipelineStep {
    text-align: left;
    padding: 8px 10px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: #1c2421;
    font-size: 13px;
    min-height: 32px;
}
QPushButton#PipelineStep:hover {
    background: #f7f2e8;
    border-color: #cbbfaa;
    color: #1c2421;
}
QPushButton#PipelineStep[active="true"] {
    background: #1c2421;
    color: #f7f2e8;
    border-color: #1c2421;
    font-weight: 600;
}
QPushButton#PrimaryCta {
    background: #c45c26;
    color: #fff8f2;
    border: none;
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 14px;
    font-weight: 700;
    min-height: 44px;
}
QPushButton#PrimaryCta:hover {
    background: #a84c1d;
    color: #fff8f2;
}
QPushButton#PrimaryCta:disabled {
    background: #b7a999;
    color: #efe8dc;
}

QPushButton {
    background: #f7f2e8;
    border: 1px solid #cbbfaa;
    border-radius: 8px;
    padding: 7px 12px;
    color: #1c2421;
}
QPushButton:hover {
    background: #fffdf8;
    border-color: #a89274;
    color: #1c2421;
}
QPushButton:pressed {
    background: #e8e2d6;
}
QPushButton:disabled {
    color: #8a8378;
    background: #ebe6dc;
    border-color: #d5cec2;
}

QGroupBox {
    background: #f7f2e8;
    border: 1px solid #d0c7b6;
    border-radius: 10px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: #1c2421;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #2f463c;
}

QProgressBar {
    border: 1px solid #cbbfaa;
    border-radius: 6px;
    background: #efe8dc;
    color: #1c2421;
    text-align: center;
    min-height: 16px;
}
QProgressBar::chunk {
    background: #3d5c4f;
    border-radius: 5px;
}

QTabWidget::pane {
    border: 1px solid #d0c7b6;
    border-radius: 10px;
    background: #f7f2e8;
    top: -1px;
}
QTabBar::tab {
    background: #e8e2d6;
    color: #2a3531;
    padding: 10px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    min-width: 88px;
}
QTabBar::tab:selected {
    background: #f7f2e8;
    color: #1c2421;
    font-weight: 700;
    border: 1px solid #d0c7b6;
    border-bottom-color: #f7f2e8;
}
QTabBar::tab:hover:!selected {
    background: #f0ebe1;
    color: #1c2421;
}

QSplitter::handle {
    background: #d0c7b6;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}

QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:horizontal {
    height: 12px;
    background: #efe8dc;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #cbbfaa;
    min-width: 32px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #a89274;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar:vertical {
    width: 12px;
    background: #efe8dc;
}
QScrollBar::handle:vertical {
    background: #cbbfaa;
    min-height: 32px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #a89274;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QListWidget, QTreeWidget, QTableWidget {
    outline: none;
}
QListWidget::item, QTreeWidget::item, QTableWidget::item {
    color: #1c2421;
}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
    background: #3d5c4f;
    color: #f7f2e8;
}

#WorkspaceFrame, #SideFrame {
    background: #f7f2e8;
    border: 1px solid #d0c7b6;
    border-radius: 12px;
}
#WorkspaceFrame QLabel, #SideFrame QLabel {
    color: #1c2421;
    background: transparent;
}

#DropZone {
    border: 2px dashed #a89274;
    border-radius: 12px;
    background: #fffdf8;
    min-height: 140px;
}
#DropZone QLabel {
    color: #4a5751;
    background: transparent;
}
#DropZone:hover {
    border-color: #c45c26;
    background: #fff6ef;
}
"""


def apply_theme(app) -> None:
    """Apply Fusion + light palette so OS dark mode cannot bleach labels white."""
    app.setStyle("Fusion")

    ink = QColor("#1c2421")
    sand = QColor("#f3efe6")
    paper = QColor("#fffdf8")
    button = QColor("#f7f2e8")
    muted = QColor("#4a5751")
    accent = QColor("#3d5c4f")
    accent_text = QColor("#f7f2e8")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, sand)
    palette.setColor(QPalette.ColorRole.WindowText, ink)
    palette.setColor(QPalette.ColorRole.Base, paper)
    palette.setColor(QPalette.ColorRole.AlternateBase, button)
    palette.setColor(QPalette.ColorRole.Text, ink)
    palette.setColor(QPalette.ColorRole.Button, button)
    palette.setColor(QPalette.ColorRole.ButtonText, ink)
    palette.setColor(QPalette.ColorRole.BrightText, accent_text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1c2421"))
    palette.setColor(QPalette.ColorRole.ToolTipText, accent_text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, muted)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, accent_text)
    palette.setColor(QPalette.ColorRole.Link, accent)
    app.setPalette(palette)
    app.setStyleSheet(APP_QSS)
