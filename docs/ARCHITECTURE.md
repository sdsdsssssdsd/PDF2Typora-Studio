# PDF2Typora Studio — Architecture

## Overview

PDF2Typora Studio converts textbook PDFs into Typora-compatible Markdown using local Vision AI (Ollama) with optional cloud providers.

## Layer Separation

```
GUI → Workers / Services → Runtime Manager / Providers / Render → HTTP / Process / PyMuPDF
```

- **GUI** (`gui/`): PyQt6 only; never calls `page.get_pixmap` or HTTP directly.
- **Workers** (`workers/`): QRunnable for Ollama and PDF render jobs.
- **Services** (`services/`): Business logic (`ProjectService`, `RenderService`, …).
- **Core** (`core/`): Domain models, stage enums, pipeline state.
- **Storage** (`storage/`): SQLite + safe schema migration.
- **AI** (`ai/`): Pluggable Vision providers + Ollama runtime manager.

## Render Pipeline (Phase 3)

```
GUI → RenderWorker → RenderService → PyMuPDF → pages/page_XXXX.png
                           ↓
                    page_stage_states (render)
```

- 1-based page numbers in UI / DB; 0-based only inside `page_number_to_index()`.
- Cache key: `pdf_hash + page + dpi + format + colorspace + alpha + pipeline_version`.
- Atomic write: `*.part.png` → `*.tmp.png` → `page_XXXX.png`.

## AI Runtime vs Provider

```
OllamaRuntimeManager  — process lifecycle
OllamaApiClient       — HTTP /api/*
OllamaVisionProvider  — VisionProvider implementation
```

## Current Phase

**Phase 0–6 complete**: … batch Vision pipeline, **Figure extraction & marker resolution**.

## Figure Pipeline (Phase 6)

```
Canonical markdown + page_results JSON
→ FigureCandidateService (get_image_info / vector clusters)
→ FigureMatcher → FigureExtractor (native / PDF clip @ 300 DPI)
→ figures/pPPPP_figNN.png + resolved_pages/page_PPPP.md
```

- Canonical `markdown_pages/` is never modified
- FIGURE markers replaced only in `resolved_pages/`

## Batch Vision (Phase 5)

```
GUI → BatchTranscriptionWorker → BatchTranscriptionService
        → TranscriptionService → OllamaVisionProvider (inference_lock)
        → Auto-accept / Review Queue / Canonical cache
```

- SQLite schema v4: `batch_runs`, `batch_items`
- Model qualification is digest-bound (`config/model_profiles.json`)

