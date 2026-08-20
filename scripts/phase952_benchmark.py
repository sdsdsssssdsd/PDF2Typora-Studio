"""CLI: Phase 9.5.2 multi-engine benchmark on one PDF.

Usage:
  python scripts/phase952_benchmark.py --pdf path/to.pdf --pages 1-8
  python scripts/phase952_benchmark.py --project workspace/MyBook
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.document_parsers.registry import ENGINE_ORDER, list_engines
from core.project import Project
from services.document_engine_benchmark import DocumentEngineBenchmark
from utils.page_range import parse_page_range


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 9.5.2 document engine benchmark")
    ap.add_argument("--pdf", type=Path, help="PDF path")
    ap.add_argument("--project", type=Path, help="Project workspace root")
    ap.add_argument("--pages", default="1", help="Page range, e.g. 1-8 or 1,4,8")
    ap.add_argument(
        "--engines",
        default=",".join(ENGINE_ORDER),
        help="Comma-separated engine ids",
    )
    ap.add_argument("--out", type=Path, default=None, help="Output directory")
    args = ap.parse_args()

    print("Engine availability:")
    for e in list_engines():
        flag = "YES" if e["available"] else "no"
        print(f"  [{flag}] {e['id']:12} {e['name']}")

    pdf: Path
    pages_dir: Path | None = None
    page_count = 1
    if args.project:
        project = Project.load_from_directory(args.project)
        pdf = project.info.source_pdf
        pages_dir = project.pages_dir
        page_count = project.info.page_count
        out = args.out or (project.root / "reports")
    elif args.pdf:
        pdf = args.pdf
        page_count = 9999
        out = args.out or (ROOT / "reports" / "phase952")
    else:
        ap.error("Provide --pdf or --project")
        return 2

    try:
        pages = parse_page_range(args.pages, page_count)
    except Exception as exc:  # noqa: BLE001
        # fallback simple parse
        pages = []
        for part in str(args.pages).split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                pages.extend(range(int(a), int(b) + 1))
            elif part:
                pages.append(int(part))
        if not pages:
            raise SystemExit(f"bad pages: {exc}") from exc

    engines = [x.strip() for x in str(args.engines).split(",") if x.strip()]
    bench = DocumentEngineBenchmark()
    report = bench.run(
        pdf_path=pdf,
        pages=pages,
        engines=engines,
        pages_dir=pages_dir,
        on_progress=lambda m: print(f"  … {m}"),
    )
    paths = bench.write_report(report, out)
    print("\n" + report.to_markdown_table())
    print(f"Wrote {paths['md']}")
    print(f"Wrote {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
