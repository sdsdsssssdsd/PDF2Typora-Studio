"""外部 API 配置模块 — 自备 Key（BYOK），Key 存 OS keyring。"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ai.providers.api_provider_manager import ApiProviderManager
from config.config_manager import load_config, save_user_config
from gui.widgets.combo_utils import configure_model_combo
from services.api_credential_store import ApiCredentialStore

# Shared choices for both transcription and cleanup
_ROUTE_CHOICES: list[tuple[str, str]] = [
    ("本地 Ollama（免费，需本机已装模型）", "ollama"),
    ("OpenAI 兼容（自备 Key，按量付费）", "openai_compatible"),
    ("通义千问 / DashScope（自备 Key，按量付费）", "qwen_vision"),
    ("DeepSeek（自备 Key；公开接口通常无 Vision）", "deepseek"),
    ("不使用云端 / 跳过", "none"),
]

_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "openai_compatible": "OpenAI 兼容网关",
    "qwen_vision": "通义千问（DashScope 兼容模式）",
}


class APISettingsDialog(QDialog):
    """BYOK remote API credentials + unified task routing."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("外部 API 配置")
        self.resize(680, 620)
        self._cfg = load_config()
        self._store = ApiCredentialStore()
        self._manager = ApiProviderManager(self._cfg, self._store)
        self._last_models: list[str] = []
        self._syncing_routes = False

        root = QVBoxLayout(self)

        bill = QLabel(
            "<b>说明：本软件不提供免费云端额度。</b><br>"
            "OpenAI 兼容 / 通义千问 / DeepSeek 都是你自己账号的付费 API："
            "在官网开通并充值后，把 <b>Base URL + API Key</b> 填到下方，"
            "调用费用由对应厂商向你收取。Key 只存系统凭据库，不会写入 yaml。"
        )
        bill.setWordWrap(True)
        bill.setTextFormat(Qt.TextFormat.RichText)
        bill.setStyleSheet(
            "background:#fff6ef; border:1px solid #e0c4a8; border-radius:8px; padding:10px;"
        )
        root.addWidget(bill)

        # --- routing ---
        route_box = QGroupBox("任务路由")
        route_form = QFormLayout(route_box)

        self.same_api = QCheckBox("页面转录 与 文本清理 使用同一 API")
        self.same_api.setChecked(True)
        self.same_api.toggled.connect(self._on_same_api_toggled)
        route_form.addRow(self.same_api)

        self.route_vision = QComboBox()
        self.route_clean = QComboBox()
        for label, data in _ROUTE_CHOICES:
            self.route_vision.addItem(label, data)
            self.route_clean.addItem(label, data)
        route_form.addRow("页面转录（看图识字）:", self.route_vision)
        route_form.addRow("文本清理（改 Markdown）:", self.route_clean)
        self.route_vision.currentIndexChanged.connect(self._on_vision_route_changed)
        root.addWidget(route_box)

        tip = QLabel(
            "同一 API：例如都选 DeepSeek 或都选「OpenAI 兼容」，填一套 Key 即可两用。\n"
            "在 Hybrid OCR+API 引擎下，「页面转录」= 本地 PDF/OCR 证据 + 文本 API 重建 Markdown"
            "（DeepSeek 无 Vision 也能做转录）。本地免费请选 Ollama + Vision Only。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#4a5751;")
        root.addWidget(tip)

        # --- provider credentials ---
        cred_box = QGroupBox("凭证（选一个 Provider 填写你的 Key）")
        form = QFormLayout(cred_box)

        self.provider = QComboBox()
        for pid, label in _PROVIDER_LABELS.items():
            self.provider.addItem(label, pid)
        form.addRow("配置目标:", self.provider)

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("例如 https://api.openai.com/v1")
        form.addRow("Base URL:", self.base_url)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("在厂商控制台复制的 sk-… / API Key")
        form.addRow("API Key（你自己的）:", self.api_key)

        self.model = QComboBox()
        self.model.setEditable(True)
        configure_model_combo(self.model, max_visible=16)
        form.addRow("默认模型:", self.model)

        self.cap_text = QCheckBox("Text")
        self.cap_json = QCheckBox("JSON")
        self.cap_vision = QCheckBox("Vision（仅 Vision Only 路径需要）")
        self.cap_text.setChecked(True)
        self.cap_json.setChecked(True)
        caps = QHBoxLayout()
        caps.addWidget(self.cap_text)
        caps.addWidget(self.cap_json)
        caps.addWidget(self.cap_vision)
        form.addRow("能力标记:", caps)
        root.addWidget(cred_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        if not self._store.is_persistent():
            warn = QLabel(
                "未安装 keyring：API Key 仅保存在当前进程内存，关闭后丢失。"
                "请执行：pip install keyring"
            )
            warn.setWordWrap(True)
            root.addWidget(warn)

        btns = QHBoxLayout()
        self.test_btn = QPushButton("测试连接并拉取模型")
        self.test_btn.clicked.connect(self._on_test)
        btns.addWidget(self.test_btn)
        btns.addStretch()
        root.addLayout(btns)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._on_save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self.provider.currentIndexChanged.connect(self._load_provider)
        self._load_routing()
        self._load_provider()
        self._on_same_api_toggled(self.same_api.isChecked())

    def _provider_id(self) -> str:
        return str(self.provider.currentData() or "openai_compatible")

    def _set_combo_data(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _on_same_api_toggled(self, checked: bool) -> None:
        self.route_clean.setEnabled(not checked)
        if checked:
            self._sync_clean_from_vision()

    def _on_vision_route_changed(self, _index: int = 0) -> None:
        if self.same_api.isChecked():
            self._sync_clean_from_vision()

    def _sync_clean_from_vision(self) -> None:
        if self._syncing_routes:
            return
        self._syncing_routes = True
        try:
            self._set_combo_data(
                self.route_clean, str(self.route_vision.currentData() or "ollama")
            )
        finally:
            self._syncing_routes = False

    def _load_routing(self) -> None:
        api = self._cfg.get("api_providers") or {}
        routing = api.get("task_routing") or {}
        vision = str(routing.get("page_transcription") or "ollama")
        clean = str(routing.get("text_cleanup") or vision)
        same = bool(routing.get("same_api_for_clean_and_vision", vision == clean))
        self.same_api.blockSignals(True)
        self.same_api.setChecked(same)
        self.same_api.blockSignals(False)
        self._set_combo_data(self.route_vision, vision)
        self._set_combo_data(self.route_clean, clean if not same else vision)

    def _load_provider(self) -> None:
        pid = self._provider_id()
        api = (self._cfg.get("api_providers") or {}).get("providers") or {}
        raw = api.get(pid) or {}
        defaults = {
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "supports_vision": False,
            },
            "openai_compatible": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "supports_vision": True,
            },
            "qwen_vision": {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-vl-plus",
                "supports_vision": True,
            },
        }
        d = defaults.get(pid, {})
        self.base_url.setText(str(raw.get("base_url") or d.get("base_url") or ""))
        model = str(raw.get("model") or d.get("model") or "")
        self.model.clear()
        if model:
            self.model.addItem(model)
            self.model.setEditText(model)
        self.cap_text.setChecked(bool(raw.get("supports_text", True)))
        self.cap_json.setChecked(bool(raw.get("supports_json", True)))
        self.cap_vision.setChecked(
            bool(raw.get("supports_vision", d.get("supports_vision", False)))
        )
        cid = raw.get("credential_id") or self._store.credential_id(pid)
        secret = self._store.get_secret(str(cid))
        self.api_key.setText(secret or "")
        self.status.setText(
            f"credential_id={cid} · 持久化={self._store.is_persistent()} · 费用由厂商按你的用量结算"
        )

    def _on_test(self) -> None:
        if not self.api_key.text().strip():
            QMessageBox.information(
                self,
                "需要 API Key",
                "请先填入你在厂商控制台申请的 Key（本软件不提供免费云端调用）。",
            )
            return
        self._persist_to_manager_memory()
        pid = self._provider_id()
        result = self._manager.probe(pid)
        if not result.ok:
            self.status.setText(f"失败：{result.error}")
            QMessageBox.warning(self, "连接失败", result.error or "unknown")
            return

        self._last_models = list(result.models)
        current = self.model.currentText().strip()
        self.model.clear()
        for name in result.models:
            self.model.addItem(name)
        if current:
            idx = self.model.findText(current)
            if idx >= 0:
                self.model.setCurrentIndex(idx)
            else:
                self.model.setEditText(current)
        elif self.model.count():
            self.model.setCurrentIndex(0)

        if result.supports_vision is not None:
            self.cap_vision.setChecked(bool(result.supports_vision))
        preview = ", ".join(result.models[:12]) or "(无模型)"
        more = f" 等共 {len(result.models)} 个" if len(result.models) > 12 else ""
        self.status.setText(f"连接成功 · 模型：{preview}{more}")
        if result.supports_vision is False and pid == "deepseek":
            self.status.setText(
                self.status.text()
                + " · DeepSeek 无 Vision：请配合 Hybrid（OCR+文本重建）使用，不要选 Vision Only。"
            )

    def _persist_to_manager_memory(self) -> None:
        pid = self._provider_id()
        key = self.api_key.text().strip()
        if key:
            self._manager.set_api_key(pid, key)
        providers = dict((self._cfg.get("api_providers") or {}).get("providers") or {})
        providers[pid] = {
            "display_name": _PROVIDER_LABELS.get(pid, pid),
            "base_url": self.base_url.text().strip(),
            "model": self.model.currentText().strip(),
            "credential_id": self._store.credential_id(pid),
            "supports_text": self.cap_text.isChecked(),
            "supports_json": self.cap_json.isChecked(),
            "supports_vision": self.cap_vision.isChecked(),
        }
        self._cfg.setdefault("api_providers", {})["providers"] = providers
        self._manager = ApiProviderManager(self._cfg, self._store)

    def _on_save(self) -> None:
        if self.same_api.isChecked():
            self._sync_clean_from_vision()

        vision = str(self.route_vision.currentData() or "ollama")
        clean = str(self.route_clean.currentData() or vision)
        if self.same_api.isChecked():
            clean = vision

        # External vision/reconstruction without key
        if vision not in {"ollama", "none"} and not self.api_key.text().strip():
            # allow if key already in keyring for that provider
            if not self._store.get_secret(self._store.credential_id(vision)):
                # also check currently edited provider
                if self._provider_id() != vision or not self.api_key.text().strip():
                    QMessageBox.warning(
                        self,
                        "缺少 API Key",
                        f"页面转录已选「{vision}」，请先在下方为对应 Provider 填写你自己的 API Key。\n"
                        "云端调用不是免费的，费用由你向厂商支付。",
                    )
                    return

        pid = self._provider_id()
        key = self.api_key.text().strip()
        cid = self._store.credential_id(pid)
        if key:
            self._store.set_secret(cid, key)

        existing = dict(
            ((load_config().get("api_providers") or {}).get("providers") or {})
        )
        existing[pid] = {
            "display_name": _PROVIDER_LABELS.get(pid, pid),
            "base_url": self.base_url.text().strip(),
            "model": self.model.currentText().strip(),
            "credential_id": cid,
            "supports_text": self.cap_text.isChecked(),
            "supports_json": self.cap_json.isChecked(),
            "supports_vision": self.cap_vision.isChecked(),
        }
        updates: dict[str, Any] = {
            "api_providers": {
                "providers": existing,
                "task_routing": {
                    "same_api_for_clean_and_vision": self.same_api.isChecked(),
                    "text_cleanup": clean,
                    "page_transcription": vision,
                    "visual_verification": vision,
                    "figure_locator": vision,
                },
            }
        }
        save_user_config(updates)
        self.accept()
