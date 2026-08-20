"""Persisted Vision model qualification profiles (name + digest)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.models import ModelQualification
from config.config_manager import project_root


@dataclass
class ModelProfile:
    model_name: str
    digest: str
    qualification: ModelQualification = ModelQualification.UNTESTED
    preferred_context: int | None = None
    supports_vision: bool = True
    supports_structured_output: bool = True
    successful_pages: int = 0
    failed_pages: int = 0
    last_tested_at: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.model_name}::{self.digest or 'unknown'}"


class ModelProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (project_root() / "config" / "model_profiles.json")
        self._profiles: dict[str, ModelProfile] = {}
        self.load()

    def load(self) -> None:
        self._profiles = {}
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("profiles") or []:
            q = ModelQualification(item.get("qualification", "UNTESTED"))
            p = ModelProfile(
                model_name=item["model_name"],
                digest=item.get("digest") or "",
                qualification=q,
                preferred_context=item.get("preferred_context"),
                supports_vision=bool(item.get("supports_vision", True)),
                supports_structured_output=bool(
                    item.get("supports_structured_output", True)
                ),
                successful_pages=int(item.get("successful_pages", 0)),
                failed_pages=int(item.get("failed_pages", 0)),
                last_tested_at=item.get("last_tested_at"),
                notes=list(item.get("notes") or []),
            )
            self._profiles[p.key] = p

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": [
                {
                    **{k: v for k, v in asdict(p).items() if k != "qualification"},
                    "qualification": p.qualification.value,
                }
                for p in self._profiles.values()
            ]
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, model_name: str, digest: str) -> ModelProfile:
        key = f"{model_name}::{digest or 'unknown'}"
        if key not in self._profiles:
            # digest change → UNTESTED, do not inherit
            self._profiles[key] = ModelProfile(
                model_name=model_name,
                digest=digest or "",
                qualification=_initial_qualification(model_name),
            )
            if "ministral" in model_name.lower():
                self._profiles[key].qualification = ModelQualification.DISABLED
                self._profiles[key].notes.append(
                    "Phase 4: INVALID_SCHEMA / TIMEOUT — disabled for default batch"
                )
            elif "gemma3" in model_name.lower() or "gemma" in model_name.lower():
                self._profiles[key].qualification = ModelQualification.LIMITED
                self._profiles[key].notes.append(
                    "Phase 4: structured output works but prompt leak / image URL issues"
                )
            self.save()
        return self._profiles[key]

    def upsert(self, profile: ModelProfile) -> None:
        self._profiles[profile.key] = profile
        self.save()

    def qualified_models(self) -> list[ModelProfile]:
        return [
            p
            for p in self._profiles.values()
            if p.qualification == ModelQualification.QUALIFIED
        ]

    def list_all(self) -> list[ModelProfile]:
        return list(self._profiles.values())


def _initial_qualification(model_name: str) -> ModelQualification:
    low = model_name.lower()
    if "ministral" in low:
        return ModelQualification.DISABLED
    if "gemma" in low:
        return ModelQualification.LIMITED
    return ModelQualification.UNTESTED
