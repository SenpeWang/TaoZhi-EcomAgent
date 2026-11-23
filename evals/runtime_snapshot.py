"""评测运行环境快照：数据集版本描述符、Git 提交、Prompt 源文件摘要与算法版本摘要。

只读实现，供两套评测运行器与评测中心入库层共用，保证"数据集版本 + 算法版本"口径唯一：
- 数据集版本从 `evals/datasets/<version>/dataset.json` 读取，内容 sha 以文件实际摘要校验；
- 算法版本由 Git 提交、Prompt 源文件摘要、判定器摘要与模型标识共同决定。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "evals" / "datasets"
PROMPT_MODULES = (
    "src/taozhi/application/research/planner.py",
    "src/taozhi/application/research/reporter.py",
    "src/taozhi/application/research/subagents.py",
    "src/taozhi/application/rag/specialists.py",
    "evals/agent_eval/judges.py",
    "evals/rag_eval/run.py",
)
MODEL_ENV_KEYS = (
    "MODEL",
    "SENSENOVA_OVERSEAS_MODEL",
    "DOTS_MODEL",
    "BAILIAN_MODEL",
)


def dataset_versions() -> list[str]:
    """列出仓库内带版本描述符的数据集版本目录（按名称排序）。"""
    return sorted(
        path.parent.name
        for path in DATASETS.glob("*/dataset.json")
        if path.is_file()
    )


def current_dataset_version() -> str:
    """返回当前使用的数据集版本（目录名最大者，版本号即目录名）。"""
    versions = dataset_versions()
    if not versions:
        raise RuntimeError("仓库内没有可用的数据集版本描述符 dataset.json")
    return versions[-1]


def load_descriptor(version: str | None = None) -> dict:
    """读取数据集版本描述符；缺失或结构非法时给出明确错误。"""
    resolved = version or current_dataset_version()
    path = DATASETS / resolved / "dataset.json"
    if not path.is_file():
        raise RuntimeError(f"数据集版本描述符不存在：{path}")
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    if descriptor.get("version") != resolved or not isinstance(descriptor.get("suites"), dict):
        raise RuntimeError(f"数据集版本描述符结构非法：{path}")
    return descriptor


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_suite_files(version: str, suite: str) -> None:
    """逐文件校验数据集内容摘要，内容变化必须升版本号后才能继续使用。"""
    descriptor = load_descriptor(version)
    definition = descriptor["suites"][suite]
    base = DATASETS / version
    mismatched = []
    for relative, expected in (definition.get("files") or {}).items():
        path = base / relative
        if not path.is_file():
            mismatched.append(f"{relative} 缺失")
            continue
        actual = _sha256_file(path)
        if actual != expected:
            mismatched.append(f"{relative} sha256 期望 {expected[:12]} 实际 {actual[:12]}")
    if mismatched:
        raise RuntimeError(f"数据集 {suite}（{version}）内容与登记不一致，请升级版本号：" + "；".join(mismatched))


def dataset_meta(suite: str, version: str | None = None) -> dict:
    """返回数据集版本元数据，供报告内嵌与入库校验。"""
    resolved = version or current_dataset_version()
    descriptor = load_descriptor(resolved)
    definition = descriptor["suites"][suite]
    return {
        "version": resolved,
        "suite": suite,
        "eval_kind": definition.get("eval_kind"),
        "case_count": definition.get("case_count"),
        "content_sha256": definition.get("content_sha256"),
        "files": definition.get("files") or {},
        "modes": definition.get("modes") or [],
        "scopes": definition.get("scopes") or [],
    }


def git_sha() -> str:
    """读取当前 Git 提交；无 Git 或读取失败时回退到构建注入的 ECOM_GIT_SHA。"""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return os.environ.get("ECOM_GIT_SHA") or "unknown"


def prompt_module_shas() -> dict[str, str]:
    """计算承载 Prompt 的源文件摘要；缺失文件跳过，不伪造。"""
    shas = {}
    for relative in PROMPT_MODULES:
        path = ROOT / relative
        if path.is_file():
            shas[relative] = _sha256_file(path)
    return shas


def model_labels() -> dict[str, str]:
    """记录模型标识（不含任何密钥或地址中的凭据）。"""
    return {key: value for key, value in ((key, os.environ.get(key, "")) for key in MODEL_ENV_KEYS) if value}


def algorithm_snapshot(*, judges_sha: str | None = None, model_config_sha: str | None = None) -> dict:
    """组装算法版本快照：代码、模型、Prompt 与判定器摘要。"""
    return {
        "git_sha": git_sha(),
        "models": model_labels(),
        "prompt_module_sha256": prompt_module_shas(),
        "judges_sha256": judges_sha,
        "model_config_sha256": model_config_sha,
    }


def write_progress(path: str | Path | None, *, stage: str, progress: int, detail: str = "") -> None:
    """把单行进度追加到 JSONL；写失败不影响评测本身。"""
    if not path:
        return
    try:
        line = json.dumps({"stage": stage, "progress": max(0, min(100, int(progress))), "detail": detail},
                          ensure_ascii=False) + "\n"
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
    except Exception:
        pass