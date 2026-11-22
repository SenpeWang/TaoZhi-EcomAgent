"""检查远端 CI 声明的环境变量能否被真实配置加载器接受。

本地门禁读取项目根目录的 `.env`，远端 CI 只有工作流里声明的环境变量；
一旦两者漂移，远端会在启动阶段直接崩溃。本脚本按作业解析工作流 `env`，
在隔离环境中调用 `load_config()` 复现远端行为。

用法：
    python3 scripts/check_ci_config.py
    python3 scripts/check_ci_config.py --workflow .github/workflows/ci.yml
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PROBE = "from taozhi.config import load_config;load_config()"
DYNAMIC_MARK = "${{"
DEPENDENCY_ERRORS = ("ModuleNotFoundError", "ImportError")


class DependencyMissing(RuntimeError):
    """当前解释器缺少项目依赖，无法复现远端配置加载。"""


def workflow_jobs(workflow: Path) -> list[tuple[str, dict[str, str]]]:
    """解析工作流里声明了 ECOM_MODE 的作业及其环境变量。

    Args:
        workflow: 工作流 YAML 文件路径。

    Returns:
        (作业名, 环境变量字典) 列表，顶层 env 作为作业级 env 的默认值。
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    shared = dict(document.get("env") or {})
    jobs: list[tuple[str, dict[str, str]]] = []
    for name, job in (document.get("jobs") or {}).items():
        env = {**shared, **((job or {}).get("env") or {})}
        if "ECOM_MODE" in env:
            jobs.append((name, {str(key): str(value) for key, value in env.items()}))
    return jobs


def probe(env: dict[str, str]) -> tuple[bool, str]:
    """在隔离环境中用一组 CI 变量加载运行时配置。

    Args:
        env: 作业声明的环境变量。

    Returns:
        (是否通过, 失败时输出末尾内容)。
    """
    home = Path(tempfile.mkdtemp(prefix="ci-config-probe-"))
    sandbox = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(ROOT / "src"),
        "ECOM_ENV_FILE": str(home / "absent.env"),
        "ECOM_PRIVATE_DIR": str(home / "private"),
        **env,
    }
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=sandbox,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (result.stderr or result.stdout).strip()
    if result.returncode == 0:
        return True, ""
    if any(marker in output for marker in DEPENDENCY_ERRORS):
        raise DependencyMissing(f"{sys.executable} 缺少项目依赖：{output.splitlines()[-1]}")
    return False, output.splitlines()[-1] if output else "未知错误"


def main() -> int:
    """校验工作流环境变量与配置加载器的一致性。

    Returns:
        进程退出码，0 表示全部作业通过。
    """
    parser = argparse.ArgumentParser(description="校验远端 CI 环境变量契约")
    parser.add_argument("--workflow", help="只检查指定工作流文件")
    args = parser.parse_args()
    files = [Path(args.workflow)] if args.workflow else sorted(WORKFLOW_DIR.glob("*.y*ml"))
    if not files:
        print("未找到工作流文件，跳过远端 CI 环境检查")
        return 0
    failures = 0
    for workflow in files:
        for job, env in workflow_jobs(workflow):
            if any(DYNAMIC_MARK in value for value in env.values()):
                print(f"跳过 {workflow.name} 作业 {job}：环境变量含动态表达式，无法静态复现")
                continue
            try:
                ok, detail = probe(env)
            except DependencyMissing as error:
                print(f"无法检查 {workflow.name} 作业 {job}：{error}")
                print(f"  修复：改用项目虚拟环境运行 python3 scripts/check_ci_config.py，当前解释器不在项目环境内")
                return 2
            if ok:
                print(f"通过 {workflow.name} 作业 {job}")
                continue
            failures += 1
            print(f"失败 {workflow.name} 作业 {job}：{detail}")
            print(f"  修复：在 {workflow} 的 env 段补齐该变量；本地 make test 读 .env，暴露不了这个缺失")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())