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
            f"- Judge Score: `{result.judge.get('score', 0)}` (mode: `{result.judge.get('judge_mode', '')}`)",
            "",
            "## Stage Results",
        ]
        for stage, status in result.stage_results.items():
            lines.append(f"- `{stage}`: `{status}`")
        lines.extend(["", "## Assertions"])
        for item in result.assertions:
            lines.append(f"- `{item['type']}`: `{'passed' if item['passed'] else 'failed'}` - {item['message']}")
        lines.extend(["", "## Judge"])
        judge = result.judge or {}
        lines.append(f"- Mode: `{judge.get('judge_mode', '')}`")
        lines.append(f"- Model: `{judge.get('model', '') or '(规则判定)'}`")
        lines.append(f"- Score: `{judge.get('score', 0)}`")
        lines.append(f"- Passed: `{judge.get('passed', False)}`")
        if judge.get("reason"):
            lines.append(f"- Reason: {judge['reason']}")
        if judge.get("issues"):
            lines.append("- Issues:")
            lines.extend(f"  - {item}" for item in judge["issues"])
        if judge.get("suggestions"):
            lines.append("- Suggestions:")
            lines.extend(f"  - {item}" for item in judge["suggestions"])
        lines.extend(["", "## Tool Calls"])
        if result.tool_calls:
            for index, call in enumerate(result.tool_calls, start=1):
                lines.append(f"### {index}. `{call['tool_name']}` (`{call['status']}`)")
                lines.append("入参:")
                lines.append(f"```json\n{json.dumps(call.get('arguments') or {}, ensure_ascii=False, indent=2)}\n```")
                if call.get("result"):
                    lines.append("出参:")
                    lines.append(f"```json\n{json.dumps(call['result'], ensure_ascii=False, indent=2)}\n```")
        else:
            lines.append("- (无工具调用)")
        if result.loaded_skill_references:
            lines.extend(["", "## Loaded Skill References"])
            for reference in result.loaded_skill_references:
                path = reference["path"] if isinstance(reference, dict) else reference.path
                loaded_at = reference["loaded_at"] if isinstance(reference, dict) else reference.loaded_at
                lines.append(f"- `{path}` at `{loaded_at}`")
        lines.extend(["", "## Conversation"])
        if result.conversation:
            for index, message in enumerate(result.conversation, start=1):
                role = message.get("role", "message")
                content = message.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                lines.append(f"### {index}. {role}")
                lines.append(f"```text\n{content}\n```")
        else:
            lines.append("- (无对话记录)")
        external = result.final_output.get("external_interactions", []) if isinstance(result.final_output, dict) else []
        if external:
            lines.extend(["", "## External Agent Interactions"])
            for interaction in external:
                lines.append(f"### 指令 {interaction.get('turn', '?')}")
                lines.append("发给外部智能体:")
                lines.append(f"```text\n{interaction.get('instruction', '')}\n```")
                lines.append("外部智能体回答:")
                lines.append(f"```text\n{interaction.get('response', '')}\n```")
                for call in interaction.get("tool_calls") or []:
                    lines.append(f"- 外部工具: `{call.get('tool_name')}` (`{call.get('status')}`)")
        lines.extend(["", "## Final Response", "", result.final_output.get("final_response", "")])
        lines.extend(["", "## Errors"])
        if result.errors:
            lines.extend(f"- {error}" for error in result.errors)
        else:
            lines.append("- (无)")
        return "\n".join(lines)

    def write_reports(self, output_dir: str, result: TestResult) -> tuple[Path, Path]:
        base_dir = Path(output_dir)
        json_dir = base_dir / "json"
        md_dir = base_dir / "markdown"
        json_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)
        json_path = json_dir / f"{result.scenario_id}.json"
        md_path = md_dir / f"{result.scenario_id}.md"
        json_text = self.generate_json(result)
        md_text = self.generate_markdown(result)
        json_path.write_text(json_text, encoding="utf-8")
        md_path.write_text(md_text, encoding="utf-8")
        self._persist_to_db(result, json_text, md_text)
        return json_path, md_path

    def _persist_to_db(self, result: TestResult, json_text: str, md_text: str) -> None:
        """阶段二：把报告全文写入 reports 表（SQLite 本地 / PostGIS 服务器，由 DATABASE_URL 决定）。

        DB 写入失败只记日志、不影响主流程——评测结果已落在文件系统，
        持久化是增强能力，不能因数据库问题让整个 run 失败。
        """
        from geoskillbench.api import db

        executor = ""
        if isinstance(result.final_output, dict):
            executor = result.final_output.get("executor", "") or ""
        try:
            db.save_report(
                {
                    "run_id": result.run_id,
                    "scenario_id": result.scenario_id,
                    "scenario_name": result.scenario_name,
                    "executor": executor,
                    "status": result.status,
                    "json": json_text,
                    "md": md_text,
                }
            )
        except Exception as exc:  # pragma: no cover - DB 故障不应中断评测
            import logging

            logging.getLogger(__name__).warning("Failed to persist report to DB: %s", exc)
