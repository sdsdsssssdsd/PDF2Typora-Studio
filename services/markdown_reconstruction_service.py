"""DeepSeek / text-API Markdown reconstruction from PageEvidenceManifest."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.config_manager import project_root
from core.evidence_models import PageEvidenceManifest
from services.escape_sanitizer import MarkdownEscapeSanitizer
from services.style_reconstructor import StyleReconstructor
from services.text_coverage_validator import TextCoverageValidator
from utils.logger import get_logger

logger = get_logger("markdown_reconstruction")
_ESCAPE = MarkdownEscapeSanitizer()
_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class ReconstructionResult:
    ok: bool
    markdown: str = ""
    raw: str = ""
    figures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_review: bool = False
    uncovered_block_ids: list[str] = field(default_factory=list)
    coverage_ok: bool | None = None
    error: str | None = None
    provider: str = ""
    model: str = ""


class MarkdownReconstructionService:
    """Text-only reconstruction — never invents characters from pixels."""

    version = "1"

    def __init__(self, text_client: Any | None = None) -> None:
        self.text_client = text_client
        self.coverage = TextCoverageValidator()
        self.style = StyleReconstructor()
        self.prompt_path = project_root() / "prompts" / "reconstruction.txt"

    def load_prompt(self) -> str:
        if self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8")
        return "Rebuild Typora Markdown from evidence JSON only. Output JSON."

    def reconstruct(
        self,
        evidence: PageEvidenceManifest,
        *,
        model: str | None = None,
    ) -> ReconstructionResult:
        payload = evidence.reconstruction_payload()
        system = self.load_prompt()
        user = (
            "Page Evidence JSON:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + f"\n\nPAGE_NUMBER={evidence.page_number}\n"
            "Return the JSON object now."
        )

        if self.text_client is None:
            return self._deterministic_fallback(evidence, reason="no_text_client")

        try:
            raw = self._call_text_api(system=system, user=user, model=model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconstruction API failed: %s", exc)
            fb = self._deterministic_fallback(evidence, reason=f"api_error:{exc}")
            fb.error = str(exc)
            return fb

        parsed = self._parse_response(raw, page_number=evidence.page_number)
        md = _ESCAPE.sanitize(parsed.get("markdown") or "")
        warnings = list(parsed.get("warnings") or [])
        uncovered = list(parsed.get("uncovered_block_ids") or [])
        figures = list(parsed.get("figures") or [])

        # Hard check: required figure labels present as markers
        for label in evidence.figure_labels:
            needle = f"label={label}"
            alt = f"Fig. {label}"
            if needle not in md and f"Fig. {label}" not in md and alt not in md:
                if not any(
                    str(f.get("label")) == str(label) for f in figures if isinstance(f, dict)
                ):
                    warnings.append(f"FIGURE_GROUP_MISSING_IN_MARKDOWN:{label}")
                    uncovered.append(f"fig:{label}")

        source_for_cov = evidence.pdf_plain_text or evidence.ocr_plain_text
        cov = self.coverage.validate(pdf_text=source_for_cov, markdown=md)
        if not cov.ok:
            warnings.extend(cov.issues)
            warnings.append("TEXT_COVERAGE_FAILED")

        needs = bool(parsed.get("needs_review")) or (not cov.ok) or bool(uncovered)
        if uncovered:
            warnings.append("UNCOVERED_SOURCE_BLOCK")

        return ReconstructionResult(
            ok=True,
            markdown=md,
            raw=raw,
            figures=figures,
            warnings=warnings,
            needs_review=needs,
            uncovered_block_ids=uncovered,
            coverage_ok=cov.ok,
            provider=getattr(self.text_client, "provider_id", "") or "text_api",
            model=model or "",
        )

    def _call_text_api(self, *, system: str, user: str, model: str | None) -> str:
        client = self.text_client
        # ApiTextCleanupAdapter / ApiProviderManager styles
        if hasattr(client, "clean_markdown"):
            prev = getattr(client, "model", None)
            if model:
                try:
                    client.model = model
                except Exception:  # noqa: BLE001
                    pass
            try:
                return str(
                    client.clean_markdown(
                        markdown=user,
                        page_number=0,
                        prompt=system,
                        schema=None,
                    )
                )
            finally:
                if model and prev is not None:
                    try:
                        client.model = prev
                    except Exception:  # noqa: BLE001
                        pass
        if hasattr(client, "chat_text"):
            return str(
                client.chat_text(
                    getattr(client, "provider_id", "deepseek"),
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    model=model,
                )
            )
        if hasattr(client, "complete"):
            return str(client.complete(system, user))
        raise RuntimeError("text_client_has_no_chat_method")

    def _parse_response(self, raw: str, *, page_number: int) -> dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        m = _JSON_RE.search(text)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        # Treat whole text as markdown fallback
        return {
            "page_number": page_number,
            "markdown": text,
            "figures": [],
            "warnings": ["reconstruction_json_parse_failed"],
            "needs_review": True,
            "uncovered_block_ids": [],
        }

    def _deterministic_fallback(
        self, evidence: PageEvidenceManifest, *, reason: str
    ) -> ReconstructionResult:
        """PDF-span styled dump when API unavailable — still better than empty."""
        parts: list[str] = []
        figures: list[dict[str, Any]] = []
        for b in evidence.blocks:
            if b.type == "figure_group":
                label = str(b.extra.get("label") or "")
                marker = f"<!-- FIGURE page={evidence.page_number} index={len(figures)+1} -->"
                parts.append(marker)
                parts.append("")
                if b.text:
                    parts.append(b.text)
                figures.append(
                    {
                        "label": label,
                        "marker": marker,
                        "figure_index": len(figures) + 1,
                    }
                )
                continue
            if b.source == "ocr" and evidence.mode.value.startswith("PDF_NATIVE"):
                # OCR lines only as secondary when native present — skip duplicate body
                continue
            text = b.text
            if b.bold and text.strip():
                text = f"**{text.strip()}**"
            if b.color and b.color.lower() not in {"#000000", "#000"}:
                text = f'<span style="color:{b.color}">{text}</span>'
            if b.type == "heading":
                parts.append(f"## {text}")
            else:
                parts.append(text)
            parts.append("")
        md = _ESCAPE.sanitize("\n".join(parts).strip() + "\n")
        return ReconstructionResult(
            ok=True,
            markdown=md,
            warnings=[f"deterministic_fallback:{reason}"],
            needs_review=True,
            figures=figures,
            provider="deterministic",
        )
