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


def _cmd_eval(args: argparse.Namespace) -> int:
    from .evaluation import report_markdown, run_evaluation, save_report

    report = run_evaluation(limit=args.limit, top_k=args.top_k,
                            with_llm_judge=not args.no_judge)
    path = save_report(report)
    print(report_markdown(report))
    print(f"\n报告已保存：{path}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """把企业入库目录下的真实资料全量入库（企业级链路）。

    链路与生产 API 上传完全一致：附件入 Blob → 建资料草稿 → worker 解析/切片
    → bge 词向量 → 写入 PG chunks 并同步 ES → 负责人批准发布审批后可见。
    """
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("enterprise.ingest")
    try:
        from .enterprise.config import ROOT, load_config
        from .enterprise.db import database_connection, fetch_one
        from .enterprise.policy import load_principal
        from .enterprise.services import decide, generate_id, new_document
        from .enterprise.storage import get_blob_store
        from .enterprise.worker import process_ingestion_job
    except Exception as exc:  # noqa: BLE001
        print(f"企业级依赖不可用：{exc}", file=sys.stderr)
        return 1

    cfg = load_config()
    root = Path(getattr(args, "dir", "") or (ROOT / "data/incoming" / cfg.tenant_code))
    if not root.exists():
        print(f"企业入库目录不存在：{root}", file=sys.stderr)
        return 1
    suffixes = {".md", ".txt", ".markdown", ".pdf", ".docx"}
    files = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in suffixes)
    limit = int(getattr(args, "limit", 0) or 0)
    if limit:
        files = files[:limit]
    if not files:
        print(f"入库目录无待处理文件：{root}")
        return 0

    mimes = {".pdf": "application/pdf",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    store = get_blob_store()
    ok, fail = 0, []

    # 事务 1：建资料草稿并入队（提交后 worker/解析才能看到）
    with database_connection() as c:
        tenant = fetch_one(c, "SELECT id FROM tenants WHERE code=%s", (cfg.tenant_code,))
        if not tenant:
            print(f"未找到企业租户 code={cfg.tenant_code}，请先执行 manage_database.py migrate",
                  file=sys.stderr)
            return 1
        boss = fetch_one(c, "SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id AND m.tenant_id=u.tenant_id "
                            "WHERE u.tenant_id=%s AND u.active AND m.active AND m.role='boss' ORDER BY u.created_at LIMIT 1",
                         (tenant["id"],))
        if not boss:
            print("未找到企业负责人(boss)账号，请先执行 manage_database.py bootstrap-owner",
                  file=sys.stderr)
            return 1
        p = load_principal(c, boss["id"])
        boss_id, tenant_id = boss["id"], p.tenant_id
        queued = []
        for f in files:
            try:
                title = _file_title(f)
                name = generate_id("blob") + f.suffix.lower()
                store.put_blob(p.tenant_id, name, f.read_bytes())
                res = new_document(c, p, title=title, body="待解析附件", node_id=p.root,
                                   scope="company", level=1, blob=name,
                                   mime=mimes.get(f.suffix.lower(), "text/plain"))
                queued.append((res["id"], res["task_id"], f.name))
            except Exception as exc:  # noqa: BLE001
                fail.append((f.name, str(exc)))
                log.warning("建资料失败 %s：%s", f.name, exc)

    # 生产由 worker 服务消费入库任务；CLI 等待其解析完成。
    # 若任务一直无人认领（worker 未启动），CLI 自行认领处理，保证离线也可用。
    def _claim(task_id: str):
        with database_connection() as c:
            return fetch_one(c, "UPDATE jobs SET state='running',run_version=run_version+1,"
                                "attempts=attempts+1,worker_id='cli',"
                                "lease_until=now()+(%s*interval '1 second'),"
                                "started_at=COALESCE(started_at,now()),error='' "
                                "WHERE id=%s AND state='queued' RETURNING *",
                             (cfg.lease_seconds, task_id))

    deadline = time.time() + 300
    for did, task_id, fname in queued:
        try:
            state = "ingesting"
            while time.time() < deadline:
                with database_connection() as c:
                    row = fetch_one(c, "SELECT state FROM documents WHERE id=%s", (did,))
                    state = row["state"] if row else "missing"
                if state in ("review", "published", "failed"):
                    break
                job = _claim(task_id)          # 未被 worker 领取则由 CLI 处理
                if job:
                    process_ingestion_job(job)
                    continue
                time.sleep(2)
            if state not in ("review", "published"):
                fail.append((fname, f"任务未在超时前完成解析（当前状态 {state}），请确认 worker 服务已启动"))
                log.warning("%s 未完成解析：%s", fname, state)
                continue
            # 批准发布审批（review → published）
            with database_connection() as c2:
                p2 = load_principal(c2, boss_id)
                ap = fetch_one(c2, "SELECT id FROM approvals WHERE tenant_id=%s AND target_id=%s "
                                   "AND kind='publish' AND state='pending' ORDER BY created_at DESC LIMIT 1",
                               (tenant_id, did))
                if ap:
                    decide(c2, p2, ap["id"], True, "CLI 全量入库自动发布")
            ok += 1
            log.info("已入库并发布：%s", fname)
        except Exception as exc:  # noqa: BLE001
            fail.append((fname, str(exc)))
            log.warning("入库失败 %s：%s", fname, exc)
    print(f"入库完成：成功 {ok} 篇 / 失败 {len(fail)} 篇，目录 {root}")
    for name, err in fail[:10]:
        print(f"  失败 {name}: {err}")
    return 1 if fail else 0


def _file_title(path: Path) -> str:
    """取文档首行标题（去 # 号），无则回退文件名。"""
    try:
        if path.suffix.lower() in {".md", ".txt", ".markdown"}:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    return stripped[:80]
    except OSError:
        pass
    return path.stem.replace("_", " ")[:80]


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

    p_eval = sub.add_parser("eval", help="运行检索层评测")
    p_eval.add_argument("--limit", type=int, default=None)
    p_eval.add_argument("--top-k", type=int, default=5)
    p_eval.add_argument("--no-judge", action="store_true", help="跳过 LLM-as-Judge")
    p_eval.set_defaults(func=_cmd_eval)

    p_ingest = sub.add_parser("ingest", help="把企业入库目录的真实资料全量入库（解析/切片/向量化/发布）")
    p_ingest.add_argument("--keywords", default="商品知识")
    p_ingest.add_argument("--sources", default="")
    p_ingest.add_argument("--limit", type=int, default=10)
    p_ingest.add_argument("--dir", default="", help="企业入库目录，默认 data/incoming/<tenant_code>")
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
