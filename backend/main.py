"""
main.py — FastAPI 애플리케이션의 입구.

여기서 하는 일은 딱 네 가지다.
1) 앱 객체 생성 + Swagger 문서 메타 정보 설정   → /docs 에서 API 문서 확인
2) CORS 설정                                   → Vercel 프론트가 Render 백엔드를 부를 수 있게 허용
3) 라우터 등록                                 → Phase 3 이후 /api/data 등을 여기에 붙인다
4) 헬스체크 엔드포인트                         → Render 콜드스타트 깨우기 + 설정 상태 확인

비즈니스 로직은 이 파일에 쓰지 않는다. 계산은 services/, HTTP 처리는 routers/ 담당.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

import database
from config import get_settings
from routers import chat, conversations, data
from services.ai_service import AIServiceError
from services.errors import ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("fx-ai")

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "원/달러 환율(USD/KRW) 시계열 데이터를 저장·분석하고, "
        "그 요약을 시스템 프롬프트에 주입해 AI 가 내 데이터에 근거해 답하는 API."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- CORS ---------------------------------------------------------------
# 브라우저는 다른 출처(origin)로 가는 요청을 기본적으로 막는다.
# 프론트(Vercel 도메인)와 백엔드(Render 도메인)는 출처가 다르므로 명시적 허용이 필요하다.
# 허용 목록을 코드가 아닌 환경변수로 둔 이유: 배포 후 도메인이 정해져도 재배포만으로 바꿀 수 있게.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 전역 예외 처리 ------------------------------------------------------
# 라우터마다 try/except 를 반복하지 않고, 예외 종류별 HTTP 응답을 여기서 한 번에 정한다.
#   ServiceError(404/409 등)   → 서비스가 정한 상태코드 + 사용자용 메시지
#   AIServiceError             → 429(한도) / 504(시간 초과) / 502 등 + 사용자용 메시지
#   FirebaseConfigError        → 503 (서버 설정 문제)
#   그 외 모든 예외            → 500 + 일반 메시지 (내부 스택은 로그에만 남기고 응답엔 노출하지 않음)
# 입력 검증 실패(422)는 FastAPI 가 Pydantic 오류를 자동으로 돌려준다.


@app.exception_handler(ServiceError)
async def handle_service_error(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(AIServiceError)
async def handle_ai_error(request: Request, exc: AIServiceError):
    # ai_service 가 이미 사용자용 한국어 메시지와 상태코드(429/504/502/500)로 번역해 두었다.
    logger.warning("AI 호출 실패 (%s): %s | 원인: %r", exc.status_code, exc.message, exc.__cause__)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(database.FirebaseConfigError)
async def handle_firebase_config_error(request: Request, exc: database.FirebaseConfigError):
    logger.error("Firebase 설정 오류: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "데이터베이스 설정이 올바르지 않습니다. 서버 관리자에게 문의하세요."},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    logger.exception("처리되지 않은 오류: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."},
    )


# --- 라우터 등록 ---------------------------------------------------------
app.include_router(data.router)
app.include_router(conversations.router)
app.include_router(chat.router)


# --- 기본 엔드포인트 -----------------------------------------------------


@app.get("/", include_in_schema=False)
def root():
    """루트로 들어오면 바로 API 문서로 보낸다."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["system"], summary="서버 생존 확인")
def health():
    """
    Render 무료 티어는 15분간 요청이 없으면 잠든다.
    프론트가 처음 열릴 때 이 엔드포인트를 먼저 때려 서버를 깨우고,
    응답이 올 때까지 '깨우는 중' 배너를 보여주는 용도로 쓴다.
    외부 연결을 확인하지 않으므로 항상 빠르게 응답한다.
    """
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/detail", tags=["system"], summary="설정·연결 상태 점검")
def health_detail():
    """
    환경변수가 제대로 들어갔는지, Firestore 에 실제로 붙는지 확인한다.
    ⚠ 키 값 자체는 절대 반환하지 않고, '설정됨/안 됨' 여부만 알려준다.
    """
    return {
        "status": "ok",
        "config": settings.public_status(),
        "firestore": database.ping(),
    }
