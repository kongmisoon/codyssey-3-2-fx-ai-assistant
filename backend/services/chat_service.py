"""
services/chat_service.py — AI 채팅의 전체 흐름을 조율한다.

POST /api/chat 한 번에 일어나는 일
  0) (이어서 하는 대화면) 기존 대화를 불러온다
     - 대화가 없으면 404, 메시지 한도가 꽉 찼으면 400 → AI 를 부르기 전에 끝낸다(무료 사용량 절약)
  1) 데이터 요약 계산      analysis_service.compute_summary(data_service.get_all_records())
                           → GET /api/data/summary 와 같은 함수. HTTP 로 자기 자신을 다시 부르지 않는다.
  2) 시스템 프롬프트 조립  prompt_builder.build_system_prompt(summary)   ← 컨텍스트 주입
  3) AI 호출               ai_service.generate_reply(system_prompt, 최근 대화 + 새 질문)
  4) 대화 자동 저장        새 대화면 생성, 기존 대화면 이어 붙이기

왜 AI 호출 "뒤에" 저장하는가?
AI 가 실패(쿼터 초과·타임아웃)했을 때 질문만 저장되고 답이 없는 반쪽짜리 대화가 남지 않게 하기 위해서다.
질문과 답을 한 쌍으로만 저장한다.
"""

from models.schemas import MAX_MESSAGES
from services import analysis_service, conversation_service, data_service
from services.ai_service import generate_reply
from services.errors import BadRequestError
from services.prompt_builder import build_system_prompt

# AI 에게 함께 보내는 과거 대화 길이. 10턴 = 질문 10개 + 답 10개.
# 길수록 문맥을 잘 기억하지만 토큰(비용·속도)이 늘어난다.
HISTORY_TURNS = 10
HISTORY_MESSAGES = HISTORY_TURNS * 2


def current_system_prompt(force_refresh: bool = False) -> tuple[str, dict]:
    """지금 데이터 기준의 시스템 프롬프트와, 그 근거가 된 요약을 함께 돌려준다."""
    summary = analysis_service.compute_summary(data_service.get_all_records(force_refresh=force_refresh))
    return build_system_prompt(summary), summary


def chat(message: str, conversation_id: str | None = None) -> dict:
    # 0) 기존 대화 확인 — AI 호출 전에 실패할 수 있는 것부터 확인한다
    history: list[dict] = []
    if conversation_id:
        conversation = conversation_service.get_conversation(conversation_id)  # 없으면 NotFoundError(404)
        if conversation["message_count"] + 2 > MAX_MESSAGES:
            raise BadRequestError(
                f"대화가 너무 길어졌습니다(최대 {MAX_MESSAGES}개). 새 대화를 시작해 주세요."
            )
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in conversation["messages"][-HISTORY_MESSAGES:]
        ]

    # 1) 요약 → 2) 시스템 프롬프트
    system_prompt, summary = current_system_prompt()

    # 3) AI 호출 (실패하면 AIServiceError 가 그대로 올라가고, 아무것도 저장되지 않는다)
    reply = generate_reply(system_prompt, history + [{"role": "user", "content": message}])

    # 4) 질문·답을 한 쌍으로 저장
    pair = [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
    if conversation_id:
        saved = conversation_service.append_messages(conversation_id, pair)
    else:
        saved = conversation_service.create_conversation(pair)

    return {
        "reply": reply,
        "conversation_id": saved["id"],
        "title": saved["title"],
        "message_count": saved["message_count"],
        "context": {
            "period": summary.get("period"),
            "count": summary.get("count", 0),
            "trend_direction": summary.get("trend_direction"),
            "trend": summary.get("trend"),
            "history_messages_used": len(history),
        },
    }
