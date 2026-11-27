"""评测中心接口：数据集版本、算法版本、基线、批次、指标、审计与评测任务。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from taozhi.application.services import evaluation_service
from taozhi.schemas.evaluation import (
    CreateEvalProfileInput, EvaluationAuditPage, EvaluationBaselinePage, EvaluationCaseDetail,
    EvaluationCasePage, EvaluationComparison, EvaluationDatasetDetail, EvaluationDatasetPage,
    EvaluationDatasetRaw, EvaluationMetricCatalog, EvaluationRunSnapshotPage,
    EvaluationJob, EvaluationKind, EvaluationLayers, EvaluationMode, EvaluationProfile,
    EvaluationProfilePage, EvaluationRunDetail, EvaluationRunPage, EvaluationScope, EvaluationStatus,
    EvaluationTrends, SetBaselineInput, TriggerEvaluationInput, TriggerReceipt,
)
from taozhi.server.auth import identity


router = APIRouter(prefix="/api/evaluations/runs", tags=["evaluations"])
catalog = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
jobs = APIRouter(prefix="/api/evaluations/jobs", tags=["evaluations"])
metrics = APIRouter(prefix="/api/evaluations/metrics", tags=["evaluations"])
Identifier = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
CaseIdentifier = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
PageSize = Annotated[int, Query(ge=1, le=100)]
Pattern = r"^[A-Za-z0-9_.-]{1,128}$"


@router.get("", response_model=EvaluationRunPage)
def runs(owner_id: str | None = Query(None, min_length=1, max_length=128, pattern=r"^[^\x00]+$"),
         eval_kind: EvaluationKind | None = None, mode: EvaluationMode | None = None,
         scope: EvaluationScope | None = None, gate_passed: bool | None = None,
         dataset_id: str | None = Query(None, pattern=Pattern),
         dataset_version: str | None = Query(None, pattern=Pattern),
         profile_id: str | None = Query(None, pattern=Pattern),
         algorithm_version: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,64}$"),
         baseline_only: bool = False,
         cursor: str | None = Query(None, max_length=256), limit: PageSize = 30, p=Depends(identity)):
    """按可见范围筛选评测批次并游标分页。"""
    return evaluation_service.list_runs(p, owner_id=owner_id, eval_kind=eval_kind, mode=mode, scope=scope,
                                        gate_passed=gate_passed, dataset_id=dataset_id,
                                        dataset_version=dataset_version, profile_id=profile_id,
                                        algorithm_version=algorithm_version, baseline_only=baseline_only,
                                        cursor=cursor, limit=limit)


@catalog.get("/datasets", response_model=EvaluationDatasetPage)
def datasets(eval_kind: EvaluationKind | None = None, version: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,32}$"),
             include_archived: bool = False, p=Depends(identity)):
    """列出数据集版本及批次聚合指标。"""
    return evaluation_service.list_datasets(p, eval_kind=eval_kind, version=version,
                                            include_archived=include_archived)


@catalog.get("/datasets/{dataset_id}/raw", response_model=EvaluationDatasetRaw)
def dataset_raw(dataset_id: Identifier, p=Depends(identity)):
    """返回数据集的原始登记内容与全部用例。"""
    return evaluation_service.get_dataset_raw(p, dataset_id)


@catalog.get("/datasets/{dataset_id}/files")
def dataset_file(dataset_id: Identifier, path: str = Query(..., max_length=256), p=Depends(identity)):
    """下载数据集登记清单内的原始文件并附校验摘要头。"""
    record = evaluation_service.get_dataset_file(p, dataset_id, path)
    media_type = {
        ".json": "application/json", ".jsonl": "application/x-ndjson",
        ".md": "text/markdown", ".yaml": "text/yaml", ".yml": "text/yaml",
    }.get("." + record["filename"].rsplit(".", 1)[-1], "application/octet-stream")
    return Response(
        content=record["payload"],
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{record["filename"]}"',
            "X-Content-Sha256": record["sha256"],
        },
    )


@catalog.get("/datasets/{dataset_id}", response_model=EvaluationDatasetDetail)
def dataset_detail(dataset_id: Identifier, cursor: str | None = Query(None, max_length=16),
                   limit: Annotated[int, Query(ge=1, le=200)] = 50,
                   suite: str | None = Query(None, max_length=64), p=Depends(identity)):
    """读取数据集版本详情与用例分页。"""
    return evaluation_service.get_dataset(p, dataset_id, cursor=cursor, limit=limit, suite=suite)


@catalog.get("/profiles", response_model=EvaluationProfilePage)
def profiles(eval_kind: EvaluationKind | None = None,
             dataset_version: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,32}$"),
             p=Depends(identity)):
    """列出算法版本及其批次聚合指标。"""
    return evaluation_service.list_profiles(p, eval_kind=eval_kind, dataset_version=dataset_version)


@catalog.post("/profiles", response_model=EvaluationProfile, status_code=201)
def create_profile(body: CreateEvalProfileInput, p=Depends(identity)):
    """登记不可变算法版本。"""
    return evaluation_service.create_profile(p, body.model_dump())


@catalog.get("/baselines", response_model=EvaluationBaselinePage)
def baselines(eval_kind: EvaluationKind | None = None,
              dataset_version: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,32}$"),
              include_history: bool = True, p=Depends(identity)):
    """列出 active 与历史基线。"""
    return evaluation_service.list_baselines(p, eval_kind=eval_kind, dataset_version=dataset_version,
                                             include_history=include_history)


@catalog.post("/baselines", status_code=201)
def set_baseline(body: SetBaselineInput, p=Depends(identity)):
    """把指定批次设为该口径的 active 基线。"""
    return evaluation_service.set_baseline(p, body.run_id, body.note)


@catalog.get("/audit-logs", response_model=EvaluationAuditPage)
def audit_logs(action: str | None = Query(None, pattern=r"^[a-z_]{1,32}$"),
               cursor: str | None = Query(None, max_length=256), limit: PageSize = 30, p=Depends(identity)):
    """读取评测写入审计留痕。"""
    return evaluation_service.list_audit_logs(p, action=action, cursor=cursor, limit=limit)


@catalog.post("/runs/trigger", response_model=TriggerReceipt, status_code=202)
def trigger_run(body: TriggerEvaluationInput, p=Depends(identity)):
    """触发一次真实评测任务。"""
    return evaluation_service.trigger(p, body.model_dump())


@metrics.get("/catalog", response_model=EvaluationMetricCatalog)
def metric_catalog(p=Depends(identity)):
    """返回完整指标目录。"""
    return evaluation_service.catalog(p)


@metrics.get("/trends", response_model=EvaluationTrends)
def trend_metrics(eval_kind: EvaluationKind | None = None,
                  dataset_version: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,32}$"),
                  scope: EvaluationScope | None = None, mode: EvaluationMode | None = None,
                  limit: Annotated[int, Query(ge=1, le=200)] = 30, p=Depends(identity)):
    """返回批次主指标趋势与基线对照。"""
    return evaluation_service.trends(p, eval_kind=eval_kind, dataset_version=dataset_version, scope=scope,
                                     mode=mode, limit=limit)


@router.get("/{run_id}", response_model=EvaluationRunDetail)
def run(run_id: Identifier, p=Depends(identity)):
    """读取批次详情与基线对比。"""
    return evaluation_service.get_run(p, run_id)


@router.get("/{run_id}/layers", response_model=EvaluationLayers)
def run_layers(run_id: Identifier, p=Depends(identity)):
    """返回 RAG 批次的分层指标。"""
    return evaluation_service.layers(p, run_id)


@router.get("/{run_id}/report")
def run_report(run_id: Identifier, p=Depends(identity)):
    """下载批次原始报告并附校验摘要头。"""
    report = evaluation_service.get_run_report(p, run_id)
    return Response(
        content=report["payload"],
        media_type=report["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="eval-report-{run_id}.json"',
            "X-Report-Sha256": report["sha256"],
        },
    )


@router.get("/{run_id}/comparison", response_model=EvaluationComparison)
def comparison(run_id: Identifier, baseline_id: Annotated[str, Query(pattern=Pattern)], p=Depends(identity)):
    """按逐例配对比较两个批次。"""
    return evaluation_service.compare(p, run_id, baseline_id)


@router.get("/{run_id}/dataset-snapshot", response_model=EvaluationRunSnapshotPage)
def run_dataset_snapshot(run_id: Identifier, cursor: str | None = Query(None, pattern=Pattern),
                         limit: PageSize = 50, p=Depends(identity)):
    """读取批次关联的数据集内容快照。"""
    return evaluation_service.get_run_snapshot(p, run_id, cursor=cursor, limit=limit)


@router.get("/{run_id}/cases", response_model=EvaluationCasePage)
def cases(run_id: Identifier, status: EvaluationStatus | None = None,
          suite: str | None = Query(None, max_length=32),
          cursor: str | None = Query(None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$"),
          limit: PageSize = 30, p=Depends(identity)):
    """按状态与套件分页读取批次逐例判定。"""
    return evaluation_service.list_cases(p, run_id, status=status, suite=suite, cursor=cursor, limit=limit)


@router.get("/{run_id}/cases/{case_id}", response_model=EvaluationCaseDetail)
def case(run_id: Identifier, case_id: CaseIdentifier, p=Depends(identity)):
    """读取单个用例的判定详情。"""
    return evaluation_service.get_case(p, run_id, case_id)


@jobs.get("/{job_id}", response_model=EvaluationJob)
def job(job_id: Identifier, p=Depends(identity)):
    """读取评测任务状态与进度。"""
    return evaluation_service.job_status(p, job_id)


@jobs.post("/{job_id}/cancel")
def cancel(job_id: Identifier, p=Depends(identity)):
    """请求取消评测任务。"""
    return evaluation_service.cancel_job(p, job_id)
