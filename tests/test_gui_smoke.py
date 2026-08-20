"""Import smoke tests — require display for full GUI test."""

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_main_window_creates(qapp):
    window = MainWindow()
    assert window.windowTitle() == "PDF2Typora Studio"
    assert window.batch_panel is not None
    assert window.review_queue is not None
    assert window.figure_panel is not None
    assert window.figure_review is not None
    assert window.assemble_panel is not None
    assert window.continuity_review is not None
    assert window.cleaner_panel is not None
    assert window.cleaner_review is not None
    assert window.final_panel is not None
    assert window.pipeline_nav is not None
    assert window.workspace_tabs is not None
    assert window.stage_stack.count() == 7
    window.close()
