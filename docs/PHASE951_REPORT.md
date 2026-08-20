"""Phase 9.5.1 — OCR + DeepSeek Reconstruction (from 模块结构设计9.6.md)

NOTE: The design file is named 9.6 but explicitly defers LoRA training.
This phase implements Hybrid OCR/PDF evidence → text-API Markdown reconstruction.
LoRA / Phase 9.6 training is NOT started.
"""

from __future__ import annotations

## Summary

Document `模块结构设计9.6.md` recommends **Phase 9.5.1** first:

```text
PDFTextStyleExtractor + PP-OCR
  → PageEvidenceManifest
  → DeepSeek / text API reconstruction
  → Coverage + FigureGroup checks
```

and says training (true Phase 9.6 LoRA) should wait until more corrected pages exist.

## Implemented

| Piece | Path |
|-------|------|
| Evidence models + `PageTextSourceMode` / `PageEngineMode` | `core/evidence_models.py` |
| PP-OCR adapter (optional paddleocr) | `ai/document_parsers/ppocr_adapter.py` |
| Evidence builder (PDF native + OCR + Fig captions) | `services/page_evidence_builder.py` |
| Markdown reconstruction (API + deterministic fallback) | `services/markdown_reconstruction_service.py` |
| Hybrid orchestration | `services/hybrid_transcription_service.py` |
| Reconstruction prompt | `prompts/reconstruction.txt` |
| Page engine UI (Hybrid recommended) | `gui/widgets/batch_transcription_panel.py` |
| Single-page Hybrid run path | `gui/widgets/transcription_panel.py` |
| Config `transcription.page_engine` | `config/default.yaml` |
| Unit tests | `tests/test_phase951_hybrid.py` |

## Engine modes

- `hybrid_ocr_api` (default, recommended) — PDF + OCR evidence → text API (DeepSeek etc. BYOK)
- `vision_only` — previous Gemma/Ollama Vision path (A/B compare)
- `pdf_ocr_local` / `parser_only` — reserved selectors (UI present; hybrid builder is the shared core)

## Behavior notes

- Without PaddleOCR installed: still builds PDF-native evidence; warns `paddleocr_not_installed`.
- Without API Key: deterministic styled Markdown fallback + `needs_review`.
- Figure labels from captions must appear in output or `FIGURE_GROUP_MISSING_IN_MARKDOWN` is raised.
- Text coverage validator remains in the reconstruction path.

## Explicitly NOT done (per document)

- Phase 9.6 Domain Fine-tuning / LoRA
- Training loops / weight export
- Phase 10 packaging

## How to try

1. Render pages for a project.
2. Open **外部 API 配置**, set page transcription to DeepSeek (or OpenAI-compatible), fill your Key.
3. On step 3, set engine to **Hybrid OCR + API**.
4. Run single-page transcription — see `experiments/hybrid/page_XXXX/`.

## Tests

```bash
python -m pytest tests/test_phase951_hybrid.py -q
```
