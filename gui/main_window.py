"""Main application window."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ai.model_profiles import ModelProfileStore
from ai.runtime.ollama_manager import OllamaRuntimeManager
from ai.providers.ollama_provider import OllamaVisionProvider
from config.config_manager import get_ollama_settings, load_config
from core.models import (
    BatchItemStatus,
    ModelQualification,
    PipelineStage,
    RenderRequest,
    RenderSettings,
    StageStatus,
)
from core.project import Project
from gui.dialogs.ollama_dialog import OllamaDialog
from gui.widgets.assemble_panel import AssemblePanel
from gui.widgets.batch_transcription_panel import BatchTranscriptionPanel
from gui.widgets.benchmark_panel import BenchmarkPanel
from gui.widgets.cleaner_panel import CleanerPanel
from gui.widgets.cleaner_review import CleanerReview
from gui.widgets.continuity_review import ContinuityReview
from gui.widgets.figure_panel import FigurePanel
from gui.widgets.figure_review import FigureReview
from gui.widgets.final_panel import FinalPanel
from gui.widgets.import_panel import ImportPanel
from gui.widgets.log_panel import LogPanel
from gui.widgets.page_list import PageList
from gui.widgets.page_viewer import PageViewer
from gui.widgets.pipeline_nav import PipelineNav
from gui.widgets.render_panel import RenderPanel
from gui.widgets.review_queue import ReviewQueue
from gui.widgets.transcription_panel import TranscriptionPanel
from services.assemble_readiness_service import AssembleReadinessService
from services.batch_cleaner_service import BatchCleanerService
from services.batch_transcription_service import BatchTranscriptionService
from services.clean_readiness_service import CleanReadinessService
from services.continuity_analyzer import ContinuityAnalyzer
from services.deterministic_cleaner import DeterministicCleaner
from services.final_freeze_service import FinalFreezeService
from services.final_readiness_service import FinalReadinessService
from services.figure_readiness_service import FigureReadinessService
from services.figure_review_service import FigureReviewService
from services.page_source_resolver import PageSourceResolver
from services.project_service import ProjectService
from services.raw_page_splitter import RawPageSplitter
from services.render_service import validate_dpi
from services.transcription_service import TranscriptionService
from services.typora_launcher import TyporaLauncher
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256
from utils.logger import get_logger
from utils.page_range import PageRangeError, all_pages, parse_page_range
from workers.assembly_worker import AssemblyWorker
from workers.batch_cleaner_worker import BatchCleanerWorker
from workers.batch_figure_worker import BatchFigureWorker
from workers.batch_transcription_worker import BatchTranscriptionWorker
from workers.final_worker import FinalWorker
from workers.import_worker import ImportWorker
from workers.ollama_worker import OllamaTaskWorker
from workers.render_worker import RenderWorker

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF2Typora Studio")
        self.setMinimumSize(1280, 800)
        self.resize(1600, 960)

        self._project_service = ProjectService()
        self._current_project: Project | None = None
        self._ollama_manager = OllamaRuntimeManager(
            settings=get_ollama_settings(load_config())
        )
        note = self._ollama_manager.auto_configure(persist=True)
        self._ollama_boot_note = note
        self._pool = QThreadPool.globalInstance()
        self._render_worker: RenderWorker | None = None
        self._import_worker: ImportWorker | None = None
        self._model_refresh_worker: OllamaTaskWorker | None = None
        self._batch_worker: BatchTranscriptionWorker | None = None
        self._figure_worker: BatchFigureWorker | None = None
        self._assembly_worker: AssemblyWorker | None = None
        self._cleaner_worker: BatchCleanerWorker | None = None
        self._final_worker: FinalWorker | None = None
        self._last_validation_clean_hash: str = ""
        self._batch_run_id: int | None = None
        self._profiles = ModelProfileStore()
        self._continuity_candidates: list = []

        # ---- widgets (logic unchanged; layout reorganized) ----
        self.import_panel = ImportPanel()
        self.import_panel.import_requested.connect(self._on_import_clicked)
        self.import_panel.pdf_selected.connect(self._on_pdf_selected)

        self.render_panel = RenderPanel()
        self.render_panel.start_requested.connect(self._on_start_render)
        self.render_panel.cancel_requested.connect(self._on_cancel_render)

        self.batch_panel = BatchTranscriptionPanel()
        self.batch_panel.start_requested.connect(self._on_start_batch)
        self.batch_panel.pause_requested.connect(self._on_pause_batch)
        self.batch_panel.resume_requested.connect(self._on_resume_batch)
        self.batch_panel.cancel_requested.connect(self._on_cancel_batch)
        self.batch_panel.qualify_requested.connect(self._on_qualify_models)
        self.batch_panel.refresh_models_requested.connect(self._on_refresh_vision_models)
        self.batch_panel.open_api_settings_requested.connect(self._open_api_settings)

        self.figure_panel = FigurePanel()
        self.figure_panel.start_requested.connect(self._on_start_figures)
        self.figure_panel.cancel_requested.connect(self._on_cancel_figures)
        self.figure_panel.open_review_requested.connect(self._on_open_figure_workspace)

        self.assemble_panel = AssemblePanel()
        self.assemble_panel.check_continuity_requested.connect(self._on_check_continuity)
        self.assemble_panel.open_continuity_requested.connect(self._on_open_continuity_workspace)
        self.assemble_panel.assemble_requested.connect(self._on_start_assemble)
        self.assemble_panel.allow_unresolved.toggled.connect(
            lambda _: self._refresh_assemble_panel()
        )

        self.cleaner_panel = CleanerPanel()
        self.cleaner_panel.analyze_requested.connect(self._on_cleaner_analyze)
        self.cleaner_panel.start_requested.connect(self._on_start_cleaner)
        self.cleaner_panel.pause_requested.connect(self._on_pause_cleaner)
        self.cleaner_panel.resume_requested.connect(self._on_resume_cleaner)
        self.cleaner_panel.cancel_requested.connect(self._on_cancel_cleaner)
        self.cleaner_panel.open_clean_requested.connect(self._on_open_clean_md)

        self.final_panel = FinalPanel()
        export_cfg = load_config().get("export") or {}
        default_export = export_cfg.get("default_root") or "./exports"
        from config.config_manager import project_root as app_root

        exp_path = Path(default_export)
        if not exp_path.is_absolute():
            exp_path = (app_root() / exp_path).resolve()
        self.final_panel.set_export_root(str(exp_path))
        self.final_panel.validate_requested.connect(self._on_final_validate)
        self.final_panel.freeze_requested.connect(self._on_final_freeze)
        self.final_panel.export_requested.connect(self._on_final_export)
        self.final_panel.open_export_dir_requested.connect(self._on_open_export_dir)
        self.final_panel.open_typora_requested.connect(self._on_open_typora)
        self.final_panel.choose_export_dir_requested.connect(self._on_choose_export_dir)

        self.benchmark_panel = BenchmarkPanel()
        self.benchmark_panel.run_requested.connect(self._on_run_engine_benchmark)
        self.benchmark_panel.open_report_requested.connect(self._on_open_benchmark_report)

        self.page_list = PageList()
        self.page_list.page_selected.connect(self._on_page_selected)
        self.page_list.rerender_requested.connect(self._on_rerender_page)
        self.page_viewer = PageViewer()
        self.transcription_panel = TranscriptionPanel()
        self.transcription_panel.accepted.connect(self._on_transcription_accepted)
        self.transcription_panel.open_api_settings_requested.connect(
            self._open_api_settings
        )
        self.transcription_panel.refresh_models_requested.connect(
            self._on_refresh_vision_models
        )

        self.review_queue = ReviewQueue()
        self.review_queue.page_selected.connect(self._on_review_page)
        self.review_queue.accept_requested.connect(self._on_review_accept)
        self.review_queue.skip_requested.connect(self._on_review_skip)
        self.review_queue.retranscribe_requested.connect(self._on_review_retranscribe)
        self.review_queue.figure_selected.connect(self._on_figure_queue_selected)
        self.review_queue.cleaner_page_selected.connect(self._on_cleaner_queue_selected)

        self.figure_review = FigureReview()
        self.figure_review.open_figure_requested.connect(self._on_figure_review_open)
        self.figure_review.preview_requested.connect(self._on_figure_preview)
        self.figure_review.accept_requested.connect(self._on_figure_accept)
        self.figure_review.skip_requested.connect(self._on_figure_skip)
        self.figure_review.not_figure_requested.connect(self._on_figure_not_a_figure)
        self.figure_review.marker_placement_requested.connect(
            self._on_figure_marker_placement
        )

        self.continuity_review = ContinuityReview()
        self.continuity_review.save_requested.connect(self._on_continuity_save)
        self.continuity_review.next_requested.connect(self.continuity_review.select_next)

        self.cleaner_review = CleanerReview()
        self.cleaner_review.accept_cleaned_requested.connect(self._on_cleaner_accept)
        self.cleaner_review.keep_source_requested.connect(self._on_cleaner_keep_source)
        self.cleaner_review.reprocess_requested.connect(self._on_cleaner_reprocess)

        self.log_panel = LogPanel()

        self._build_shell()
        self._build_menu()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("按左侧流程操作：导入 → 渲染 → 转录 → … → 导出")
        self._log("PDF2Typora Studio 已启动 — 按左侧「转换流程」逐步操作")
        if getattr(self, "_ollama_boot_note", ""):
            self._log(self._ollama_boot_note)
        # Restore page engine from config
        eng = str((load_config().get("transcription") or {}).get("page_engine") or "hybrid_ocr_api")
        self.batch_panel.set_engine(eng)
        self.transcription_panel.set_page_engine(eng)
        self.batch_panel.engine_combo.currentIndexChanged.connect(self._on_page_engine_changed)
        self._sync_transcription_server()
        QTimer.singleShot(400, lambda: self._on_refresh_vision_models(interactive=False))

    def _build_shell(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Brand header
        brand = QFrame()
        brand.setObjectName("BrandBar")
        brand_l = QVBoxLayout(brand)
        brand_l.setContentsMargins(20, 12, 20, 12)
        title = QLabel("PDF2Typora Studio")
        title.setObjectName("BrandTitle")
        title.setFont(QFont("Segoe UI Semibold", 20))
        sub = QLabel("PDF → Typora Markdown · 左侧按步骤转换，中间查看结果，右侧处理待审")
        sub.setObjectName("BrandSubtitle")
        brand_l.addWidget(title)
        brand_l.addWidget(sub)
        root.addWidget(brand)

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)
        root.addLayout(body, stretch=1)

        # Left: pipeline + stage controls (splitter so stage area can grow)
        left = QFrame()
        left.setObjectName("PipelineRail")
        left.setMinimumWidth(360)
        left.setMaximumWidth(520)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)

        self.pipeline_nav = PipelineNav()
        self.pipeline_nav.step_selected.connect(self._on_pipeline_step)
        self.pipeline_nav.run_current_requested.connect(self._on_pipeline_run)

        stage_scroll = QScrollArea()
        stage_scroll.setWidgetResizable(True)
        stage_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 内容比左栏窄时会出现左右滑动条，避免文案/下拉被裁切
        stage_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        stage_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        stage_scroll.setMinimumHeight(280)
        stage_host = QWidget()
        stage_host.setMinimumWidth(520)
        stage_host_l = QVBoxLayout(stage_host)
        stage_host_l.setContentsMargins(10, 4, 10, 10)
        self.stage_stack = QStackedWidget()
        self.stage_stack.setMinimumWidth(500)
        self._stage_index = {
            "import": 0,
            "render": 1,
            "transcribe": 2,
            "figures": 3,
            "assemble": 4,
            "clean": 5,
            "final": 6,
            "benchmark": 7,
        }
        for panel in (
            self.import_panel,
            self.render_panel,
            self.batch_panel,
            self.figure_panel,
            self.assemble_panel,
            self.cleaner_panel,
            self.final_panel,
            self.benchmark_panel,
        ):
            panel.setMinimumWidth(500)
            self.stage_stack.addWidget(panel)
        stage_host_l.addWidget(self.stage_stack)
        stage_scroll.setWidget(stage_host)
        self._stage_scroll = stage_scroll

        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.setChildrenCollapsible(False)
        left_split.addWidget(self.pipeline_nav)
        left_split.addWidget(stage_scroll)
        left_split.setStretchFactor(0, 0)
        left_split.setStretchFactor(1, 1)
        left_split.setSizes([260, 600])
        left_l.addWidget(left_split)
        body.addWidget(left)

        # Center + right vertical split (workspace / log)
        mid_split = QSplitter(Qt.Orientation.Vertical)
        mid_split.setChildrenCollapsible(False)

        work_row = QSplitter(Qt.Orientation.Horizontal)
        work_row.setChildrenCollapsible(False)

        workspace = QFrame()
        workspace.setObjectName("WorkspaceFrame")
        ws_l = QVBoxLayout(workspace)
        ws_l.setContentsMargins(8, 8, 8, 8)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setDocumentMode(True)

        # Tab: pages
        pages_split = QSplitter(Qt.Orientation.Horizontal)
        pages_split.setChildrenCollapsible(False)
        self.page_list.setMinimumWidth(160)
        self.page_viewer.setMinimumWidth(360)
        pages_split.addWidget(self.page_list)
        pages_split.addWidget(self.page_viewer)
        pages_split.setStretchFactor(0, 1)
        pages_split.setStretchFactor(1, 4)
        pages_split.setSizes([200, 700])
        self.workspace_tabs.addTab(pages_split, "页面预览")

        self.transcription_panel.setMinimumWidth(420)
        self.workspace_tabs.addTab(self.transcription_panel, "转录结果")

        self.figure_review.setMinimumWidth(480)
        self.workspace_tabs.addTab(self.figure_review, "Figure 审阅")

        self.continuity_review.setMinimumWidth(420)
        self.workspace_tabs.addTab(self.continuity_review, "连续性")

        self.cleaner_review.setMinimumWidth(420)
        self.workspace_tabs.addTab(self.cleaner_review, "Cleaner 审阅")

        ws_l.addWidget(self.workspace_tabs)
        work_row.addWidget(workspace)

        # Right: review queue
        side = QFrame()
        side.setObjectName("SideFrame")
        side.setMinimumWidth(280)
        side_l = QVBoxLayout(side)
        side_l.setContentsMargins(8, 8, 8, 8)
        side_title = QLabel("待审队列")
        side_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #1c2421;")
        side_l.addWidget(side_title)
        side_l.addWidget(self.review_queue, stretch=1)
        work_row.addWidget(side)
        work_row.setStretchFactor(0, 5)
        work_row.setStretchFactor(1, 2)
        work_row.setSizes([1000, 320])

        mid_split.addWidget(work_row)
        self.log_panel.setMinimumHeight(100)
        mid_split.addWidget(self.log_panel)
        mid_split.setStretchFactor(0, 6)
        mid_split.setStretchFactor(1, 1)
        mid_split.setSizes([720, 140])

        body.addWidget(mid_split, stretch=1)

    def _on_pipeline_step(self, key: str) -> None:
        idx = self._stage_index.get(key, 0)
        self.stage_stack.setCurrentIndex(idx)
        # Jump workspace to a helpful tab for this stage
        tab_map = {
            "import": 0,
            "render": 0,
            "transcribe": 1,
            "figures": 2,
            "assemble": 3,
            "clean": 4,
            "final": 0,
        }
        self.workspace_tabs.setCurrentIndex(tab_map.get(key, 0))
        tips = {
            "import": "拖入 PDF，或点「导入 PDF」",
            "render": "确认 DPI 后点「开始渲染」",
            "transcribe": "先选转录方式（Hybrid=OCR；Vision Only=看图），再运行",
            "figures": "点「分析 Figures」，有待审则打开 Figure 审阅",
            "assemble": "确认就绪后点「Assemble」生成 raw.md",
            "clean": "可先分析，再「开始清洗」",
            "final": "验证 → 生成 final.md → 导出 Typora",
            "benchmark": "勾选引擎后运行 Phase 9.5.2 Benchmark",
        }
        self.statusBar().showMessage(tips.get(key, ""))
        if key == "assemble":
            self._refresh_assemble_panel()
        elif key == "figures":
            self._update_figure_readiness()

    def _on_pipeline_run(self, key: str) -> None:
        """Primary CTA — run the obvious action for the current step."""
        self._on_pipeline_step(key)
        try:
            if key == "import":
                self._on_import_clicked()
            elif key == "render":
                self._on_start_render()
            elif key == "transcribe":
                if self.batch_panel.start_btn.isEnabled():
                    self._on_start_batch()
                else:
                    self._on_qualify_models()
            elif key == "figures":
                self._on_start_figures()
            elif key == "assemble":
                self._on_start_assemble()
            elif key == "clean":
                self._on_start_cleaner()
            elif key == "final":
                self._on_final_validate()
            elif key == "benchmark":
                self._on_run_engine_benchmark()
        except Exception as exc:  # noqa: BLE001
            logger.exception("pipeline run failed")
            QMessageBox.warning(self, "无法执行", str(exc))

    def _on_open_figure_workspace(self) -> None:
        self.pipeline_nav.select("figures")
        self.workspace_tabs.setCurrentIndex(2)
        self._reload_figure_review()

    def _on_open_continuity_workspace(self) -> None:
        self.pipeline_nav.select("assemble")
        self.workspace_tabs.setCurrentIndex(3)
        self._on_check_continuity()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("设置")
        act_ai = QAction("本地 Ollama 设置…", self)
        act_ai.triggered.connect(self._open_ollama_dialog)
        menu.addAction(act_ai)
        act_api = QAction("外部 API 配置…", self)
        act_api.triggered.connect(self._open_api_settings)
        menu.addAction(act_api)

        help_menu = self.menuBar().addMenu("帮助")
        act_flow = QAction("转换流程说明", self)
        act_flow.triggered.connect(self._show_flow_help)
        help_menu.addAction(act_flow)

    def _show_flow_help(self) -> None:
        QMessageBox.information(
            self,
            "转换流程",
            "1 导入 PDF\n"
            "2 渲染页面图片\n"
            "3 AI 批量转录（可先做资格测试）\n"
            "4 处理插图并审阅\n"
            "5 拼装 raw.md\n"
            "6 清洗 Markdown\n"
            "7 验证并导出 Typora 项目\n\n"
            "左侧橙色按钮会执行当前步骤的主操作。",
        )

    def _on_page_engine_changed(self, _index: int = 0) -> None:
        mode = self.batch_panel.selected_engine()
        self.transcription_panel.set_page_engine(mode)
        from config.config_manager import save_user_config

        save_user_config({"transcription": {"page_engine": mode}})
        self._update_engine_labels()
        self._log(f"页面转录方式 → {mode}")
        tip = {
            "hybrid_ocr_api": "Hybrid：本地 OCR + 文本 API（不必 Vision）",
            "vision_only": "Vision Only：需要多模态看图模型",
            "pdf_ocr_local": "PDF+OCR+本地 LLM",
            "parser_only": "仅文档解析器",
        }.get(mode, mode)
        self.statusBar().showMessage(tip, 8000)
        # Switch model list to match engine (API vs local Vision)
        self._on_refresh_vision_models(interactive=False)

    def _api_route_label(self, route: str) -> str:
        return {
            "deepseek": "DeepSeek API",
            "openai_compatible": "OpenAI 兼容 API",
            "qwen_vision": "通义千问 API",
            "ollama": "本地 Ollama",
            "none": "未配置",
        }.get(route, f"API:{route}")

    def _reconstruction_route(self) -> str:
        """Route used for Hybrid text reconstruction models."""
        from ai.providers.provider_factory import cleanup_route, vision_route

        v = vision_route()
        if v not in {"", "ollama", "none"}:
            return v
        c = cleanup_route()
        if c not in {"", "ollama", "none"}:
            return c
        return v or "ollama"

    def _open_ollama_dialog(self) -> None:
        dlg = OllamaDialog(manager=self._ollama_manager, parent=self)
        dlg.exec()
        self._sync_transcription_server()
        self.transcription_panel.refresh_models()
        self._refresh_batch_models()

    def _open_api_settings(self) -> None:
        from gui.dialogs.api_settings_dialog import APISettingsDialog

        dlg = APISettingsDialog(parent=self)
        if dlg.exec():
            self._log("已保存外部 API 配置")
            self._update_engine_labels()
            self._on_refresh_vision_models(interactive=True)

    def _update_engine_labels(self) -> None:
        from ai.providers.provider_factory import (
            cleanup_route,
            is_external_vision_route,
            vision_route,
        )

        v = vision_route()
        c = cleanup_route()
        if v == c:
            if is_external_vision_route(v):
                text = f"API：{v}（文本重建/清理）· 看字靠本地 OCR"
            else:
                text = "本地 Ollama · 若 Hybrid 则 OCR+本地；Vision Only 才看图"
        else:
            text = f"转录API={v} · 清理API={c} · Hybrid 时 OCR 在本地"
        mode = self.batch_panel.selected_engine()
        if mode == "vision_only":
            text = f"方式：Vision 看图 · {text}"
        elif mode.startswith("hybrid") or mode == "pdf_ocr_local":
            text = f"方式：OCR+文本重建 · {text}"
        else:
            text = f"方式：{mode} · {text}"
        self.batch_panel.set_engine_label(text)
        self.transcription_panel.set_engine_label(text)

    def _sync_transcription_server(self) -> None:
        from ai.providers.provider_factory import is_external_vision_route, vision_route

        if is_external_vision_route(vision_route()):
            provider = self._provider()
            self.transcription_panel.set_provider(provider)
            self._update_engine_labels()
            return
        status = self._ollama_manager.get_runtime_status()
        url = (
            self._ollama_manager.find_reachable_base_url()
            or status.base_url
            or self._ollama_manager.resolve_base_url()
        )
        self.transcription_panel.set_ollama_base_url(url)
        self._update_engine_labels()

    def _provider(self):
        from ai.providers.provider_factory import create_vision_provider

        return create_vision_provider(ollama_manager=self._ollama_manager)

    def _on_refresh_vision_models(self, interactive: bool = True) -> None:
        from ai.providers.provider_factory import is_external_route

        if self._model_refresh_worker is not None:
            self.statusBar().showMessage("正在刷新模型…", 2000)
            return

        self._model_refresh_interactive = interactive
        self._update_engine_labels()
        engine = self.batch_panel.selected_engine()
        hybrid = engine != "vision_only"

        if hybrid:
            route = self._reconstruction_route()
            tag = self._api_route_label(route)
            if is_external_route(route):
                self.batch_panel.refresh_models_btn.setEnabled(False)
                self.batch_panel.status_label.setText(
                    f"正在拉取【{tag}】文本重建模型…"
                )
                self.transcription_panel.status_label.setText(
                    f"状态：正在拉取【{tag}】模型…"
                )
                self.statusBar().showMessage(f"刷新 {tag} 模型列表…")
                worker = OllamaTaskWorker(
                    lambda: self._refresh_api_models_job(route), "api_models"
                )
                self._model_refresh_worker = worker
                worker.signals.finished.connect(self._on_api_models_done)
                worker.signals.error.connect(self._on_models_ensure_error)
                self._pool.start(worker)
                return
            # Hybrid but still on ollama route — show local text models with clear tag
            self.batch_panel.refresh_models_btn.setEnabled(False)
            self.batch_panel.status_label.setText(
                "Hybrid 当前路由是本地 Ollama；正在列出本地模型（建议改外部 API）…"
            )
            self.statusBar().showMessage("刷新本地 Ollama 模型（Hybrid）…")
            manager = self._ollama_manager

            def job_local():
                return manager.ensure_ready_for_models(start_if_needed=True)

            worker = OllamaTaskWorker(job_local, "ensure_ollama_hybrid")
            self._model_refresh_worker = worker
            worker.signals.finished.connect(self._on_hybrid_local_models_done)
            worker.signals.error.connect(self._on_models_ensure_error)
            self._pool.start(worker)
            return

        # Vision Only path
        from ai.providers.provider_factory import is_external_vision_route, vision_route

        route = vision_route()
        if is_external_vision_route(route):
            self.batch_panel.refresh_models_btn.setEnabled(False)
            tag = self._api_route_label(route)
            self.batch_panel.status_label.setText(f"正在拉取【{tag}】Vision 模型…")
            self.transcription_panel.status_label.setText(
                f"状态：正在拉取【{tag}】Vision…"
            )
            worker = OllamaTaskWorker(
                lambda: self._refresh_api_models_job(route, vision_only=True),
                "api_vision",
            )
            self._model_refresh_worker = worker
            worker.signals.finished.connect(self._on_api_models_done)
            worker.signals.error.connect(self._on_models_ensure_error)
            self._pool.start(worker)
            return

        self.batch_panel.refresh_models_btn.setEnabled(False)
        self.batch_panel.status_label.setText("正在连接本地 Ollama 并拉取 Vision 模型…")
        self.transcription_panel.status_label.setText("状态：正在连接本地 Ollama…")
        self.statusBar().showMessage("正在连接 Ollama…")

        manager = self._ollama_manager

        def job():
            return manager.ensure_ready_for_models(start_if_needed=True)

        worker = OllamaTaskWorker(job, "ensure_ollama")
        self._model_refresh_worker = worker
        worker.signals.finished.connect(self._on_models_ensure_done)
        worker.signals.error.connect(self._on_models_ensure_error)
        self._pool.start(worker)

    def _refresh_api_models_job(
        self, route: str, *, vision_only: bool = False
    ) -> dict:
        from ai.providers.openai_compatible_provider import OpenAICompatibleProvider
        from ai.providers.api_provider_manager import ApiProviderManager
        from config.config_manager import load_config
        from services.api_credential_store import ApiCredentialStore

        cfg = load_config()
        store = ApiCredentialStore()
        manager = ApiProviderManager(cfg, store)
        raw = (manager.providers or {}).get(route) or {}
        key = manager.get_api_key(route) or ""
        tag = self._api_route_label(route)
        if not raw.get("base_url") or not key:
            return {
                "ok": False,
                "message": (
                    f"【{tag}】未配置完整。请打开「外部 API 配置」填写 Base URL 与 API Key，"
                    f"并把页面转录路由设为对应服务。"
                ),
                "names": [],
                "route": route,
                "tag": tag,
                "vision_only": vision_only,
            }
        provider = OpenAICompatibleProvider.from_manager(manager, route)
        try:
            names = provider.list_models()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "message": f"【{tag}】拉取模型失败：{exc}",
                "names": [],
                "route": route,
                "tag": tag,
                "vision_only": vision_only,
            }
        default = str(raw.get("model") or "") or getattr(provider, "model", "") or ""
        if default and default not in names:
            names = [default, *names]
        if vision_only:
            # Prefer names that look multimodal
            from ai.providers.ollama_api_client import _looks_like_vision

            vis = [n for n in names if _looks_like_vision(n, None, [])]
            if vis:
                names = vis
        return {
            "ok": True,
            "message": f"【{tag}】共 {len(names)} 个模型可供选择",
            "names": names,
            "provider": provider,
            "route": route,
            "tag": tag,
            "base_url": provider.base_url,
            "default": default,
            "vision_only": vision_only,
        }

    def _on_api_models_done(self, result) -> None:
        self._model_refresh_worker = None
        self.batch_panel.refresh_models_btn.setEnabled(True)
        interactive = getattr(self, "_model_refresh_interactive", True)
        if not isinstance(result, dict) or not result.get("ok"):
            msg = (
                result.get("message")
                if isinstance(result, dict)
                else "外部 API 不可用"
            )
            self.batch_panel.set_models([], has_qualified=False)
            self.batch_panel.status_label.setText(str(msg).split("\n")[0])
            self.transcription_panel.set_model_choices(
                [("（无可用 API 模型）", None)],
                status=f"状态：{str(msg).split(chr(10))[0]}",
            )
            self._log(str(msg))
            if interactive:
                QMessageBox.warning(self, "无法加载 API 模型", str(msg))
            return

        tag = str(result.get("tag") or self._api_route_label(result.get("route", "")))
        names: list[str] = list(result.get("names") or [])
        provider = result.get("provider")
        if provider is not None:
            self.transcription_panel.set_provider(provider)

        choices = [(f"[{tag}] {n}", n) for n in names]
        default = result.get("default") or (names[0] if names else None)
        self.transcription_panel.set_model_choices(
            choices,
            status=f"状态：{result.get('message')}（请选带 [{tag}] 的项）",
            select=default,
        )
        items = [(n, "", ModelQualification.UNTESTED.value) for n in names]
        self.batch_panel.set_models(
            items, has_qualified=bool(names), source_tag=tag
        )
        if names:
            self.batch_panel.start_btn.setEnabled(True)
        self._log(str(result.get("message")))
        self.statusBar().showMessage(str(result.get("message")), 6000)

    def _on_hybrid_local_models_done(self, result) -> None:
        """Hybrid engine but routing still points at Ollama — tag as 本地."""
        self._model_refresh_worker = None
        self.batch_panel.refresh_models_btn.setEnabled(True)
        if not getattr(result, "ok", False):
            self._on_models_ensure_done(result)
            return
        url = result.base_url
        self.transcription_panel.set_ollama_base_url(url)
        names = list(getattr(result, "model_names", None) or [])
        if not names:
            # fall back to list_tags via refresh_batch
            self._refresh_batch_models(source_tag="本地 Ollama")
            # also fill transcription from batch
            items = []
            for i in range(self.batch_panel.primary_combo.count()):
                mid = self.batch_panel.primary_combo.itemData(i)
                lab = self.batch_panel.primary_combo.itemText(i)
                if mid:
                    items.append((lab, str(mid)))
            self.transcription_panel.set_model_choices(
                items or [("（无本地模型）", None)],
                status=(
                    "状态：Hybrid 路由仍是本地 Ollama。"
                    "建议在「外部 API」把页面转录改为 DeepSeek 等，再刷新。"
                ),
            )
            self._log(result.message)
            return
        tag = "本地 Ollama"
        choices = [(f"[{tag}] {n}", n) for n in names]
        self.transcription_panel.set_model_choices(
            choices,
            status=(
                f"状态：已加载 {len(names)} 个本地模型。"
                "Hybrid 推荐改用外部文本 API（列表会显示 [DeepSeek API] 等前缀）。"
            ),
        )
        items = [(n, "", ModelQualification.UNTESTED.value) for n in names]
        self.batch_panel.set_models(items, has_qualified=True, source_tag=tag)
        self._log(result.message)
        self.statusBar().showMessage(result.message, 6000)

    def _on_models_ensure_done(self, result) -> None:
        self._model_refresh_worker = None
        self.batch_panel.refresh_models_btn.setEnabled(True)
        interactive = getattr(self, "_model_refresh_interactive", True)
        if not getattr(result, "ok", False) or not getattr(result, "base_url", ""):
            msg = getattr(result, "message", "Ollama 不可用")
            self.batch_panel.set_models([], has_qualified=False)
            self.batch_panel.status_label.setText(msg.split("\n")[0])
            self.transcription_panel.set_model_choices(
                [("（无可用本地模型）", None)],
                status=f"状态：{msg.split(chr(10))[0]}",
            )
            self._log(msg)
            self.statusBar().showMessage("模型列表为空 — 请检查 Ollama", 8000)
            if interactive:
                QMessageBox.warning(self, "无法加载本地模型", msg)
            return

        url = result.base_url
        self.transcription_panel.set_ollama_base_url(url)
        tag = "本地 Ollama · Vision"
        names = list(getattr(result, "model_names", None) or [])
        self._refresh_batch_models(source_tag=tag)
        if not names:
            # Use whatever batch got from tags
            choices = []
            for i in range(self.batch_panel.primary_combo.count()):
                mid = self.batch_panel.primary_combo.itemData(i)
                lab = self.batch_panel.primary_combo.itemText(i)
                if mid:
                    choices.append((lab, str(mid)))
            self.transcription_panel.set_model_choices(
                choices or [("（无本地模型）", None)],
                status=f"状态：{result.message}",
            )
        else:
            choices = [(f"[{tag}] {n}", n) for n in names]
            self.transcription_panel.set_model_choices(
                choices,
                status=f"状态：已加载 {len(names)} 个【{tag}】模型",
            )
        self._log(result.message)
        if not names and self.batch_panel.primary_combo.count() == 0:
            self.statusBar().showMessage(result.message, 10000)
            if interactive:
                QMessageBox.information(self, "模型列表为空", result.message)
        else:
            self.statusBar().showMessage(result.message, 6000)

    def _on_models_ensure_error(self, message: str) -> None:
        self._model_refresh_worker = None
        self.batch_panel.refresh_models_btn.setEnabled(True)
        self.batch_panel.status_label.setText(f"刷新失败：{message}")
        self.transcription_panel.status_label.setText(f"状态：刷新失败 — {message}")
        self._log(f"刷新模型失败: {message}")
        QMessageBox.warning(self, "刷新模型失败", message)

    def _log(self, message: str) -> None:
        self.log_panel.append(message)
        logger.info(message)

    def _refresh_batch_models(self, *, source_tag: str = "本地 Ollama") -> None:
        from ai.providers.provider_factory import is_external_route

        # When Hybrid uses external API, model list is filled by _on_api_models_done
        if self.batch_panel.selected_engine() != "vision_only":
            route = self._reconstruction_route()
            if is_external_route(route):
                return
        items: list[tuple[str, str, str]] = []
        has_q = False
        try:
            status = self._ollama_manager.get_runtime_status()
            url = (
                self._ollama_manager.find_reachable_base_url()
                or status.base_url
                or self._ollama_manager.resolve_base_url()
            )
            from ai.providers.ollama_api_client import OllamaApiClient

            quick = OllamaApiClient(url, connect_timeout=2.0, request_timeout=8.0)
            tags = quick.list_tags()
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_tags failed: %s", exc)
            self.batch_panel.set_models([], has_qualified=False)
            self.batch_panel.status_label.setText(
                f"无法拉取模型列表：{exc}。请启动 Ollama 后点「刷新模型」。"
            )
            return
        for tag in tags:
            name = str(tag.get("name") or "")
            digest = str(tag.get("digest") or "")
            if not name:
                continue
            p = self._profiles.get(name, digest)
            items.append((name, digest, p.qualification.value))
            if p.qualification == ModelQualification.QUALIFIED:
                has_q = True
        self.batch_panel.set_models(
            items, has_qualified=has_q, source_tag=source_tag
        )

    def _on_import_clicked(self) -> None:
        if self._import_worker is not None:
            self.statusBar().showMessage("正在导入中，请稍候…", 3000)
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PDF 文件",
            "",
            "PDF 文件 (*.pdf);;所有文件 (*.*)",
        )
        if path:
            self._on_pdf_selected(path)

    def _on_pdf_selected(self, path: str) -> None:
        if self._import_worker is not None:
            self.statusBar().showMessage("正在导入中，请稍候…", 3000)
            return

        pdf_path = Path(path)
        self._log(f"正在导入: {pdf_path.name}")
        self.statusBar().showMessage("正在导入 PDF…")
        self.import_panel.import_btn.setEnabled(False)
        self.import_panel.set_pdf_info(
            file_name=pdf_path.name,
            page_count=0,
            file_size=0,
            status="正在后台导入…",
        )
        self.pipeline_nav.set_cta_enabled(False)

        worker = ImportWorker(
            pdf_path,
            workspace_root=self._project_service.workspace_root,
        )
        self._import_worker = worker
        worker.signals.progress.connect(self._on_import_progress)
        worker.signals.info_ready.connect(self._on_import_info)
        worker.signals.completed.connect(self._on_import_completed)
        worker.signals.error.connect(self._on_import_error)
        self._pool.start(worker)

    def _on_import_progress(self, percent: int, message: str) -> None:
        self.statusBar().showMessage(f"{message} ({percent}%)")
        self.import_panel.status_label.setText(message)

    def _on_import_info(self, pdf_info) -> None:
        self.import_panel.set_pdf_info(
            file_name=pdf_info.file_name,
            page_count=pdf_info.page_count,
            file_size=pdf_info.file_size,
            status="正在复制 PDF 并创建项目…",
        )

    def _on_import_completed(self, project: Project) -> None:
        self._import_worker = None
        self.import_panel.import_btn.setEnabled(True)
        self.pipeline_nav.set_cta_enabled(True)

        self._bind_project(project, defer_network=True)

        info = project.info
        meta = info.metadata or {}
        self.import_panel.set_pdf_info(
            file_name=Path(meta.get("original_path", info.source_pdf.name)).name
            if meta.get("original_path")
            else info.name,
            page_count=info.page_count,
            file_size=int(meta.get("file_size") or 0),
            project_path=str(project.root),
            status="PDF 导入成功",
        )
        self._log(f"项目已创建: {project.root}")
        self._log(f"页数: {info.page_count} | 数据库: {project.db_path.name}")
        self.statusBar().showMessage("PDF 导入成功 — 请进行「2 · 渲染页面」", 8000)
        self.pipeline_nav.select("render")

    def _on_import_error(self, message: str) -> None:
        self._import_worker = None
        self.import_panel.import_btn.setEnabled(True)
        self.pipeline_nav.set_cta_enabled(True)
        self.import_panel.status_label.setText(f"导入失败: {message}")
        self._log(f"错误: {message}")
        QMessageBox.critical(self, "导入失败", message)
        self.statusBar().showMessage("导入失败")

    def _bind_project(self, project: Project, *, defer_network: bool = False) -> None:
        self._current_project = project
        self.render_panel.set_project_ready(True)
        self.page_list.set_page_count(project.info.page_count)
        self.page_viewer.set_project(project.pages_dir, project.info.page_count)
        self.transcription_panel.set_project(project)
        self.transcription_panel.set_page(1)
        self._recover_batch_if_needed()
        self._reload_stage_statuses()
        self._reload_review_queue()
        self._refresh_figure_panel()
        self._refresh_assemble_panel()
        self._refresh_cleaner_panel()
        self._refresh_final_panel()

        if defer_network:
            # Ollama 探测放到下一事件循环，避免导入完成后界面再卡一下
            QTimer.singleShot(0, self._post_import_network_refresh)
        else:
            self._post_import_network_refresh()

    def _post_import_network_refresh(self) -> None:
        self._sync_transcription_server()
        self.transcription_panel.refresh_models()
        self._refresh_batch_models()

    def _refresh_figure_panel(self) -> None:
        project = self._current_project
        if project is None:
            self.figure_panel.set_project_ready(False)
            return
        md_dir = project.markdown_pages_dir
        ready = any(md_dir.glob("page_*.md")) if md_dir.is_dir() else False
        self.figure_panel.set_project_ready(ready)
        self._reload_figure_review()

    def _on_transcription_accepted(self, page_number: int) -> None:
        self._log(f"已接受第 {page_number} 页转录结果")
        self._reload_stage_statuses()

    def _reload_stage_statuses(self) -> None:
        project = self._current_project
        if project is None:
            return
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            mapping: dict[int, str] = {}
            for row in repo.list_stage_states("render"):
                mapping[int(row["page_number"])] = str(row["status"])
            for row in repo.list_stage_states("transcribe"):
                st = str(row["status"])
                if st in {
                    StageStatus.NEEDS_REVIEW.value,
                    StageStatus.FAILED.value,
                    StageStatus.SUCCESS.value,
                    StageStatus.CACHED.value,
                    StageStatus.RUNNING.value,
                }:
                    mapping[int(row["page_number"])] = st
            for row in repo.list_stage_states("figures"):
                st = str(row["status"])
                if st in {
                    StageStatus.NEEDS_REVIEW.value,
                    StageStatus.FAILED.value,
                    StageStatus.SUCCESS.value,
                }:
                    mapping[int(row["page_number"])] = st
            self.page_list.apply_statuses(mapping)
        finally:
            db.close()

    def _on_page_selected(self, page_number: int) -> None:
        self.page_viewer.show_page(page_number)
        self.transcription_panel.set_page(page_number)

    def _on_start_render(self) -> None:
        self._start_render(force=False, pages=None)

    def _on_rerender_page(self, page_number: int) -> None:
        self._start_render(force=True, pages=(page_number,))

    def _start_render(
        self,
        *,
        force: bool,
        pages: tuple[int, ...] | None,
    ) -> None:
        project = self._current_project
        if project is None:
            return
        if self._render_worker is not None:
            QMessageBox.information(self, "渲染中", "已有渲染任务在运行。")
            return

        try:
            dpi = validate_dpi(self.render_panel.selected_dpi())
        except ValueError as exc:
            QMessageBox.warning(self, "DPI 无效", str(exc))
            return

        total = project.info.page_count
        if pages is None:
            expr = self.render_panel.page_range_expression()
            try:
                page_list = (
                    all_pages(total)
                    if expr is None
                    else parse_page_range(expr, total)
                )
            except PageRangeError as exc:
                QMessageBox.warning(self, "页面范围无效", str(exc))
                return
            pages = tuple(page_list)

        if not pages:
            QMessageBox.warning(self, "页面范围无效", "没有要渲染的页面。")
            return

        request = RenderRequest(
            pdf_path=project.info.source_pdf,
            output_dir=project.pages_dir,
            pages=pages,
            settings=RenderSettings(dpi=dpi),
            force=force,
            pdf_hash=project.pdf_hash(),
            db_path=project.db_path,
        )
        worker = RenderWorker(request)
        self._render_worker = worker
        self.render_panel.set_rendering(True)
        self.render_panel.reset_progress()
        self._log(
            f"开始渲染：{len(pages)} 页 @ {dpi} DPI"
            + ("（强制）" if force else "")
        )

        worker.signals.page_started.connect(self._on_render_page_started)
        worker.signals.page_finished.connect(self._on_render_page_finished)
        worker.signals.progress_changed.connect(self._on_render_progress)
        worker.signals.error.connect(self._on_render_error)
        worker.signals.cancelled.connect(self._on_render_cancelled)
        worker.signals.completed.connect(self._on_render_completed)
        self._pool.start(worker)

    def _on_cancel_render(self) -> None:
        if self._render_worker is not None:
            self._render_worker.request_cancel()
            self._log("已请求取消渲染（当前页完成后停止）")

    def _on_render_page_started(self, page_number: int) -> None:
        self.page_list.set_status(page_number, StageStatus.RUNNING.value)

    def _on_render_page_finished(self, result) -> None:
        status = StageStatus.SUCCESS.value
        if getattr(result, "cancelled", False):
            status = StageStatus.CANCELLED.value
        elif not result.success:
            status = StageStatus.FAILED.value
        elif result.cached:
            status = StageStatus.CACHED.value
        self.page_list.set_status(result.page_number, status)
        if result.success:
            self.page_viewer.show_page(result.page_number)
            self.page_list.select_page(result.page_number)

    def _on_render_progress(self, done: int, total: int, message: str) -> None:
        self.render_panel.update_progress(done, total, message)

    def _on_render_error(self, message: str) -> None:
        self._log(f"渲染失败: {message}")
        QMessageBox.critical(self, "无法打开 PDF", message)
        self._finish_render_ui()

    def _recover_batch_if_needed(self) -> None:
        project = self._current_project
        if project is None:
            return
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            n = repo.recover_interrupted_batches()
            unfinished = repo.latest_unfinished_batch_run()
        finally:
            db.close()
        if n:
            self._log(f"已将 {n} 个卡住的 Batch 标记为 INTERRUPTED")
        if unfinished is None:
            return
        run_id = int(unfinished["id"])
        reply = QMessageBox.question(
            self,
            "未完成的批量转录",
            f"发现未完成的 Batch #{run_id}（{unfinished['status']}）。\n"
            "是否继续？选择「No」将放弃该 Batch。",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._batch_run_id = run_id
            self._start_batch_worker(run_id)
        else:
            db = Database(project.db_path)
            try:
                db.initialize()
                ProjectRepository(db).update_batch_run(
                    run_id, status="CANCELLED"
                )
            finally:
                db.close()

    def _on_qualify_models(self) -> None:
        project = self._current_project
        if project is None:
            QMessageBox.information(self, "资格测试", "请先导入 PDF 并渲染页面。")
            return
        model = self.batch_panel.selected_primary()
        # allow LIMITED names from combo display: use transcription panel model
        if not model:
            model = self.transcription_panel.model_combo.currentData()
        if not model:
            QMessageBox.information(self, "资格测试", "请先选择模型。")
            return
        pages = [p for p in (1, 4, 8) if p <= project.info.page_count]
        if len(pages) < 3:
            pages = list(range(1, min(3, project.info.page_count) + 1))
        self._log(f"开始 Phase 5A 资格测试：{model} 页 {pages}")
        trans = TranscriptionService(
            self._provider(), project.root, project.db_path
        )
        service = BatchTranscriptionService(
            transcription=trans,
            project_root=project.root,
            db_path=project.db_path,
            profiles=self._profiles,
            page_count=project.info.page_count,
            config=load_config(),
        )
        from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

        class QualifySignals(QObject):
            finished = pyqtSignal(object)
            error = pyqtSignal(str)

        ctx = self.transcription_panel.ctx_combo.currentData()

        class QualifyWorker(QRunnable):
            def __init__(self) -> None:
                super().__init__()
                self.signals = QualifySignals()
                self.setAutoDelete(True)

            def run(self_inner) -> None:  # noqa: N805
                try:
                    report = service.qualify_pages(
                        model=str(model), pages=pages, num_ctx=ctx
                    )
                    self_inner.signals.finished.emit(report)
                except Exception as exc:  # noqa: BLE001
                    self_inner.signals.error.emit(str(exc))

        w = QualifyWorker()

        def on_ok(report: object) -> None:
            self._log(f"资格测试完成: {report}")
            self._refresh_batch_models()
            QMessageBox.information(self, "资格测试", str(report))

        def on_err(msg: str) -> None:
            self._log(f"资格测试失败: {msg}")
            QMessageBox.critical(self, "资格测试失败", msg)

        w.signals.finished.connect(on_ok)
        w.signals.error.connect(on_err)
        self._pool.start(w)

    def _on_start_batch(self) -> None:
        project = self._current_project
        if project is None:
            return
        if self._batch_worker is not None:
            QMessageBox.information(self, "批量转录", "已有批量任务在运行。")
            return
        primary = self.batch_panel.selected_primary()
        if not primary:
            QMessageBox.warning(self, "批量转录", "请选择主模型。")
            return
        engine = self.batch_panel.selected_engine()
        skip_qualify = self.batch_panel.skip_qualification()
        digest = ""
        try:
            digest = TranscriptionService(
                self._provider(), project.root, project.db_path
            ).get_model_digest(primary)
        except Exception:  # noqa: BLE001
            digest = ""
        p = self._profiles.get(primary, digest)
        if (
            engine == "vision_only"
            and not skip_qualify
            and p.qualification != ModelQualification.QUALIFIED
        ):
            QMessageBox.warning(
                self,
                "批量转录",
                "主模型尚未 QUALIFIED。请先运行「3 页资格测试」，"
                "或勾选「跳过 3 页资格测试」后直接开始。",
            )
            return
        if engine == "vision_only" and skip_qualify and p.qualification != ModelQualification.QUALIFIED:
            self._log(
                f"已跳过资格测试，以 {p.qualification.value} 状态启动 Vision 批量：{primary}"
            )
        expr = self.batch_panel.page_range_expression()
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            if expr:
                try:
                    pages = parse_page_range(expr, project.info.page_count)
                except PageRangeError as exc:
                    QMessageBox.warning(self, "页面范围无效", str(exc))
                    return
            else:
                pages = []
                for n in range(1, project.info.page_count + 1):
                    row = repo.get_stage_state(n, PipelineStage.TRANSCRIBE)
                    st = (row or {}).get("status")
                    if st not in {
                        StageStatus.SUCCESS.value,
                        StageStatus.CACHED.value,
                    }:
                        pages.append(n)
                if not pages:
                    pages = list(range(1, project.info.page_count + 1))
            trans = TranscriptionService(
                self._provider(), project.root, project.db_path
            )
            service = BatchTranscriptionService(
                transcription=trans,
                project_root=project.root,
                db_path=project.db_path,
                profiles=self._profiles,
                page_count=project.info.page_count,
                config=load_config(),
            )
            created = service.create_run(
                pages=pages,
                primary_model=primary,
                fallback_model=self.batch_panel.selected_fallback(),
                require_qualified=not skip_qualify,
            )
        finally:
            db.close()
        self._log(
            f"Batch #{created.run_id} 已创建："
            f"{len(created.queued_pages)} 页入队，"
            f"{created.skipped_unrendered} 页因未渲染跳过"
        )
        self._batch_run_id = created.run_id
        self._start_batch_worker(created.run_id)

    def _start_batch_worker(self, run_id: int) -> None:
        project = self._current_project
        if project is None:
            return
        worker = BatchTranscriptionWorker(
            provider=self._provider(),
            project_root=project.root,
            db_path=project.db_path,
            run_id=run_id,
            page_count=project.info.page_count,
            profiles=self._profiles,
            page_engine=self.batch_panel.selected_engine(),
        )
        self._batch_worker = worker
        self.batch_panel.set_running(True)
        worker.signals.page_started.connect(self._on_batch_page_started)
        worker.signals.page_finished.connect(self._on_batch_page_finished)
        worker.signals.progress.connect(self._log)
        worker.signals.paused.connect(self._on_batch_paused)
        worker.signals.cancelled.connect(self._on_batch_cancelled)
        worker.signals.completed.connect(self._on_batch_completed)
        worker.signals.error.connect(self._on_batch_error)
        self._pool.start(worker)

    def _on_pause_batch(self) -> None:
        if self._batch_worker is not None:
            self._batch_worker.request_pause()
            self._log("已请求暂停批量转录（当前页完成后停止）")

    def _on_resume_batch(self) -> None:
        if self._batch_run_id is None:
            return
        if self._batch_worker is not None:
            return
        self._start_batch_worker(self._batch_run_id)

    def _on_cancel_batch(self) -> None:
        if self._batch_worker is not None:
            self._batch_worker.request_cancel()
            self._log("已请求取消批量转录")

    def _on_batch_page_started(self, page: int) -> None:
        self.page_list.set_status(page, StageStatus.RUNNING.value)
        self.page_list.select_page(page)
        self.page_viewer.show_page(page)

    def _on_batch_page_finished(self, result: object) -> None:
        status = getattr(result, "status", "")
        page = getattr(result, "page_number", 0)
        mapping = {
            BatchItemStatus.AUTO_ACCEPTED.value: StageStatus.SUCCESS.value,
            BatchItemStatus.CACHED.value: StageStatus.CACHED.value,
            BatchItemStatus.NEEDS_REVIEW.value: StageStatus.NEEDS_REVIEW.value,
            BatchItemStatus.FAILED.value: StageStatus.FAILED.value,
            BatchItemStatus.CANCELLED.value: StageStatus.CANCELLED.value,
        }
        if page:
            self.page_list.set_status(page, mapping.get(str(status), str(status)))
        self._log(f"第 {page} 页 → {status}")
        self._reload_review_queue()

    def _on_batch_paused(self, run_id: int) -> None:
        self._log(f"Batch #{run_id} 已暂停")
        self._batch_worker = None
        self.batch_panel.set_paused(True)

    def _on_batch_cancelled(self, run_id: int) -> None:
        self._log(f"Batch #{run_id} 已取消")
        self._finish_batch_ui()

    def _on_batch_completed(self, report: object) -> None:
        self._log(f"批量转录完成: {report}")
        self._finish_batch_ui()
        self._reload_stage_statuses()
        self._reload_review_queue()
        self._refresh_assemble_panel()
        self._update_figure_readiness()

    def _on_batch_error(self, message: str) -> None:
        self._log(f"批量转录失败: {message}")
        QMessageBox.critical(self, "批量转录失败", message)
        self._finish_batch_ui()

    def _finish_batch_ui(self) -> None:
        self._batch_worker = None
        self.batch_panel.set_running(False)
        self.batch_panel.set_paused(False)
        self._refresh_batch_models()

    def _reload_review_queue(self) -> None:
        project = self._current_project
        if project is None:
            return
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            items = repo.list_review_pages()
        finally:
            db.close()
        self.review_queue.set_transcription_items(items)
        db2 = Database(project.db_path)
        try:
            db2.initialize()
            repo2 = ProjectRepository(db2)
            fig_items = repo2.list_figure_review_items()
        finally:
            db2.close()
        self.review_queue.set_figure_items(fig_items)

    def _on_review_page(self, page: int) -> None:
        project = self._current_project
        if project is None:
            return
        self.workspace_tabs.setCurrentIndex(1)
        self.page_viewer.show_page(page)
        self.page_list.select_page(page)
        self.transcription_panel.set_page(page)
        md_path = project.root / "markdown_pages" / f"page_{page:04d}.md"
        json_path = project.root / "page_results" / f"page_{page:04d}.json"
        issues = ""
        markdown = ""
        if json_path.exists():
            import json

            data = json.loads(json_path.read_text(encoding="utf-8"))
            issues = ", ".join((data.get("result") or {}).get("warnings") or [])
        exp = project.root / "experiments" / "transcription" / f"page_{page:04d}"
        attempts: list[tuple[str, Path]] = []
        if exp.is_dir():
            for d in sorted(exp.iterdir(), reverse=True):
                md = d / "markdown.md"
                if md.exists():
                    attempts.append((d.name, d))
                    if not markdown:
                        markdown = md.read_text(encoding="utf-8")
        if md_path.exists() and not markdown:
            markdown = md_path.read_text(encoding="utf-8")
        self.review_queue.show_page_detail(page, issues, markdown, attempts)

    def _on_review_accept(self, page: int, markdown: str) -> None:
        project = self._current_project
        if project is None:
            return
        exp = project.root / "experiments" / "transcription" / f"page_{page:04d}"
        if not exp.is_dir():
            return
        trans = TranscriptionService(
            self._provider(), project.root, project.db_path
        )
        latest = sorted(exp.iterdir(), reverse=True)
        if not latest:
            return
        attempt = trans._load_attempt(latest[0], cached=False)
        trans.accept_result(
            page_number=page,
            attempt=attempt,
            markdown_override=markdown or None,
            manually_edited=bool(markdown),
            acceptance_mode="manual",
        )
        self._log(f"已手工接受第 {page} 页")
        self._reload_stage_statuses()
        self._reload_review_queue()

    def _on_review_skip(self, page: int) -> None:
        self._log(f"跳过审核第 {page} 页")

    def _on_review_retranscribe(self, page: int) -> None:
        self.transcription_panel.set_page(page)
        self.transcription_panel._run_once()

    def _on_start_figures(self) -> None:
        project = self._current_project
        if project is None:
            return
        if self._batch_worker is not None:
            QMessageBox.information(
                self, "Figure", "批量转录正在运行，请先暂停或取消。"
            )
            return
        if self._figure_worker is not None:
            QMessageBox.information(self, "Figure", "已有 Figure 任务在运行。")
            return
        pages = list(range(1, project.info.page_count + 1))
        worker = BatchFigureWorker(
            project_root=project.root,
            pdf_path=project.info.source_pdf,
            db_path=project.db_path,
            pdf_hash=project.pdf_hash(),
            pages=pages,
            analyze_only=self.figure_panel.is_analyze_only(),
        )
        self._figure_worker = worker
        self.figure_panel.set_running(True)
        worker.signals.page_finished.connect(self._on_figure_page_finished)
        worker.signals.completed.connect(self._on_figure_completed)
        worker.signals.cancelled.connect(self._on_figure_cancelled)
        worker.signals.error.connect(self._on_figure_error)
        self._pool.start(worker)
        self._log(f"开始 Figure 分析：{len(pages)} 页")

    def _on_cancel_figures(self) -> None:
        if self._figure_worker is not None:
            self._figure_worker.request_cancel()
            self._log("已请求取消 Figure 任务")

    def _on_figure_page_finished(self, result: object) -> None:
        page = getattr(result, "page_number", 0)
        st = getattr(result, "stage_status", "")
        if page:
            self.page_list.set_status(page, st)
        self._log(f"Figure 第 {page} 页 → {st}")

    def _on_figure_completed(self, summary: object) -> None:
        self._log(f"Figure 批次完成: {summary}")
        self._figure_worker = None
        self.figure_panel.set_running(False)
        if isinstance(summary, dict):
            self.figure_panel.update_stats(
                native=int(summary.get("native_extracted", 0)),
                clip=int(summary.get("pdf_clipped", 0)),
                review=int(summary.get("needs_review", 0)),
                failed=int(summary.get("failed", 0)),
                message="Figure 分析完成",
            )
        self._reload_stage_statuses()
        self._reload_figure_review()
        self._update_figure_readiness()
        self._refresh_assemble_panel()

    def _on_figure_cancelled(self) -> None:
        self._log("Figure 任务已取消")
        self._figure_worker = None
        self.figure_panel.set_running(False)

    def _on_figure_error(self, msg: str) -> None:
        self._log(f"Figure 失败: {msg}")
        QMessageBox.critical(self, "Figure 失败", msg)
        self._figure_worker = None
        self.figure_panel.set_running(False)

    def _figure_review_svc(self) -> FigureReviewService | None:
        project = self._current_project
        if project is None:
            return None
        return FigureReviewService(
            project_root=project.root,
            pdf_path=project.info.source_pdf,
            db_path=project.db_path,
            pdf_hash=project.pdf_hash(),
            config=load_config(),
        )

    def _update_figure_readiness(self) -> None:
        project = self._current_project
        if project is None:
            return
        svc = FigureReadinessService(
            project_root=project.root,
            db_path=project.db_path,
            config=load_config(),
        )
        self.figure_panel.update_readiness(svc.summarize())

    def _on_figure_queue_selected(self, page: int, fig_idx: int) -> None:
        self.workspace_tabs.setCurrentIndex(2)
        self.figure_review._select_item(page, fig_idx)
        self._on_figure_review_open(page, fig_idx)

    def _on_figure_review_open(self, page: int, fig_idx: int) -> None:
        svc = self._figure_review_svc()
        project = self._current_project
        if svc is None or project is None:
            return
        try:
            ctx = svc.load_context(page, fig_idx)
        except Exception as exc:  # noqa: BLE001
            self._log(f"加载 Figure Review 失败: {exc}")
            return
        row = ctx.get("figure_row") or {}
        req = ctx.get("request")
        mr = ctx["marker_report"]
        candidates = []
        for c in ctx.get("candidates") or []:
            score = 0.0
            if req:
                m = svc.matcher.match(req, [c], marker_ok=True)
                score = m.score
            candidates.append(
                {
                    "candidate_id": c.candidate_id,
                    "type": c.candidate_type,
                    "bbox_1000": c.bbox_1000,
                    "width": c.width,
                    "height": c.height,
                    "xref": c.xref,
                    "score": score,
                }
            )
        ai_bbox = row.get("ai_bbox_1000")
        if isinstance(ai_bbox, str):
            import json

            ai_bbox = tuple(json.loads(ai_bbox))
        resolved = project.root / "resolved_pages" / f"page_{page:04d}.md"
        canon = project.root / "markdown_pages" / f"page_{page:04d}.md"
        resolved_md = (
            resolved.read_text(encoding="utf-8")
            if resolved.exists()
            else canon.read_text(encoding="utf-8")
            if canon.exists()
            else ""
        )
        info = (
            f"status={row.get('status')} method={row.get('source_method')} "
            f"score={row.get('match_score')} type={row.get('figure_type')}"
        )
        self.figure_review.load_figure_detail(
            page_number=page,
            figure_index=fig_idx,
            page_image=Path(ctx["page_image"]) if ctx.get("page_image") else None,
            info=info,
            marker_issues=mr.issues,
            candidates=candidates,
            ai_bbox=ai_bbox,
            resolved_md=resolved_md,
        )

    def _on_figure_preview(
        self, page: int, fig_idx: int, bbox: object, candidate_id: object
    ) -> None:
        svc = self._figure_review_svc()
        if svc is None or not isinstance(bbox, tuple):
            return
        try:
            path = svc.generate_preview(
                page_number=page,
                figure_index=fig_idx,
                bbox_1000=bbox,
                candidate_id=str(candidate_id) if candidate_id else None,
            )
            self.figure_review.show_preview_image(path)
            self._log(f"Preview 已生成: {path.name}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Preview", str(exc))

    def _on_figure_accept(self, page: int, fig_idx: int, bbox: object) -> None:
        svc = self._figure_review_svc()
        if svc is None or not isinstance(bbox, tuple):
            return
        try:
            svc.accept_figure(page_number=page, figure_index=fig_idx, bbox_1000=bbox)
            self._log(f"已接受 Figure p{page:04d} fig{fig_idx:02d}")
            self._reload_figure_review()
            self._reload_review_queue()
            self._reload_stage_statuses()
            self._update_figure_readiness()
            self._refresh_assemble_panel()
            if load_config().get("figures", {}).get("review", {}).get("auto_advance", True):
                self.figure_review.advance_after_action()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "接受 Figure", str(exc))

    def _refresh_assemble_panel(self) -> None:
        project = self._current_project
        if project is None:
            self.assemble_panel.update_readiness({"ready": False}, 0)
            return
        cfg = load_config()
        if self.assemble_panel.allow_unresolved.isChecked():
            cfg = dict(cfg)
            assemble = dict(cfg.get("assemble") or {})
            assemble["allow_unresolved_figures"] = True
            cfg["assemble"] = assemble
            figures = dict(cfg.get("figures") or {})
            readiness = dict(figures.get("readiness") or {})
            readiness["allow_unresolved_override"] = True
            readiness["require_all_resolved"] = False
            figures["readiness"] = readiness
            cfg["figures"] = figures
        svc = AssembleReadinessService(
            project_root=project.root, db_path=project.db_path, config=cfg
        )
        summary = svc.summarize()
        self.assemble_panel.update_readiness(
            summary, continuity_candidates=len(self._continuity_candidates)
        )

    def _on_check_continuity(self) -> None:
        project = self._current_project
        if project is None:
            return
        analyzer = ContinuityAnalyzer(load_config())
        pages = list(range(1, project.info.page_count + 1))
        cands = analyzer.analyze_project(project_root=project.root, page_numbers=pages)
        db = Database(project.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        items = []
        try:
            for c in cands:
                patch = repo.get_continuity_patch(c.left_page, c.right_page)
                items.append(
                    {
                        "left_page": c.left_page,
                        "right_page": c.right_page,
                        "left_tail": c.left_tail,
                        "right_head": c.right_head,
                        "source_flags": c.source_flags,
                        "suspicion_score": c.suspicion_score,
                        "action": (patch or {}).get("action"),
                        "custom_text": (patch or {}).get("custom_text") or "",
                    }
                )
        finally:
            db.close()
        self._continuity_candidates = items
        self.continuity_review.set_candidates(items)
        self._refresh_assemble_panel()
        self._log(f"连续性候选: {len(items)}")

    def _on_continuity_save(
        self, left: int, right: int, action: str, custom: str
    ) -> None:
        project = self._current_project
        if project is None:
            return
        resolver = PageSourceResolver(project_root=project.root, db_path=project.db_path)
        entries, _ = resolver.resolve_pages([left, right], allow_unresolved_figures=True)
        hashes = {e.page: e.sha256 for e in entries}
        db = Database(project.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_continuity_patch(
                left_page=left,
                right_page=right,
                action=action,
                custom_text=custom or None,
                left_context="",
                right_context="",
                source_hash_left=hashes.get(left, ""),
                source_hash_right=hashes.get(right, ""),
            )
        finally:
            db.close()
        self._log(f"已保存 continuity patch {left}→{right}: {action}")

    def _on_start_assemble(self) -> None:
        project = self._current_project
        if project is None:
            return
        if self._assembly_worker is not None:
            QMessageBox.information(self, "Assemble", "Assemble 正在运行。")
            return
        # Always re-check after transcription/figures — panel may be stale.
        self._refresh_assemble_panel()
        worker = AssemblyWorker(
            project_root=project.root,
            db_path=project.db_path,
            page_numbers=list(range(1, project.info.page_count + 1)),
            allow_unresolved_figures=self.assemble_panel.allow_unresolved.isChecked(),
        )
        self._assembly_worker = worker
        self.assemble_panel.set_running(True)
        worker.signals.progress.connect(lambda m: self.assemble_panel.set_status_message(m))
        worker.signals.completed.connect(self._on_assemble_completed)
        worker.signals.cancelled.connect(self._on_assemble_cancelled)
        worker.signals.error.connect(self._on_assemble_error)
        self._pool.start(worker)
        self._log("开始生成 intermediate/raw.md")

    def _on_assemble_completed(self, result: object) -> None:
        self._assembly_worker = None
        self.assemble_panel.set_running(False)
        cached = getattr(result, "cached", False)
        path = getattr(result, "output_path", None)
        self.assemble_panel.set_status_message(
            "CACHED" if cached else f"SUCCESS → {path}"
        )
        self._log(
            f"Assemble 完成: pages={getattr(result, 'total_pages', 0)} "
            f"cached={cached} hash={getattr(result, 'assembly_hash', '')}"
        )
        self._refresh_assemble_panel()

    def _on_assemble_cancelled(self) -> None:
        self._assembly_worker = None
        self.assemble_panel.set_running(False)
        self.assemble_panel.set_status_message("CANCELLED")
        self._log("Assemble 已取消")

    def _on_assemble_error(self, msg: str) -> None:
        self._assembly_worker = None
        self.assemble_panel.set_running(False)
        self.assemble_panel.set_status_message(f"FAILED: {msg}")
        self._log(f"Assemble 失败: {msg}")
        self._refresh_assemble_panel()
        tip = msg
        if "Figure Reviews Remaining" in msg or "figures_not_ready" in msg:
            tip = (
                "拼装被插图状态拦住了。\n\n"
                "请重新跑一遍「插图提取」（caption 自动裁剪，无需人工审核）；\n"
                "若仍失败，可在 Assemble 面板勾选「允许未解决 Figure」后生成 raw.md。\n\n"
                f"详情：{msg}"
            )
        QMessageBox.warning(self, "拼装未就绪", tip)

    def _cleaner_svc(self) -> BatchCleanerService | None:
        project = self._current_project
        if project is None:
            return None
        return BatchCleanerService(
            project_root=project.root,
            db_path=project.db_path,
            config=load_config(),
        )

    def _refresh_cleaner_panel(self) -> None:
        project = self._current_project
        if project is None:
            self.cleaner_panel.set_project_ready(False)
            return
        raw_ok = (project.root / "intermediate" / "raw.md").exists()
        self.cleaner_panel.set_project_ready(raw_ok)
        readiness = CleanReadinessService(
            project_root=project.root, db_path=project.db_path, config=load_config()
        ).summarize()
        self.cleaner_panel.update_readiness(readiness)
        self._reload_cleaner_review()

    def _on_cleaner_analyze(self) -> None:
        svc = self._cleaner_svc()
        if svc is None:
            return
        summary = svc.analyze()
        self.cleaner_panel.update_analysis(summary)
        self._log(
            f"Cleaner 分析: already={summary.get('already_clean')} "
            f"rules={summary.get('needs_rule_fix')} ai={summary.get('needs_ai')}"
        )

    def _on_start_cleaner(self) -> None:
        project = self._current_project
        if project is None:
            return
        if self._cleaner_worker is not None:
            QMessageBox.information(self, "Cleaner", "清洗任务正在运行。")
            return
        cfg = load_config()
        cleaner_cfg = dict(cfg.get("cleaner") or {})
        cleaner_cfg["mode"] = self.cleaner_panel.selected_mode()
        cfg = dict(cfg)
        cfg["cleaner"] = cleaner_cfg
        from ai.providers.provider_factory import create_text_provider, cleanup_route

        text_provider = create_text_provider(config=cfg)
        route = cleanup_route(cfg)
        if text_provider is None and route not in {"ollama", "none", ""}:
            QMessageBox.warning(
                self,
                "文本清理 API 未配置",
                f"当前文本清理路由为「{route}」，但缺少 Base URL / API Key。\n"
                "请打开「外部 API 配置」填写你自己的付费 Key，"
                "或勾选「页面转录与文本清理使用同一 API」。",
            )
            return
        worker = BatchCleanerWorker(
            project_root=project.root,
            db_path=project.db_path,
            pages=list(range(1, project.info.page_count + 1)),
            text_provider=text_provider,
        )
        # inject config via service inside worker uses load_config — write temp override
        # by setting env is heavy; instead patch worker run with mode from panel:
        worker._desired_mode = self.cleaner_panel.selected_mode()  # type: ignore[attr-defined]
        self._cleaner_worker = worker
        self.cleaner_panel.set_running(True)
        worker.signals.progress.connect(self.cleaner_panel.set_status_message)
        worker.signals.completed.connect(self._on_cleaner_completed)
        worker.signals.cancelled.connect(self._on_cleaner_cancelled)
        worker.signals.error.connect(self._on_cleaner_error)
        self._pool.start(worker)
        engine = f"外部 API（{route}）" if text_provider else "规则 / 本地（无云端清理）"
        self._log(f"开始 Markdown Cleaner · {engine}")

    def _on_pause_cleaner(self) -> None:
        if self._cleaner_worker is not None:
            self._cleaner_worker.request_pause()
            self.cleaner_panel.set_paused(True)

    def _on_resume_cleaner(self) -> None:
        if self._cleaner_worker is not None:
            self._cleaner_worker.request_resume()
            self.cleaner_panel.set_paused(False)

    def _on_cancel_cleaner(self) -> None:
        if self._cleaner_worker is not None:
            self._cleaner_worker.request_cancel()

    def _on_cleaner_completed(self, summary: object) -> None:
        self._cleaner_worker = None
        self.cleaner_panel.set_running(False)
        if isinstance(summary, dict):
            self.cleaner_panel.update_stats(summary)
            self.cleaner_panel.set_status_message(
                "CACHED" if summary.get("document_cached") else "SUCCESS"
            )
        self._refresh_cleaner_panel()
        self._refresh_final_panel()
        self._log(f"Cleaner 完成: {summary}")

    def _on_cleaner_cancelled(self) -> None:
        self._cleaner_worker = None
        self.cleaner_panel.set_running(False)
        self.cleaner_panel.set_status_message("CANCELLED")

    def _on_cleaner_error(self, msg: str) -> None:
        self._cleaner_worker = None
        self.cleaner_panel.set_running(False)
        self.cleaner_panel.set_status_message("FAILED")
        QMessageBox.critical(self, "Cleaner 失败", msg)

    def _on_open_clean_md(self) -> None:
        project = self._current_project
        if project is None:
            return
        path = project.root / "intermediate" / "clean.md"
        if not path.exists():
            QMessageBox.information(self, "clean.md", "尚未生成 clean.md")
            return
        self._log(f"clean.md: {path}")
        QMessageBox.information(self, "clean.md", str(path))

    def _reload_cleaner_review(self) -> None:
        project = self._current_project
        if project is None:
            return
        db = Database(project.db_path)
        try:
            db.initialize()
            items = ProjectRepository(db).list_cleaner_review_items()
        finally:
            db.close()
        self.review_queue.set_cleaner_items(items)
        self.cleaner_review.set_items(items)
        if items:
            self._on_cleaner_queue_selected(int(items[0]["page_number"]))

    def _on_cleaner_queue_selected(self, page: int) -> None:
        project = self._current_project
        if project is None:
            return
        self.workspace_tabs.setCurrentIndex(4)
        raw = project.root / "intermediate" / "raw.md"
        if not raw.exists():
            return
        frag = next(
            (f for f in RawPageSplitter().split_file(raw) if f.page_number == page),
            None,
        )
        if frag is None:
            return
        label = DeterministicCleaner.load_printed_label(project.root, page)
        det = DeterministicCleaner(load_config()).clean(
            page_number=page, body=frag.body, printed_page_label=label
        )
        proposal = project.root / "experiments" / "cleaner" / f"page_{page:04d}" / "proposal.md"
        cleaned = (
            proposal.read_text(encoding="utf-8")
            if proposal.exists()
            else det.cleaned
        )
        clean_page = project.root / "clean_pages" / f"page_{page:04d}.md"
        if clean_page.exists() and not proposal.exists():
            cleaned = clean_page.read_text(encoding="utf-8")
        self.cleaner_review.show_page(
            page,
            det.cleaned,
            cleaned,
            f"page={page} · open Cleaner Review 操作",
        )

    def _on_cleaner_accept(self, page: int, text: str) -> None:
        svc = self._cleaner_svc()
        if svc is None:
            return
        result = svc.accept_cleaned(page, text, manually_edited=True)
        self._log(f"Cleaner 接受 p{page:04d}: {result.stage_status}")
        self._refresh_cleaner_panel()

    def _on_cleaner_keep_source(self, page: int) -> None:
        svc = self._cleaner_svc()
        if svc is None:
            return
        result = svc.accept_keep_source(page)
        self._log(f"Cleaner keep_source p{page:04d}: {result.stage_status}")
        self._refresh_cleaner_panel()
        self._refresh_final_panel()

    def _on_cleaner_reprocess(self, page: int) -> None:
        project = self._current_project
        if project is None:
            return
        worker = BatchCleanerWorker(
            project_root=project.root,
            db_path=project.db_path,
            pages=[page],
            force=True,
        )
        self._cleaner_worker = worker
        self.cleaner_panel.set_running(True)
        worker.signals.completed.connect(self._on_cleaner_completed)
        worker.signals.error.connect(self._on_cleaner_error)
        self._pool.start(worker)

    def _on_figure_marker_placement(
        self, page: int, fig_idx: int, offset: int, before: str, after: str
    ) -> None:
        svc = self._figure_review_svc()
        if svc is None:
            return
        svc.confirm_marker_placement(
            page_number=page,
            figure_index=fig_idx,
            char_offset=offset,
            before_context=before,
            after_context=after,
        )
        svc.rebuild_resolved_page(page)
        self._log(f"已确认 marker 插入位置 p{page:04d} fig{fig_idx:02d} @ {offset}")

    def _on_figure_skip(self, page: int, fig_idx: int) -> None:
        svc = self._figure_review_svc()
        if svc is None:
            return
        svc.skip_figure(page_number=page, figure_index=fig_idx)
        self._reload_figure_review()
        self._update_figure_readiness()

    def _on_figure_not_a_figure(self, page: int, fig_idx: int) -> None:
        svc = self._figure_review_svc()
        if svc is None:
            return
        svc.not_a_figure(page_number=page, figure_index=fig_idx)
        self._reload_figure_review()
        self._update_figure_readiness()

    def _reload_figure_review(self) -> None:
        project = self._current_project
        if project is None:
            return
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            items = repo.list_figure_review_items()
        finally:
            db.close()
        self.review_queue.set_figure_items(items)
        self.figure_review.set_items(items)
        self._update_figure_readiness()
        if items:
            first = items[0]
            self._on_figure_review_open(
                int(first["page_number"]), int(first["figure_index"])
            )

    def _on_render_cancelled(self, summary: object) -> None:
        self._log(self._format_summary("渲染已取消", summary))
        self._finish_render_ui()

    def _on_render_completed(self, summary: object) -> None:
        self._log(self._format_summary("渲染完成", summary))
        self._finish_render_ui()

    def _finish_render_ui(self) -> None:
        self._render_worker = None
        self.render_panel.set_rendering(False)
        self.statusBar().showMessage("就绪", 3000)

    @staticmethod
    def _format_summary(title: str, summary: object) -> str:
        if not isinstance(summary, dict):
            return title
        return (
            f"{title}：成功 {summary.get('success', 0)}，"
            f"缓存 {summary.get('cached', 0)}，"
            f"失败 {summary.get('failed', 0)}，"
            f"取消 {summary.get('cancelled', 0)}"
        )

    def _refresh_final_panel(self) -> None:
        project = self._current_project
        if project is None:
            self.final_panel.set_project_ready(False)
            self.final_panel.update_final(ready=False, message="无项目")
            return
        cfg = load_config()
        readiness = FinalReadinessService(
            project_root=project.root, db_path=project.db_path, config=cfg
        ).summarize()
        self.final_panel.update_readiness(readiness)
        freeze = FinalFreezeService(
            project_root=project.root, db_path=project.db_path, config=cfg
        )
        clean = project.root / "intermediate" / "clean.md"
        final = project.root / "final.md"
        clean_hash = file_sha256(clean) if clean.exists() else ""
        if (
            self._last_validation_clean_hash
            and clean_hash
            and clean_hash != self._last_validation_clean_hash
        ):
            self.final_panel.update_validation(
                status="STALE", details={}, clean_hash=clean_hash, stale=True
            )
        final_ready = (
            final.exists()
            and clean.exists()
            and file_sha256(final) == clean_hash
            and not freeze.is_final_stale()
        )
        self.final_panel.update_final(
            ready=final_ready,
            message="STALE" if final.exists() and not final_ready else "未生成",
        )

    def _start_final_worker(self, worker: FinalWorker) -> None:
        if self._final_worker is not None:
            QMessageBox.information(self, "Final", "Final/Export 任务正在运行。")
            return
        self._final_worker = worker
        self.final_panel.set_running(True)
        worker.signals.progress.connect(self.final_panel.set_status_message)
        worker.signals.completed.connect(self._on_final_completed)
        worker.signals.error.connect(self._on_final_error)
        self._pool.start(worker)

    def _on_final_validate(self) -> None:
        project = self._current_project
        if project is None:
            return
        worker = FinalWorker(
            action="validate",
            project_root=project.root,
            db_path=project.db_path,
        )
        self._start_final_worker(worker)
        self._log("开始 Final Validation")

    def _on_final_freeze(self) -> None:
        project = self._current_project
        if project is None:
            return
        worker = FinalWorker(
            action="freeze",
            project_root=project.root,
            db_path=project.db_path,
        )
        self._start_final_worker(worker)
        self._log("开始生成 final.md")

    def _on_final_export(self) -> None:
        project = self._current_project
        if project is None:
            return
        root = self.final_panel.export_root()
        worker = FinalWorker(
            action="export",
            project_root=project.root,
            db_path=project.db_path,
            export_root=Path(root) if root else None,
            include_source_pdf=self.final_panel.include_source_pdf(),
        )
        self._start_final_worker(worker)
        self._log("开始 Typora Export")

    def _on_choose_export_dir(self) -> None:
        chosen = self.final_panel.choose_directory(self.final_panel.export_root())
        if chosen:
            self.final_panel.set_export_root(chosen)

    def _on_open_export_dir(self) -> None:
        path = self.final_panel.last_export_dir()
        if path and path.exists():
            import os

            os.startfile(str(path))  # type: ignore[attr-defined]

    def _on_open_typora(self) -> None:
        path = self.final_panel.last_export_dir()
        if path is None:
            return
        project = self._current_project
        name = project.info.name if project else path.name
        md = path / f"{name}.md"
        if not md.exists():
            candidates = list(path.glob("*.md"))
            md = candidates[0] if candidates else md
        result = TyporaLauncher(load_config()).launch(md)
        if result.success:
            self._log(f"已打开 Markdown ({result.method}): {md}")
        else:
            QMessageBox.information(
                self,
                "Typora",
                f"未检测到 Typora 或无法启动。\n可手动打开：\n{md}\n\n{result.error or ''}",
            )

    def _on_final_completed(self, payload: object) -> None:
        self._final_worker = None
        self.final_panel.set_running(False)
        if not isinstance(payload, dict):
            return
        action = payload.get("action")
        result = payload.get("result")
        if action == "validate" and result is not None:
            v = getattr(result, "validation", None)
            details = {
                "page_markers": getattr(v, "page_markers", 0),
                "figure_markers": getattr(v, "figure_markers", 0),
                "image_links_total": getattr(v, "image_links_total", 0),
                "image_links_valid": getattr(v, "image_links_valid", 0),
                "absolute_paths": getattr(v, "absolute_paths", 0),
                "math_warnings": getattr(v, "math_warnings", []),
                "blocking": getattr(v, "blocking", []),
            }
            status = "PASS" if getattr(result, "success", False) else "FAIL"
            clean_hash = getattr(result, "clean_sha256", "") or ""
            self._last_validation_clean_hash = clean_hash
            self.final_panel.update_validation(
                status=status, details=details, clean_hash=clean_hash
            )
            self.final_panel.set_status_message(status)
            self._log(f"Final Validation: {status} blocking={details.get('blocking')}")
        elif action == "freeze" and result is not None:
            ok = bool(getattr(result, "success", False))
            self.final_panel.update_final(
                ready=ok,
                message=getattr(result, "status", ""),
            )
            if ok:
                v = getattr(result, "validation", None)
                details = {
                    "page_markers": getattr(v, "page_markers", 0),
                    "figure_markers": getattr(v, "figure_markers", 0),
                    "image_links_total": getattr(v, "image_links_total", 0),
                    "image_links_valid": getattr(v, "image_links_valid", 0),
                    "absolute_paths": getattr(v, "absolute_paths", 0),
                    "math_warnings": getattr(v, "math_warnings", []),
                    "blocking": getattr(v, "blocking", []),
                }
                self._last_validation_clean_hash = getattr(result, "clean_sha256", "")
                self.final_panel.update_validation(
                    status="PASS",
                    details=details,
                    clean_hash=self._last_validation_clean_hash,
                )
            self.final_panel.set_status_message(getattr(result, "status", ""))
            self._log(
                f"Final freeze: {getattr(result, 'status', '')} "
                f"sha={getattr(result, 'final_sha256', '')}"
            )
        elif action == "export" and result is not None:
            ok = bool(getattr(result, "success", False))
            path = getattr(result, "export_path", None)
            self.final_panel.set_export_result(
                path if ok else None,
                message=getattr(result, "status", ""),
            )
            self._log(
                f"Export: {getattr(result, 'status', '')} → {path} "
                f"err={getattr(result, 'error', None)}"
            )
            if not ok:
                QMessageBox.warning(
                    self, "Export 失败", getattr(result, "error", "") or "failed"
                )
        self._refresh_final_panel()

    def _on_final_error(self, msg: str) -> None:
        self._final_worker = None
        self.final_panel.set_running(False)
        self.final_panel.set_status_message("FAILED")
        self._log(f"Final/Export 失败: {msg}")
        QMessageBox.critical(self, "Final/Export 失败", msg)

    def _on_run_engine_benchmark(self) -> None:
        project = self._current_project
        if project is None:
            QMessageBox.information(self, "Benchmark", "请先导入 PDF。")
            return
        engines = self.benchmark_panel.selected_engines()
        if not engines:
            QMessageBox.information(self, "Benchmark", "请至少勾选一个引擎。")
            return
        expr = self.benchmark_panel.page_range_text()
        try:
            from utils.page_range import parse_page_range

            pages = parse_page_range(expr, project.info.page_count)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "页码无效", str(exc))
            return

        from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
        from services.document_engine_benchmark import DocumentEngineBenchmark

        self.benchmark_panel.set_running(True)
        self.benchmark_panel.append_log(
            f"开始 Benchmark：pages={pages} engines={engines}"
        )
        self._log(f"Phase 9.5.2 Benchmark 开始：{engines} pages={pages}")

        class _Sig(QObject):
            progress = pyqtSignal(str)
            finished = pyqtSignal(object, object)
            error = pyqtSignal(str)

        class _Worker(QRunnable):
            def __init__(self) -> None:
                super().__init__()
                self.signals = _Sig()
                self.setAutoDelete(True)

            def run(self_inner) -> None:  # noqa: N805
                try:
                    bench = DocumentEngineBenchmark()
                    report = bench.run(
                        pdf_path=project.info.source_pdf,
                        pages=pages,
                        engines=engines,
                        pages_dir=project.pages_dir,
                        on_progress=lambda m: self_inner.signals.progress.emit(m),
                    )
                    paths = bench.write_report(
                        report, project.root / "reports", stem="phase952_benchmark"
                    )
                    self_inner.signals.finished.emit(report, paths)
                except Exception as exc:  # noqa: BLE001
                    self_inner.signals.error.emit(str(exc))

        w = _Worker()

        def on_prog(msg: str) -> None:
            self.benchmark_panel.append_log(msg)

        def on_ok(report: object, paths: object) -> None:
            self.benchmark_panel.set_running(False)
            md_path = ""
            if isinstance(paths, dict):
                md_path = str(paths.get("md") or "")
            self.benchmark_panel.set_last_report(md_path)
            table = getattr(report, "to_markdown_table", lambda: "")()
            self.benchmark_panel.append_log(table)
            self.benchmark_panel.append_log(f"报告: {md_path}")
            self._log(f"Phase 9.5.2 Benchmark 完成 → {md_path}")
            QMessageBox.information(
                self,
                "Benchmark 完成",
                f"已写入报告：\n{md_path}\n\n未安装的引擎会显示 not installed，属预期。",
            )

        def on_err(msg: str) -> None:
            self.benchmark_panel.set_running(False)
            self.benchmark_panel.append_log(f"失败: {msg}")
            self._log(f"Benchmark 失败: {msg}")
            QMessageBox.critical(self, "Benchmark 失败", msg)

        w.signals.progress.connect(on_prog)
        w.signals.finished.connect(on_ok)
        w.signals.error.connect(on_err)
        self._pool.start(w)

    def _on_open_benchmark_report(self) -> None:
        path = self.benchmark_panel.last_report()
        project = self._current_project
        if not path and project is not None:
            reports = sorted(
                (project.root / "reports").glob("phase952_benchmark_*.md"),
                reverse=True,
            )
            path = str(reports[0]) if reports else ""
        if not path:
            QMessageBox.information(self, "Benchmark", "还没有报告，请先运行。")
            return
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._final_worker is not None:
            self._final_worker.request_cancel()
        if self._cleaner_worker is not None:
            self._cleaner_worker.request_cancel()
        if self._assembly_worker is not None:
            self._assembly_worker.request_cancel()
        if self._figure_worker is not None:
            self._figure_worker.request_cancel()
        if self._batch_worker is not None:
            self._batch_worker.request_pause()
        if self._render_worker is not None:
            self._render_worker.request_cancel()
        try:
            self._ollama_manager.stop_managed()
        except Exception:  # noqa: BLE001
            logger.exception("Error stopping managed Ollama on exit")
        super().closeEvent(event)

    @property
    def current_project(self) -> Project | None:
        return self._current_project

    @property
    def ollama_manager(self) -> OllamaRuntimeManager:
        return self._ollama_manager
