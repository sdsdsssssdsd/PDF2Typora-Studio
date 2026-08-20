"""Shared GUI helpers for model combo boxes."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox


def configure_model_combo(combo: QComboBox, *, max_visible: int = 16) -> None:
    """Make model dropdown tall enough to show many entries."""
    combo.setMaxVisibleItems(max_visible)
    combo.setMinimumContentsLength(18)
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    view = combo.view()
    # ~28px per row; ensure popup isn't clipped to ~3 items
    view.setMinimumHeight(max_visible * 28)
    view.setMinimumWidth(max(combo.minimumWidth(), 280))
