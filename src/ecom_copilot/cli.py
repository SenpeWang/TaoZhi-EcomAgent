"""命令行入口：research / api / ui / eval / ingest / graph。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _cmd_research(args: argparse.Namespace) -> int:
    from .agents.workflow import get_workflow
    from .schemas.common import PermissionContext
    from .schemas.research import ResearchDepth, ResearchTask

    task = ResearchTask(
        question=args.question,
        depth=ResearchDepth(args.depth),
        user_id=args.user_id,
        org_tag=args.org_tag,
        category=args.category,
        product_line=args.product_line,
        brand=args.brand,
        max_sources=args.max_sources,
    )
    permission = PermissionContext(user_id=task.user_id, org_tag=task.org_tag)
    workflow = get_workflow()
    started = time.time()
    if args.stream:
        for event in workflow.stream(task, permission):
            print(f"[{event.get('stage')}] {json.dumps(event, ensure_ascii=False, default=str)[:400]}")
    state = workflow.run(task, permission) if not args.stream else workflow.results[task.task_id]
    report = state.get("report")
    print(f"\n任务 {task.task_id} 状态：{task.status.value} 用时 {time.time() - started:.1f}s")
    if report is None:
        print("未生成报告，错误：", state.get("errors"))
        return 1
    if args.out:
        from .reporting import export

        path = export(report, args.format, Path(args.out))
        print(f"报告已导出：{path}")
    else:
        print(report.to_markdown()[:6000])
    print(f"\n置信度 {report.confidence:.2f}｜引用覆盖率 {report.citation_coverage:.0%}"
          f"｜核验假设 {len(state.get('hypotheses', []))} 条")
    return 0


def _enterprise_serve(args: argparse.Namespace, mode: str) -> int:
    import os
    project=Path(__file__).resolve().parents[2]
    python=project/".venv/bin/python"
    if not python.exists():
        print("请先运行 scripts/install_runtime.sh 准备企业运行环境",file=sys.stderr)
        return 1
    env={**os.environ,"ECOM_ENV_FILE":str(project/(".env."+mode)),"PYTHONPATH":str(project/"src")}
    cmd=[str(python),"-m","uvicorn","ecom_copilot.enterprise.api:app","--host",args.host,"--port",str(args.port),"--no-access-log"]
    os.execve(str(python),cmd,env)
    return 0

def _cmd_api(args: argparse.Namespace) -> int:
    return _enterprise_serve(args,"production")

def _cmd_ui(args: argparse.Namespace) -> int:
    return _enterprise_serve(args,"demo")


def _cmd_eval(args: argparse.Namespace) -> int:
    from .evaluation import report_markdown, run_evaluation, save_report

    report = run_evaluation(limit=args.limit, top_k=args.top_k,
                            with_llm_judge=not args.no_judge)
    path = save_report(report)
    print(report_markdown(report))
    print(f"\n报告已保存：{path}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    from .ingestion.base import FetchRequest
    from .ingestion.sources import adapters_by_name, default_adapters
    from .retrieval import get_retrieval_engine

    adapters = adapters_by_name(args.sources.split(",")) if args.sources else default_adapters()
    request = FetchRequest(keywords=args.keywords.split(",") if args.keywords else [],
                           limit=args.limit)
    docs = []
    for adapter in adapters:
        got = adapter.safe_fetch(request)
        docs.extend(got)
        print(f"{adapter.name}: {len(got)} 篇")
    added = get_retrieval_engine().index_documents(docs)
    print(f"入库切片：{added}")
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    from .graph import get_graph_store, run_cypher

    store = get_graph_store()
    if args.cypher:
        result = run_cypher(args.cypher, store, include_evidence=True)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)[:4000])
    else:
        print(json.dumps(store.stats().model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecom-copilot",
        description="电商商品知识智能问答系统",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_research = sub.add_parser("research", help="执行一次商品知识问答/深度问答")
    p_research.add_argument(
        "question",
        help="商品知识问题，如：MC-500 和 MC-300 有什么区别 / iPhone 16 Pro Max 贴哪款膜 / UV 打印机堵头怎么办",
    )
    p_research.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    p_research.add_argument("--category", default="", help="商品类目，如 钢化膜/膜切机")
    p_research.add_argument("--product-line", default="", help="产品线，如 膜法工坊 C 系列")
    p_research.add_argument("--brand", default="", help="品牌，如 膜法工坊")
    p_research.add_argument("--user-id", default="cli")
    p_research.add_argument("--org-tag", default="default")
    p_research.add_argument("--max-sources", type=int, default=12)
    p_research.add_argument("--stream", action="store_true", help="打印流式执行过程")
    p_research.add_argument("--out", help="导出路径")
    p_research.add_argument("--format", default="markdown",
                            choices=["markdown", "html", "docx", "pdf"])
    p_research.set_defaults(func=_cmd_research)

    p_api = sub.add_parser("api", help="启动 FastAPI 服务")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=18080)
    p_api.add_argument("--reload", action="store_true")
    p_api.set_defaults(func=_cmd_api)

    p_ui = sub.add_parser("ui", help="启动全中文企业工作台")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=18501)
    p_ui.set_defaults(func=_cmd_ui)

    p_eval = sub.add_parser("eval", help="运行检索层评测")
    p_eval.add_argument("--limit", type=int, default=None)
    p_eval.add_argument("--top-k", type=int, default=5)
    p_eval.add_argument("--no-judge", action="store_true", help="跳过 LLM-as-Judge")
    p_eval.set_defaults(func=_cmd_eval)

    p_ingest = sub.add_parser("ingest", help="采集并入库")
    p_ingest.add_argument("--keywords", default="商品知识")
    p_ingest.add_argument("--sources", default="")
    p_ingest.add_argument("--limit", type=int, default=10)
    p_ingest.set_defaults(func=_cmd_ingest)

    p_graph = sub.add_parser("graph", help="图谱查询")
    p_graph.add_argument("--cypher", default="")
    p_graph.set_defaults(func=_cmd_graph)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
