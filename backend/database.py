"""
database.py — Firestore 연결을 딱 한 번만 만들어 앱 전체가 재사용하도록 관리한다.

왜 필요한가?
1) firebase_admin.initialize_app() 을 두 번 부르면 예외가 난다. 요청마다 초기화할 수 없으므로
   "이미 초기화됐는지" 확인하는 관문이 반드시 하나 있어야 한다.
2) 인증 정보를 얻는 경로가 로컬(키 파일)과 배포(환경변수 JSON 문자열)에서 서로 다르다.
   그 분기를 이 파일 안에 가둬두면, 나머지 코드는 get_db() 만 부르면 된다.
3) Render 대시보드에 JSON 을 붙여넣으면 private_key 의 줄바꿈이 "\n" 문자 두 글자로
   들어가는 사고가 흔하다. 그 복구 처리도 여기서 한다.
"""

import json
import os

import firebase_admin
from firebase_admin import credentials, firestore

from config import get_settings

# 모듈 수준 캐시 — 프로세스당 하나의 Firestore 클라이언트
_db_client = None


class FirebaseConfigError(RuntimeError):
    """인증 정보를 찾지 못했거나 형식이 잘못된 경우."""


def _normalize_private_key(info: dict) -> dict:
    """
    Render 등 대시보드에 JSON 을 붙여넣으면 private_key 안의 개행이
    실제 줄바꿈이 아니라 백슬래시+n 두 글자로 저장되는 일이 잦다.
    그대로 두면 '인증서 파싱 실패'가 나므로 진짜 줄바꿈으로 되돌린다.
    """
    key = info.get("private_key")
    if isinstance(key, str) and "\\n" in key:
        info["private_key"] = key.replace("\\n", "\n")
    return info


def _load_credentials() -> credentials.Base:
    """(A) 환경변수 JSON 문자열 → (B) 키 파일 경로 순서로 인증 정보를 찾는다."""
    settings = get_settings()

    # (A) 배포 환경: JSON 전체가 환경변수 문자열로 들어온다.
    raw = settings.firebase_service_account_json
    if raw:
        try:
            info = _normalize_private_key(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise FirebaseConfigError(
                "FIREBASE_SERVICE_ACCOUNT_JSON 값이 올바른 JSON 이 아닙니다. "
                "키 파일 내용을 통째로(중괄호 포함) 붙여넣었는지 확인하세요."
            ) from exc
        return credentials.Certificate(info)

    # (B) 로컬 환경: 키 파일 경로
    path = settings.google_application_credentials
    if path and os.path.exists(path):
        return credentials.Certificate(path)

    raise FirebaseConfigError(
        "Firebase 인증 정보를 찾을 수 없습니다.\n"
        "  - 로컬:  backend/ 에 서비스 계정 키 파일을 두고 .env 의 "
        "GOOGLE_APPLICATION_CREDENTIALS 에 경로를 적으세요.\n"
        "  - 배포:  FIREBASE_SERVICE_ACCOUNT_JSON 환경변수에 키 JSON 전체를 넣으세요."
    )


def get_db():
    """Firestore 클라이언트를 반환한다. 최초 호출 시에만 실제 초기화가 일어난다."""
    global _db_client

    if _db_client is not None:
        return _db_client

    # 이미 초기화된 앱이 있으면 재초기화하지 않는다(uvicorn --reload 시 중복 방지).
    if not firebase_admin._apps:
        firebase_admin.initialize_app(_load_credentials())

    _db_client = firestore.client()
    return _db_client


def ping() -> dict:
    """
    헬스체크용 연결 확인.
    실제로 컬렉션을 1건만 읽어보고 성공/실패를 돌려준다.
    예외를 밖으로 던지지 않는 이유: 헬스체크가 500 으로 죽으면 원인 파악이 더 어려워지기 때문.
    """
    try:
        db = get_db()
        settings = get_settings()
        # limit(1) 이므로 데이터가 많아도 비용이 거의 들지 않는다.
        next(db.collection(settings.data_collection).limit(1).stream(), None)
        return {"connected": True, "error": None}
    except Exception as exc:  # noqa: BLE001 - 헬스체크는 어떤 실패든 요약해서 보고해야 한다
        return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}
