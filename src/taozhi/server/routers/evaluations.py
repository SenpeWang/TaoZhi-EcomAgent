"""已认证工作台成员可用的评测中心只读接口。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from taozhi.application.services import evaluation_service as service
from taozhi.schemas.evaluation import (
    EvaluationCaseDetail, EvaluationCasePage, EvaluationComparison, EvaluationMode,
    EvaluationRunDetail, EvaluationRunPage, EvaluationStatus,
)
from taozhi.server.auth import identity


router = APIRouter(prefix="/api/evaluations/runs", tags=["evaluations"])
Identifier = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=EvaluationRunPage)
def runs(owner_id: str | None = Query(None, min_length=1, max_length=128, pattern=r"^[^\x00]+$"),
         mode: EvaluationMode | None = None, gate_passed: bool | None = None,
         cursor: str | None = Query(None, max_length=256), limit: PageSize = 30, p=Depends(identity)):
    return service.list_runs(p, owner_id=owner_id, mode=mode, gate_passed=gate_passed, cursor=cursor, limit=limit)


@router.get("/{run_id}", response_model=EvaluationRunDetail)
def run(run_id: Identifier, p=Depends(identity)):
    return service.get_run(p, run_id)


@router.get("/{run_id}/comparison", response_model=EvaluationComparison)
def comparison(run_id: Identifier, baseline_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,128}$")],
               p=Depends(identity)):
    return service.compare(p, run_id, baseline_id)


@router.get("/{run_id}/cases", response_model=EvaluationCasePage)
def cases(run_id: Identifier, status: EvaluationStatus | None = None,
          cursor: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{1,128}$"),
          limit: PageSize = 30, p=Depends(identity)):
    return service.list_cases(p, run_id, status=status, cursor=cursor, limit=limit)


@router.get("/{run_id}/cases/{case_id}", response_model=EvaluationCaseDetail)
def case(run_id: Identifier, case_id: Identifier, p=Depends(identity)):
    return service.get_case(p, run_id, case_id)
