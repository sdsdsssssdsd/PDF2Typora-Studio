"""Optional PP-OCR adapter — graceful when PaddleOCR is not installed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger("ppocr_adapter")


@dataclass
class OCRLine:
    text: str
    bbox: list[float] | None = None  # [x0,y0,x1,y1] in image pixels
    confidence: float | None = None


@dataclass
class OCRPageResult:
    ok: bool
    engine: str
    lines: list[OCRLine] = field(default_factory=list)
    plain_text: str = ""
    installed: bool = False
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PPOCRAdapter:
    """PP-OCRv5-style text recognition via paddleocr if installed."""

    engine = "ppocr"

    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def recognize_image(self, image_path: Path) -> OCRPageResult:
        if not self.available():
            return OCRPageResult(
                ok=False,
                engine=self.engine,
                installed=False,
                error="paddleocr_not_installed",
            )
        try:
            from paddleocr import PaddleOCR

            # Use lightweight defaults; Chinese+English
            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            result = ocr.ocr(str(image_path), cls=True)
            lines: list[OCRLine] = []
            parts: list[str] = []
            # result is list per image
            pages = result or []
            for page in pages:
                if not page:
                    continue
                for item in page:
                    box = item[0] if item else None
                    txt_info = item[1] if item and len(item) > 1 else ("", 0.0)
                    text = str(txt_info[0] or "")
                    conf = float(txt_info[1]) if len(txt_info) > 1 else None
                    bbox = None
                    if box and len(box) >= 4:
                        xs = [float(p[0]) for p in box]
                        ys = [float(p[1]) for p in box]
                        bbox = [min(xs), min(ys), max(xs), max(ys)]
                    if text.strip():
                        lines.append(OCRLine(text=text, bbox=bbox, confidence=conf))
                        parts.append(text)
            plain = "\n".join(parts)
            return OCRPageResult(
                ok=True,
                engine=self.engine,
                lines=lines,
                plain_text=plain,
                installed=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("PaddleOCR failed: %s", exc)
            return OCRPageResult(
                ok=False,
                engine=self.engine,
                installed=True,
                error=str(exc),
            )
