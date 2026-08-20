"""OS keyring-backed API credential store — never write keys to yaml/logs."""

from __future__ import annotations

from typing import Any

SERVICE_NAME = "PDF2TyporaStudio"


class ApiCredentialStore:
    """Store secrets in Windows Credential Manager / OS keyring when available."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self.service = service
        self._memory: dict[str, str] = {}

    def _backend(self) -> Any | None:
        try:
            import keyring  # type: ignore

            return keyring
        except Exception:  # noqa: BLE001
            return None

    def credential_id(self, provider: str, account: str = "default") -> str:
        return f"{provider}:{account}"

    def set_secret(self, credential_id: str, secret: str) -> None:
        if not secret:
            self.delete_secret(credential_id)
            return
        backend = self._backend()
        if backend is not None:
            backend.set_password(self.service, credential_id, secret)
            self._memory.pop(credential_id, None)
            return
        # Fallback: process-memory only (not persisted) — GUI must warn
        self._memory[credential_id] = secret

    def get_secret(self, credential_id: str) -> str | None:
        backend = self._backend()
        if backend is not None:
            try:
                return backend.get_password(self.service, credential_id)
            except Exception:  # noqa: BLE001
                return self._memory.get(credential_id)
        return self._memory.get(credential_id)

    def delete_secret(self, credential_id: str) -> None:
        backend = self._backend()
        if backend is not None:
            try:
                backend.delete_password(self.service, credential_id)
            except Exception:  # noqa: BLE001
                pass
        self._memory.pop(credential_id, None)

    def is_persistent(self) -> bool:
        return self._backend() is not None

    @staticmethod
    def redact(value: str | None) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "********"
        return value[:4] + "…" + value[-2:]
