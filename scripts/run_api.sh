#!/usr/bin/env bash
# 兼容原启动命令，统一交由新版进程监督器管理 API 和 Worker。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/scripts/start_services.sh"
