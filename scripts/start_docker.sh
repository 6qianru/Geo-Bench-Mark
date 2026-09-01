#!/usr/bin/env bash
# 一键起 Docker 编排：backend + frontend + postgis（可选 mock-agent）。
# 用法：
#   ./scripts/start_docker.sh                 # 启 backend/frontend/postgis
#   ./scripts/start_docker.sh mock            # 额外启 mock-agent（离线验证外部 agent）
#   ./scripts/start_docker.sh down            # 停止并移除容器（保留数据卷）
#   ./scripts/start_docker.sh logs            # 跟随日志
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  : # docker compose 可用
else
  echo "错误：需要 docker + docker compose。" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "提示：未找到 .env —— 密钥 (DEEPSEEK_API_KEY/DATABASE_URL) 为空，将用默认值（数据库走 SQLite 兜底）。"
fi

case "${1:-}" in
  mock)
    echo "起编排（含 mock-agent）：docker compose --profile mock up --build"
    exec docker compose --profile mock up --build
    ;;
  down)
    exec docker compose down
    ;;
  logs)
    exec docker compose logs -f --tail=100
    ;;
  *)
    echo "起编排：docker compose up --build"
    exec docker compose up --build
    ;;
esac
