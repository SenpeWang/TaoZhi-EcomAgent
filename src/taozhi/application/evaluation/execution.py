"""评测任务执行：Worker 内运行评测运行器、回报进度、取消与自动入库。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from taozhi.config import load_config
from taozhi.infrastructure.persistence import evaluation_repo, job_repo
from taozhi.infrastructure.persistence.database import database_connection

_LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[4]
DATASETS = ROOT / "evals" / "datasets"
RUNTIME = ROOT / "data" / "runtime" / "eval_jobs"
POLL_SECONDS = 2.0
SCOPE_FLAGS = {
    "full": ["--full"],
    "quick": ["--quick"],
    "retrieval": ["--retrieval-only"],
    "adversarial": ["--adversarial-only"],
    "behavior": ["--behavior-only"],
}


class EvaluationCancelled(RuntimeError):
    """评测任务被请求取消。"""


class EvaluationFailed(RuntimeError):
    """评测执行失败（崩溃、超时或报告缺失）。"""


def _python() -> str:
    return sys.executable or "python3"


def _environment(progress_file: Path, report_dir: Path) -> dict[str, str]:
    """组装子进程环境：进度文件、报告隔离目录与可导入路径。"""
    env = os.environ.copy()
    env["ECOM_EVAL_PROGRESS_FILE"] = str(progress_file)
    env["ECOM_EVAL_REPORT_DIR"] = str(report_dir)
    paths = [str(ROOT), str(ROOT / "src")]
    existing = env.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def _dataset_file(dataset: dict, name: str) -> Path | None:
    """按数据集登记的文件清单解析仓库内文件路径。"""
    files = (dataset.get("metadata") or {}).get("files") or {}
    relative = files.get(name)
    if not relative:
        return None
    path = DATASETS / dataset["version"] / relative
    return path if path.is_file() else None


def _runner_command(job: dict, dataset: dict, report_dir: Path, progress_file: Path) -> list[str]:
    """按评测类型、口径与执行方式组装运行器命令。"""
    python = _python()
    report_arg = report_dir / "report.json"
    if job["payload"]["eval_kind"] == "agent":
        command = [python, "-m", "evals.agent_eval.runner", "--output-dir", str(report_dir),
                   "--progress-jsonl", str(progress_file)]
        if job["payload"]["mode"] == "synthetic_trace_regrade":
            replay = _dataset_file(dataset, "agent/research_replay.json") or (
                DATASETS / dataset["version"] / "agent/research_replay.json")
            command.extend(["--dataset", "synthetic", "--replay", str(replay)])
        return command
    command = [python, "-m", "evals.rag_eval.run", *SCOPE_FLAGS.get(job["payload"]["scope"], ["--full"]),
               "--report-path", str(report_arg), "--progress-jsonl", str(progress_file)]
    return command


def _latest_report(report_dir: Path, eval_kind: str) -> Path:
    """定位运行器产出的报告文件；合成回放会写入子目录。"""
    candidates = sorted(
        (path for path in report_dir.rglob("*.json") if path.name != "report.json" or eval_kind == "rag"),
        key=lambda path: path.stat().st_mtime,
    )
    if eval_kind == "rag":
        direct = report_dir / "report.json"
        if direct.is_file():
            return direct
    if not candidates:
        raise EvaluationFailed("评测运行器未产出报告文件")
    return candidates[-1]


def _publish_command(job: dict, report_path: Path) -> list[str]:
    """组装入库命令，由评测中心 CLI 完成校验、对象存储与落库。"""
    payload = job["payload"]
    command = [_python(), "-m", "evals.center.cli", "publish", "--kind", payload["eval_kind"],
               "--report", str(report_path), "--tenant-id", job["tenant_id"], "--owner-id", job["owner_id"],
               "--dataset-id", payload["dataset_id"], "--dataset-version", payload["dataset_version"],
               "--scope", payload["scope"], "--mode", payload["mode"]]
    if payload.get("profile_id"):
        command.extend(["--profile-id", payload["profile_id"]])
    if payload.get("mark_baseline"):
        command.append("--baseline")
    if payload.get("dataset_snapshot_id"):
        command.extend(["--snapshot-id", payload["dataset_snapshot_id"]])
    if payload.get("run_seq") is not None:
        command.extend(["--run-seq", str(payload["run_seq"])])
    if payload.get("parent_run_id"):
        command.extend(["--parent-run-id", payload["parent_run_id"]])
    if payload.get("trigger_note"):
        command.extend(["--trigger-note", payload["trigger_note"]])
    return command


def _update_progress(tenant_id: str, job_id: str, run_version: int, stage: str, progress: int) -> None:
    """把进度写回任务表；任务已取消或版本失效时停止更新。"""
    try:
        with database_connection() as conn:
            job_repo.update_job_stage(conn, tenant_id, job_id, run_version, stage[:200], max(0, min(100, progress)))
    except Exception:
        return


def _cancel_requested(tenant_id: str, job_id: str) -> bool:
    """读取任务的取消标记。"""
    with database_connection() as conn:
        current = job_repo.get_job_by_id(conn, job_id)
    return bool(current and current["cancel_requested"])


def _terminate(process: subprocess.Popen) -> None:
    """终止评测子进程组，先礼后兵。"""
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def _tail_progress(path: Path, tenant_id: str, job_id: str, run_version: int, stop: threading.Event) -> None:
    """尾随进度 JSONL 并回写任务阶段与百分比。"""
    offset = 0
    while not stop.is_set():
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                for raw in handle:
                    offset = handle.tell()
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    stage = str(record.get("stage") or "")
                    progress = int(record.get("progress") or 0)
                    if stage:
                        _update_progress(tenant_id, job_id, run_version, stage, progress)
        except FileNotFoundError:
            pass
        time.sleep(POLL_SECONDS)


def _run_process(command: list[str], *, env: dict[str, str], log_path: Path, tenant_id: str, job_id: str,
                 run_version: int, timeout_seconds: int, progress_file: Path) -> int:
    """运行为器子进程：尾随进度、响应取消与超时。"""
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        stop = threading.Event()
        tail = threading.Thread(target=_tail_progress,
                                args=(progress_file, tenant_id, job_id, run_version, stop), daemon=True)
        tail.start()
        deadline = time.monotonic() + timeout_seconds
        try:
            while process.poll() is None:
                if _cancel_requested(tenant_id, job_id):
                    _terminate(process)
                    raise EvaluationCancelled("评测任务已取消")
                if time.monotonic() >= deadline:
                    _terminate(process)
                    raise EvaluationFailed(f"评测执行超过 {timeout_seconds} 秒未完成")
                time.sleep(POLL_SECONDS)
            return process.returncode
        finally:
            stop.set()


def process_evaluation_job(job: dict) -> None:
    """执行一次评测任务：运行 → 校验 → 入库，并把终态写回任务表。"""
    cfg = load_config()
    job_id = job["id"]
    timeout_seconds = int(getattr(cfg, "eval_timeout_seconds", 3600))
    work_dir = RUNTIME / job_id
    report_dir = work_dir / "report"
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    progress_file = work_dir / "progress.jsonl"
    progress_file.write_text("", encoding="utf-8")
    log_path = work_dir / "runner.log"
    try:
        with database_connection() as conn:
            dataset = evaluation_repo.dataset_row(conn, job["payload"]["dataset_id"])
        if dataset is None:
            raise EvaluationFailed("评测数据集不存在")
        command = _runner_command(job, dataset, report_dir, progress_file)
        _LOG.info("评测任务 %s 开始执行（%s/%s/%s）", job_id, job["payload"]["eval_kind"],
                  job["payload"]["scope"], job["payload"]["mode"])
        _update_progress(job["tenant_id"], job_id, job["run_version"], "启动评测运行器", 3)
        exit_code = _run_process(command, env=_environment(progress_file, report_dir), log_path=log_path,
                                 tenant_id=job["tenant_id"], job_id=job_id, run_version=job["run_version"],
                                 timeout_seconds=timeout_seconds, progress_file=progress_file)
        report_path = _latest_report(report_dir, job["payload"]["eval_kind"])
        _update_progress(job["tenant_id"], job_id, job["run_version"], "校验并入库评测报告", 93)
        publish = subprocess.run(_publish_command(job, report_path),
                                 cwd=str(ROOT), env=_environment(progress_file, report_dir),
                                 capture_output=True, text=True, timeout=600)
        if publish.returncode != 0:
            reason = (publish.stderr or publish.stdout or "").strip().splitlines()[-1:] or ["入库失败"]
            raise EvaluationFailed(f"评测报告入库失败：{reason[0][:200]}")
        result = {}
        for line in reversed((publish.stdout or "").strip().splitlines()):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        with database_connection() as conn:
            job_repo.complete_job(conn, tenant_id=job["tenant_id"], job_id=job_id, run_version=job["run_version"],
                                  state="completed", stage="评测完成",
                                  result={**result, "exit_code": exit_code}, sources=[])
        _LOG.info("评测任务 %s 完成：批次 %s", job_id, result.get("run_id"))
    except EvaluationCancelled:
        with database_connection() as conn:
            job_repo.complete_job(conn, tenant_id=job["tenant_id"], job_id=job_id, run_version=job["run_version"],
                                  state="cancelled", stage="评测已取消", result={"reason": "已取消"}, sources=[])
    except Exception as exc:
        message = str(exc) if isinstance(exc, EvaluationFailed) else f"评测执行失败（{type(exc).__name__}）"
        _LOG.error("评测任务 %s 失败：%s", job_id, type(exc).__name__)
        with database_connection() as conn:
            job_repo.complete_job(conn, tenant_id=job["tenant_id"], job_id=job_id, run_version=job["run_version"],
                                  state="failed", stage=message[:200], result={"error": message[:500]}, sources=[])