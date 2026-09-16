"""
routers/conversations.py — /api/conversations 엔드포인트.

"대화 불러오기" 방식: 과제 선택지 중 (A) 를 택했다.
- GET /api/conversations       : 목록만 (messages 미포함 — 제목·시각·개수·미리보기)
- GET /api/conversations/{id}  : 선택한 대화의 전체 messages
목록에 messages 를 넣지 않는 이유는 대화가 쌓일수록 목록 응답이 불필요하게 커지기 때문이다.
"""

from fastapi import APIRouter, Path, Query, status

from models.schemas import (
    CONVERSATION_ID_PATTERN,
    ConversationCreate,
    ConversationDetail,
    ConversationListItem,
    MessageResponse,
)
from services import conversation_service

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# ID 형식을 제한해 두면 'a/b' 같은 값으로 다른 경로의 문서를 가리키는 일을 원천 차단할 수 있다.
ConversationId = Path(
    ...,
    pattern=CONVERSATION_ID_PATTERN,
    description="대화 ID (목록 조회 결과의 id)",
)


@router.post(
    "",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="대화 저장",
)
def create_conversation(payload: ConversationCreate):
    messages = [m.model_dump() for m in payload.messages]
    return conversation_service.create_conversation(messages, title=payload.title)


@router.get(
    "",
    response_model=list[ConversationListItem],
    summary="대화 목록 조회 (messages 미포함)",
    description="최근에 갱신된 대화가 먼저 옵니다. 전체 메시지는 GET /api/conversations/{id} 로 조회하세요.",
)
def list_conversations(
    limit: int = Query(50, ge=1, le=200, description="최대 건수 (1~200)"),
):
    return conversation_service.list_conversations(limit=limit)


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="대화 불러오기 (전체 messages)",
    responses={404: {"description": "대화가 없음"}},
)
def get_conversation(conversation_id: str = ConversationId):
    return conversation_service.get_conversation(conversation_id)


@router.delete(
    "/{conversation_id}",
    response_model=MessageResponse,
    summary="대화 삭제",
    responses={404: {"description": "대화가 없음"}},
)
def delete_conversation(conversation_id: str = ConversationId):
    conversation_service.delete_conversation(conversation_id)
    return {"message": "삭제되었습니다.", "id": conversation_id}
