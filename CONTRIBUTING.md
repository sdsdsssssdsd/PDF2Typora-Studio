# Contributing

Thanks for looking at the project. It is early-stage and still moving quickly; small, reviewable changes are easier to land than large rewrites.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Optional local config:

```bash
copy config\user.yaml.example config\user.yaml
```

Run the app:

```bash
python main.py
```

## Tests

From the repo root:

```bash
pytest
```

Useful subsets:

```bash
pytest tests/test_caption_anchored_figures.py tests/test_figure_pipeline.py -q
pytest tests/test_assemble_phase7.py tests/test_cleaner_phase8.py -q
```

GUI smoke tests need a display; skip them in headless CI if they fail for that reason.

## Pull requests

- Prefer one concern per PR (feature, fix, or docs).
- Do not commit `.venv/`, `workspace/`, `exports/`, `logs/`, `.env`, or `config/user.yaml`.
- Do not put API keys in the tree; the app stores them in the OS keyring.
- Match existing naming and layout under `services/`, `gui/`, `tests/`.

## Reporting a conversion regression

If a PDF that used to convert well now fails, or a new sample breaks a stage:

1. Open a **PDF conversion failure** issue (template in the repo).
2. Say which stage failed: render / transcribe / figures / assemble / clean / final.
3. Attach a **minimal reproducible sample** when you can (a few pages, stripped of private data). Full books are hard to debug in public issues.
4. Include app version or git commit, Python version, OS, and whether you used Vision Only or Hybrid OCR+API.
5. Paste the relevant log lines from `logs/` or the in-app log panel (redact keys and paths you care about).

If you can, note whether `markdown_pages/`, `figures/`, or `intermediate/raw.md` were produced before the failure — that narrows the stage quickly.

## Code of conduct (short)

Be concrete in issues and reviews. Assume good faith. Do not paste secrets or others’ copyrighted fulltexts into the tracker when a redacted sample will do.
