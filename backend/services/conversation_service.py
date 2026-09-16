"""
services/conversation_service.py — 대화 기록(conversations 컬렉션) 저장·조회·삭제.

문서 구조 (대화 1개 = 문서 1개)
    {
      title: "최근 환율 추세가 어때?",
      messages: [{role, content, timestamp}, ...],   ← 메시지를 배열로 통째 보관
      message_count: 2,                                ← 목록용 요약 필드 (비정규화)
      preview: "최근 20영업일 평균이 직전 대비 ...",
      created_at, updated_at
    }

설계 판단
1) 메시지를 하위 컬렉션이 아니라 배열 필드로 둔다.
   대화를 불러올 때 문서 1개만 읽으면 되고(읽기 1회), 코드도 단순하다.
   대신 문서 크기 한도(1MB)가 있으므로 메시지 수(200)와 길이(4000자)를 제한한다.
2) 목록 조회는 select() 로 messages 를 빼고 가져온다.
   목록에 필요한 message_count·preview 는 저장할 때 미리 계산해 둔다.
3) 메시지 timestamp 는 서버의 파이썬 시계(UTC)로 찍는다.
   Firestore 의 SERVER_TIMESTAMP 는 배열 안에 넣을 수 없기 때문이다.
4) 메시지 추가(append_messages)는 트랜잭션으로 "읽기 → 한도 확인 → 쓰기"를 묶는다.
   ArrayUnion 은 내용이 같은 원소를 하나로 합쳐버리므로 대화 기록에는 쓰지 않는다.
"""

from datetime import datetime, timezone

from firebase_admin import firestore
from google.api_core import exceptions as gexc

from config import get_settings
from database import get_db
from models.schemas import MAX_MESSAGES
from services.errors import BadRequestError, NotFoundError

TITLE_LEN = 20
PREVIEW_LEN = 40
DEFAULT_TITLE = "새 대화"
LIST_FIELDS = ["title", "message_count", "preview", "created_at", "updated_at"]


def _collection():
    return get_db().collection(get_settings().conversation_collection)


def _shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())  # 줄바꿈·연속 공백을 한 칸으로
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _make_title(messages: list[dict]) -> str:
    first_user = next((m["content"] for m in messages if m["role"] == "user"), "")
    return _shorten(first_user, TITLE_LEN) or DEFAULT_TITLE


def _make_preview(messages: list[dict]) -> str:
    return _shorten(messages[-1]["content"], PREVIEW_LEN) if messages else ""


def _normalize(messages: list[dict]) -> list[dict]:
    """저장용으로 정리: 필요한 키만 남기고, 시각이 없으면 지금(UTC), 시간대가 없으면 UTC 로 간주."""
    now = datetime.now(timezone.utc)
    result = []
    for m in messages:
        ts = m.get("timestamp") or now
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        result.append({"role": m["role"], "content": m["content"], "timestamp": ts})
    return result


def _to_list_item(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    return {
        "id": snapshot.id,
        "title": data.get("title") or DEFAULT_TITLE,
        "message_count": data.get("message_count", 0),
        "preview": data.get("preview", ""),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _to_detail(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    return {**_to_list_item(snapshot), "messages": data.get("messages", [])}


# ------------------------------------------------------------------ 조회


def list_conversations(limit: int = 50) -> list[dict]:
    """최근 대화가 위로 오도록 updated_at 내림차순. messages 는 받아오지 않는다."""
    query = (
        _collection()
        .select(LIST_FIELDS)
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    return [_to_list_item(s) for s in query.stream()]


def get_conversation(conversation_id: str) -> dict:
    snapshot = _collection().document(conversation_id).get()
    if not snapshot.exists:
        raise NotFoundError("대화를 찾을 수 없습니다.")
    return _to_detail(snapshot)


# ------------------------------------------------------------------ 생성 · 추가


def create_conversation(messages: list[dict], title: str | None = None) -> dict:
    if not messages:
        raise BadRequestError("저장할 메시지가 없습니다.")
    if len(messages) > MAX_MESSAGES:
        raise BadRequestError(f"대화 1개에는 메시지를 최대 {MAX_MESSAGES}개까지 저장할 수 있습니다.")

    normalized = _normalize(messages)
    ref = _collection().document()  # 자동 ID
    ref.set(
        {
            "title": title or _make_title(normalized),
            "messages": normalized,
            "message_count": len(normalized),
            "preview": _make_preview(normalized),
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )
    return _to_detail(ref.get())


def append_messages(conversation_id: str, messages: list[dict]) -> dict:
    """기존 대화 뒤에 메시지를 이어 붙인다. (Phase 6 챗봇이 사용)"""
    if not messages:
        raise BadRequestError("추가할 메시지가 없습니다.")

    ref = _collection().document(conversation_id)
    new_messages = _normalize(messages)

    @firestore.transactional
    def _append(transaction):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            raise NotFoundError("대화를 찾을 수 없습니다.")
        current = (snapshot.to_dict() or {}).get("messages", [])
        merged = current + new_messages
        if len(merged) > MAX_MESSAGES:
            raise BadRequestError(
                f"대화가 너무 길어졌습니다(최대 {MAX_MESSAGES}개). 새 대화를 시작해 주세요."
            )
        transaction.update(
            ref,
            {
                "messages": merged,
                "message_count": len(merged),
                "preview": _make_preview(merged),
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )

    _append(get_db().transaction())
    return _to_detail(ref.get())


# ------------------------------------------------------------------ 삭제


def delete_conversation(conversation_id: str) -> None:
    ref = _collection().document(conversation_id)
    try:
        ref.delete(option=get_db().write_option(exists=True))
    except gexc.NotFound as exc:
        raise NotFoundError("대화를 찾을 수 없습니다.") from exc
