"""
services/data_service.py — 환율 데이터(data 컬렉션)의 저장·조회·수정·삭제 로직.

왜 라우터와 분리하는가?
- 라우터는 "HTTP 요청을 받아 응답을 돌려주는 일"만 한다.
- 이 파일은 "Firestore 에 어떻게 저장하는가"만 안다. HTTP 는 모른다.
  → Phase 4 요약 계산, Phase 6 챗봇, 보너스의 Function Calling 도구가
    HTTP 를 거치지 않고 이 함수들을 그대로 재사용할 수 있다.

설계 결정: 문서 ID = 날짜(YYYY-MM-DD)
- 환율은 하루에 한 값만 존재한다. 날짜를 ID 로 쓰면 DB 차원에서 중복이 불가능해진다.
- 생성은 create() 를 쓴다. create 는 "이미 있으면 실패"를 서버가 원자적으로 보장하므로
  "있는지 읽어보고 → 없으면 쓰기" 사이에 다른 요청이 끼어드는 경쟁 상태가 생기지 않는다.
- 날짜를 바꾸는 수정은 "새 ID 로 생성 + 옛 ID 삭제"를 트랜잭션으로 묶어 한 번에 처리한다.
"""

import threading
import time

from firebase_admin import firestore
from google.api_core import exceptions as gexc
from google.cloud.firestore_v1 import FieldFilter

from config import get_settings
from database import get_db
from services.errors import ConflictError, NotFoundError

# 전체 레코드 캐시 — 요약 계산용
# 요약은 522건 전체를 읽어야 하고, 챗봇은 메시지마다 요약을 쓴다.
# 매번 읽으면 대화 1번에 Firestore 읽기 522회 → 무료 한도(하루 5만 회)로 약 95번이면 소진된다.
# 그래서 한 번 읽은 목록을 메모리에 두고, 이 서버를 통한 쓰기(생성·수정·삭제)가 일어나면 즉시 비운다.
# Firebase 콘솔에서 직접 고친 경우를 대비해 10분이 지나면 자동으로 다시 읽는다.
CACHE_TTL_SECONDS = 600
_cache_lock = threading.Lock()
_cache: dict = {"records": None, "loaded_at": 0.0, "generation": 0}


def _collection():
    return get_db().collection(get_settings().data_collection)


def invalidate_cache() -> None:
    """쓰기 직후 호출. generation 을 올려서, 진행 중이던 읽기가 옛 데이터를 캐시에 넣지 못하게 한다."""
    with _cache_lock:
        _cache["records"] = None
        _cache["generation"] += 1


def get_all_records(force_refresh: bool = False) -> list[dict]:
    """요약 계산용 전체 레코드 [{date, value}, ...]. 캐시가 유효하면 DB 를 읽지 않는다."""
    with _cache_lock:
        cached = _cache["records"]
        is_fresh = cached is not None and time.monotonic() - _cache["loaded_at"] < CACHE_TTL_SECONDS
        if is_fresh and not force_refresh:
            return list(cached)
        generation = _cache["generation"]

    # 필요한 두 필드만 받아온다 (전송량 절약 — 읽기 횟수 과금은 문서 수 기준이라 동일)
    records = [
        {"date": snap.get("date"), "value": snap.get("value")}
        for snap in _collection().select(["date", "value"]).stream()
    ]

    with _cache_lock:
        # 읽는 도중 쓰기가 있었다면(generation 변경) 방금 읽은 목록은 이미 낡았을 수 있으니 저장하지 않는다.
        if _cache["generation"] == generation:
            _cache["records"] = records
            _cache["loaded_at"] = time.monotonic()
    return list(records)


def _to_record(snapshot) -> dict:
    """Firestore 문서 스냅샷 → API 응답용 dict"""
    data = snapshot.to_dict() or {}
    return {
        "id": snapshot.id,
        "date": data.get("date", snapshot.id),
        "value": data.get("value"),
        "memo": data.get("memo", ""),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


# ------------------------------------------------------------------ 조회


def list_records(
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    descending: bool = False,
) -> list[dict]:
    """
    날짜 범위로 조회한다.
    date 는 'YYYY-MM-DD' 문자열이라 사전순 정렬 = 날짜순 정렬이 성립한다.
    그래서 문자열 비교(>=, <=)로 기간 필터가 가능하다.
    """
    query = _collection()
    if start_date:
        query = query.where(filter=FieldFilter("date", ">=", start_date))
    if end_date:
        query = query.where(filter=FieldFilter("date", "<=", end_date))

    direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
    query = query.order_by("date", direction=direction).limit(limit)
    return [_to_record(s) for s in query.stream()]


# ------------------------------------------------------------------ 생성


def create_record(data: dict) -> dict:
    ref = _collection().document(data["date"])
    try:
        ref.create(
            {
                **data,
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )
    except gexc.Conflict as exc:  # AlreadyExists 는 Conflict 의 하위 클래스
        raise ConflictError(
            f"{data['date']} 데이터가 이미 있습니다. 수정하려면 PUT /api/data/{data['date']} 를 사용하세요."
        ) from exc
    invalidate_cache()
    # SERVER_TIMESTAMP 는 서버에서 채워지므로 실제 값을 보려면 다시 읽어야 한다.
    return _to_record(ref.get())


# ------------------------------------------------------------------ 수정


def update_record(record_id: str, changes: dict) -> dict:
    col = _collection()
    old_ref = col.document(record_id)
    new_date = changes.get("date", record_id)

    # (1) 날짜가 그대로면 필드만 갱신
    if new_date == record_id:
        try:
            old_ref.update({**changes, "updated_at": firestore.SERVER_TIMESTAMP})
        except gexc.NotFound as exc:
            raise NotFoundError(f"{record_id} 데이터를 찾을 수 없습니다.") from exc
        invalidate_cache()
        return _to_record(old_ref.get())

    # (2) 날짜가 바뀌면 문서를 새 ID 로 옮긴다 — 트랜잭션으로 원자적 처리
    new_ref = col.document(new_date)

    @firestore.transactional
    def _move(transaction):
        old_snap = old_ref.get(transaction=transaction)
        if not old_snap.exists:
            raise NotFoundError(f"{record_id} 데이터를 찾을 수 없습니다.")
        if new_ref.get(transaction=transaction).exists:
            raise ConflictError(f"{new_date} 데이터가 이미 있어 날짜를 변경할 수 없습니다.")

        moved = {**old_snap.to_dict(), **changes, "updated_at": firestore.SERVER_TIMESTAMP}
        transaction.create(new_ref, moved)
        transaction.delete(old_ref)

    _move(get_db().transaction())
    invalidate_cache()
    return _to_record(new_ref.get())


# ------------------------------------------------------------------ 삭제


def delete_record(record_id: str) -> None:
    ref = _collection().document(record_id)
    try:
        # exists=True 전제조건: 문서가 없으면 서버가 NotFound 를 돌려준다(읽기 없이 한 번에 확인).
        ref.delete(option=get_db().write_option(exists=True))
    except gexc.NotFound as exc:
        raise NotFoundError(f"{record_id} 데이터를 찾을 수 없습니다.") from exc
    invalidate_cache()
