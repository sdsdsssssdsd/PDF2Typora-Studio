"""Project domain model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.models import ProjectInfo
from utils.paths import ensure_dir


class Project:
    """Represents a single book conversion workspace."""

    PROJECT_JSON = "project.json"

    def __init__(self, info: ProjectInfo) -> None:
        self.info = info

    @property
    def root(self) -> Path:
        return self.info.root_path

    @property
    def pages_dir(self) -> Path:
        return self.root / "pages"

    @property
    def figures_dir(self) -> Path:
        return self.root / "figures"

    @property
    def resolved_pages_dir(self) -> Path:
        return self.root / "resolved_pages"

    @property
    def markdown_pages_dir(self) -> Path:
        return self.root / "markdown_pages"

    @property
    def page_results_dir(self) -> Path:
        return self.root / "page_results"

    @property
    def intermediate_dir(self) -> Path:
        return self.root / "intermediate"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def db_path(self) -> Path:
        return self.info.db_path

    def ensure_directories(self) -> None:
        for d in (
            self.pages_dir,
            self.figures_dir,
            self.markdown_pages_dir,
            self.resolved_pages_dir,
            self.page_results_dir,
            self.intermediate_dir,
            self.logs_dir,
        ):
            ensure_dir(d)

    def save_manifest(self) -> None:
        manifest: dict[str, Any] = {
            "name": self.info.name,
            "source_pdf": str(self.info.source_pdf.name),
            "page_count": self.info.page_count,
            "created_at": self.info.created_at,
            "metadata": self.info.metadata,
        }
        path = self.root / self.PROJECT_JSON
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_from_directory(cls, root: Path) -> Project:
        manifest_path = root / cls.PROJECT_JSON
        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing {cls.PROJECT_JSON} in {root}")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        info = ProjectInfo(
            name=data["name"],
            root_path=root,
            source_pdf=root / data.get("source_pdf", "source.pdf"),
            db_path=root / "project.db",
            page_count=data["page_count"],
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )
        return cls(info)

    def pdf_hash(self) -> str:
        return str(self.info.metadata.get("pdf_hash") or "")

    def set_pdf_hash(self, digest: str) -> None:
        self.info.metadata["pdf_hash"] = digest
        self.save_manifest()
