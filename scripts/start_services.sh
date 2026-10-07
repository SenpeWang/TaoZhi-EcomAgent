#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$HOME/.local/share/ecom-v3"
cd "$ROOT"
mkdir -p "$STATE" data/runtime
exec 9>"$STATE/start.lock"
flock -w 20 9
CONF="$STATE/supervisor.conf"
if .venv/bin/python -m supervisor.supervisorctl -c "$CONF" status >/dev/null 2>&1; then
 printf '%s\n' '企业工作台已运行'
 exit 0
fi
if [ -S "$STATE/supervisor.sock" ]; then
 .venv/bin/python -m supervisor.supervisorctl -c "$CONF" start all
else
 nohup .venv/bin/python -m supervisor.supervisord -c "$CONF" > data/runtime/supervisor-start.log 2>&1 < /dev/null &
fi
sleep 3
.venv/bin/python -m supervisor.supervisorctl -c "$CONF" status
