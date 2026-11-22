"""远端 CI 环境契约检查脚本的单元测试。"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_ci_config.py"


def _run(workflow: Path) -> subprocess.CompletedProcess[str]:
    """以指定工作流执行契约检查脚本。

    Args:
        workflow: 工作流文件路径。

    Returns:
        子进程执行结果。
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workflow", str(workflow)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_current_workflow_matches_runtime_config():
    """当前工作流声明的环境变量必须能直接加载运行时配置。"""
    result = _run(ROOT / ".github" / "workflows" / "ci.yml")
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_test_database_variable_is_reported(tmp_path):
    """测试模式漏配 ECOM_TEST_DATABASE_URL 时必须报错并指出变量名。"""
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        "jobs:\n"
        "  verify:\n"
        "    env:\n"
        "      ECOM_MODE: test\n"
        "      ECOM_DATABASE_URL: postgresql://ecom_test@127.0.0.1:5432/ecom_test\n",
        encoding="utf-8",
    )
    result = _run(workflow)
    assert result.returncode == 1
    assert "ECOM_TEST_DATABASE_URL" in result.stdout