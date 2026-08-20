"""Placeholder widgets for later phases."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QWidget, QVBoxLayout


def _placeholder(title: str) -> QWidget:
    w = QWidget()
    layout = QVBoxLayout(w)
    lbl = QLabel(f"{title}\n（后续阶段实现）")
    lbl.setStyleSheet("color: #999;")
    layout.addWidget(lbl)
    return w
