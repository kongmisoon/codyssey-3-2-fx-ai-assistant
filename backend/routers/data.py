"""
routers/data.py — /api/data 엔드포인트.

라우터가 하는 일은 세 가지뿐이다.
1) URL·쿼리·본문을 받는다 (검증은 Pydantic / Query / Path 가 이미 끝낸 상태)
2) 서비스 함수를 호출한다
3) 결과를 응답 모델에 맞춰 돌려준다

예외 처리는 main.py 의 전역 핸들러가 담당한다.
서비스가 NotFoundError 를 던지면 404, ConflictError 면 409 로 자동 변환된다.

⚠ 라우트 선언 순서
GET /api/data/summary (Phase 4) 는 /api/data/{record_id} 계열보다 위에 선언해야 한다.
FastAPI 는 위에서부터 매칭하므로, 아래에 두면 "summary" 가 {record_id} 로 잡힐 수 있다.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, status

from models.schemas import (
    DATE_PATTERN,
    FxDataCreate,
    FxDataResponse,
    FxDataUpdate,
    MessageResponse,
)
from services import data_service

router = APIRouter(prefix="/api/data", tags=["data"])

RecordId = Path(
    ...,
    pattern=DATE_PATTERN,
    description="문서 ID (= 날짜, YYYY-MM-DD)",
    examples=["2026-09-04"],
)


@router.post(
    "",
    response_model=FxDataResponse,
    status_code=status.HTTP_201_CREATED,
    summary="환율 데이터 추가",
    responses={409: {"description": "같은 날짜의 데이터가 이미 존재"}},
)
def create_data(payload: FxDataCreate):
    return data_service.create_record(payload.model_dump())


@router.get(
    "",
    response_model=list[FxDataResponse],
    summary="환율 데이터 목록 조회",
)
def list_data(
    start_date: str | None = Query(None, pattern=DATE_PATTERN, description="시작일 (포함)"),
    end_date: str | None = Query(None, pattern=DATE_PATTERN, description="종료일 (포함)"),
    limit: int = Query(100, ge=1, le=1000, description="최대 건수 (1~1000)"),
    order: Literal["asc", "desc"] = Query("asc", description="날짜 정렬 (asc=과거순, desc=최신순)"),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date 가 end_date 보다 늦을 수 없습니다.")
    return data_service.list_records(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        descending=(order == "desc"),
    )


# ---- GET /api/data/summary 는 Phase 4 에서 이 위치(= {record_id} 라우트보다 위)에 추가한다 ----


@router.put(
    "/{record_id}",
    response_model=FxDataResponse,
    summary="환율 데이터 수정 (부분 수정)",
    responses={
        404: {"description": "해당 날짜의 데이터가 없음"},
        409: {"description": "변경하려는 날짜에 이미 데이터가 있음"},
    },
)
def update_data(payload: FxDataUpdate, record_id: str = RecordId):
    return data_service.update_record(record_id, payload.to_changes())


@router.delete(
    "/{record_id}",
    response_model=MessageResponse,
    summary="환율 데이터 삭제",
    responses={404: {"description": "해당 날짜의 데이터가 없음"}},
)
def delete_data(record_id: str = RecordId):
    data_service.delete_record(record_id)
    return {"message": "삭제되었습니다.", "id": record_id}
