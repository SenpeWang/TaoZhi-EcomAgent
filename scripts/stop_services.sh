#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
.venv/bin/python -m supervisor.supervisorctl -c "$HOME/.local/share/ecom-agent/supervisor.conf" shutdown
printf '%s\n' '应用进程已停止；企业数据库保留'
