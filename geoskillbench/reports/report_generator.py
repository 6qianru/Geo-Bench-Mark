from __future__ import annotations

import json
from pathlib import Path

from geoskillbench.models.result import TestResult


class ReportGenerator:
    def generate_json(self, result: TestResult) -> str:
        return result.model_dump_json(indent=2)

    def generate_markdown(self, result: TestResult) -> str:
        lines = [
            f"# {result.scenario_name}",
            "",
            f"- Scenario ID: `{result.scenario_id}`",
            f"- Status: `{result.status}`",
            f"- Duration: `{result.duration_ms} ms`",
            f"- Judge Score: `{result.judge.get('score', 0)}`",
            "",
            "## Stage Results",
        ]
        for stage, status in result.stage_results.items():
            lines.append(f"- `{stage}`: `{status}`")
        lines.extend(["", "## Assertions"])
        for item in result.assertions:
            lines.append(f"- `{item['type']}`: `{'passed' if item['passed'] else 'failed'}` - {item['message']}")
        lines.extend(["", "## Tool Calls"])
        for call in result.tool_calls:
            lines.append(f"- `{call['tool_name']}`: `{call['status']}` args={call['arguments']}")
        if result.loaded_skill_references:
            lines.extend(["", "## Loaded Skill References"])
            for reference in result.loaded_skill_references:
                path = reference["path"] if isinstance(reference, dict) else reference.path
                loaded_at = reference["loaded_at"] if isinstance(reference, dict) else reference.loaded_at
                lines.append(f"- `{path}` at `{loaded_at}`")
        lines.extend(["", "## Final Response", "", result.final_output.get("final_response", "")])
        return "\n".join(lines)

    def write_reports(self, output_dir: str, result: TestResult) -> tuple[Path, Path]:
        base_dir = Path(output_dir)
        json_dir = base_dir / "json"
        md_dir = base_dir / "markdown"
        json_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)
        json_path = json_dir / f"{result.scenario_id}.json"
        md_path = md_dir / f"{result.scenario_id}.md"
        json_path.write_text(self.generate_json(result), encoding="utf-8")
        md_path.write_text(self.generate_markdown(result), encoding="utf-8")
        return json_path, md_path
