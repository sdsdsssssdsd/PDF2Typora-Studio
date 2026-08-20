# Phase 9 Report — Final Validation + `final.md` + Typora Export

**Status:** COMPLETE  
**Stopped before:** Phase 10 (portable packaging)  
**pytest:** 123 passed  
**Pilot project:** `workspace/_phase4_vision/O-001_Kuzilek2017_DataPaper`  
**Source PDF:** `E:\PDFtomd\O-001_Kuzilek2017_DataPaper.pdf`

## Verdict

Current document final validation: **PASS**  
`final.md` is byte-identical to `intermediate/clean.md`.  
Typora export: **SUCCESS** (second run **UP_TO_DATE**).  
Math-heavy textbook qualification: **PENDING** (release warning only, not blocking this PDF).

Typora launch: **not performed** (`typora.executable_path` empty). Export still PASS.

## Schema v9

- `CURRENT_SCHEMA_VERSION = 9`
- New table: `export_runs`
- Reused: `document_artifacts` (`type=final`)
- No `final_runs` / `final_artifacts` tables

## FinalReadinessService

READY when:

- project + `source.pdf` + `clean.md` exist
- Clean / Assemble / Figures readiness all true
- no RUNNING render/transcribe/figure/clean stages
- no blocking Transcription / Figure / Cleaner reviews

`READY_WITH_WARNINGS` is used for `math_heavy_validation_pending`.

## FinalValidator (check only, never rewrite)

Blocking: missing/empty/non-UTF8 clean.md, PAGE/FIGURE markers, `---`, placeholders, outer ```markdown fence, `\(`/`\[`, unbalanced `$`/`$$` / LaTeX envs / `aligned`, absolute/http/unsafe image paths, path traversal, missing `figures/` files.

## Pilot hashes

| Artifact | SHA256 |
|---|---|
| `intermediate/clean.md` | `15869979649aa9f279b6d90e0e1a18d3412fe4005f1da11c5c1faead8578a141` |
| `final.md` | `15869979649aa9f279b6d90e0e1a18d3412fe4005f1da11c5c1faead8578a141` |
| exported `.md` | `15869979649aa9f279b6d90e0e1a18d3412fe4005f1da11c5c1faead8578a141` |

- byte-identical: **yes**
- `final.md` size: **15255 bytes**
- PAGE markers: **0**
- FIGURE markers: **0**
- image links: **4 / 4 valid**, missing **0**
- absolute paths: **0**
- unsafe paths: **0**
- horizontal rules: **0**
- math: **PASS** (no `\(`/`\[`; `$`/`$$`/envs balanced)
- freeze 2nd: **UP_TO_DATE**
- export 2nd: **UP_TO_DATE**
- upstream `raw.md` / `markdown_pages` / `resolved_pages` / `figures`: **UNCHANGED**
- Phase 9 AI calls: **0**

## Typora export

```
exports/O-001_Kuzilek2017_DataPaper/
  O-001_Kuzilek2017_DataPaper.md
  figures/  (4 png)
  source.pdf
```

- exported markdown hash == final hash
- `source.pdf` hash matches project `source.pdf` (672465 bytes)
- workspace junk (`project.db`, logs, experiments) **not** copied

## Fresh E2E

Script: `scripts/phase9_e2e.py`  
Isolated workspace: `workspace/_e2e_phase9_O-001_Kuzilek2017_DataPaper/O-001_Kuzilek2017_DataPaper_3`  
Does **not** run under default pytest.  
PDF hash: `08d310c9e2a1d086da8f70bf85c70f08fec4d047166078750b703a08f265b3d6`  
Model: `gemma3:4b-it-q4_K_M` digest `a2af6cc3eb7fa8be8504abaf9b04e88f17a119ec3f04a3addf55f92841195f5a`

First run (fresh):

- Render: 8 success, 0 cache, 6.3s
- Vision: 8 real calls, auto-accepted **3**, review **5**, failed **0**, 251s
- **Paused honestly at Transcription Review** (pages 1, 3, 4, 7, 8)
- Did **not** forge RESOLVED / guess bbox
- Assemble / Cleaner / Final / Export: not started (blocked by review)

`--resume` second run (same project): **2.3s**, stopped at the same Transcription Review, no extra Vision calls.

Cache UP_TO_DATE for Final/Export was verified on the completed Phase 8 pilot (`phase9_live.py`), not on this incomplete fresh project.

To continue after GUI review:

```
python scripts/phase9_e2e.py --resume --workspace "E:\PDFtomd\workspace\_e2e_phase9_O-001_Kuzilek2017_DataPaper\O-001_Kuzilek2017_DataPaper_3"
```

## Files

Added:

- `core/final_models.py`
- `services/final_readiness_service.py`
- `services/final_validator.py`
- `services/final_freeze_service.py`
- `services/typora_export_service.py`
- `services/typora_launcher.py`
- `workers/final_worker.py`
- `gui/widgets/final_panel.py`
- `tests/test_final_phase9.py`
- `scripts/phase9_live.py`
- `scripts/phase9_e2e.py`
- `docs/PHASE9_REPORT.md`

Modified:

- `storage/database.py` (v9 + `export_runs`)
- `storage/repository.py`
- `config/default.yaml` (`final` / `export` / `typora`)
- `gui/main_window.py`
- `tests/test_gui_smoke.py`, `test_transcription.py`, `test_database.py`, `test_cleaner_phase8.py`
- `docs/ROADMAP.md`

## Phase 10 not started

No PyInstaller, no bundled Ollama installer.
