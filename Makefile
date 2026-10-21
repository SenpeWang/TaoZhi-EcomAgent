PYTHON := .venv/bin/python
PHOENIX_PY := $(HOME)/.local/share/ecom-agent/phoenix-venv/bin/python
PROJECT_ROOT := $(CURDIR)
SUPERVISOR_CONFIG := $(HOME)/.local/share/ecom-agent/supervisor.conf

.PHONY: help start stop status test check build-web clean install-hooks check-repository lint-web format-web check-format check-commits evals-tests evals-quick evals-full evals-baseline evals-sync evals-pipeline evals-follow evals-archive evals-archive-check

COMMIT_BASE ?= origin/main
COMMIT_HEAD ?= HEAD

help:
	@echo "make lint-web / format-web / check-format 前端检查与格式整理"
	@echo "make check-commits  检查指定基准到 HEAD 的新增提交"
	@echo "make install-hooks  启用 Git 提交和推送检查"
	@echo "make check-repository 检查已提交公开文件和目录登记"
	@echo "make start          启动企业工作台与独立任务进程"
	@echo "make stop           停止应用，保留数据库"
	@echo "make status         查看服务状态"
	@echo "make test           在独立测试库运行离线回归（含 evals 单元测试）"
	@echo "make evals-tests    只跑评估代码自身的单元测试（不需要在线服务）"
	@echo "make evals-quick    评估：检索+对抗层（需企业服务在线；对比基线并过门禁）"
	@echo "make evals-full     评估：三层全跑（需企业服务在线；对比基线并过门禁）"
	@echo "make evals-sync     评估集与最新报告同步到 Phoenix（旁路；Phoenix 离线只跳过）"
	@echo "make evals-pipeline 三层评估 + 同步 Phoenix，一条命令走完整流程"
	@echo "make evals-follow   跟随评估进度逐例推 Phoenix（另开一个终端，跑批时用）"
	@echo "make evals-archive  把当前 baseline.json 归档成只读快照（进 Git 留档）"
	@echo "make evals-archive-check  校验归档快照与登记一致（不符退出码 1）"
	@echo "make check          检查 Python 语法"
	@echo "make build-web      构建并切换前端"
	@echo "make clean          清除项目 Python 与 pytest 缓存"

start:
	bash scripts/start_services.sh

stop:
	bash scripts/stop_services.sh

status:
	$(PYTHON) -m supervisor.supervisorctl -c "$(SUPERVISOR_CONFIG)" status

test:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.test" PYTHONPATH=src $(PYTHON) -m pytest tests evals/tests -m 'not slow' -q --disable-warnings

check:
	$(PYTHON) -m compileall -q src scripts migrations tests

build-web:
	bash scripts/build_frontend.sh

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
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.test" PYTHONPATH=src $(PYTHON) -m pytest evals/tests -q

evals-quick:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.production" PYTHONPATH=src $(PYTHON) evals/run_evals.py --quick --compare --gate

evals-full:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.production" PYTHONPATH=src $(PYTHON) evals/run_evals.py --full --compare --gate

evals-baseline:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.production" PYTHONPATH=src $(PYTHON) evals/run_evals.py --full --save-baseline

# Phoenix 客户端装在独立 venv，不进项目 .venv：观测依赖不污染生产环境
evals-sync:
	@test -x "$(PHOENIX_PY)" || { echo "缺 Phoenix 客户端：$(PHOENIX_PY)"; echo "搭建方式见 evals/README.md 的 Phoenix 同步一节"; exit 1; }
	$(PHOENIX_PY) evals/sync_phoenix.py --all

# 跟随逐例进度实时推送：评估跑批约 2 小时，report 结束才落盘，这条通道补那段空窗
evals-follow:
	@test -x "$(PHOENIX_PY)" || { echo "缺 Phoenix 客户端：$(PHOENIX_PY)"; echo "搭建方式见 evals/README.md 的 Phoenix 同步一节"; exit 1; }
	$(PHOENIX_PY) evals/sync_phoenix.py --follow

# 基线归档：一次跑批一份只读快照进 Git。baseline.json 会被下次跑批覆盖，
# 归档目录保留"当时跑出来是什么"，横向对比不用翻 git log。
evals-archive:
	$(PYTHON) scripts/archive_baseline.py archive

# 归档完整性：md5 不符说明有人动过历史基线，必须在 CI 里红出来
evals-archive-check:
	$(PYTHON) scripts/archive_baseline.py check

# 门禁没过也要同步：失败样例正是最需要被看到、被归因的那批
evals-pipeline:
	@$(MAKE) --no-print-directory evals-full || { code=$$?; \
	  echo "评估未过门禁（退出码 $$code），仍同步结果供归因"; \
	  $(MAKE) --no-print-directory evals-sync; exit $$code; }
	@$(MAKE) --no-print-directory evals-sync
