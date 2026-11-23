"""已认证工作台成员可用的评测中心只读接口。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from taozhi.application.services import evaluation_service
from taozhi.schemas.evaluation import (
    CreateEvalProfileInput, EvaluationCaseDetail, EvaluationCasePage, EvaluationComparison,
    EvaluationDatasetDetail, EvaluationDatasetPage, EvaluationKind, EvaluationMode,
    EvaluationProfile, EvaluationProfilePage, EvaluationRunDetail, EvaluationRunPage,
    EvaluationStatus, EvaluationTriggerReceipt, TriggerEvaluationInput,
)
from taozhi.server.auth import identity


router = APIRouter(prefix="/api/evaluations/runs", tags=["evaluations"])
catalog = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
Identifier = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=EvaluationRunPage)
def runs(owner_id: str | None = Query(None, min_length=1, max_length=128, pattern=r"^[^\x00]+$"),
         eval_kind: EvaluationKind | None = None,
         mode: EvaluationMode | None = None, gate_passed: bool | None = None,
         dataset_id: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,128}$"),
         profile_id: str | None = Query(None, pattern=r"^[A-Za-z0-9_.-]{1,128}$"),
         cursor: str | None = Query(None, max_length=256), limit: PageSize = 30, p=Depends(identity)):
    return evaluation_service.list_runs(p, owner_id=owner_id, eval_kind=eval_kind, mode=mode, gate_passed=gate_passed,
                                        dataset_id=dataset_id, profile_id=profile_id, cursor=cursor, limit=limit)


@catalog.get("/datasets", response_model=EvaluationDatasetPage)
def datasets(eval_kind: EvaluationKind | None = None, p=Depends(identity)):
    return evaluation_service.list_datasets(p, eval_kind=eval_kind)


@catalog.get("/datasets/{dataset_id}", response_model=EvaluationDatasetDetail)
def dataset_detail(dataset_id: Identifier, p=Depends(identity)):
    return evaluation_service.get_dataset(p, dataset_id)


@catalog.get("/profiles", response_model=EvaluationProfilePage)
def profiles(eval_kind: EvaluationKind | None = None, p=Depends(identity)):
    return evaluation_service.list_profiles(p, eval_kind=eval_kind)


@catalog.post("/profiles", response_model=EvaluationProfile, status_code=201)
def create_profile(body: CreateEvalProfileInput, p=Depends(identity)):
    return evaluation_service.create_profile(p, body.model_dump())


@catalog.post("/runs/trigger", response_model=EvaluationTriggerReceipt, status_code=202)
def trigger_run(body: TriggerEvaluationInput, p=Depends(identity)):
    return evaluation_service.trigger_evaluation(p, body.model_dump())


@router.get("/{run_id}", response_model=EvaluationRunDetail)
def run(run_id: Identifier, p=Depends(identity)):
    return evaluation_service.get_run(p, run_id)


@router.get("/{run_id}/comparison", response_model=EvaluationComparison)
def comparison(run_id: Identifier, baseline_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,128}$")],
               p=Depends(identity)):
    return evaluation_service.compare(p, run_id, baseline_id)


@router.get("/{run_id}/cases", response_model=EvaluationCasePage)
def cases(run_id: Identifier, status: EvaluationStatus | None = None,
          cursor: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{1,128}$"),
          limit: PageSize = 30, p=Depends(identity)):
    return evaluation_service.list_cases(p, run_id, status=status, cursor=cursor, limit=limit)


@router.get("/{run_id}/cases/{case_id}", response_model=EvaluationCaseDetail)
def case(run_id: Identifier, case_id: Identifier, p=Depends(identity)):
    return evaluation_service.get_case(p, run_id, case_id)
