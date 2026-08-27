from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import yaml

from geoskillbench.models.scenario import SkillConfig
from geoskillbench.models.skill import AgentSkill, SkillReference


ALLOWED_SKILL_EXTENSIONS = {".md", ".txt", ".json", ".yml", ".yaml"}
MAX_REFERENCE_FILE_SIZE = 2 * 1024 * 1024
MAX_ZIP_FILE_SIZE = 20 * 1024 * 1024
MAX_REFERENCE_COUNT = 100
MAX_DIR_DEPTH = 8


class SkillLoader:
    def __init__(self) -> None:
        self._temp_dirs: list[tempfile.TemporaryDirectory] = []

    def load(self, skill_config: SkillConfig, base_path: str) -> AgentSkill:
        if skill_config.load_mode == "file":
            return self._load_file(skill_config, base_path)
        if skill_config.load_mode == "package":
            return self._load_package_dir(Path(base_path, skill_config.path).resolve(), skill_config)
        if skill_config.load_mode == "package_zip":
            return self._load_package_zip(Path(base_path, skill_config.path).resolve(), skill_config)
        raise ValueError(f"Unsupported skill load mode: {skill_config.load_mode}")

    def render_prompt(self, skill: AgentSkill) -> str:
        if skill.type == "prompt_skill_package":
            reference_lines = []
            for reference in skill.references:
                required_text = "required" if reference.required else "optional"
                tags = f" tags={','.join(reference.tags)}" if reference.tags else ""
                tools = f" tools={','.join(reference.tools)}" if reference.tools else ""
                reference_lines.append(f"- {reference.path} | {reference.title} | {required_text}{tags}{tools}")
            reference_index = "\n".join(reference_lines) or "- No references indexed"
            assumptions = ", ".join(skill.assumptions) or "None"
            return (
                f"Skill Package: {skill.name} ({skill.id}@{skill.version})\n"
                f"Description: {skill.description}\n"
                f"Entry file: {skill.entry_file}\n"
                f"Assumptions: {assumptions}\n"
                "Rules:\n"
                "1. You currently have only the package entry file loaded.\n"
                "2. If a subtask requires package references, you must load them through load_skill_reference before proceeding.\n"
                "3. Do not assume the contents of unloaded references.\n"
                "4. Do not read files outside the current skill package.\n"
                f"Reference Index:\n{reference_index}\n"
                f"Entry Prompt:\n{skill.base_prompt or ''}\n"
            )

        required_inputs = ", ".join(skill.required_inputs) or "None"
        optional_inputs = ", ".join(skill.optional_inputs) or "None"
        tools = ", ".join(skill.recommended_mcp_tools) or "None"
        sequence = " -> ".join(skill.tool_sequence) or "None"
        outputs = ", ".join(skill.expected_outputs) or "None"
        failures = "; ".join(skill.common_failures) or "None"
        return (
            f"Skill: {skill.name} ({skill.id}@{skill.version})\n"
            f"Description: {skill.description}\n"
            f"When to use: {skill.when_to_use}\n"
            f"Required inputs: {required_inputs}\n"
            f"Optional inputs: {optional_inputs}\n"
            f"Recommended tools: {tools}\n"
            f"Tool sequence: {sequence}\n"
            f"Instructions:\n{skill.instructions}\n"
            f"Expected outputs: {outputs}\n"
            f"Common failures: {failures}\n"
        )

    def _load_file(self, skill_config: SkillConfig, base_path: str) -> AgentSkill:
        skill_path = Path(base_path, skill_config.path).resolve()
        with skill_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return AgentSkill.model_validate(data)

    def _load_package_dir(self, package_dir: Path, skill_config: SkillConfig) -> AgentSkill:
        if not package_dir.is_dir():
            raise ValueError(f"Skill package directory not found: {package_dir}")
        entry_file = skill_config.entry or "SKILL.md"
        entry_path = (package_dir / entry_file).resolve()
        if not entry_path.exists():
            raise ValueError(f"Skill package entry file not found: {entry_file}")
        base_prompt = entry_path.read_text(encoding="utf-8")
        metadata = self._read_metadata(package_dir)
        references = self.build_reference_index(package_dir, metadata)
        skill_id = metadata.get("id", package_dir.name)
        return AgentSkill(
            id=skill_id,
            name=metadata.get("name", skill_id),
            version=metadata.get("version", "1.0.0"),
            type=metadata.get("type", "prompt_skill_package"),
            category=metadata.get("category"),
            description=metadata.get("description", ""),
            entry_file=entry_file,
            base_dir=str(package_dir),
            base_prompt=base_prompt,
            metadata=metadata,
            references=references,
            recommended_mcp_tools=metadata.get("recommended_tools", []),
            assumptions=metadata.get("assumptions", []),
            lazy_load_references=skill_config.lazy_load_references,
        )

    def _load_package_zip(self, zip_path: Path, skill_config: SkillConfig) -> AgentSkill:
        if not zip_path.exists():
            raise ValueError(f"Skill package zip not found: {zip_path}")
        if zip_path.stat().st_size > MAX_ZIP_FILE_SIZE:
            raise ValueError(f"Skill package zip too large: {zip_path}")
        temp_dir = tempfile.TemporaryDirectory(prefix="geoskillbench_skillpkg_")
        self._temp_dirs.append(temp_dir)
        extract_root = Path(temp_dir.name)
        self._safe_extract_zip(zip_path, extract_root)
        package_root = self._locate_package_root(extract_root)
        return self._load_package_dir(package_root, skill_config)

    def build_reference_index(self, base_dir: Path, metadata: dict | None = None) -> list[SkillReference]:
        metadata = metadata or {}
        if metadata.get("references"):
            references: list[SkillReference] = []
            for index, item in enumerate(metadata["references"]):
                references.append(
                    SkillReference(
                        id=item.get("id", f"ref-{index}"),
                        path=item["path"],
                        title=item.get("title", Path(item["path"]).stem),
                        summary=item.get("summary"),
                        required=item.get("required", False),
                        tags=item.get("tags", []),
                        trigger_keywords=item.get("trigger_keywords", []),
                        tools=item.get("tools", []),
                    )
                )
            return references

        references_dir = base_dir / metadata.get("references_dir", "references")
        if not references_dir.exists():
            return []
        discovered: list[SkillReference] = []
        for index, path in enumerate(sorted(references_dir.rglob("*"))):
            if not path.is_file():
                continue
            relative_path = path.relative_to(base_dir).as_posix()
            title = self._extract_title(path)
            discovered.append(SkillReference(id=f"ref-{index}", path=relative_path, title=title))
        if len(discovered) > MAX_REFERENCE_COUNT:
            raise ValueError(f"Too many skill references: {len(discovered)}")
        return discovered

    def _read_metadata(self, package_dir: Path) -> dict:
        for name in ("skill.metadata.json", "skill.json"):
            metadata_path = package_dir / name
            if metadata_path.exists():
                return json.loads(metadata_path.read_text(encoding="utf-8"))
        return {}

    def _extract_title(self, path: Path) -> str:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
            if stripped:
                return stripped[:80]
        return path.stem

    def _safe_extract_zip(self, zip_path: Path, dest_dir: Path) -> None:
        dest_dir = dest_dir.resolve()
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                member_name = member.filename
                if member_name.endswith("/"):
                    continue
                target = (dest_dir / member_name).resolve()
                if not target.is_relative_to(dest_dir):
                    raise ValueError(f"Unsafe zip path: {member_name}")
                if target.suffix.lower() not in ALLOWED_SKILL_EXTENSIONS:
                    raise ValueError(f"Unsupported file type in skill package: {target.suffix}")
                if member.file_size > MAX_REFERENCE_FILE_SIZE:
                    raise ValueError(f"Skill package file too large: {member_name}")
                if len(target.relative_to(dest_dir).parts) > MAX_DIR_DEPTH:
                    raise ValueError(f"Skill package path too deep: {member_name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    dst.write(src.read())

    def _locate_package_root(self, extract_root: Path) -> Path:
        direct_entry = extract_root / "SKILL.md"
        if direct_entry.exists():
            return extract_root
        candidates = [path.parent for path in extract_root.rglob("SKILL.md")]
        if len(candidates) != 1:
            raise ValueError("Could not uniquely determine skill package root after zip extraction.")
        return candidates[0]
