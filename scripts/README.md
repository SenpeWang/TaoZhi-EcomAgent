# 运维脚本

部署、数据管理、备份与门禁脚本，均可从仓库根目录直接执行。

| 脚本 | 用途 |
| --- | --- |
| `import_corpus.py` | 批量导入企业语料：按目录映射权限层级，解析、切片并生成向量；也可用 `make import-corpus` |
| `manage_database.py` | 初始化老板账号、库表维护与切片段落生成 |
| `rebuild_es_index.py` | 按已发布切片全量重建 Elasticsearch 索引 |
| `backup_database.py` | 备份数据库并做隔离库恢复演练 |
| `provision_accounts.py` | 按企业规格初始化账号与任职 |
| `seed_business_data.py`、`seed_metrics.py`、`seed_business_metrics.py` | 灌入业务夹具与指标口径 |
| `backfill_embeddings.py` | 回填缺失向量 |
| `embedding_server.py` | 本地向量推理服务，供 embedding 容器调用 |
| `wait_for_startup.py` | API/Worker 容器入口的依赖就绪闸门：等数据库、中间件与向量服务可用后再启动目标进程 |
| `check_repository.py` | 敏感文件、秘密与目录契约检查 |
| `check_development.py` | 前端规范、暂存快照与提交信息校验 |
| `archive_baseline.py`、`clean_cache.py` | 基线归档与缓存清理 |
| `run_with_env.py` | 先载入本地 `.env` 再执行命令，供宿主侧评测与运维使用 |
