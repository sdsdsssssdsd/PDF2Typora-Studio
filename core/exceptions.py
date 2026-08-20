"""Core domain exceptions."""

from __future__ import annotations


class PDF2TyporaError(Exception):
    """Base application error."""


class ProjectError(PDF2TyporaError):
    """Project lifecycle error."""


class PDFError(PDF2TyporaError):
    """PDF processing error."""


class ConfigError(PDF2TyporaError):
    """Configuration error."""


class OllamaError(PDF2TyporaError):
    """Base Ollama-related error."""


class OllamaRuntimeNotFoundError(OllamaError):
    """Bundled ollama.exe not found."""


class OllamaStartupError(OllamaError):
    """Failed to start bundled Ollama."""


class OllamaConnectionError(OllamaError):
    """Cannot reach Ollama HTTP API."""


class OllamaApiError(OllamaError):
    """Ollama API returned an error response."""


class OllamaVisionNotSupportedError(OllamaError):
    """Selected model does not advertise vision capability."""


class TranscriptionError(PDF2TyporaError):
    """Base transcription error."""

    code: str = "UNKNOWN_ERROR"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class TranscriptionSchemaError(TranscriptionError):
    code = "INVALID_SCHEMA"


class TranscriptionValidationError(TranscriptionError):
    code = "VALIDATION_FAILED"


class TranscriptionOOMError(TranscriptionError):
    code = "OOM"


class TranscriptionTimeoutError(TranscriptionError):
    code = "TIMEOUT"


class TranscriptionCancelledError(TranscriptionError):
    code = "CANCELLED"
