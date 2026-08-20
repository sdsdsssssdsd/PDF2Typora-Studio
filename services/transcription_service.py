"""Single-page structured Vision transcription service."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai.schemas.transcription import (
    TRANSCRIPTION_PIPELINE_VERSION,
    TRANSCRIPTION_SCHEMA_VERSION,
    PageTranscriptionResult,
)
from config.config_manager import project_root
from core.exceptions import (
    TranscriptionCancelledError,
    TranscriptionError,
    TranscriptionOOMError,
    TranscriptionSchemaError,
    TranscriptionTimeoutError,
)
from core.models import PipelineStage, StageStatus, TranscriptionOptions
from services.escape_sanitizer import MarkdownEscapeSanitizer
from services.transcription_validator import TranscriptionValidator
from services.training_dataset_collector import TrainingDatasetCollector
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256, transcription_request_hash
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("transcription_service")
_ESCAPE = MarkdownEscapeSanitizer()


@dataclass
class TranscriptionAttempt:
    attempt_dir: Path
    request_hash: str
    model: str
    model_digest: str
    status: str
    result: PageTranscriptionResult | None = None
    validation_warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    cached: bool = False
    raw_content: str = ""
    needs_review: bool = False
    can_auto_accept: bool = False
    blocking_codes: list[str] = field(default_factory=list)
    fallback_used: bool = False


def classify_ollama_error(exc: BaseException) -> tuple[str, str]:
    msg = str(exc)
    low = msg.lower()
    if "cancel" in low:
        return "CANCELLED", msg
    if "timeout" in low or "timed out" in low:
        return "TIMEOUT", msg
    if any(
        x in low
        for x in (
            "out of memory",
            "cuda",
            "oom",
            "insufficient memory",
            "allocation",
        )
    ):
        return "OOM", msg
    if "connection" in low or "refused" in low or "offline" in low:
        return "OLLAMA_OFFLINE", msg
    if "not found" in low and "model" in low:
        return "MODEL_NOT_FOUND", msg
    if "exceed" in low and "context" in low:
        return "CONTEXT_OVERFLOW", msg
    return "UNKNOWN_ERROR", msg


class TranscriptionService:
    def __init__(
        self,
        provider: Any,
        project_root_path: Path,
        db_path: Path,
    ) -> None:
        self.provider = provider
        self.project_root = project_root_path
        self.db_path = db_path
        self.validator = TranscriptionValidator()
        self.prompt_path = project_root() / "prompts" / "transcription.txt"

    def load_prompt_template(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def build_prompt(self, page_number: int, schema: dict[str, Any]) -> str:
        return self.build_system_prompt(page_number, schema)

    def build_system_prompt(self, page_number: int, schema: dict[str, Any]) -> str:
        _ = schema
        template = self.load_prompt_template()
        return template.replace("{PAGE_NUMBER}", str(page_number))

    def build_user_prompt(
        self, page_number: int, extra: str | None = None
    ) -> str:
        text = (
            f"Transcribe PDF physical page {page_number}.\n"
            "The attached image is the only source document.\n"
            "Return only the structured result requested by the response schema."
        )
        if extra:
            text = f"{text}\n\n{extra}"
        return text

    def get_model_digest(self, model: str) -> str:
        tags = self.provider._client.list_tags()
        for tag in tags:
            if tag.get("name") == model:
                return str(tag.get("digest") or "")
        return ""

    def find_cached_attempt(self, page_number: int, request_hash: str) -> Path | None:
        base = (
            self.project_root
            / "experiments"
            / "transcription"
            / f"page_{page_number:04d}"
        )
        if not base.is_dir():
            return None
        for attempt in sorted(base.iterdir(), reverse=True):
            req = attempt / "request.json"
            if not req.exists():
                continue
            try:
                data = json.loads(req.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("request_hash") == request_hash and (
                attempt / "response.json"
            ).exists():
                return attempt
        return None

    def transcribe_page(
        self,
        *,
        page_number: int,
        image_path: Path,
        model: str,
        options: TranscriptionOptions | None = None,
        extra_user: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> TranscriptionAttempt:
        options = options or TranscriptionOptions()
        if not image_path.exists():
            raise TranscriptionError(f"Image not found: {image_path}", "UNKNOWN_ERROR")

        image_hash = file_sha256(image_path)
        schema = PageTranscriptionResult.model_json_schema()
        system_prompt = self.build_system_prompt(page_number, schema)
        user_prompt = self.build_user_prompt(page_number, extra_user)
        prompt = system_prompt + "\n---\n" + user_prompt
        prompt_hash = text_sha256(system_prompt)  # user extra must not change cache of clean run
        if extra_user:
            prompt_hash = text_sha256(system_prompt + extra_user)
        model_digest = self.get_model_digest(model)
        request_hash = transcription_request_hash(
            image_hash=image_hash,
            page_number=page_number,
            model_name=model,
            model_digest=model_digest,
            prompt_hash=prompt_hash,
            schema_version=TRANSCRIPTION_SCHEMA_VERSION,
            options=options,
            pipeline_version=TRANSCRIPTION_PIPELINE_VERSION,
        )

        if options.use_cache and not options.force:
            cached = self.find_cached_attempt(page_number, request_hash)
            if cached is not None:
                return self._load_attempt(cached, cached=True)

        attempt_dir = self._new_attempt_dir(page_number, model, request_hash)
        ensure_dir(attempt_dir)
        (attempt_dir / "prompt_snapshot.txt").write_text(prompt, encoding="utf-8")

        request_meta = {
            "page": page_number,
            "image_path": str(image_path),
            "image_hash": image_hash,
            "provider": "ollama",
            "model": model,
            "model_digest": model_digest,
            "prompt_hash": prompt_hash,
            "request_hash": request_hash,
            "schema_version": TRANSCRIPTION_SCHEMA_VERSION,
            "pipeline_version": TRANSCRIPTION_PIPELINE_VERSION,
            "temperature": options.temperature,
            "context_length": options.num_ctx,
            "think": options.think,
            "keep_alive": options.keep_alive,
        }
        (attempt_dir / "request.json").write_text(
            json.dumps(request_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        request_id = repo.insert_ai_request(
            page_number=page_number,
            provider="ollama",
            model=model,
            model_digest=model_digest,
            request_hash=request_hash,
            prompt_hash=prompt_hash,
            image_hash=image_hash,
            schema_version=TRANSCRIPTION_SCHEMA_VERSION,
            temperature=options.temperature,
            context_length=options.num_ctx,
            think=options.think,
            status="RUNNING",
            artifact_path=str(attempt_dir),
        )

        last_error: Exception | None = None
        max_attempts = 1 + max(0, options.schema_retry_attempts)

        try:
            for attempt_i in range(1, max_attempts + 1):
                if cancel_check and cancel_check():
                    raise TranscriptionCancelledError("cancelled by user")

                try:
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                    raw, metrics = self.provider.transcribe_page_structured(
                        image_path=image_path,
                        page_number=page_number,
                        prompt=user_prompt,
                        schema=schema,
                        model=model,
                        options=options,
                        messages=messages,
                    )
                except Exception as exc:  # noqa: BLE001
                    code, msg = classify_ollama_error(exc)
                    (attempt_dir / f"error_attempt_{attempt_i}.txt").write_text(
                        msg, encoding="utf-8"
                    )
                    # Context overflow: retry with larger / auto ctx
                    if "exceed" in msg.lower() and "context" in msg.lower():
                        if options.num_ctx is None or options.num_ctx == 4096:
                            options = TranscriptionOptions(
                                temperature=options.temperature,
                                num_ctx=8192,
                                think=options.think,
                                keep_alive=options.keep_alive,
                                schema_retry_attempts=options.schema_retry_attempts,
                                use_cache=False,
                                force=True,
                            )
                            last_error = TranscriptionError(msg, "CONTEXT_OVERFLOW")
                            continue
                        if options.num_ctx == 8192:
                            last_error = TranscriptionError(msg, "CONTEXT_EXCEEDED")
                            break
                    last_error = (
                        TranscriptionOOMError(msg)
                        if code == "OOM"
                        else TranscriptionTimeoutError(msg)
                        if code == "TIMEOUT"
                        else TranscriptionError(msg, code)
                    )
                    if code in {"OOM", "TIMEOUT", "OLLAMA_OFFLINE", "MODEL_NOT_FOUND"}:
                        break
                    continue

                (attempt_dir / "raw_response.txt").write_text(raw, encoding="utf-8")
                (attempt_dir / "metrics.json").write_text(
                    json.dumps(metrics, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                try:
                    result = PageTranscriptionResult.model_validate_json(raw)
                except Exception as exc:  # noqa: BLE001
                    last_error = TranscriptionSchemaError(str(exc))
                    (attempt_dir / f"invalid_schema_{attempt_i}.txt").write_text(
                        raw[:5000], encoding="utf-8"
                    )
                    repo.update_ai_request(
                        request_id,
                        status="INVALID_SCHEMA",
                        error_message=str(exc),
                        **_metrics_for_db(metrics),
                    )
                    continue

                # Force page_number to requested if model drifted but content ok
                if result.page_number != page_number:
                    result = result.model_copy(update={"page_number": page_number})

                # Phase 9.5: fix erroneous literal \n without harming LaTeX
                sanitized = _ESCAPE.sanitize(result.markdown or "")
                if sanitized != result.markdown:
                    result = result.model_copy(update={"markdown": sanitized})

                report = self.validator.validate(result, requested_page=page_number)
                result = report.merge_into(result)
                (attempt_dir / "response.json").write_text(
                    result.model_dump_json(indent=2),
                    encoding="utf-8",
                )
                (attempt_dir / "markdown.md").write_text(result.markdown, encoding="utf-8")
                (attempt_dir / "validation.json").write_text(
                    json.dumps(
                        {
                            "ok": report.ok,
                            "warnings": report.warnings,
                            "errors": report.errors,
                            "needs_review": report.needs_review,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                if not report.ok:
                    last_error = TranscriptionSchemaError(
                        "; ".join(report.errors) or "validation failed"
                    )
                    repo.update_ai_request(
                        request_id,
                        status="VALIDATION_FAILED",
                        error_message="; ".join(report.errors),
                        **_metrics_for_db(metrics),
                    )
                    continue

                status = "SUCCESS"
                repo.update_ai_request(
                    request_id,
                    status=status,
                    artifact_path=str(attempt_dir),
                    **_metrics_for_db(metrics),
                )
                db.close()
                auto = report.can_auto_accept(result)
                return TranscriptionAttempt(
                    attempt_dir=attempt_dir,
                    request_hash=request_hash,
                    model=model,
                    model_digest=model_digest,
                    status=status,
                    result=result,
                    validation_warnings=report.warnings,
                    metrics=metrics,
                    raw_content=raw,
                    needs_review=report.needs_review or result.needs_review,
                    can_auto_accept=auto,
                    blocking_codes=[i.code for i in report.blocking],
                )

            # exhausted retries
            code = getattr(last_error, "code", "FAILED") if last_error else "FAILED"
            msg = str(last_error) if last_error else "transcription failed"
            repo.update_ai_request(
                request_id, status=str(code), error_message=msg
            )
            db.close()
            return TranscriptionAttempt(
                attempt_dir=attempt_dir,
                request_hash=request_hash,
                model=model,
                model_digest=model_digest,
                status=str(code),
                error=msg,
                error_code=str(code),
            )
        except TranscriptionCancelledError as exc:
            repo.update_ai_request(request_id, status="CANCELLED", error_message=str(exc))
            db.close()
            return TranscriptionAttempt(
                attempt_dir=attempt_dir,
                request_hash=request_hash,
                model=model,
                model_digest=model_digest,
                status="CANCELLED",
                error=str(exc),
                error_code="CANCELLED",
            )
        except Exception:
            db.close()
            raise

    def accept_result(
        self,
        *,
        page_number: int,
        attempt: TranscriptionAttempt,
        markdown_override: str | None = None,
        manually_edited: bool = False,
        acceptance_mode: str = "manual",
        batch_run_id: int | None = None,
        validator_version: str | None = None,
    ) -> Path:
        if attempt.result is None:
            raise TranscriptionError("No result to accept", "VALIDATION_FAILED")

        result = attempt.result
        if markdown_override is not None:
            result = result.model_copy(update={"markdown": markdown_override})
            report = self.validator.validate(result, requested_page=page_number)
            result = report.merge_into(result)

        sanitized = _ESCAPE.sanitize(result.markdown or "")
        if sanitized != result.markdown:
            result = result.model_copy(update={"markdown": sanitized})

        md_body = result.markdown.lstrip()
        canonical_md = f"<!-- PAGE: {page_number:04d} -->\n\n{md_body}"

        md_dir = ensure_dir(self.project_root / "markdown_pages")
        json_dir = ensure_dir(self.project_root / "page_results")
        md_path = md_dir / f"page_{page_number:04d}.md"
        json_path = json_dir / f"page_{page_number:04d}.json"

        # Protect previous canonical
        if json_path.exists() or md_path.exists():
            hist = ensure_dir(
                self.project_root
                / "history"
                / "transcription"
                / f"page_{page_number:04d}"
            )
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if md_path.exists():
                (hist / f"{stamp}.md").write_text(
                    md_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            if json_path.exists():
                (hist / f"{stamp}.json").write_text(
                    json_path.read_text(encoding="utf-8"), encoding="utf-8"
                )

        from ai.schemas.transcription import VALIDATOR_VERSION

        provenance = {
            "provider": "ollama",
            "model": attempt.model,
            "model_digest": attempt.model_digest,
            "image_hash": None,
            "prompt_hash": None,
            "request_hash": attempt.request_hash,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "manually_edited": manually_edited,
            "attempt_dir": str(attempt.attempt_dir),
        }
        req_path = attempt.attempt_dir / "request.json"
        if req_path.exists():
            req = json.loads(req_path.read_text(encoding="utf-8"))
            provenance["image_hash"] = req.get("image_hash")
            provenance["prompt_hash"] = req.get("prompt_hash")

        payload = {
            "result": result.model_dump(),
            "provenance": provenance,
            "acceptance": {
                "mode": acceptance_mode,
                "validator_version": validator_version or VALIDATOR_VERSION,
                "accepted_at": provenance["accepted_at"],
                "batch_run_id": batch_run_id,
            },
        }

        self._atomic_write_text(md_path, canonical_md)
        self._atomic_write_text(
            json_path, json.dumps(payload, ensure_ascii=False, indent=2)
        )

        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            now = datetime.now(timezone.utc).isoformat()
            status = (
                StageStatus.SUCCESS
                if not result.needs_review
                else StageStatus.SUCCESS
            )
            # needs_review is recorded in JSON; stage still SUCCESS when accepted
            repo.upsert_stage_state(
                page_number,
                PipelineStage.TRANSCRIBE,
                status,
                artifact_path=str(json_path),
                settings_hash=attempt.request_hash,
                error_message=None,
                finished_at=now,
            )
            from core.models import PageStatus

            repo.update_page_status(
                page_number,
                PageStatus.SUCCESS if not result.needs_review else PageStatus.NEEDS_REVIEW,
            )
            # also store markdown/json paths if columns exist via raw SQL
            conn = db.connect()
            conn.execute(
                """
                UPDATE pages SET markdown_path = ?, json_path = ?, model_name = ?,
                    provider = ?, prompt_hash = ?, image_hash = ?, updated_at = ?
                WHERE page_number = ?
                """,
                (
                    str(md_path),
                    str(json_path),
                    attempt.model,
                    "ollama",
                    provenance.get("prompt_hash"),
                    provenance.get("image_hash"),
                    now,
                    page_number,
                ),
            )
            conn.commit()
        finally:
            db.close()

        try:
            TrainingDatasetCollector(self.project_root).record_page(
                page_number=page_number,
                page_png=self.project_root / "pages" / f"page_{page_number:04d}.png",
                model_output=result.model_dump(),
                corrected_md=canonical_md,
                corrections={
                    "acceptance_mode": acceptance_mode,
                    "manually_edited": manually_edited,
                    "escape_sanitized": True,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("training dataset collect failed")

        return md_path

    def _new_attempt_dir(
        self, page_number: int, model: str, request_hash: str
    ) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_model = re.sub(r"[^\w.-]+", "_", model)[:40]
        name = f"{stamp}_{safe_model}_{request_hash[:8]}"
        return (
            self.project_root
            / "experiments"
            / "transcription"
            / f"page_{page_number:04d}"
            / name
        )

    def _load_attempt(self, attempt_dir: Path, *, cached: bool) -> TranscriptionAttempt:
        req = json.loads((attempt_dir / "request.json").read_text(encoding="utf-8"))
        result = PageTranscriptionResult.model_validate_json(
            (attempt_dir / "response.json").read_text(encoding="utf-8")
        )
        metrics = {}
        mp = attempt_dir / "metrics.json"
        if mp.exists():
            metrics = json.loads(mp.read_text(encoding="utf-8"))
        warnings = []
        needs_review = result.needs_review
        vp = attempt_dir / "validation.json"
        if vp.exists():
            vdata = json.loads(vp.read_text(encoding="utf-8"))
            warnings = vdata.get("warnings") or []
            needs_review = bool(vdata.get("needs_review") or needs_review)
        raw = ""
        rp = attempt_dir / "raw_response.txt"
        if rp.exists():
            raw = rp.read_text(encoding="utf-8")
        report = self.validator.validate(result, requested_page=result.page_number)
        return TranscriptionAttempt(
            attempt_dir=attempt_dir,
            request_hash=str(req.get("request_hash") or ""),
            model=str(req.get("model") or ""),
            model_digest=str(req.get("model_digest") or ""),
            status="SUCCESS",
            result=result,
            validation_warnings=warnings,
            metrics=metrics,
            cached=cached,
            raw_content=raw,
            needs_review=needs_review or report.needs_review,
            can_auto_accept=report.can_auto_accept(result),
            blocking_codes=[i.code for i in report.blocking],
        )

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        ensure_dir(path.parent)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def _metrics_for_db(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_duration_ns": metrics.get("total_duration_ns"),
        "load_duration_ns": metrics.get("load_duration_ns"),
        "prompt_eval_count": metrics.get("prompt_eval_count"),
        "prompt_eval_duration_ns": metrics.get("prompt_eval_duration_ns"),
        "eval_count": metrics.get("eval_count"),
        "eval_duration_ns": metrics.get("eval_duration_ns"),
    }
