"""Phase 9.5.2 multi-engine document parser benchmark."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai.document_parsers.registry import ENGINE_ORDER, get_provider, list_engines
from core.document_page_model import DocumentPageEvidence
from utils.paths import ensure_dir

_WORD_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,}")


@dataclass
class EnginePageMetrics:
    page: int
    engine: str
    ok: bool
    installed: bool
    missing_text_ratio: float | None
    figure_recall: float | None
    figure_id_ok: bool | None
    table_count: int
    formula_count: int
    reading_order_score: float | None
    time_ms: float
    error: str | None = None
    figure_labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkReport:
    pdf_path: str
    pages: list[int]
    engines: list[str]
    rows: list[EnginePageMetrics]
    created_at: str
    engine_availability: list[dict]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_path": self.pdf_path,
            "pages": self.pages,
            "engines": self.engines,
            "created_at": self.created_at,
            "engine_availability": self.engine_availability,
            "rows": [r.to_dict() for r in self.rows],
        }

    def to_markdown_table(self) -> str:
        header = (
            "| Page | Engine | Missing Text | Figure Recall | Figure ID | "
            "Table | Formula | Reading Order | Time ms | Status |\n"
            "| ---- | ------ | -----------: | ------------: | --------: | "
            "----: | ------: | ------------: | ------: | ------ |\n"
        )
        lines = [header]
        for r in self.rows:
            mt = "—" if r.missing_text_ratio is None else f"{r.missing_text_ratio:.2%}"
            fr = "—" if r.figure_recall is None else f"{r.figure_recall:.2%}"
            fid = "—" if r.figure_id_ok is None else ("PASS" if r.figure_id_ok else "FAIL")
            ro = "—" if r.reading_order_score is None else f"{r.reading_order_score:.2f}"
            st = "OK" if r.ok else (r.error or "FAIL")
            lines.append(
                f"| {r.page} | {r.engine} | {mt} | {fr} | {fid} | "
                f"{r.table_count} | {r.formula_count} | {ro} | "
                f"{r.time_ms:.0f} | {st} |\n"
            )
        return "".join(lines)


class DocumentEngineBenchmark:
    """Same PDF → multiple parsers → unified metrics."""

    version = "1"

    def run(
        self,
        *,
        pdf_path: Path,
        pages: list[int],
        engines: list[str] | None = None,
        pages_dir: Path | None = None,
        reference_engine: str = "native_pdf",
        on_progress: Callable[[str], None] | None = None,
    ) -> BenchmarkReport:
        engines = list(engines or ENGINE_ORDER)
        availability = list_engines()
        ref_by_page: dict[int, DocumentPageEvidence] = {}
        rows: list[EnginePageMetrics] = []

        # Reference pass
        ref = get_provider(reference_engine)
        if ref is not None:
            for page in pages:
                img = _page_image(pages_dir, page)
                if on_progress:
                    on_progress(f"reference {reference_engine} page {page}")
                ref_by_page[page] = ref.analyze_page(pdf_path, page, page_image=img)

        for engine in engines:
            provider = get_provider(engine)
            for page in pages:
                if on_progress:
                    on_progress(f"{engine} page {page}")
                if provider is None:
                    rows.append(
                        EnginePageMetrics(
                            page=page,
                            engine=engine,
                            ok=False,
                            installed=False,
                            missing_text_ratio=None,
                            figure_recall=None,
                            figure_id_ok=None,
                            table_count=0,
                            formula_count=0,
                            reading_order_score=None,
                            time_ms=0.0,
                            error="unknown_engine",
                        )
                    )
                    continue
                img = _page_image(pages_dir, page)
                evidence = provider.analyze_page(pdf_path, page, page_image=img)
                ref_ev = ref_by_page.get(page)
                rows.append(score_against_reference(evidence, ref_ev))

        return BenchmarkReport(
            pdf_path=str(pdf_path),
            pages=pages,
            engines=engines,
            rows=rows,
            created_at=datetime.now(timezone.utc).isoformat(),
            engine_availability=availability,
        )

    def write_report(
        self, report: BenchmarkReport, out_dir: Path, *, stem: str = "phase952_benchmark"
    ) -> dict[str, Path]:
        out = ensure_dir(out_dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = out / f"{stem}_{stamp}.json"
        md_path = out / f"{stem}_{stamp}.md"
        json_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md = (
            "# Phase 9.5.2 Document Engine Benchmark\n\n"
            f"- PDF: `{report.pdf_path}`\n"
            f"- Pages: {report.pages}\n"
            f"- Engines: {', '.join(report.engines)}\n"
            f"- Created: {report.created_at}\n\n"
            "## Availability\n\n"
            + "\n".join(
                f"- **{e['name']}** (`{e['id']}`): "
                f"{'available' if e['available'] else 'not installed'} — {e['license']}"
                for e in report.engine_availability
            )
            + "\n\n## Results\n\n"
            + report.to_markdown_table()
            + "\n\n## Notes\n\n"
            "- Missing Text = 1 - |tokens(engine) ∩ tokens(native_pdf)| / |tokens(native_pdf)| "
            "(lower is better).\n"
            "- Figure Recall vs native caption labels when reference has figures.\n"
            "- Engines not installed still appear as rows with status error.\n"
            "- Do not vendor upstream source; adapters only.\n"
        )
        md_path.write_text(md, encoding="utf-8")
        return {"json": json_path, "md": md_path}


def score_against_reference(
    evidence: DocumentPageEvidence,
    reference: DocumentPageEvidence | None,
) -> EnginePageMetrics:
    missing = None
    fig_recall = None
    fig_id_ok = None
    order = None
    if evidence.ok and reference is not None and reference.ok:
        missing = missing_text_ratio(reference.plain_text, evidence.plain_text or evidence.markdown)
        ref_labels = [str(x).lower() for x in reference.figure_labels]
        got_labels = [str(x).lower() for x in evidence.figure_labels]
        if ref_labels:
            hit = sum(1 for lab in ref_labels if lab in got_labels or any(lab in g for g in got_labels))
            fig_recall = hit / len(ref_labels)
            fig_id_ok = fig_recall >= 0.999
        elif not got_labels:
            fig_recall = 1.0
            fig_id_ok = True
        else:
            fig_recall = 0.0
            fig_id_ok = False
        order = reading_order_score(evidence)

    return EnginePageMetrics(
        page=evidence.page_number,
        engine=evidence.engine,
        ok=bool(evidence.ok),
        installed=bool(evidence.installed),
        missing_text_ratio=missing,
        figure_recall=fig_recall,
        figure_id_ok=fig_id_ok,
        table_count=int(evidence.table_count),
        formula_count=int(evidence.formula_count),
        reading_order_score=order,
        time_ms=float(evidence.duration_ms or 0.0),
        error=evidence.error,
        figure_labels=list(evidence.figure_labels),
        warnings=list(evidence.warnings),
    )


def missing_text_ratio(reference: str, candidate: str) -> float:
    ref_toks = set(_WORD_RE.findall((reference or "").lower()))
    if not ref_toks:
        return 0.0
    cand = set(_WORD_RE.findall((candidate or "").lower()))
    covered = len(ref_toks & cand)
    return 1.0 - (covered / len(ref_toks))


def reading_order_score(evidence: DocumentPageEvidence) -> float:
    """1.0 if reading_order is non-decreasing by block index."""
    orders = [b.reading_order for b in evidence.blocks]
    if len(orders) < 2:
        return 1.0
    good = sum(1 for a, b in zip(orders, orders[1:]) if b >= a)
    return good / (len(orders) - 1)


def _page_image(pages_dir: Path | None, page: int) -> Path | None:
    if pages_dir is None:
        return None
    png = pages_dir / f"page_{page:04d}.png"
    if png.exists():
        return png
    jpg = pages_dir / f"page_{page:04d}.jpg"
    return jpg if jpg.exists() else None
