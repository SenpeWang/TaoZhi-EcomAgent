"""分层架构守卫：外层可依赖内层，内层绝不反向依赖外层。

依赖方向铁律（只允许由外向内）：
    interface  ->  infrastructure  ->  application  ->  domain

domain 与 application 不得 import Web 框架、数据库驱动、消息队列、对象存储、
HTTP 客户端、观测 SDK，也不得读取配置（taozhi.config 属于外层）。

本测试不做任何豁免：一旦出现反向依赖或框架泄漏，立即失败。
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "taozhi"

LAYER_ORDER = {"domain": 0, "application": 1, "infrastructure": 2, "interface": 3}

LAYER_MAP = {
    "domain": ["domain/"],
    "application": [
        "orchestration/", "rag/", "agent/", "schemas/", "core/",
        "core/agent/", "core/compaction/", "core/session/", "core/skills/", "core/commands/",
    ],
    "infrastructure": [
        "infrastructure/", "knowledge/", "ai/", "observability/", "config.py",
        "core/memory/", "core/tools/",
    ],
    "interface": ["api/", "server/", "agents/"],
}

# 顶层包标记文件，内容为空，不含任何 import
PACKAGE_MARKERS = {"__init__.py"}

# 内层禁止的第三方依赖（domain/application 与基础设施解耦）
FORBIDDEN_IN_DOMAIN = {
    "fastapi", "starlette", "uvicorn", "psycopg", "psycopg2", "sqlalchemy",
    "redis", "kafka", "pika", "celery", "elasticsearch", "minio", "httpx",
    "opentelemetry", "numpy", "pandas", "langgraph", "langchain",
}
FORBIDDEN_IN_APPLICATION = FORBIDDEN_IN_DOMAIN - {"numpy", "langgraph", "langchain"}


def get_layer(path: Path):
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


def _iter_imports(tree):
    """遍历模块内全部 import，包含函数体内的延迟 import。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield a.name, n.lineno
        elif isinstance(n, ast.ImportFrom):
            yield (n.module or ""), n.lineno


def test_every_file_is_mapped_to_a_layer():
    """新增文件必须登记层级，否则守卫会漏判。"""
    unmapped = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if get_layer(p) is None]
    assert not unmapped, "存在未登记层级的源文件：\n" + "\n".join(sorted(unmapped))


def test_no_reverse_layer_dependency():
    """内层绝不反向依赖外层。"""
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        layer = get_layer(p)
        if layer is None:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for module, lineno in _iter_imports(tree):
            if not module.startswith("taozhi."):
                continue
            target = module[len("taozhi."):]
            target_layer = get_layer(ROOT / target.replace(".", "/"))
            if target_layer is None:
                continue
            if LAYER_ORDER[target_layer] > LAYER_ORDER[layer]:
                violations.append(
                    f"{p.relative_to(ROOT)}:{lineno} ({layer}) 反向依赖 {module} ({target_layer})"
                )
    assert not violations, "反向依赖：\n" + "\n".join(violations)


def test_inner_layers_have_no_framework_leak():
    """domain/application 不得 import 框架与基础设施客户端。"""
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        layer = get_layer(p)
        if layer not in ("domain", "application"):
            continue
        forbidden = FORBIDDEN_IN_DOMAIN if layer == "domain" else FORBIDDEN_IN_APPLICATION
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for module, lineno in _iter_imports(tree):
            root_module = module.split(".")[0]
            if root_module in forbidden:
                violations.append(f"{p.relative_to(ROOT)}:{lineno} ({layer}) 引入框架 {module}")
    assert not violations, "框架泄漏：\n" + "\n".join(violations)


def test_inner_layers_do_not_read_config():
    """配置读取属于外层职责，内层必须通过参数注入。"""
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        layer = get_layer(p)
        if layer not in ("domain", "application"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for module, lineno in _iter_imports(tree):
            if module in ("taozhi.config",) or module.endswith(".config"):
                violations.append(f"{p.relative_to(ROOT)}:{lineno} ({layer}) 读取 {module}")
    assert not violations, "内层读取配置：\n" + "\n".join(violations)
