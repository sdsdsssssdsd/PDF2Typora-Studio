"""Phase 9.5.2 — Open-source Document Engine Benchmark & Fusion

Source: `模块结构设计9.5.2.md`

Scope: adapters + same-PDF benchmark. **No training. No Phase 10.**
"""

from __future__ import annotations

## Goal

```text
同一 PDF
  → Native / MinerU / Marker / Docling / Chandra
  → 统一 DocumentPageEvidence
  → 逐页指标表
  → 决定谁进入正式 Pipeline（本阶段只测，不强制切换默认引擎）
```

## Implemented

| Piece | Path |
|-------|------|
| `DocumentPageEvidence` / `DocumentBlock` | `core/document_page_model.py` |
| `DocumentParserProvider` ABC | `ai/document_parsers/base.py` |
| Native PyMuPDF provider (always on) | `ai/document_parsers/native_pdf_provider.py` |
| MinerU / Marker / Docling / Chandra adapters | `ai/document_parsers/*_provider.py` |
| Registry | `ai/document_parsers/registry.py` |
| Evidence → `PageEvidenceManifest` fusion | `services/evidence_fusion.py` |
| Benchmark + markdown table | `services/document_engine_benchmark.py` |
| CLI | `scripts/phase952_benchmark.py` |
| GUI panel + pipeline step「实验 · 引擎对比」 | `gui/widgets/benchmark_panel.py` |
| Config `quality.document_engines` | `config/default.yaml` |
| Tests | `tests/test_phase952_benchmark.py` |

## Design rules followed

- **Do not vendor** MinerU/Marker/Docling/Chandra source trees — optional imports / CLI only.
- Uninstalled engines return `ok=False` with clear `*_not_installed` (still appear in the table).
- Default conversion path remains Hybrid OCR+API / Vision; Benchmark is **experimental**.
- License notes recorded per provider (Docling MIT; others Apache-2.0 + model caveats).

## Metrics (acceptance table)

| Column | Meaning |
|--------|---------|
| Missing Text | `1 - token_overlap(engine, native_pdf)` |
| Figure Recall | vs native caption labels |
| Figure ID | PASS if recall ≈ 1 |
| Table / Formula | counts from blocks |
| Reading Order | monotonic `reading_order` score |
| Time | ms |

## How to run

```bash
# unit tests (native always; others may be not installed)
python -m pytest tests/test_phase952_benchmark.py -q

# CLI on a project
python scripts/phase952_benchmark.py --project workspace/O-001_Kuzilek2017_DataPaper_14 --pages 1-8

# or PDF directly
python scripts/phase952_benchmark.py --pdf path/to.pdf --pages 1,4,8
```

GUI: left rail → **实验 · 引擎对比** →勾选引擎 → 运行 Benchmark → 报告写入 `project/reports/phase952_benchmark_*.md`.

## Explicitly NOT done

- LoRA / training (olmOCR-style) — deferred
- Hard-wiring MinerU/Marker as default production engine
- Phase 10 packaging
- Prompt-only iteration as the main fix

## Next decision (after real installs)

Compare reports, then choose one of:

- `PyMuPDF + MinerU`
- `PyMuPDF + Marker + Chandra`
- `Docling DOM + Chandra for hard blocks + DeepSeek for conflicts`
