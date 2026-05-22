from __future__ import annotations

import hashlib
from pathlib import Path

from geoskillbench.models.skill import AgentSkill
from geoskillbench.recorder.execution_recorder import ExecutionRecorder


ALLOWED_REFERENCE_EXTENSIONS = {".md", ".txt", ".json", ".yml", ".yaml"}


class SkillReferenceTool:
    def __init__(self, skill_package: AgentSkill, recorder: ExecutionRecorder):
        if skill_package.type != "prompt_skill_package" or not skill_package.base_dir:
            raise ValueError("SkillReferenceTool requires a prompt_skill_package with a base directory.")
        self.skill_package = skill_package
        self.recorder = recorder
        self.base_dir = Path(skill_package.base_dir).resolve()

    def load_skill_reference(self, path: str) -> str:
        target = self.safe_resolve_reference(path)
        content = target.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        title = next((reference.title for reference in self.skill_package.references if reference.path == path), target.stem)
        self.recorder.record_skill_reference_loaded(path=path, title=title, content_hash=content_hash)
        return content

    def safe_resolve_reference(self, relative_path: str) -> Path:
        target = (self.base_dir / relative_path).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise ValueError("Invalid reference path")
        if target.suffix.lower() not in ALLOWED_REFERENCE_EXTENSIONS:
            raise ValueError("Unsupported reference file type")
        if not target.exists():
            raise FileNotFoundError(f"Reference not found: {relative_path}")
        return target
