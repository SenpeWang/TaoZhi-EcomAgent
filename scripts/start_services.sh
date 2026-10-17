#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$HOME/.local/share/ecom-agent"
cd "$ROOT"
mkdir -p "$STATE" data/runtime
exec 9>"$STATE/start.lock"
flock -w 20 9
CONF="$STATE/supervisor.conf"
if .venv/bin/python -m supervisor.supervisorctl -c "$CONF" status >/dev/null 2>&1; then
 printf '%s\n' '企业工作台已运行'
 exit 0
fi
if [ -S "$STATE/supervisor.sock" ] && .venv/bin/python -m supervisor.supervisorctl -c "$CONF" status >/dev/null 2>&1; then
 .venv/bin/python -m supervisor.supervisorctl -c "$CONF" start all
else
 rm -f "$STATE/supervisor.sock"
 # 9>&- 必须加：否则 supervisord 继承本脚本的 flock fd 9，脚本退出后锁仍被
 # 持有，之后任何重跑都会在 flock -w 20 处超时失败（本脚本只能成功跑一次）。
 nohup .venv/bin/python -m supervisor.supervisord -c "$CONF" > data/runtime/supervisor-start.log 2>&1 < /dev/null 9>&- &
fi
# 等 socket 就绪：冷启动时磁盘慢，固定 sleep 3 可能不够，最多等 60 秒
for _ in $(seq 1 30); do
 .venv/bin/python -m supervisor.supervisorctl -c "$CONF" status >/dev/null 2>&1 && break
 sleep 2
done
.venv/bin/python -m supervisor.supervisorctl -c "$CONF" status
