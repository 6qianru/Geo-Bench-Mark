#!/usr/bin/env bash
# 一键启动前后端（开发用）：backend(uvicorn:8000) + frontend(vite:5173)
# 用法：./start_all.sh
# Ctrl+C 同时停两个进程。Windows Git Bash 下直接用 venv 解释器，不依赖 source activate。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# venv 解释器路径（Windows 布局 Scripts/，Unix 布局 bin/）
if [[ -f ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"
elif [[ -f ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  PY="python"
fi

# 首次运行：建 venv、装依赖（包不可导入时才重装，避免每次启动都重跑 pip install -e .）
if [[ ! -d ".venv" ]]; then
  echo "[start_all] creating venv..."
  python -m venv .venv
  "$PY" -m pip install -e . || true
elif ! "$PY" -c "import geoskillbench" >/dev/null 2>&1; then
  echo "[start_all] installing package deps..."
  "$PY" -m pip install -e . || true
fi

if [[ ! -d "frontend/node_modules" ]]; then
  echo "[start_all] installing frontend deps..."
  (cd frontend && npm install)
fi

echo "[start_all] backend:  http://127.0.0.1:8000  (uvicorn)"
echo "[start_all] frontend: http://127.0.0.1:5173  (vite)"

"$PY" -m uvicorn geoskillbench.api.app:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

(cd frontend && npm run dev -- --host 0.0.0.0 --port 5173) &
FRONTEND_PID=$!

# Git Bash 下 $! 是 Cygwin PID，taskkill 需要 Windows PID（ps -W 第 1 列 Cygwin / 第 4 列 WINPID）
sleep 0.3
BACKEND_WINPID=$(ps -W | awk -v p="$BACKEND_PID" '$1==p {print $4; exit}')
FRONTEND_WINPID=$(ps -W | awk -v p="$FRONTEND_PID" '$1==p {print $4; exit}')

shutdown() {
  echo ""
  echo "[start_all] stopping..."
  # Windows：taskkill 树杀（uvicorn --reload 是 reloader 父 + worker 子，Cygwin kill 杀不掉原生子进程）
  # Unix：普通 kill
  if command -v taskkill >/dev/null 2>&1; then
    [ -n "$BACKEND_WINPID" ] && taskkill //PID "$BACKEND_WINPID" //T //F >/dev/null 2>&1 || true
    [ -n "$FRONTEND_WINPID" ] && taskkill //PID "$FRONTEND_WINPID" //T //F >/dev/null 2>&1 || true
  else
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  fi
  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
}
trap shutdown INT TERM EXIT

wait
