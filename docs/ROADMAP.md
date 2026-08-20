# PDF2Typora Studio — Roadmap

## Phase 0 — Project Skeleton ✅
## Phase 1 — PDF Import ✅
## Phase 2 — Portable Ollama ✅
## Phase 3 — PDF Rendering ✅

## Phase 4 — Single-page Vision ✅

- [x] Pydantic `PageTranscriptionResult` + FigureDetection
- [x] Ollama structured outputs (`format` JSON Schema)
- [x] TranscriptionService + Validator
- [x] Experiments vs Canonical accept flow
- [x] schema v3 / ai_requests provenance
- [x] Vision Workbench GUI + model comparison
- [x] Real Vision model smoke (when Ollama available)

## Phase 5 — Batch Vision Pipeline ✅

- [x] Prompt harden: system + short user + schema only in `format`
- [x] Prompt leak / invented image URL blockers
- [x] ModelProfile by name+digest (QUALIFIED / LIMITED / DISABLED)
- [x] schema v4 `batch_runs` / `batch_items`
- [x] BatchTranscriptionService + Worker: retry, ctx 8192 max, fallback
- [x] Auto-accept gate + Review Queue + pause/resume/cancel/crash recovery
- [x] GPU inference lock (concurrency = 1)
- [x] Canonical cache + history on overwrite

## Phase 6 — Figure Pipeline ✅

- [x] AI proposal + PDF native/vector candidates + PDF clip fallback
- [x] Figure matcher (IoU/containment/center distance)
- [x] `figures/` artifacts + `resolved_pages/` (canonical untouched)
- [x] schema v5 figure provenance
- [x] Batch figure worker + Figure Review GUI (skeleton)
- [x] Marker mismatch → NEEDS_REVIEW (Page 4 regression)

## Phase 6.5 — Marker Repair + Figure Review ✅

- [x] `FigureMarkerNormalizer` — loose marker detection + syntax-only repair
- [x] schema v6 — marker repair / manual bbox / review provenance columns
- [x] `ResolvedPageBuilder` — deterministic resolved page + hash v2
- [x] `FigureReviewService` — preview (`.cache/figure_preview/`), accept, placement
- [x] Full Figure Review GUI — QGraphicsView zoom, manual crop, candidates, shortcuts
- [x] Review Queue tabs — Transcription / Figures
- [x] `FigureReadinessService` — Ready / Not Ready gate (no Assemble button)
- [x] Config: `marker_normalization`, `review`, `preview`, `readiness`
- [x] Pilot pages 3/4/7 — **4/4 RESOLVED**, canonical SHA256 unchanged
- [x] pytest **78 passed**

## Phase 7 — Deterministic Markdown Assemble ✅

- [x] `PageSourceResolver` — resolved > canonical, DB page order
- [x] `AssembleReadinessService` — transcription + figures + sources gate
- [x] `MarkdownAssembler` → `intermediate/raw.md` (atomic, cache, archive)
- [x] `AssembledMarkdownValidator` — PAGE markers / figures / no unresolved FIGURE
- [x] `ContinuityAnalyzer` + `continuity_patches` overlay (no AI rewrite)
- [x] schema v7 — continuity_patches / assemble_runs / document_artifacts
- [x] Assemble Panel + Continuity Review GUI + AssemblyWorker
- [x] 8-page Pilot: PAGE=8, FIGURE=0 unresolved, figures=4 valid, 2nd=CACHED
- [x] Upstream SHA256 unchanged; pytest **94 passed**
- [x] **Stopped before Cleaner** (no clean.md / final.md)

## Phase 8 — Deterministic Markdown Cleaner ✅

- [x] `RawPageSplitter` — split raw.md by PAGE markers
- [x] `DeterministicCleaner` — HR / printed label / math delimiters / outer fence
- [x] `CleaningNeedAnalyzer` + SMART mode (rules-first, AI only if needed)
- [x] Content Preservation Validator — math/table/numeric/URL/image/prose
- [x] `clean_pages/` + `clean_traced.md` + `clean.md` (no final.md)
- [x] schema v8 `cleaner_reviews`; Batch Cleaner worker pause/resume/cancel
- [x] Cleaner Panel + Cleaner Review + Clean Readiness
- [x] 8-page Pilot: rules-only 8, AI=0, PAGE=0 in clean.md, 2nd=CACHED
- [x] raw/upstream SHA256 unchanged; pytest **108 passed**
- [x] **Stopped before Phase 9** (no final.md)

## Phase 9 — Final Validation + Export ✅

- [x] `FinalReadinessService` — Clean/Assemble/Figures + no blocking review / no RUNNING
- [x] `FinalValidator` — check only (PAGE/FIGURE/`---`/images/math); never rewrite
- [x] `final.md` atomic copy of `clean.md` (byte-identical SHA256)
- [x] schema v9 `export_runs`; `document_artifacts` type=`final`
- [x] `TyporaExportService` staging → `exports/<name>/` (md + figures + source.pdf)
- [x] `TyporaLauncher` — configured exe or `os.startfile`; Export does not require Typora
- [x] Final Panel + `FinalWorker` (no AI / no GPU lock)
- [x] Pilot: validation PASS, 4/4 figures, freeze/export 2nd = UP_TO_DATE
- [x] `scripts/phase9_e2e.py` — honest Review stop + `--resume`; not in default pytest
- [x] pytest **123 passed**; **Stopped before Phase 10**

## Phase 9.5 — Quality Reconstruction ✅

- [x] PDF text/style extractor (`rawdict` spans → bold/color/bbox)
- [x] `PageLayoutManifest` before Vision/Figure fusion
- [x] `FigureGroup` + caption/spatial grouping; multi-subfigure → PDF_CLIP
- [x] `FigureReconciler` v2 bidirectional (UNREFERENCED_FIGURE_CANDIDATE)
- [x] Identity by Fig label — not AI `figure_index`
- [x] `StyleReconstructor` + `TextCoverageValidator` + `EscapeSanitizer`
- [x] API Provider Manager + PyQt API settings (keyring, no key in yaml)
- [x] PaddleOCR-VL / MinerU optional adapters (graceful if missing)
- [x] Training dataset collector (no training yet)
- [x] schema v10; pytest **131 passed**
- [x] **Stopped before Phase 10 / 9.6 LoRA**

## Phase 10 — Portable Packaging
## Phase 9.6 — LoRA / SFT Evaluation (deferred)
