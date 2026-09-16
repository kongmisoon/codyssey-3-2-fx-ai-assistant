"""
routers/chat.py — /api/chat 엔드포인트.

흐름의 실제 조율은 services/chat_service.py 가 한다.
AI 실패(AIServiceError)는 main.py 의 전역 핸들러가 429/504 등으로 변환한다.
"""

from fastapi import APIRouter, Query

from models.schemas import ChatRequest, ChatResponse, SystemPromptResponse
from services import chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="AI 채팅 (데이터 요약 주입 + 대화 자동 저장)",
    description=(
        "1) 환율 데이터 요약 계산 → 2) 요약을 시스템 프롬프트에 주입 → 3) AI 호출 → "
        "4) 질문·답변을 conversations 에 자동 저장.\n\n"
        "처음 질문할 때는 `conversation_id` 를 생략하고, 응답의 `conversation_id` 를 다음 요청에 넣으면 "
        "최근 10턴(20개 메시지)을 기억한 채 이어서 대화한다.\n\n"
        "AI 호출이 실패하면 아무것도 저장하지 않는다."
    ),
    responses={
        400: {"description": "대화가 메시지 한도(200개)에 도달"},
        404: {"description": "conversation_id 에 해당하는 대화가 없음"},
        429: {"description": "AI 사용량 한도 초과"},
        504: {"description": "AI 응답 시간 초과"},
    },
)
def chat(payload: ChatRequest):
    return chat_service.chat(payload.message, payload.conversation_id)


@router.get(
    "/system-prompt",
    response_model=SystemPromptResponse,
    summary="현재 주입되는 시스템 프롬프트 보기",
    description=(
        "컨텍스트 주입이 실제로 어떤 글을 AI 에 보내는지 확인하는 용도. "
        "데이터 요약만 담겨 있고 API 키 등 비밀값은 포함되지 않는다."
    ),
)
def get_system_prompt(
    refresh: bool = Query(False, description="캐시를 무시하고 DB 에서 다시 읽기"),
):
    prompt, summary = chat_service.current_system_prompt(force_refresh=refresh)
    return {
        "system_prompt": prompt,
        "characters": len(prompt),
        "period": summary.get("period"),
        "count": summary.get("count", 0),
    }
