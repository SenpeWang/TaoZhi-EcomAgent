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
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src" / "taozhi"

LAYER_ORDER = {
    "interface": 3,
    "application": 2,
    "infrastructure": 1,
    "domain": 0,
}

LAYER_MAP = {
    "interface": [
        "server/",
        "schemas/",
    ],
    "application": [
        "application/",
        "orchestration/",
        "core/agent/",
    ],
    "infrastructure": [
        "infrastructure/",
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

PACKAGE_MARKERS = {
    "__init__.py",
    "core/__init__.py",
}

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


def get_layer(path: Path) -> str | None:
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


def _package_import(name: str) -> str | None:
    """解析项目包内的绝对模块路径。"""
    if name == "taozhi":
        return ""
    if name.startswith("taozhi."):
        return name.removeprefix("taozhi.")
    return None


def _relative_import(rel_path: Path, node: ast.ImportFrom) -> str | None:
    """解析相对导入定位的模块或包。"""
    parts = list(rel_path.parts[:-1])
    up = node.level - 1
    if up > len(parts):
        return None
    base_parts = parts[: len(parts) - up] if up > 0 else parts
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _module_path(name: str) -> Path | None:
    """返回模块或包的源码，普通导入符号没有独立路径。"""
    folder = ROOT / name.replace(".", "/")
    if name and folder.with_suffix(".py").is_file():
        return folder.with_suffix(".py")
    package = folder / "__init__.py"
    return package if package.is_file() else None


def _iter_all_imports(rel_path: Path, tree: ast.AST) -> Iterator[tuple[str, int]]:
    """识别项目内直接导入及从包导入的子模块。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = _package_import(alias.name)
                if name is not None:
                    yield name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            parent = _relative_import(rel_path, node) if node.level else _package_import(node.module or "")
            if parent is None:
                continue
            yield parent, node.lineno
            prefix = f"{parent}." if parent else ""
            for alias in node.names:
                name = prefix + alias.name
                if _module_path(name) is not None:
                    yield name, node.lineno


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
            target = _module_path(target_submod)
            target_layer = get_layer(target) if target else None
            if target_layer and LAYER_ORDER[source_layer] < LAYER_ORDER[target_layer]:
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


def test_interface_routers_do_not_access_db_directly():
    """HTTP 路由与装配入口不得直接打开数据库连接或执行原始 SQL。"""
    targets = [ROOT / "server" / "api.py"] + sorted((ROOT / "server" / "routers").glob("*.py"))
    violations = []
    for p in targets:
        rel = p.relative_to(ROOT)
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for name, lineno in _iter_all_imports(rel, tree):
            if name == "infrastructure.persistence.database":
                violations.append(f"{rel}:{lineno} 直接引入数据库连接模块")
    assert not violations, "接口路由违规直连数据库：\n" + "\n".join(violations)


def test_application_does_not_import_fastapi_or_schemas():
    """应用层不得反向依赖 Web 框架或接口 DTO。"""
    forbidden_roots = {"fastapi", "starlette", "uvicorn"}
    violations = []
    for p in sorted(ROOT.rglob("*.py")):
        if get_layer(p) != "application":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            lineno = getattr(n, "lineno", 0)
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.split(".")[0] in forbidden_roots or a.name.startswith("taozhi.schemas"):
                        violations.append(f"{p.relative_to(ROOT)}:{lineno} 引入 {a.name}")
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] in forbidden_roots or n.module.startswith("taozhi.schemas"):
                    violations.append(f"{p.relative_to(ROOT)}:{lineno} 引入 {n.module}")
    assert not violations, "应用层违规依赖 Web/DTO：\n" + "\n".join(violations)


def test_http_startup_does_not_load_task_execution():
    """在新解释器装配真实 HTTP 路由，避免普通请求预加载工作流或全文检索引擎。"""
    script = """
import sys
import taozhi.server.api

execution_modules = {
    'taozhi.application.documents.ingestion',
    'taozhi.application.bi.workflow',
    'taozhi.application.rag.execution',
    'taozhi.application.rag.workflow',
    'taozhi.application.research.execution',
    'taozhi.application.research.graph',
    'taozhi.infrastructure.mcp.server',
    'taozhi.infrastructure.knowledge.retriever',
}
loaded = execution_modules.intersection(sys.modules)
assert not loaded, f'HTTP 装配加载了执行模块 {sorted(loaded)}'
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT.parent, check=True)


def test_database_and_document_parser_import_without_execution_adapters():
    """只加载数据库和原件解析时，不初始化 MCP、模型、混合检索或 PDF 运行时。"""
    script = """
import sys
from taozhi.infrastructure.knowledge.ingest import validate_upload
from taozhi.infrastructure.persistence.database import database_connection

unexpected = {
    'taozhi.ai.client', 'taozhi.infrastructure.knowledge.retriever',
    'taozhi.infrastructure.mcp.server', 'taozhi.infrastructure.task_queue', 'fitz',
}
loaded = unexpected.intersection(sys.modules)
assert not loaded, f'底层模块加载了无关适配 {sorted(loaded)}'
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT.parent, check=True)


def test_legacy_monolith_files_stay_deleted():
    """防止已重构清理的旧混合单体与兼容别名文件回流。"""
    legacy_paths = [
        "server/pipeline.py",
        "server/services.py",
        "server/passwords.py",
        "application/services/document_service.py",
        "knowledge/__init__.py",
        "orchestration/agent_system.py",
    ]
    present = [rel for rel in legacy_paths if (ROOT / rel).exists()]
    assert not present, "禁止保留已废弃的混合旧文件或别名文件：" + ", ".join(present)
