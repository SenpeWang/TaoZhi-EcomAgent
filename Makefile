PYTHON := .venv/bin/python
PROJECT_ROOT := $(CURDIR)
SUPERVISOR_CONFIG := $(HOME)/.local/share/ecom-v3/supervisor.conf

.PHONY: help start stop status test check build-web verify-browser clean install-hooks check-repository lint-web format-web check-format check-commits

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
	@echo "make test           在独立测试库运行离线回归"
	@echo "make check          检查 Python 语法"
	@echo "make build-web      构建并切换前端"
	@echo "make verify-browser 验收演示环境四种身份"
	@echo "make clean          清除项目 Python 与 pytest 缓存"

start:
	bash scripts/start_services.sh

stop:
	bash scripts/stop_services.sh

status:
	$(PYTHON) -m supervisor.supervisorctl -c "$(SUPERVISOR_CONFIG)" status

test:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.test" PYTHONPATH=src $(PYTHON) -m pytest tests -m 'not slow' -q --disable-warnings

check:
	$(PYTHON) -m compileall -q src scripts migrations tests

build-web:
	bash scripts/build_frontend.sh

verify-browser:
	ECOM_BROWSER_URL=http://127.0.0.1:18501 ECOM_BROWSER_SKIP_MODEL=true $(PYTHON) scripts/verify_browser.py

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

evals-quick:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.demo" PYTHONPATH=src $(PYTHON) scripts/run_evals.py --quick --compare

evals-full:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.demo" PYTHONPATH=src $(PYTHON) scripts/run_evals.py --full --compare

evals-baseline:
	ECOM_ENV_FILE="$(PROJECT_ROOT)/.env.demo" PYTHONPATH=src $(PYTHON) scripts/run_evals.py --quick --save-baseline
