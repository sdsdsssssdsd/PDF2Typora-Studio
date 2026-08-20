"""Ollama / AI settings dialog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ai.providers.ollama_api_client import OllamaModelInfo
from ai.providers.ollama_provider import OllamaVisionProvider
from ai.runtime.ollama_manager import (
    OllamaDirectoryInfo,
    OllamaMode,
    OllamaRuntimeManager,
    OllamaRuntimeState,
)
from config.config_manager import get_ollama_settings, load_config, save_user_config
from gui.dialogs.benchmark_dialog import BenchmarkDialog
from utils.hardware_probe import format_hardware_summary, probe_hardware
from utils.logger import get_logger
from workers.ollama_worker import (
    make_detect_worker,
    make_refresh_models_worker,
    make_start_worker,
    make_stop_worker,
)

logger = get_logger("ollama_dialog")


class OllamaDialog(QDialog):
    def __init__(
        self,
        manager: OllamaRuntimeManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ollama / AI 设置")
        self.setMinimumSize(720, 640)
        self.resize(780, 700)

        self._settings = get_ollama_settings(load_config())
        self.manager = manager or OllamaRuntimeManager(settings=self._settings)
        self.pool = QThreadPool.globalInstance()
        self._models: list[OllamaModelInfo] = []
        self._busy = False

        self._build_ui()
        self._apply_mode_ui()
        self._refresh_hardware()
        self._run_detect()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Mode
        mode_box = QGroupBox("运行模式")
        mode_layout = QVBoxLayout(mode_box)
        self.radio_bundled = QRadioButton("项目内置 Ollama")
        self.radio_external = QRadioButton("系统 / 外部 Ollama")
        self.radio_custom = QRadioButton("自定义 Server")
        self.mode_group = QButtonGroup(self)
        for i, btn in enumerate(
            (self.radio_bundled, self.radio_external, self.radio_custom)
        ):
            self.mode_group.addButton(btn, i)
            mode_layout.addWidget(btn)
            btn.toggled.connect(self._on_mode_changed)
        root.addWidget(mode_box)

        # Paths / server
        info_box = QGroupBox("Runtime / Server")
        form = QFormLayout(info_box)
        self.runtime_label = QLabel(str(self.manager.runtime_path))
        self.runtime_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.models_label = QLabel(str(self.manager.models_path))
        self.models_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.server_edit = QLineEdit(self.manager.resolve_base_url())
        self.status_label = QLabel("—")
        self.version_label = QLabel("—")
        form.addRow("Bundled Runtime:", self.runtime_label)
        form.addRow("Models:", self.models_label)
        form.addRow("Server:", self.server_edit)
        form.addRow("状态:", self.status_label)
        form.addRow("Version:", self.version_label)
        root.addWidget(info_box)

        # Actions
        btn_row = QHBoxLayout()
        self.btn_detect = QPushButton("检测")
        self.btn_start = QPushButton("启动")
        self.btn_stop = QPushButton("停止")
        self.btn_detect.clicked.connect(self._run_detect)
        self.btn_start.clicked.connect(self._run_start)
        self.btn_stop.clicked.connect(self._run_stop)
        btn_row.addWidget(self.btn_detect)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # Models
        model_box = QGroupBox("模型")
        model_layout = QVBoxLayout(model_box)
        filter_row = QHBoxLayout()
        self.chk_vision_only = QCheckBox("只显示 Vision 模型")
        self.chk_vision_only.setChecked(self._settings.get("vision_only_filter", True))
        self.chk_vision_only.toggled.connect(self._populate_model_table)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._run_refresh_models)
        filter_row.addWidget(self.chk_vision_only)
        filter_row.addWidget(self.btn_refresh)
        filter_row.addStretch()
        model_layout.addLayout(filter_row)

        self.model_table = QTableWidget(0, 5)
        self.model_table.setHorizontalHeaderLabels(
            ["模型", "大小", "参数", "量化", "能力"]
        )
        self.model_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.model_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.model_table.horizontalHeader().setStretchLastSection(True)
        model_layout.addWidget(self.model_table)

        select_row = QHBoxLayout()
        select_row.addWidget(QLabel("当前模型:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(280)
        select_row.addWidget(self.model_combo, stretch=1)
        self.btn_benchmark = QPushButton("Benchmark")
        self.btn_benchmark.clicked.connect(self._open_benchmark)
        select_row.addWidget(self.btn_benchmark)
        model_layout.addLayout(select_row)
        self.models_hint = QLabel("")
        self.models_hint.setWordWrap(True)
        model_layout.addWidget(self.models_hint)
        root.addWidget(model_box)

        # External directory inspect
        ext_box = QGroupBox("Existing Ollama 目录检测")
        ext_layout = QVBoxLayout(ext_box)
        ext_row = QHBoxLayout()
        self.ext_path_edit = QLineEdit()
        self.btn_browse_ext = QPushButton("选择 Ollama 目录")
        self.btn_browse_ext.clicked.connect(self._browse_external_dir)
        ext_row.addWidget(self.ext_path_edit)
        ext_row.addWidget(self.btn_browse_ext)
        ext_layout.addLayout(ext_row)
        self.ext_info_label = QLabel("尚未检测目录（只检测，不修改、不复制）")
        self.ext_info_label.setWordWrap(True)
        ext_layout.addWidget(self.ext_info_label)
        root.addWidget(ext_box)

        # Hardware
        hw_box = QGroupBox("硬件信息")
        hw_layout = QVBoxLayout(hw_box)
        self.hw_label = QLabel("探测中…")
        self.hw_label.setWordWrap(True)
        hw_layout.addWidget(self.hw_label)
        root.addWidget(hw_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Close
        )
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        root.addWidget(buttons)

        # Initial mode radio
        mode = self._settings.get("mode", "bundled")
        if mode == "external":
            self.radio_external.setChecked(True)
        elif mode == "custom_server":
            self.radio_custom.setChecked(True)
        else:
            self.radio_bundled.setChecked(True)

        selected = self._settings.get("selected_model") or ""
        if selected:
            self.model_combo.addItem(selected)
            self.model_combo.setCurrentText(selected)

    def _refresh_hardware(self) -> None:
        info = probe_hardware()
        self.hw_label.setText(format_hardware_summary(info))

    def _current_mode(self) -> OllamaMode:
        if self.radio_external.isChecked():
            return OllamaMode.EXTERNAL
        if self.radio_custom.isChecked():
            return OllamaMode.CUSTOM_SERVER
        return OllamaMode.BUNDLED

    def _on_mode_changed(self) -> None:
        self._apply_mode_ui()
        mode = self._current_mode()
        self.manager.mode = mode
        if mode == OllamaMode.EXTERNAL:
            self.server_edit.setText(self.manager.external_base_url)
        elif mode == OllamaMode.CUSTOM_SERVER:
            url = self.manager.custom_base_url or "http://127.0.0.1:11434"
            self.server_edit.setText(url)
        else:
            status = self.manager.get_runtime_status()
            self.server_edit.setText(status.base_url or "http://127.0.0.1:11435")

    def _apply_mode_ui(self) -> None:
        mode = self._current_mode()
        bundled = mode == OllamaMode.BUNDLED
        self.btn_start.setEnabled(bundled and not self._busy)
        # Stop only meaningful for app-managed process
        can_stop = bundled and bool(
            self.manager._process_info and self.manager._process_info.started_by_app
        )
        self.btn_stop.setEnabled(can_stop and not self._busy)
        self.server_edit.setReadOnly(mode == OllamaMode.BUNDLED)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (
            self.btn_detect,
            self.btn_start,
            self.btn_stop,
            self.btn_refresh,
            self.btn_benchmark,
        ):
            btn.setEnabled(not busy)
        self._apply_mode_ui()

    def _provider(self) -> OllamaVisionProvider:
        url = self.server_edit.text().strip().rstrip("/")
        return OllamaVisionProvider(
            url,
            model=self.model_combo.currentText().strip(),
            connect_timeout=self.manager.connect_seconds,
            request_timeout=self.manager.request_seconds,
        )

    def _run_detect(self) -> None:
        mode = self._current_mode()
        self.manager.mode = mode
        if mode != OllamaMode.BUNDLED:
            self.manager.external_base_url = self.server_edit.text().strip().rstrip("/")
            if mode == OllamaMode.CUSTOM_SERVER:
                self.manager.custom_base_url = self.server_edit.text().strip().rstrip("/")

        self._set_busy(True)
        worker = make_detect_worker(self.manager)

        def on_ok(result: object) -> None:
            self._set_busy(False)
            data = result if isinstance(result, dict) else {}
            status = data.get("status")
            health = data.get("health")
            bundled: OllamaDirectoryInfo | None = data.get("bundled")
            if status is not None:
                self._update_status_labels(status, health)
            if bundled and not bundled.executable_found and mode == OllamaMode.BUNDLED:
                self.status_label.setText("未安装 Bundled Runtime")
                self.models_hint.setText(
                    "runtime/ollama/ 中未找到 ollama.exe。可改用外部 Ollama，"
                    "或放入 standalone runtime。"
                )
            else:
                self._run_refresh_models()

        def on_err(msg: str) -> None:
            self._set_busy(False)
            self.status_label.setText(f"检测失败: {msg}")
            QMessageBox.warning(self, "检测失败", msg)

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        self.pool.start(worker)

    def _update_status_labels(self, status: Any, health: Any) -> None:
        state = getattr(status, "state", None)
        state_text = state.value if isinstance(state, OllamaRuntimeState) else str(state)
        running = getattr(status, "running", False)
        version = getattr(health, "version", None) or getattr(status, "version", None)
        err = getattr(health, "error", None) or getattr(status, "error", None)
        if running:
            self.status_label.setText(f"● Running ({state_text})")
        else:
            self.status_label.setText(f"○ {state_text}" + (f" — {err}" if err else ""))
        self.version_label.setText(version or "—")
        base = getattr(status, "base_url", "") or self.server_edit.text()
        if base and self._current_mode() == OllamaMode.BUNDLED:
            self.server_edit.setText(base)

    def _run_start(self) -> None:
        self._set_busy(True)
        worker = make_start_worker(self.manager)

        def on_ok(result: object) -> None:
            self._set_busy(False)
            self._update_status_labels(result, result)
            self._run_refresh_models()

        def on_err(msg: str) -> None:
            self._set_busy(False)
            self.status_label.setText(f"启动失败: {msg}")
            QMessageBox.critical(self, "启动失败", msg)

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        self.pool.start(worker)

    def _run_stop(self) -> None:
        self._set_busy(True)
        worker = make_stop_worker(self.manager)

        def on_ok(result: object) -> None:
            self._set_busy(False)
            self._update_status_labels(result, result)

        def on_err(msg: str) -> None:
            self._set_busy(False)
            QMessageBox.warning(self, "停止失败", msg)

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        self.pool.start(worker)

    def _run_refresh_models(self) -> None:
        if self._current_mode() != OllamaMode.BUNDLED:
            url = self.server_edit.text().strip().rstrip("/")
            if self._current_mode() == OllamaMode.CUSTOM_SERVER:
                self.manager.custom_base_url = url
            else:
                self.manager.external_base_url = url

        provider = self._provider()
        self._set_busy(True)
        worker = make_refresh_models_worker(provider)

        def on_ok(result: object) -> None:
            self._set_busy(False)
            self._models = list(result) if isinstance(result, list) else []
            if not self._models:
                health = provider.health_check()
                if health:
                    self.models_hint.setText(
                        "当前 Ollama 服务正常，但没有检测到本地模型。"
                    )
                else:
                    self.models_hint.setText("无法连接 Ollama，模型列表为空。")
            else:
                vision_n = sum(1 for m in self._models if m.is_vision)
                self.models_hint.setText(
                    f"共 {len(self._models)} 个模型，其中 Vision {vision_n} 个。"
                )
            self._populate_model_table()

        def on_err(msg: str) -> None:
            self._set_busy(False)
            self.models_hint.setText(f"刷新模型失败: {msg}")

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        self.pool.start(worker)

    def _populate_model_table(self) -> None:
        vision_only = self.chk_vision_only.isChecked()
        rows = [m for m in self._models if (m.is_vision or not vision_only)]
        self.model_table.setRowCount(len(rows))
        current = self.model_combo.currentText()
        self.model_combo.clear()
        for i, m in enumerate(rows):
            size = _format_bytes(m.size_bytes)
            cap = "✓ Vision" if m.is_vision else "Text only"
            values = [
                m.name,
                size,
                m.parameter_size or "—",
                m.quantization_level or "—",
                cap,
            ]
            for col, val in enumerate(values):
                self.model_table.setItem(i, col, QTableWidgetItem(val))
            self.model_combo.addItem(m.name)
        if current:
            idx = self.model_combo.findText(current)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)

    def _browse_external_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Ollama 目录")
        if not path:
            return
        self.ext_path_edit.setText(path)
        info = self.manager.inspect_runtime_directory(Path(path))
        lines = [
            f"路径: {info.root}",
            f"executable: {'✓ ' + str(info.executable) if info.executable_found else '✗'}",
            f"models: {'✓ ' + str(info.model_store) if info.model_store else '✗'}",
        ]
        if info.warnings:
            lines.append("警告: " + "; ".join(info.warnings))
        lines.append("（本阶段只检测，不会复制 Runtime 或模型）")
        self.ext_info_label.setText("\n".join(lines))

    def _open_benchmark(self) -> None:
        model = self.model_combo.currentText().strip()
        if not model:
            QMessageBox.information(self, "Benchmark", "请先选择模型。")
            return
        dlg = BenchmarkDialog(
            provider=self._provider(),
            model=model,
            base_url=self.server_edit.text().strip().rstrip("/"),
            parent=self,
        )
        dlg.exec()

    def _save_and_close(self) -> None:
        mode = self._current_mode()
        url = self.server_edit.text().strip().rstrip("/")
        updates: dict[str, Any] = {
            "ollama": {
                "mode": mode.value,
                "external": {"base_url": url if mode == OllamaMode.EXTERNAL else self.manager.external_base_url},
                "custom": {"base_url": url if mode == OllamaMode.CUSTOM_SERVER else self.manager.custom_base_url},
            },
            "ai": {
                "selected_model": self.model_combo.currentText().strip(),
                "vision_only_filter": self.chk_vision_only.isChecked(),
            },
        }
        save_user_config(updates)
        self.manager.mode = mode
        if mode == OllamaMode.EXTERNAL:
            self.manager.external_base_url = url
        elif mode == OllamaMode.CUSTOM_SERVER:
            self.manager.custom_base_url = url
        logger.info("Saved Ollama settings: mode=%s model=%s", mode.value, updates["ai"]["selected_model"])
        self.accept()


def _format_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / (1024**2):.1f} MB"
    return f"{n / (1024**3):.2f} GB"
