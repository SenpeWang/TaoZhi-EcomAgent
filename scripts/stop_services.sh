#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
.venv-v3/bin/python -m supervisor.supervisorctl -c "$HOME/.local/share/ecom-v3/supervisor.conf" shutdown
printf '%s\n' '应用进程已停止；企业数据库保留'
