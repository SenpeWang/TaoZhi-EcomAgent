PYTHON := .venv/bin/python
PHOENIX_PY ?= $(HOME)/.local/share/ecom-agent/phoenix-venv/bin/python
PROJECT_ROOT := $(CURDIR)
COMPOSE := docker compose

.PHONY: help start rebuild stop status logs test check build-web clean install-hooks check-repository lint-web format-web check-format check-commits evals-tests evals-quick evals-full evals-baseline evals-agent evals-agent-replay evals-sync evals-pipeline evals-follow evals-archive evals-archive-check

COMMIT_BASE ?= origin/main
COMMIT_HEAD ?= HEAD

help:
	@echo "make lint-web / format-web / check-format 前端检查与格式整理"
	@echo "make check-commits  检查指定基准到 HEAD 的新增提交"
	@echo "make install-hooks  启用 Git 提交和推送检查"
	@echo "make check-repository 检查已提交公开文件和目录登记"
	@echo "make start          启动 Docker Compose 服务"
	@echo "make rebuild        构建镜像并启动 Docker Compose 服务"
	@echo "make stop           停止并清理运行容器（保留持久化数据卷）"
	@echo "make status         查看全部容器运行与健康状态"
	@echo "make logs           实时跟踪容器日志"
	@echo "make test           在独立测试库运行离线回归（含 evals 单元测试）"
	@echo "make evals-tests    只跑评估代码自身的单元测试（不需要在线服务）"
	@echo "make evals-agent-replay  使用合成执行记录验证 Agent 判定与报告"
	@echo "make evals-quick    评估：检索+对抗层（需企业服务在线；对比基线并过门禁）"
	@echo "make evals-full     评估：三层全跑（需企业服务在线；对比基线并过门禁）"
	@echo "make evals-sync     评估集与最新报告同步到 Phoenix（旁路；Phoenix 离线只跳过）"
	@echo "make evals-pipeline 三层评估 + 同步 Phoenix，一条命令走完整流程"
	@echo "make evals-follow   跟随评估进度逐例推 Phoenix（另开一个终端，跑批时用）"
	@echo "make evals-archive  把当前 baseline.json 归档成只读快照（进 Git 留档）"
	@echo "make evals-archive-check  校验归档快照与登记一致（不符退出码 1）"
	@echo "make check          检查 Python 语法"
	@echo "make build-web      构建前端生产包"
	@echo "make clean          清除项目 Python 与 pytest 缓存"

start:
	$(COMPOSE) up -d

rebuild:
	$(COMPOSE) up -d --build

stop:
	$(COMPOSE) down

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

test:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env" ECOM_MODE=test PYTHONPATH=src $(PYTHON) -m pytest tests evals/tests -m 'not slow' -q --disable-warnings

check:
	$(PYTHON) -m compileall -q src scripts migrations tests

build-web:
	cd web && npm run build

clean:
	$(PYTHON) scripts/clean_cache.py

install-hooks:
	git config --local core.hooksPath .githooks
	chmod +x .githooks/pre-commit .githooks/commit-msg .githooks/pre-push

check-repository:
	python3 scripts/check_repository.py --tracked

lint-web:
	python3 scripts/check_development.py lint

format-web:
	python3 scripts/check_development.py format

check-format:
	python3 scripts/check_development.py format-check

check-commits:
	python3 scripts/check_development.py commits --base "$(COMMIT_BASE)" --head "$(COMMIT_HEAD)"

evals-tests:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env" ECOM_MODE=test PYTHONPATH=src $(PYTHON) -m pytest evals/tests -q

evals-quick:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env" PYTHONPATH=src $(PYTHON) evals/rag_eval/run.py --quick --compare --gate

evals-full:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env" PYTHONPATH=src $(PYTHON) evals/rag_eval/run.py --full --compare --gate

evals-baseline:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env" PYTHONPATH=src $(PYTHON) evals/rag_eval/run.py --full --save-baseline

evals-agent:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env" PYTHONPATH=src $(PYTHON) -m evals.agent_eval.runner

evals-agent-replay:
	PYTHONPATH=src python3 -m evals.agent_eval.runner --case AGENT-WEB-003 --replay evals/datasets/agent_web_replay.json
	PYTHONPATH=src python3 -m evals.agent_eval.runner --dataset synthetic --replay evals/datasets/agent_research_replay.json

evals-sync:
	@test -x "$(PHOENIX_PY)" || { echo "缺 Phoenix 客户端：$(PHOENIX_PY)"; echo "搭建方式见 evals/README.md 的 Phoenix 同步一节"; exit 1; }
	PYTHONPATH=src $(PHOENIX_PY) -m evals.sync_phoenix --all

evals-follow:
	@test -x "$(PHOENIX_PY)" || { echo "缺 Phoenix 客户端：$(PHOENIX_PY)"; echo "搭建方式见 evals/README.md 的 Phoenix 同步一节"; exit 1; }
	PYTHONPATH=src $(PHOENIX_PY) -m evals.sync_phoenix --follow

evals-archive:
	$(PYTHON) scripts/archive_baseline.py archive

evals-archive-check:
	$(PYTHON) scripts/archive_baseline.py check

evals-pipeline:
	@$(MAKE) --no-print-directory evals-full || { code=$$?; \
	  echo "评估未过门禁（退出码 $$code），仍同步结果供归因"; \
	  $(MAKE) --no-print-directory evals-sync; exit $$code; }
	@$(MAKE) --no-print-directory evals-sync
