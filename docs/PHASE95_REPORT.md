# Phase 9.5 Report — Quality Reconstruction

**Status:** COMPLETE (foundation)  
**Stopped before:** Phase 10 packaging / Phase 9.6 LoRA training  
**pytest:** 131 passed

## Verdict

识别架构已从「整页 Vision 唯一真值」转向 **PDF 原生 + Layout Manifest + FigureGroup + 覆盖率/转义校验 + API Provider 骨架**。  
PaddleOCR-VL / MinerU 以 **可选适配器** 接入（未安装时优雅降级），本阶段不强制安装、不训练模型。

## Delivered

1. `PDFTextStyleExtractor` — PyMuPDF `rawdict` span（bold/italic/color/bbox）
2. `PageLayoutManifestService` → `layout/page_XXXX.json`
3. `FigureGroup` + `FigureGroupService` — Fig. N 整体为单位；多子图强制 `PDF_CLIP`
4. `FigureReconciler` v2 — AI↔PDF 双向对账；`UNREFERENCED_FIGURE_CANDIDATE`
5. `StableFigureId` / `stable_figure_filename` — **不用 figure_index 作为身份**
6. `StyleReconstructor` — PDF bold → `**`；颜色 → Typora `<span style="color:…">`
7. `TextCoverageValidator` — PDF text layer vs Markdown 覆盖率
8. `MarkdownEscapeSanitizer` — 错误字面 `\n` → 真换行；保护 `\nu`/`\neq`/`\nabla`…
9. `TrainingDatasetCollector` — `training_dataset/`（只收集，不训练）
10. `ApiProviderManager` + `APISettingsDialog` — Key 进 OS keyring，不写 `user.yaml`
11. Document parser stubs: `PaddleOCRVLAdapter` / `MinerUAdapter`
12. schema **v10** — `figure_label` / `force_pdf_clip` / `page_layout_runs`

## Pilot（同一 8 页 PDF）

项目：`workspace/_phase4_vision/O-001_Kuzilek2017_DataPaper`  
脚本：`scripts/phase95_live.py` → `phase95_live_report.json`

抽样：

| 页 | spans | captions | groups (label/members/clip) | PDF coverage |
|---|---|---|---|---|
| 1 | 419 | — | — | 0.83 |
| 3 | 286 | 2,3 | Fig.2×3 clip；Fig.3×0 | 0.92 |
| 4 | 851 | 4 | Fig.4×2 **force PDF_CLIP** | 0.70 |
| 7 | 95 | 5 | Fig.5×5 clip | 0.80 |

- Escape sanitizer / FigureGroup / API dialog 就绪
- Coverage / Reconcile 按页记录（例如 AI caption 与 PDF `Fig. N` 对不上 → `PDF_FIGURE_MISSING_IN_AI`），**不伪造通过**
- 已修复：`rawdict` 无 `text` 字段 → 改用 `dict` 提取 span

## Hard rules locked

- Figure 正式单位 = **Fig. 编号整体**，不是 subplot
- 多 raster / vector 混合 → **必须 PDF_CLIP**
- 禁止用 AI `figure_index` 作为最终身份
- 无法判定同组 → **Figure Review**，不得自动拆分错配

## Not done (intentional)

- 完整接入并跑通 PaddleOCR-VL-1.6 / MinerU 端到端（适配器已就位）
- LoRA / SFT（Phase 9.6，等训练数据积累）
- Phase 10 打包

## Files (main)

Added: `core/layout_models.py`, `services/escape_sanitizer.py`, `pdf_text_style_extractor.py`, `page_layout_manifest_service.py`, `figure_group_service.py`, `figure_reconciler.py`, `style_reconstructor.py`, `text_coverage_validator.py`, `training_dataset_collector.py`, `api_credential_store.py`, `ai/providers/api_provider_manager.py`, `ai/document_parsers/*`, `gui/dialogs/api_settings_dialog.py`, `tests/test_quality_phase95.py`, `scripts/phase95_live.py`, `docs/PHASE95_REPORT.md`

Modified: `figure_service.py`, `figure_extractor.py`, `figure_models.py`, `transcription_service.py`, `database.py`, `default.yaml`, `requirements.txt`, `main_window.py`, schema assertions, `docs/ROADMAP.md`
