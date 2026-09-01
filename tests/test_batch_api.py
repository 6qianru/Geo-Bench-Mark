from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from geoskillbench.api.app import app
from geoskillbench.models.result import TestResult


def _mock_test_result(scenario_id: str, run_id: str, passed: bool = True) -> TestResult:
    return TestResult(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_name=f"Scenario {scenario_id}",
        status="passed" if passed else "failed",
        duration_ms=400,
        stage_results={},
        tool_calls=[{"tool_name": "createBuffer", "status": "success"}],
        assertions=[],
        judge={"score": 1.0 if passed else 0.0, "passed": passed},
        conversation=[],
        final_output={"final_response": "done"},
        loaded_skill_references=[],
        errors=[],
        operational_status="succeeded",
        evaluation_verdict="passed" if passed else "failed",
        termination_reason="completed",
        archive_status="succeeded",
        cleanup_status="succeeded",
        failures=[],
    )


def test_batch_api_endpoints() -> None:
    client = TestClient(app)

    # 1. 校验非法创建（空场景）
    resp = client.post("/api/batches", json={"scenarios": []})
    assert resp.status_code == 400

    # 2. 校验场景不存在
    resp = client.post("/api/batches", json={"scenarios": ["scenarios/non_existent.yml"]})
    assert resp.status_code == 404

    # 3. 正常创建并 mock 任务执行
    with patch("geoskillbench.api.task_manager.BatchRunner") as mock_runner_cls:
        mock_runner = MagicMock()
        mock_runner.run_batch.return_value = MagicMock(model_dump=lambda: {"batch_id": "test_b", "summary": {}})
        mock_runner_cls.return_value = mock_runner

        resp = client.post(
            "/api/batches",
            json={
                "scenarios": ["scenarios/buffer_school_500m_5b_001.yml"],
                "repeat_count": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        batch_id = data["batch_id"]
        assert batch_id.startswith("batch_")
        assert data["total_runs"] == 2

        # 查询列表
        resp_list = client.get("/api/batches")
        assert resp_list.status_code == 200
        batches = resp_list.json().get("batches", [])
        assert any(b["batch_id"] == batch_id for b in batches)

        # 查询单批次
        resp_get = client.get(f"/api/batches/{batch_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["batch_id"] == batch_id



def test_batch_db_persistence(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "test_batches.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    from geoskillbench.api import db

    # 重置 DB 单例连接
    db._engine_instance = None
    db._session_factory = None

    # 验证 DB save / list / get
    batch_data = {
        "batch_id": "test_batch_db_01",
        "status": "succeeded",
        "total_runs": 5,
        "passed_runs": 4,
        "failed_runs": 1,
        "pass_rate": 0.8,
        "summary_json": '{"pass_rate": 0.8, "total_runs": 5}',
    }
    db.save_batch(batch_data)
    loaded = db.get_batch("test_batch_db_01")
    assert loaded is not None
    assert loaded["batch_id"] == "test_batch_db_01"
    assert loaded["pass_rate"] == 0.8
    assert loaded["total_runs"] == 5
    assert loaded["summary"]["pass_rate"] == 0.8

    # 清理重置
    db._engine_instance = None
    db._session_factory = None

