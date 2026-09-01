"""评测报告持久化连接层（阶段二）。

- DATABASE_URL 为空（本地开发）→ 自动用 SQLite 文件库 reports.db，零配置可跑。
- DATABASE_URL 非空（服务器部署）→ 用 PostGIS / PostgreSQL：
      DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname
  同一份代码，通过 .env 切换，结构不变。

只有一张 reports 表：存评测报告的 JSON + Markdown 全文。
（agent 结果地图不重复存——报告里已有数据服务 URL，要查直接点链接。）
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()

DEFAULT_DATABASE_URL = "sqlite:///./reports.db"  # 本地开发兜底，不依赖服务器


class Base(DeclarativeBase):
    pass


class RunReport(Base):
    __tablename__ = "reports"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String, index=True)
    scenario_name: Mapped[str] = mapped_column(String, default="")
    executor: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(String, default=lambda: datetime.now(UTC).isoformat())
    json_content: Mapped[str] = mapped_column(Text, default="")
    md_content: Mapped[str] = mapped_column(Text, default="")


def _database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    return url or DEFAULT_DATABASE_URL


def _engine():
    url = _database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


_engine_instance = None
_session_factory = None


def _get_engine():
    global _engine_instance, _session_factory
    if _engine_instance is None:
        _engine_instance = _engine()
        _session_factory = sessionmaker(bind=_engine_instance, expire_on_commit=False)
        Base.metadata.create_all(_engine_instance)  # 首次使用自动建表
    return _engine_instance


def _session():
    _get_engine()
    return _session_factory()


def save_report(report: dict) -> None:
    """持久化一条报告。report 需含 run_id/scenario_id/scenario_name/executor/status/json/md。"""
    with _session() as session:
        session.merge(
            RunReport(
                run_id=report["run_id"],
                scenario_id=report["scenario_id"],
                scenario_name=report.get("scenario_name", ""),
                executor=report.get("executor", ""),
                status=report.get("status", ""),
                json_content=report.get("json", ""),
                md_content=report.get("md", ""),
            )
        )
        session.commit()
    prune_reports()  # 只保留最近 N 条历史，旧记录自动销毁


def list_reports(scenario_id: str | None = None) -> list[dict]:
    with _session() as session:
        statement = select(RunReport).order_by(RunReport.created_at.desc())
        if scenario_id:
            statement = statement.where(RunReport.scenario_id == scenario_id)
        return [_report_dict(row) for row in session.scalars(statement)]


def get_report(run_id: str) -> dict | None:
    with _session() as session:
        row = session.get(RunReport, run_id)
        return _report_dict(row) if row else None


def _report_dict(row: RunReport) -> dict:
    return {
        "run_id": row.run_id,
        "scenario_id": row.scenario_id,
        "scenario_name": row.scenario_name,
        "executor": row.executor,
        "status": row.status,
        "created_at": row.created_at,
        "json": row.json_content,
        "md": row.md_content,
    }


def reports_db_path() -> str:
    url = _database_url()
    if url.startswith("sqlite"):
        return str(Path(url.removeprefix("sqlite:///")).resolve())
    return url


def prune_reports(keep: int | None = None) -> int:
    """只保留最近 keep 条历史评测，删除更早记录（物理删除，不可恢复）。

    阈值优先级：keep 参数 > GEO_BENCH_HISTORY_KEEP 环境变量 > 默认 100。
    返回删除的行数。DB 故障不在此吞异常——save_report 调用方的
    try/except 与 lifespan 启动清理各自的 catch 负责兜底。
    """
    limit = keep if keep is not None else _history_keep()
    with _session() as session:
        statement = delete(RunReport).where(
            RunReport.run_id.not_in(
                select(RunReport.run_id).order_by(RunReport.created_at.desc()).limit(limit)
            )
        )
        result = session.execute(statement)
        session.commit()
        return result.rowcount or 0


def _history_keep() -> int:
    raw = os.environ.get("GEO_BENCH_HISTORY_KEEP", "100").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 100
