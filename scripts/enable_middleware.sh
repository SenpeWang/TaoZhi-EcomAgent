#!/usr/bin/env bash
# 一键切换到 MinIO + Kafka + Elasticsearch 链路：
# 健康检查三件套 → 应用迁移 → 存量附件迁移 → 重建检索索引 → 重启服务。
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ECOM_ENV_FILE:-.env.demo}"
export ECOM_ENV_FILE="$ENV_FILE"
CTL=".venv/bin/python -m supervisor.supervisorctl -c $HOME/.local/share/ecom-agent/supervisor.conf"

echo "== 健康检查 =="
curl -fsS -o /dev/null http://127.0.0.1:19000/minio/health/live
curl -fsS -o /dev/null http://127.0.0.1:9200/
.venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, "src")
from kafka import KafkaProducer
from ecom_copilot.enterprise.config import load_config
p = KafkaProducer(bootstrap_servers=load_config().kafka_brokers.split(","))
p.close(timeout=5)
EOF
echo "MinIO / Kafka / ES 均可用"

echo "== 数据库迁移 =="
PYTHONPATH=src .venv/bin/python -m alembic -c alembic.ini upgrade head

echo "== 存量附件迁移 MinIO =="
PYTHONPATH=src .venv/bin/python scripts/migrate_blobs_to_minio.py

echo "== 重建 ES 检索索引 =="
PYTHONPATH=src .venv/bin/python scripts/rebuild_es_index.py

echo "== 重启服务 =="
$CTL restart worker-demo api-demo worker-production api-production
$CTL status
echo "切换完成：$ENV_FILE"
