"""
main.py — FastAPI 애플리케이션의 입구.

여기서 하는 일은 딱 네 가지다.
1) 앱 객체 생성 + Swagger 문서 메타 정보 설정   → /docs 에서 API 문서 확인
2) CORS 설정                                   → Vercel 프론트가 Render 백엔드를 부를 수 있게 허용
3) 라우터 등록                                 → Phase 3 이후 /api/data 등을 여기에 붙인다
4) 헬스체크 엔드포인트                         → Render 콜드스타트 깨우기 + 설정 상태 확인

비즈니스 로직은 이 파일에 쓰지 않는다. 계산은 services/, HTTP 처리는 routers/ 담당.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

import database
from config import get_settings

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


# --- 라우터 등록 ---------------------------------------------------------
# Phase 3 이후 아래 주석을 해제하며 하나씩 붙인다.
# from routers import data, conversations, chat
# app.include_router(data.router)
# app.include_router(conversations.router)
# app.include_router(chat.router)


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
