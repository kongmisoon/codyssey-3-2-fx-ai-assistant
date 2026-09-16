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
from typing import Literal, Optional

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


# ------------------------------------------------------------------ 요약 (GET /api/data/summary)


class SummaryMetrics(BaseModel):
    average: float = Field(..., description="평균 환율")
    median: float = Field(..., description="중앙값")
    max: float = Field(..., description="최고 환율")
    max_date: str = Field(..., description="최고 환율 날짜 (동률이면 가장 이른 날)")
    min: float = Field(..., description="최저 환율")
    min_date: str = Field(..., description="최저 환율 날짜 (동률이면 가장 이른 날)")
    range: float = Field(..., description="변동폭 (최고 - 최저)")
    std: float = Field(..., description="표준편차 (표본, n-1)")
    first_date: str
    first_rate: float = Field(..., description="기간 첫날 환율")
    last_date: str
    last_rate: float = Field(..., description="기간 마지막 날 환율")
    total_change_pct: float = Field(..., description="기간 전체 변화율 (%)")


class DailyMove(BaseModel):
    date: str
    change_pct: float = Field(..., description="전 영업일 대비 변화율 (%)")
    value: float


class DailyValue(BaseModel):
    date: str
    value: float


class MonthlyStat(BaseModel):
    month: str = Field(..., description="YYYY-MM")
    average: float
    min: float
    max: float
    count: int = Field(..., description="해당 월 영업일 수")
    change_pct: Optional[float] = Field(None, description="전월 평균 대비 변화율 (%), 첫 달은 null")


class SummaryResponse(BaseModel):
    """
    데이터 요약 — 프론트 요약 카드와 AI 시스템 프롬프트에 함께 쓰인다.
    환율의 합계(total)는 의미가 없어 제공하지 않고, 대신 변동폭·표준편차·기간 변화율을 제공한다.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "period": "2024-09-05 ~ 2026-09-04",
                    "count": 522,
                    "unit": "KRW/USD",
                    "trend_direction": "down",
                    "trend": "원화 강세 (환율 하락) — 최근 20영업일 평균 1,360.12원, 직전 20영업일 평균 1,380.45원 대비 -1.47%",
                }
            ]
        }
    )

    period: Optional[str] = Field(None, description="'시작일 ~ 종료일'")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    count: int = Field(..., description="레코드 수 (영업일)")
    unit: str
    metrics: Optional[SummaryMetrics] = Field(None, description="데이터가 없으면 null")

    recent_avg: Optional[float] = Field(None, description="최근 N영업일 평균")
    previous_avg: Optional[float] = Field(None, description="그 직전 N영업일 평균")
    change_pct: Optional[float] = Field(None, description="recent_avg 의 previous_avg 대비 변화율 (%)")
    trend_direction: Literal["up", "down", "flat", "insufficient", "none"] = Field(
        ..., description="up=환율 상승(원화 약세), down=환율 하락(원화 강세), flat=보합(±0.5% 이내)"
    )
    trend_window: int = Field(..., description="비교에 쓴 영업일 수 N (기본 20, 데이터가 적으면 줄어듦)")
    trend: str = Field(..., description="사람이 읽는 추세 문장")

    daily_change_std_pct: Optional[float] = Field(None, description="일간 변화율의 표준편차 (%) — 변동성")
    max_daily_rise: Optional[DailyMove] = Field(None, description="하루 최대 상승")
    max_daily_fall: Optional[DailyMove] = Field(None, description="하루 최대 하락")

    recent_days: list[DailyValue] = Field(default_factory=list, description="최근 5영업일")
    monthly: list[MonthlyStat] = Field(default_factory=list, description="월별 통계")


# ------------------------------------------------------------------ 대화 기록 (/api/conversations)

CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"  # Firestore 자동 ID(영숫자 20자)를 포함하는 안전한 범위
MESSAGE_MAX = 4000  # 메시지 1개 최대 글자 수
MAX_MESSAGES = 200  # 대화 1개에 담을 수 있는 최대 메시지 수 (Firestore 문서 1MB 한도 보호)
TITLE_MAX = 100


class ChatMessage(BaseModel):
    """
    대화 메시지 1개.
    role 에 'system' 은 허용하지 않는다 — 시스템 프롬프트는 서버만 만든다.
    클라이언트가 system 메시지를 저장해 두고 나중에 AI 에 섞어 보내는 우회(프롬프트 주입)를 막기 위함.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"] = Field(..., description="user=사용자, assistant=AI")
    content: str = Field(..., min_length=1, max_length=MESSAGE_MAX, description="메시지 내용")
    timestamp: Optional[datetime] = Field(None, description="작성 시각 (생략하면 서버 시각)")


class ConversationCreate(BaseModel):
    """POST /api/conversations 요청 본문"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "title": "최근 환율 추세 질문",
                    "messages": [
                        {"role": "user", "content": "최근 환율 추세가 어때?"},
                        {
                            "role": "assistant",
                            "content": "최근 20영업일 평균이 직전 대비 4.72% 낮아 원화 강세 흐름입니다.",
                        },
                    ],
                }
            ]
        },
    )

    title: Optional[str] = Field(
        None, max_length=TITLE_MAX, description="대화 제목 (생략하면 첫 질문 앞 20자)"
    )
    messages: list[ChatMessage] = Field(
        ..., min_length=1, max_length=MAX_MESSAGES, description="메시지 목록 (1~200개)"
    )

    @field_validator("title")
    @classmethod
    def _blank_title_to_none(cls, v: Optional[str]) -> Optional[str]:
        return v or None  # 공백만 보낸 제목은 자동 제목으로 대체


class ConversationListItem(BaseModel):
    """
    대화 목록의 한 줄. messages 는 포함하지 않는다.
    목록 화면에는 제목·시각·미리보기만 필요하고, 대화가 쌓일수록 전체 메시지를 내려보내면
    응답이 불필요하게 커지기 때문이다. 전체 메시지는 GET /api/conversations/{id} 로 받는다.
    """

    id: str
    title: str
    message_count: int = Field(..., description="메시지 개수")
    preview: str = Field(..., description="마지막 메시지 앞부분 (최대 40자)")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConversationDetail(ConversationListItem):
    """대화 1개 전체 (messages 포함)"""

    messages: list[ChatMessage]


# ------------------------------------------------------------------ AI 채팅 (/api/chat)

CHAT_MESSAGE_MAX = 2000


class ChatRequest(BaseModel):
    """POST /api/chat 요청 본문"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {"message": "최근 환율 추세가 어때?"},
                {"message": "가장 환율이 높았던 날은?", "conversation_id": "이전 응답의 conversation_id"},
            ]
        },
    )

    message: str = Field(..., min_length=1, max_length=CHAT_MESSAGE_MAX, description="사용자 질문")
    conversation_id: Optional[str] = Field(
        None,
        pattern=CONVERSATION_ID_PATTERN,
        description="이어서 대화할 때 이전 응답의 conversation_id. 생략하면 새 대화를 만든다.",
    )

    @field_validator("conversation_id", mode="before")
    @classmethod
    def _blank_id_to_none(cls, v):
        return v or None  # 프론트가 빈 문자열을 보내도 '새 대화'로 처리


class ChatContext(BaseModel):
    """이번 답변에 주입된 데이터 요약의 핵심 — 화면에 '어떤 데이터를 근거로 답했는지' 표시할 때 사용"""

    period: Optional[str]
    count: int
    trend_direction: str
    trend: str
    history_messages_used: int = Field(..., description="AI 에 함께 보낸 과거 메시지 수 (최대 20)")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="AI 답변")
    conversation_id: str = Field(..., description="저장된 대화 ID — 다음 질문에 그대로 보내면 이어서 대화")
    title: str
    message_count: int
    context: ChatContext


class SystemPromptResponse(BaseModel):
    """현재 데이터 기준으로 AI 에 주입되는 시스템 프롬프트 (학습·디버깅용)"""

    system_prompt: str
    characters: int
    period: Optional[str]
    count: int
