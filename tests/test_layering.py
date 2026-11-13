"""分层架构守卫：高层依赖低层，低层绝不反向依赖高层。

分层架构体系（自顶向下，由高层至低层）：
    4: interface       - 最高层：HTTP 端点、CLI 入口、Worker 守护进程、业务装配
    3: application     - 高层：业务用例编排、Agent 状态机、RAG 流水线、上下文治理
    2: infrastructure  - 底层：数据库仓储、向量检索、AI 客户端、底层采集工具、可观测性、配置
    1: domain          - 最底层/核心基石：纯业务实体、访问规则、领域异常与脱敏

依赖方向铁律：
    高层允许依赖低层，低层绝对不能反向依赖高层。
    domain 作为最底层业务基石，必须纯粹，严禁引入第三方技术框架或读取环境配置。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Generator, Optional, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "taozhi"

# 层级权重：数值越大，层级越高
LAYER_ORDER = {
    "interface": 3,
    "application": 2,
    "infrastructure": 1,
    "domain": 0,
}

LAYER_MAP = {
    "interface": [
        "api/",
        "server/",
        "agents/",
    ],
    "application": [
        "orchestration/",
        "rag/",
        "agent/",
        "schemas/",
        "core/agent/",
        "core/compaction/",
        "core/session/",
        "core/skills/",
        "core/commands/",
    ],
    "infrastructure": [
        "infrastructure/",
        "knowledge/",
        "ai/",
        "observability/",
        "core/memory/",
        "core/tools/",
        "config.py",
    ],
    "domain": [
        "domain/",
    ],
}

# 纯包标记文件（无实际执行逻辑）
PACKAGE_MARKERS = {
    "__init__.py",
    "core/__init__.py",
    "agent/__init__.py",
}

# 领域层严禁引入的第三方框架与外部技术依赖
FORBIDDEN_IN_DOMAIN = {
    "fastapi",
    "starlette",
    "uvicorn",
    "psycopg",
    "psycopg2",
    "sqlalchemy",
    "redis",
    "kafka",
    "pika",
    "celery",
    "elasticsearch",
    "minio",
    "httpx",
    "opentelemetry",
    "numpy",
    "pandas",
    "langgraph",
    "langchain",
}


def get_layer(path: Path) -> Optional[str]:
    """按最长前缀匹配把文件归入某一层；返回 None 表示未登记。"""
    rel = str(path.relative_to(ROOT))
    if rel in PACKAGE_MARKERS:
        return "domain"
    best, best_len = None, -1
    for layer, prefixes in LAYER_MAP.items():
        for pre in prefixes:
            if rel.startswith(pre) and len(pre) > best_len:
                best, best_len = layer, len(pre)
    return best


def _resolve_relative_import(rel_path: Path, node: ast.ImportFrom) -> Optional[str]:
    """解析相对导入对应的模块路径。"""
    if node.level is None or node.level == 0:
        return node.module
    parts = list(rel_path.parts[:-1])
    up = node.level - 1
    if up > len(parts):
        return None
    base_parts = parts[: len(parts) - up] if up > 0 else parts
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _iter_all_imports(rel_path: Path, tree: ast.AST) -> Generator[Tuple[str, int], None, None]:
    """遍历模块内全部 import，解析绝对导入与相对导入，包含函数体内的延迟 import。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("taozhi."):
                    yield a.name[len("taozhi.") :], n.lineno
        elif isinstance(n, ast.ImportFrom):
            if n.module and n.module.startswith("taozhi."):
                yield n.module[len("taozhi.") :], n.lineno
            elif n.level and n.level > 0:
                resolved = _resolve_relative_import(rel_path, n)
                if resolved:
                    yield resolved, n.lineno


def test_every_file_is_mapped_to_a_layer():
    """新增文件必须登记层级，否则守卫会漏判。"""
    unmapped = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if get_layer(p) is None]
    assert not unmapped, "存在未登记层级的源文件：\n" + "\n".join(sorted(unmapped))


def test_no_low_layer_depends_on_high_layer():
    """高层依赖低层，低层绝不反向依赖高层。"""
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT)
        source_layer = get_layer(p)
        if source_layer is None or str(rel) in PACKAGE_MARKERS:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for target_submod, lineno in _iter_all_imports(rel, tree):
            # 将目标模块解析为文件或目录路径
            target_path_str = target_submod.replace(".", "/")
            target_file = ROOT / target_path_str
            target_py = ROOT / f"{target_path_str}.py"
            target_layer = None
            if target_py.exists():
                target_layer = get_layer(target_py)
            elif target_file.exists():
                target_layer = get_layer(target_file / "__init__.py")
            else:
                # 尝试逐级前缀匹配
                parts = target_submod.split(".")
                for i in range(len(parts), 0, -1):
                    prefix_path = ROOT / "/".join(parts[:i])
                    if prefix_path.exists() or (ROOT / f"{'/'.join(parts[:i])}.py").exists():
                        target_layer = get_layer(prefix_path if prefix_path.is_dir() else ROOT / f"{'/'.join(parts[:i])}.py")
                        if target_layer:
                            break

            if target_layer is None:
                continue

            if LAYER_ORDER[source_layer] < LAYER_ORDER[target_layer]:
                violations.append(
                    f"{rel}:{lineno} 低层 [{source_layer}] 反向依赖高层 [{target_layer}] -> {target_submod}"
                )
    assert not violations, "存在低层反向依赖高层的违规调用：\n" + "\n".join(violations)


def test_domain_layer_purity():
    """领域层作为最纯粹业务核心，不得 import 任何外部框架与基础设施客户端。"""
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        layer = get_layer(p)
        if layer != "domain":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            mod_name = None
            lineno = getattr(n, "lineno", 0)
            if isinstance(n, ast.Import):
                for a in n.names:
                    mod_name = a.name.split(".")[0]
                    if mod_name in FORBIDDEN_IN_DOMAIN:
                        violations.append(f"{p.relative_to(ROOT)}:{lineno} (domain) 引入框架 {a.name}")
            elif isinstance(n, ast.ImportFrom) and n.module:
                mod_name = n.module.split(".")[0]
                if mod_name in FORBIDDEN_IN_DOMAIN:
                    violations.append(f"{p.relative_to(ROOT)}:{lineno} (domain) 引入框架 {n.module}")
    assert not violations, "领域层纯净度违规：\n" + "\n".join(violations)


def test_domain_layer_does_not_read_config():
    """领域层不得读取系统配置，必须保持纯内存/入参注入。"""
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        layer = get_layer(p)
        if layer != "domain":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            lineno = getattr(n, "lineno", 0)
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == "taozhi.config" or a.name.endswith(".config"):
                        violations.append(f"{p.relative_to(ROOT)}:{lineno} (domain) 读取配置 {a.name}")
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module == "taozhi.config" or n.module.endswith(".config"):
                    violations.append(f"{p.relative_to(ROOT)}:{lineno} (domain) 读取配置 {n.module}")
    assert not violations, "领域层读取配置：\n" + "\n".join(violations)
