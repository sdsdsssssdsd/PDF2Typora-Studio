# AI Provider Design

## Structured Transcription (Phase 4–5)

```
page PNG
→ TranscriptionService (system + short user + image)
→ OllamaVisionProvider.transcribe_page_structured
→ format=PageTranscriptionResult.model_json_schema()
→ extra=forbid Pydantic + leak/URL validators
→ experiments/.../attempt_*
→ Auto-accept or Review Queue → markdown_pages + page_results
```

## Schema

`ai/schemas/transcription.py` is the single source of truth.

## Rules

- Experiments never overwrite canonical `page_XXXX.md` until user accepts
- No regex salvage of invalid JSON
- One Vision model at a time on 8GB GPU; comparison uses `keep_alive=0`
- Vision-only baseline (no PDF text layer in prompt)
