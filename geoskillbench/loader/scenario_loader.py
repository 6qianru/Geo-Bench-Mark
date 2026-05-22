from __future__ import annotations

from pathlib import Path

import yaml

from geoskillbench.models.scenario import Scenario


class ScenarioLoader:
    def load(self, path: str) -> Scenario:
        scenario_path = Path(path)
        with scenario_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        scenario = Scenario.model_validate(data)
        scenario.__dict__["_base_path"] = str(scenario_path.parent.resolve())
        return scenario

    def load_dir(self, path: str) -> list[Scenario]:
        base_path = Path(path)
        scenarios: list[Scenario] = []
        for file_path in sorted(base_path.rglob("*.yml")) + sorted(base_path.rglob("*.yaml")):
            scenarios.append(self.load(str(file_path)))
        return scenarios
