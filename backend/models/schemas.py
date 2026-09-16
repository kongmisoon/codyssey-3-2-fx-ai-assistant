"""
models/schemas.py — API 가 주고받는 데이터의 "모양"과 "규칙"을 Pydantic 으로 정의한다.

왜 Pydantic 인가?
1) 검증을 선언형으로 쓸 수 있다. "value 는 500~5000 사이 숫자" 를 if 문 대신 Field(ge=, le=) 한 줄로.
2) 잘못된 요청은 라우터 함수에 들어오기도 전에 FastAPI 가 422 로 돌려보낸다.
   → 서비스/DB 코드는 "이미 검증된 데이터"만 다루면 되므로 단순해진다.
3) 같은 정의로 Swagger(/docs) 문서와 예시값이 자동 생성된다.

검증 규칙 (원/달러 환율 기준)
- date  : YYYY-MM-DD 형식 + 실제로 존재하는 날짜 (2026-02-30 거부)
- value : 500 ~ 5000 원, NaN/무한대 거부, 소수점 2자리로 반올림
- memo  : 0 ~ 200자, 앞뒤 공백 제거
- 정의되지 않은 필드가 오면 거부 (오타로 인한 조용한 무시 방지)
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
RATE_MIN = 500
RATE_MAX = 5000
MEMO_MAX = 200


def _ensure_real_date(value: str) -> str:
    """정규식은 모양만 본다. 2026-13-45 같은 값은 여기서 걸러낸다."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("존재하지 않는 날짜입니다. YYYY-MM-DD 형식의 실제 날짜를 입력하세요.") from exc
    return value


def _memo_none_to_empty(value):
    """memo 에 null 이 오면 빈 문자열로 통일한다(DB 에 None/'' 가 섞이지 않게)."""
    return "" if value is None else value


# ------------------------------------------------------------------ 요청


class FxDataCreate(BaseModel):
    """POST /api/data 요청 본문"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [{"date": "2026-09-07", "value": 1352.8, "memo": "FOMC 금리 동결 발표"}]
        },
    )

    date: str = Field(..., pattern=DATE_PATTERN, description="기준일 (YYYY-MM-DD)")
    value: float = Field(
        ..., ge=RATE_MIN, le=RATE_MAX, allow_inf_nan=False, description="원/달러 환율 종가 (원)"
    )
    memo: str = Field("", max_length=MEMO_MAX, description="메모 (선택, 최대 200자)")

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: str) -> str:
        return _ensure_real_date(v)

    @field_validator("value")
    @classmethod
    def _round_value(cls, v: float) -> float:
        return round(v, 2)

    @field_validator("memo", mode="before")
    @classmethod
    def _memo_default(cls, v):
        return _memo_none_to_empty(v)


class FxDataUpdate(BaseModel):
    """
    PUT /api/data/{id} 요청 본문 — 보낸 필드만 수정한다(부분 수정).
    date 를 보내면 문서가 새 날짜로 '이동'한다(문서 ID = 날짜이므로).
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{"value": 1360.25, "memo": "수정: 종가 정정"}]},
    )

    date: Optional[str] = Field(None, pattern=DATE_PATTERN, description="변경할 날짜 (선택)")
    value: Optional[float] = Field(
        None, ge=RATE_MIN, le=RATE_MAX, allow_inf_nan=False, description="변경할 환율 (선택)"
    )
    memo: Optional[str] = Field(None, max_length=MEMO_MAX, description="변경할 메모 (선택)")

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _ensure_real_date(v)

    @field_validator("value")
    @classmethod
    def _round_value(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None else round(v, 2)

    @model_validator(mode="after")
    def _at_least_one_field(self):
        # 명시적으로 보낸 필드 중 null 이 아닌 것이 하나는 있어야 한다.
        # (memo 는 null → '' 로 비우기를 허용하므로 따로 취급)
        sent = self.model_fields_set
        meaningful = {f for f in sent if f == "memo" or getattr(self, f) is not None}
        if not meaningful:
            raise ValueError("수정할 필드(date, value, memo) 중 하나 이상을 보내야 합니다.")
        return self

    def to_changes(self) -> dict:
        """DB 에 반영할 변경분만 dict 로 꺼낸다."""
        changes = {}
        for field in self.model_fields_set:
            val = getattr(self, field)
            if field == "memo":
                changes["memo"] = _memo_none_to_empty(val)
            elif val is not None:
                changes[field] = val
        return changes


# ------------------------------------------------------------------ 응답


class FxDataResponse(BaseModel):
    """환율 레코드 1건"""

    id: str = Field(..., description="문서 ID (= 날짜)")
    date: str
    value: float
    memo: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MessageResponse(BaseModel):
    """삭제 등 본문 없이 결과만 알려주는 응답"""

    message: str
    id: str
