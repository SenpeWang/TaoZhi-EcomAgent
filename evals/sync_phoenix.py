#!/usr/bin/env python3
"""将 RAG 评估集、逐例进度或指定的 Agent 调研报告同步到 Phoenix。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from evals import runtime_snapshot

CASES_PATH = (REPO_ROOT / "evals" / "datasets"
              / runtime_snapshot.current_dataset_version() / "rag" / "cases.jsonl")
REPORTS_DIR = REPO_ROOT / "evals/reports"
LIVE_PATH = REPORTS_DIR / "live.jsonl"

DEFAULT_URL = os.environ.get("ECOM_PHOENIX_URL", "http://127.0.0.1:6006")
DEFAULT_DATASET = os.environ.get("ECOM_PHOENIX_DATASET", "taozhi-eval-generated-v2")

ANSWER_CHARS = 2000
EVIDENCE_CHARS = 500
HTTP_TIMEOUT = 60


def load_cases() -> list:
    if not CASES_PATH.is_file():
        sys.exit(f"找不到用例集：{CASES_PATH}\n先跑 `python -m evals.dataset.generate`。")
    return [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def latest_report() -> Path:
    files = sorted(REPORTS_DIR.glob("*.json"))
    if not files:
        sys.exit(f"{REPORTS_DIR} 下没有报告，先跑一次评估。")
    return files[-1]


def case_to_example(c: dict) -> dict:
    """用例 → Phoenix dataset example。

    input 放"问什么"，output 放"答成什么样算对"，metadata 放可用于筛选的维度。
    这三者的划分决定了界面上能不能按类别筛、能不能看出金标准文档——
    output 里刻意保留 expected_documents / expected_facts，否则人在界面上
    没法判断某条答案到底错在哪。
    """
    return {
        "input": {
            "question": c["question"],
            "turns": c.get("turns") or [],
            "test_identity": c.get("test_identity") or {},
        },
        "output": {
            "expected_behavior": c.get("expected_behavior", ""),
            "expected_documents": c.get("expected_documents") or [],
            "expected_facts": c.get("expected_facts") or [],
            "pass_criteria": c.get("pass_criteria", ""),
            "forbidden_disclosure": c.get("forbidden_disclosure") or [],
        },
        "metadata": {
            "case_id": c["case_id"],
            "template": c.get("template"),
            "category": c.get("category"),
            "difficulty": c.get("difficulty"),
            "answerability": c.get("answerability"),
            "qtype": c.get("qtype"),
            "risk": c.get("risk"),
            "source": c.get("source"),
            "evidence_location": c.get("evidence_location"),
            "dataset": c.get("dataset"),
        },
    }


def slot(results: dict, cid: str) -> dict:
    return results.setdefault(cid, {"output": {}, "evaluations": []})


def apply_behavior(results: dict, d: dict) -> None:
    """一条行为层逐例结果 → 该用例的 output / evaluations。"""
    cid = d.get("id")
    if not cid:
        return
    s = slot(results, cid)
    verdict = d.get("verdict") or {}
    s["output"].update({
        "answer": (d.get("answer") or "")[:ANSWER_CHARS],
        "verdict": verdict,
        "claims": d.get("claims"),
        "supported": d.get("supported"),
        "citations": [{"title": c.get("title"), "chunk_id": c.get("chunk_id"),
                       "document_id": c.get("document_id"),
                       "quote": (c.get("quote") or "")[:EVIDENCE_CHARS]}
                      for c in (d.get("citations") or [])],
    })
    passed = d.get("pass")
    s["evaluations"].append({
        "name": "answer_pass", "kind": "LLM",
        "score": 1.0 if passed else 0.0,
        "label": "PASS" if passed else "FAIL",
        "explanation": (d.get("why") or "")[:500],
    })
    for name, key in (("answer_relevant", "relevant"), ("abstain", "abstain")):
        if key in verdict:
            val = bool(verdict.get(key))
            s["evaluations"].append({
                "name": name, "kind": "LLM",
                "score": 1.0 if val else 0.0,
                "label": ("YES" if val else "NO") if key != "abstain"
                         else ("ABSTAIN" if val else "ANSWER"),
                "explanation": None,
            })
    claims = d.get("claims") or 0
    supported = d.get("supported") or 0
    if claims:
        s["evaluations"].append({
            "name": "faithfulness_decl", "kind": "LLM",
            "score": supported / claims,
            "label": None,
            "explanation": f"{supported}/{claims} 条声明有引用原文支撑",
        })


def apply_retrieval(results: dict, c: dict) -> None:
    """一条检索层逐例结果 → retrieval.* 指标。"""
    cid = c.get("case_id")
    if not cid:
        return
    s = slot(results, cid)
    metrics = c.get("metrics") or {}
    s["output"].setdefault("retrieval", metrics)
    for m, v in metrics.items():
        if isinstance(v, (int, float)):
            s["evaluations"].append({
                "name": f"retrieval.{m}", "kind": "CODE",
                "score": float(v), "label": None, "explanation": None,
            })


def collect_results(report: dict) -> dict:
    """把三层报告拍平成 {case_id: {output, evaluations}}。

    三层的载体不同：行为层在 ``behavior.detail``（含答案与引用），检索层在
    ``retrieval.per_case``（只有分数），对抗层在 ``adversarial.detail``。
    合并到同一个 case_id 下，Phoenix 里一个用例就能看到"检索第几、答得对不对"。
    """
    out: dict = {}
    for d in (report.get("behavior") or {}).get("detail") or []:
        apply_behavior(out, d)
    for c in (report.get("retrieval") or {}).get("per_case") or []:
        apply_retrieval(out, c)

    return out


def sync_examples(client, examples: list, name: str, description: str, dry_run: bool):
    """按用例编号追加缺少的样本，返回写入后的数据集与样本索引。"""
    dataset, index = _dataset_index(client, name)
    missing = [row for row in examples if row["metadata"]["case_id"] not in index]
    print(f"[dataset] {name} 已有 {len(index)} 例，待写入 {len(missing)} 例")
    if not missing or dry_run:
        return dataset, index
    if dataset is None:
        client.datasets.create_dataset(name=name, examples=missing, dataset_description=description,
                                       timeout=HTTP_TIMEOUT)
    else:
        client.datasets.add_examples_to_dataset(dataset=name, examples=missing, timeout=HTTP_TIMEOUT)
    return _dataset_index(client, name)


def _find_experiment(client, dataset_id: str, name: str):
    items = client.experiments.list(dataset_id=dataset_id, timeout=HTTP_TIMEOUT)
    for x in items:
        if x["name"] == name:
            return x
    return None


def _experiment_target(client, dataset_id: str, base: str, force: bool):
    name = base
    revision = 1
    while True:
        existing = _find_experiment(client, dataset_id, name)
        if existing is None or not force:
            return name, existing
        revision += 1
        name = f"{base}-r{revision}"


def experiment_name(at: str, git: str) -> str:
    """experiment 命名。实时跟随与事后同步共用同一套规则，名字对上才不会重复建。

    ``at`` 是评估开跑时间（报告里的 ``at``，也是 live.jsonl 头部的 ``at``）：
    用它而不是报告文件名里的落盘时间，两个入口才能算出同一个名字。
    """
    try:
        start = datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        start = datetime.now()
    return f"eval-{start:%Y%m%d-%H%M%S}-{(git or 'nogit')[:7]}"


def _log_case(client, exp_id: str, example_id: str, payload: dict,
              start: datetime, end: datetime, *, run_id: str | None = None) -> int:
    """写入一次执行与逐项判定，已知 run 可重新写入判定。"""
    if run_id is None:
        try:
            run = client.experiments.log_run(
                experiment_id=exp_id, dataset_example_id=example_id,
                output=payload["output"], start_time=start, end_time=end, timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 409:
                return -1
            raise
        run_id = run["id"]
    for ev in payload["evaluations"]:
        client.experiments.log_evaluation(
            experiment_run_id=run_id, name=ev["name"],
            annotator_kind=ev["kind"], score=ev["score"],
            label=ev["label"], explanation=ev["explanation"],
            start_time=start, end_time=end, timeout=HTTP_TIMEOUT,
        )
    return len(payload["evaluations"])


def _logged_example_ids(client, exp_id: str) -> set:
    """已有 experiment 里已经推过的 dataset_example_id。

    跟随进程重启时靠它补写缺口而不是重写全文。读不到就明说——重复的 run 会污染
    界面上"一共跑了多少例"的判断，但比直接崩掉更可接受，所以只告警不抛。
    """
    try:
        ran = client.experiments.get_experiment(experiment_id=exp_id)
        runs = ran["task_runs"]
    except Exception as exc:
        print(f"⚠ 读不到已有实验的 run 列表（可能重复推送）：{type(exc).__name__}: {exc}")
        return set()
    return {r["dataset_example_id"] for r in runs if r.get("dataset_example_id")}


def sync_report(client, report_path: Path, report: dict, name: str,
                url: str, force: bool, dry_run: bool) -> None:
    results = collect_results(report)
    if not results:
        print("[report] 该报告没有逐例数据（旧格式），无从同步")
        return

    ds, by_case = _dataset_index(client, name)
    if ds is None:
        print(f"[report] Phoenix 里没有数据集 {name}，先跑一次 --dataset 再同步报告")
        return

    matched = sorted(set(results) & set(by_case))
    missing = sorted(set(results) - set(by_case))
    print(f"[report] {report_path.name}：可匹配 {len(matched)} 例"
          f"，报告里有但评估集没有 {len(missing)} 例")
    if missing:
        print("         未匹配（可能已从用例集移除）：", ", ".join(missing[:5]),
              "..." if len(missing) > 5 else "")

    at = report.get("at") or ""
    try:
        start = datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        start = datetime.fromtimestamp(report_path.stat().st_mtime)
        at = f"{start:%Y-%m-%d %H:%M:%S}"
    end = datetime.fromtimestamp(report_path.stat().st_mtime)
    if end < start:
        end = start

    base = experiment_name(at, report.get("git"))
    dataset_id = ds.id
    exp_name, existing = _experiment_target(client, dataset_id, base, force)
    if existing is not None:
        print(f"[experiment] {exp_name} 已存在，跳过（--force 可另建一份）")
        return
    print(f"[experiment] 新建 {exp_name}（{len(matched)} 条 run）")
    if dry_run:
        sample = matched[0] if matched else None
        if sample:
            print("         样例 evaluation:", json.dumps(
                results[sample]["evaluations"][:2], ensure_ascii=False)[:220])
        return

    six = report.get("six_metrics") or {}
    exp = client.experiments.create(
        dataset_id=dataset_id,
        experiment_name=exp_name,
        experiment_description=f"评估报告 {report_path.name}，git {report.get('git')}",
        experiment_metadata={"git": report.get("git"), "at": at,
                             "report": report_path.name,
                             "faithfulness_micro": (six.get("faithfulness") or {}).get("micro")},
        timeout=HTTP_TIMEOUT,
    )
    exp_id = exp["id"]

    runs = evals = 0
    for cid in matched:
        evals += _log_case(client, exp_id, by_case[cid], results[cid], start, end)
        runs += 1
    print(f"[experiment] 完成：{runs} 条 run / {evals} 条 evaluation")
    print(f"[url] {url}/datasets/{ds.id}/experiments")


def _dataset_index(client, name: str):
    """(dataset 对象, {case_id: node_id})；数据集不存在返回 (None, {})。"""
    try:
        ds = client.datasets.get_dataset(dataset=name, timeout=HTTP_TIMEOUT)
    except ValueError:
        return None, {}
    by_case = {}
    for e in ds:
        cid = (e.get("metadata") or {}).get("case_id")
        if cid:
            by_case[cid] = e["node_id"]
    return ds, by_case


def follow_live(client, live_path: Path, name: str, url: str,
                poll: float, idle_timeout: float, dry_run: bool) -> None:
    """跟随 evals/rag_eval/run.py 的 live.jsonl，每完成一例就推一条 run。

    为什么不复用 ``--report``：报告要等全量跑完才写，行为层 108 例串行 judge
    约 2 小时，那期间 Phoenix 里什么都没有，"实时看"就无从谈起。
    live.jsonl 是逐例追加的，这里按行消费即可；两类记录（retrieval / behavior）
    合并到同一个 case 下，与事后同步的 collect_results 口径完全一致。

    失败一律降级：读不到文件就等，断开就重试一轮，idle 超时就退出——观测不能
    反过来把评测搞挂（评测是另一个进程，这里退出不影响它）。
    """
    ds, by_case = _dataset_index(client, name)
    if ds is None:
        print(f"[follow] Phoenix 里没有数据集 {name}，先跑一次 `--dataset`")
        return
    dataset_id = ds.id

    results: dict = {}
    meta = {"at": "", "git": ""}
    exp_id, exp_name = None, ""
    logged: set = set()
    done = 0
    ended = False
    last_data = time.time()
    start = datetime.now()

    if not live_path.is_file():
        print(f"[follow] 等评估开跑（{live_path.name} 尚未生成）…", flush=True)
    while not live_path.is_file():
        if time.time() - last_data > idle_timeout:
            print(f"[follow] 等了 {idle_timeout:.0f}s 仍没有 {live_path.name}，退出")
            return
        time.sleep(max(poll, 5))

    with live_path.open("rb") as f:
        while True:
            raw = f.readline()
            if raw and not raw.endswith(b"\n"):
                f.seek(f.tell() - len(raw))
                raw = b""
            if not raw:
                if ended:
                    break
                if time.time() - last_data > idle_timeout:
                    print(f"[follow] {idle_timeout:.0f}s 无新数据，先退出"
                          f"（评估可能仍在跑，稍后再跟或跑完用 --report 同步）")
                    return
                time.sleep(poll)
                continue
            last_data = time.time()
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = rec.get("type")
            if kind == "header":
                meta.update(at=rec.get("at") or "", git=rec.get("git") or "")
                try:
                    start = datetime.strptime(meta["at"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    start = datetime.now()
                exp_name = experiment_name(meta["at"], meta["git"])
                if dry_run:
                    print(f"[follow] （dry-run）将写入 experiment {exp_name}")
                    continue
                exist = _find_experiment(client, dataset_id, exp_name)
                if exist is None:
                    exp = client.experiments.create(
                        dataset_id=dataset_id, experiment_name=exp_name,
                        experiment_description=f"实时跟随评估进度（起于 {meta['at']}），"
                                               f"git {meta['git']}",
                        experiment_metadata={"git": meta["git"], "at": meta["at"],
                                             "live": True},
                        timeout=HTTP_TIMEOUT,
                    )
                    exp_id = exp["id"]
                    print(f"[follow] 新建 experiment {exp_name}")
                else:
                    exp_id = exist["id"]
                    logged = _logged_example_ids(client, exp_id)
                    print(f"[follow] 接入已有 experiment {exp_name}"
                          f"（已推 {len(logged)} 例，只补缺口）")
                continue
            if kind == "retrieval":
                apply_retrieval(results, rec)
                continue
            if kind == "behavior":
                d = rec.get("detail") or {}
                apply_behavior(results, d)
                cid = d.get("id")
                if not cid:
                    continue
                node = by_case.get(cid)
                if not node:
                    print(f"[follow] 跳过 {cid}：评估集里没有这个 case_id")
                    continue
                if node in logged:
                    continue
                if dry_run:
                    done += 1
                    continue
                if exp_id is None:
                    print("[follow] 还没拿到 header，暂不写入本批"
                          "（等下一轮或检查 live.jsonl 首行）")
                    continue
                ev_n = _log_case(client, exp_id, node, results[cid], start, datetime.now())
                logged.add(node)
                done += 1
                if ev_n < 0:
                    print(f"[follow] {done:>3}. {cid} 已存在，跳过", flush=True)
                else:
                    print(f"[follow] {done:>3}. {cid} "
                          f"{'PASS' if d.get('pass') else 'FAIL'} +{ev_n} 项", flush=True)
                continue
            if kind == "end":
                ended = True

    if exp_name:
        print(f"[follow] 结束：共推送 {done} 例 → {url}/datasets/{dataset_id}/experiments")


def _agent_payload(case: dict) -> dict:
    labels = {"passed": "PASS", "failed": "FAIL", "unassessable": "UNASSESSABLE"}
    status = case["status"]
    verdicts = [{
        "name": "pass_verdict", "kind": "CODE", "score": {"passed": 1.0, "failed": 0.0, "unassessable": None}[status],
        "label": labels[status], "explanation": "",
    }]
    verdicts.extend({
        "name": item["judge"], "kind": "CODE", "score": item["score"],
        "label": labels[item["status"]], "explanation": item["reason"][:500],
    } for item in case["verdicts"])
    return {"output": {key: case[key] for key in ("name", "score", "status", "elapsed", "tool_calls_count")},
            "evaluations": verdicts}


def sync_agent_eval(client, report_path: Path, url: str, force: bool, dry_run: bool) -> None:
    """只导出指定的真实调研报告，重试时复用该批次已写入的执行记录。"""
    from evals.agent_eval.cases import AGENT_BENCHMARK_CASES
    from evals.agent_eval.contracts import REPORT_SCHEMA_VERSION

    report = json.loads(report_path.read_bytes())
    if not isinstance(report, dict) or report.get("schema_version") != REPORT_SCHEMA_VERSION or report.get("mode") != "live":
        raise ValueError("请选择当前格式的真实调研报告")
    examples = [{
        "input": {"prompt": case["prompt"], "keyword": case["keyword"]},
        "output": {key: case[key] for key in ("required_tools", "forbidden_tools", "required_sections", "min_chars")},
        "metadata": {"case_id": case["id"], "category": case["category"], "name": case["name"]},
    } for case in AGENT_BENCHMARK_CASES if case.get("execution_mode") != "synthetic_trace_regrade"]
    payloads = {case["case_id"]: _agent_payload(case) for case in report["case_results"]}
    if not payloads or len(payloads) != len(report["case_results"]):
        raise ValueError("调研报告缺少用例或包含重复编号")
    if payloads.keys() - {example["metadata"]["case_id"] for example in examples}:
        raise ValueError("调研报告含未登记的用例")
    metadata = {"git": report["git_sha"], "dataset_sha256": report["dataset_sha256"],
                "judges_sha256": report["judges_sha256"], "pass_rate": report["metrics"]["task_completion"]["rate"],
                "total_cases": report["metrics"]["selected"]}
    name = "agent-" + report["run_id"]
    dataset, index = sync_examples(client, examples, "taozhi-agent-eval-benchmark", "淘智多智能体调研评测基准集", dry_run)
    if dry_run:
        print(f"[agent-experiment] {name} 待同步 {len(payloads)} 例")
        return

    dataset_id = dataset.id
    name, experiment = _experiment_target(client, dataset_id, name, force)
    run_ids = {}
    if experiment is None:
        experiment = client.experiments.create(
            dataset_id=dataset_id, experiment_name=name, experiment_description=f"调研评测报告 {report_path.name}",
            experiment_metadata=metadata, timeout=HTTP_TIMEOUT,
        )
    else:
        previous = client.experiments.get_experiment(experiment_id=experiment["id"])
        run_ids = {run["dataset_example_id"]: run["id"] for run in previous["task_runs"]}
    experiment_id = experiment["id"]
    now = datetime.now(timezone.utc)
    for case_id, payload in payloads.items():
        example_id = index[case_id]
        if _log_case(client, experiment_id, example_id, payload, now, now, run_id=run_ids.get(example_id)) < 0:
            raise RuntimeError(f"用例 {case_id} 正在并发写入，可重新同步该报告")
    print(f"[agent-experiment] {name} 已同步 {len(payloads)} 例 → {url}/datasets/{dataset_id}/experiments")


def main() -> int:
    ap = argparse.ArgumentParser(description="同步评估集与评估结果到 Phoenix")
    ap.add_argument("--dataset", action="store_true", help="同步用例集到 Datasets")
    ap.add_argument("--report", nargs="?", const="latest", metavar="PATH",
                    help="把一次评估报告同步为 Experiment（缺省取最新）")
    ap.add_argument("--all", action="store_true", help="数据集 + 最新报告")
    ap.add_argument("--agent", type=Path, metavar="REPORT", help="同步指定的真实调研报告及其用例集")
    ap.add_argument("--follow", nargs="?", const=str(LIVE_PATH), metavar="PATH",
                    help="跟随 evals/rag_eval/run.py 的实时进度文件，逐例推送（缺省 "
                         "evals/reports/live.jsonl）")
    ap.add_argument("--poll", type=float, default=5.0, help="跟随轮询间隔秒（默认 5）")
    ap.add_argument("--idle-timeout", type=float, default=1200.0,
                    help="跟随模式下多久没有新数据就退出（默认 1200s）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    ap.add_argument("--force", action="store_true", help="同名 experiment 已存在时另建")
    ap.add_argument("--strict", action="store_true", help="连接失败时退出码 1")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--dataset-name", default=DEFAULT_DATASET)
    args = ap.parse_args()

    if not (args.dataset or args.report or args.all or args.follow or args.agent):
        ap.print_help()
        return 0

    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")

    try:
        from phoenix.client import Client
    except ImportError:
        print("缺少 phoenix 客户端。用独立环境运行：\n"
              "  PYTHONPATH=src ~/.local/share/ecom-agent/phoenix-venv/bin/python -m evals.sync_phoenix --all",
              file=sys.stderr)
        return 1 if args.strict else 0

    client = Client(base_url=args.url)
    try:
        client.datasets.list(timeout=HTTP_TIMEOUT)
    except Exception as exc:
        print(f"⚠ Phoenix 不可用（{args.url}）：{exc}", file=sys.stderr)
        print("  评估产物不受影响，本次跳过同步。", file=sys.stderr)
        return 1 if args.strict else 0

    try:
        if args.dataset or args.all:
            sync_examples(client, [case_to_example(case) for case in load_cases()], args.dataset_name,
                          "淘智电商 RAG 评估集", args.dry_run)

        if args.report or args.all:
            path = latest_report() if args.report in (None, "latest") else Path(args.report)
            if not path.is_file():
                sys.exit(f"报告不存在：{path}")
            sync_report(client, path, json.loads(path.read_text(encoding="utf-8")),
                        args.dataset_name, args.url, args.force, args.dry_run)

        if args.follow:
            follow_live(client, Path(args.follow), args.dataset_name, args.url,
                        args.poll, args.idle_timeout, args.dry_run)

        if args.agent:
            sync_agent_eval(client, args.agent, args.url, args.force, args.dry_run)
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        print(f"评估同步输入无效：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"⚠ 同步中断：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("  评估报告本身不受影响，修好 Phoenix 后重跑本命令即可（幂等）。",
              file=sys.stderr)
        return 1 if args.strict or args.agent else 0

    if args.dry_run:
        print("\n（--dry-run：以上均未写入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
