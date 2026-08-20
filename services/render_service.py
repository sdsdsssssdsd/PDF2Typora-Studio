"""PDF page rendering service (PyMuPDF → PNG)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from core.exceptions import PDFError
from core.models import (
    PageRenderResult,
    PageStatus,
    PipelineStage,
    RenderRequest,
    RenderSettings,
    StageStatus,
    page_number_to_index,
)
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import render_settings_hash
from utils.logger import get_logger
from utils.paths import ensure_dir, page_image_name

logger = get_logger("render_service")

MIN_DPI = 72
MAX_DPI = 600


def validate_dpi(dpi: int) -> int:
    if dpi < MIN_DPI or dpi > MAX_DPI:
        raise ValueError(f"DPI must be between {MIN_DPI} and {MAX_DPI}, got {dpi}")
    return dpi


def estimate_page_pixels(
    page_width_pt: float,
    page_height_pt: float,
    dpi: int,
) -> tuple[int, int]:
    scale = dpi / 72.0
    return int(page_width_pt * scale), int(page_height_pt * scale)


def is_valid_png(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 8:
        return False
    try:
        header = path.read_bytes()[:8]
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    except OSError:
        return False


def cleanup_render_temp_files(output_dir: Path) -> int:
    """Remove incomplete render artifacts only (``*.part.png``, ``*.tmp.png``)."""
    if not output_dir.is_dir():
        return 0
    removed = 0
    for path in output_dir.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith(".part.png") or name.endswith(".tmp.png"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("Could not remove temp file %s", path)
    return removed


class RenderService:
    """Render PDF pages to PNG with cache and stage-state persistence."""

    def render_page(
        self,
        request: RenderRequest,
        page_number: int,
        *,
        repo: ProjectRepository | None = None,
    ) -> PageRenderResult:
        settings = request.settings
        validate_dpi(settings.dpi)
        ensure_dir(request.output_dir)

        settings_hash = render_settings_hash(
            request.pdf_hash, page_number, settings
        )
        out_name = page_image_name(page_number)
        if settings.image_format.lower() != "png":
            raise PDFError("Phase 3 only supports PNG output")
        out_path = request.output_dir / out_name

        if not request.force and self._cache_hit(
            repo, page_number, settings_hash, out_path
        ):
            logger.info("Cache hit page %s (%s)", page_number, out_name)
            if repo:
                now = datetime.now(timezone.utc).isoformat()
                repo.upsert_stage_state(
                    page_number,
                    PipelineStage.RENDER,
                    StageStatus.CACHED,
                    artifact_path=str(out_path),
                    settings_hash=settings_hash,
                    error_message=None,
                    finished_at=now,
                )
                repo.update_page_status(
                    page_number,
                    PageStatus.RENDERED,
                    image_path=str(out_path),
                    image_hash=settings_hash,
                )
            w, h = self._png_size(out_path)
            return PageRenderResult(
                page_number=page_number,
                image_path=out_path,
                width_px=w,
                height_px=h,
                settings_hash=settings_hash,
                cached=True,
                success=True,
            )

        if repo:
            repo.upsert_stage_state(
                page_number,
                PipelineStage.RENDER,
                StageStatus.RUNNING,
                settings_hash=settings_hash,
                error_message=None,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            repo.update_page_status(page_number, PageStatus.RENDERING)

        try:
            width_px, height_px = self._render_to_file(
                request.pdf_path, page_number, settings, out_path
            )
        except Exception as exc:
            logger.exception("Render failed page %s", page_number)
            if repo:
                now = datetime.now(timezone.utc).isoformat()
                repo.upsert_stage_state(
                    page_number,
                    PipelineStage.RENDER,
                    StageStatus.FAILED,
                    settings_hash=settings_hash,
                    error_message=str(exc),
                    finished_at=now,
                )
                repo.update_page_status(
                    page_number, PageStatus.FAILED, error_message=str(exc)
                )
            return PageRenderResult(
                page_number=page_number,
                image_path=None,
                width_px=None,
                height_px=None,
                settings_hash=settings_hash,
                cached=False,
                success=False,
                error=str(exc),
            )

        if repo:
            now = datetime.now(timezone.utc).isoformat()
            repo.upsert_stage_state(
                page_number,
                PipelineStage.RENDER,
                StageStatus.SUCCESS,
                artifact_path=str(out_path),
                settings_hash=settings_hash,
                error_message=None,
                finished_at=now,
            )
            repo.update_page_status(
                page_number,
                PageStatus.RENDERED,
                image_path=str(out_path),
                image_hash=settings_hash,
            )

        return PageRenderResult(
            page_number=page_number,
            image_path=out_path,
            width_px=width_px,
            height_px=height_px,
            settings_hash=settings_hash,
            cached=False,
            success=True,
        )

    def render_pages(
        self,
        request: RenderRequest,
        *,
        cancel_check=None,
        on_page_start=None,
        on_page_done=None,
    ) -> list[PageRenderResult]:
        """Render pages sequentially. Single-page failure does not abort the job."""
        repo: ProjectRepository | None = None
        db: Database | None = None
        if request.db_path is not None:
            db = Database(request.db_path)
            db.initialize()
            repo = ProjectRepository(db)

        results: list[PageRenderResult] = []
        try:
            # Fail fast if PDF cannot be opened
            self._open_pdf(request.pdf_path).close()

            for page_number in request.pages:
                if cancel_check and cancel_check():
                    logger.info("Render cancelled before page %s", page_number)
                    if repo:
                        repo.upsert_stage_state(
                            page_number,
                            PipelineStage.RENDER,
                            StageStatus.CANCELLED,
                            finished_at=datetime.now(timezone.utc).isoformat(),
                        )
                    results.append(
                        PageRenderResult(
                            page_number=page_number,
                            image_path=None,
                            width_px=None,
                            height_px=None,
                            settings_hash=render_settings_hash(
                                request.pdf_hash, page_number, request.settings
                            ),
                            cached=False,
                            success=False,
                            cancelled=True,
                            error="cancelled",
                        )
                    )
                    break

                if on_page_start:
                    on_page_start(page_number)

                result = self.render_page(request, page_number, repo=repo)
                results.append(result)

                if on_page_done:
                    on_page_done(result)

                if cancel_check and cancel_check():
                    logger.info("Render cancelled after page %s", page_number)
                    break
        finally:
            if db is not None:
                db.close()

        return results

    def _cache_hit(
        self,
        repo: ProjectRepository | None,
        page_number: int,
        settings_hash: str,
        out_path: Path,
    ) -> bool:
        if not is_valid_png(out_path):
            return False
        if repo is None:
            return False
        state = repo.get_stage_state(page_number, PipelineStage.RENDER)
        if state is None:
            return False
        if state.get("status") not in (
            StageStatus.SUCCESS.value,
            StageStatus.CACHED.value,
        ):
            return False
        if state.get("settings_hash") != settings_hash:
            return False
        return True

    def _render_to_file(
        self,
        pdf_path: Path,
        page_number: int,
        settings: RenderSettings,
        out_path: Path,
    ) -> tuple[int, int]:
        doc = self._open_pdf(pdf_path)
        try:
            index = page_number_to_index(page_number)
            if index >= doc.page_count:
                raise PDFError(
                    f"Page {page_number} out of range (PDF has {doc.page_count} pages)"
                )
            page = doc[index]
            zoom = settings.dpi / 72.0
            matrix = pymupdf.Matrix(zoom, zoom)
            # Use default page display area; rotation handled by PyMuPDF
            pixmap = page.get_pixmap(
                matrix=matrix,
                alpha=settings.alpha,
                colorspace=pymupdf.csRGB,
            )
            try:
                width_px, height_px = pixmap.width, pixmap.height
                # Must keep a real image extension — PyMuPDF keys format off suffix.
                part_path = out_path.with_name(out_path.stem + ".part.png")
                if part_path.exists():
                    part_path.unlink()
                pixmap.save(str(part_path), output="png")
            finally:
                pixmap = None  # release ASAP

            if not is_valid_png(part_path):
                part_path.unlink(missing_ok=True)
                raise PDFError(f"Invalid PNG written for page {page_number}")

            # Atomic replace: keep old PNG if replace fails
            tmp_final = out_path.with_name(out_path.stem + ".tmp.png")
            if tmp_final.exists():
                tmp_final.unlink()
            part_path.replace(tmp_final)
            tmp_final.replace(out_path)
            return width_px, height_px
        finally:
            doc.close()

    @staticmethod
    def _open_pdf(pdf_path: Path) -> pymupdf.Document:
        if not pdf_path.exists():
            raise PDFError(f"PDF not found: {pdf_path}")
        try:
            doc = pymupdf.open(str(pdf_path))
        except Exception as exc:
            raise PDFError(f"Cannot open PDF: {exc}") from exc
        if doc.is_encrypted:
            doc.close()
            raise PDFError("加密 PDF 暂不支持（Phase 3）")
        return doc

    @staticmethod
    def _png_size(path: Path) -> tuple[int | None, int | None]:
        try:
            import struct

            data = path.read_bytes()
            if len(data) < 24:
                return None, None
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        except OSError:
            return None, None
