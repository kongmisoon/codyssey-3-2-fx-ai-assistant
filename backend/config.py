"""
config.py — 환경변수를 읽어 애플리케이션 설정 객체로 만드는 단 하나의 창구.

왜 필요한가?
1) os.getenv() 를 코드 여기저기에 흩어놓으면, 변수 이름을 하나 바꿀 때 전부 찾아다녀야 한다.
   설정을 한 곳에 모아두면 "이 서비스가 필요로 하는 외부 값"이 이 파일만 보면 다 보인다.
2) 키를 코드에 하드코딩하지 않기 위한 경계선 역할을 한다. 비밀값은 전부 여기를 통해서만 들어온다.
3) @lru_cache 로 한 번만 읽어 재사용한다(싱글톤). 매 요청마다 .env 를 다시 파싱할 이유가 없다.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# backend/.env 를 읽어 os.environ 에 올린다. (실행 위치와 무관하게 이 파일 옆의 .env 를 찾는다)
# Render 같은 배포 환경에는 .env 파일이 없지만, 그쪽은 대시보드에서 넣은 환경변수가 이미 있으므로
# 파일이 없어도 정상 동작한다. 이미 설정된 환경변수는 덮어쓰지 않는다.
load_dotenv(os.path.join(BACKEND_DIR, ".env"))


def _env_str(key: str, default: str = "") -> str:
    """환경변수를 문자열로 읽고 앞뒤 공백을 제거한다. 복붙 시 섞여 들어온 공백 방지."""
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    """숫자로 변환할 수 없으면 조용히 기본값으로 떨어진다(배포 중 크래시 방지)."""
    try:
        return int(_env_str(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env_str(key, str(default)))
    except ValueError:
        return default


class Settings:
    """앱 전체에서 공유하는 설정값 묶음."""

    def __init__(self) -> None:
        # --- 앱 메타 ---
        self.app_name: str = "환율 AI 비서 API"
        self.app_env: str = _env_str("APP_ENV", "local")

        # --- AI provider ---
        # 과제 요건은 GPT API 이지만, 개발 중 비용을 아끼기 위해 Gemini 무료 티어를 기본값으로 둔다.
        # 제출/시연 시에는 AI_PROVIDER=openai 한 줄만 바꾸면 코드 수정 없이 전환된다.
        self.ai_provider: str = _env_str("AI_PROVIDER", "gemini").lower()

        self.gemini_api_key: str = _env_str("GEMINI_API_KEY")
        self.gemini_model: str = _env_str("GEMINI_MODEL", "gemini-3.6-flash")

        self.openai_api_key: str = _env_str("OPENAI_API_KEY")
        self.openai_model: str = _env_str("OPENAI_MODEL", "gpt-4o-mini")

        # 응답 길이 제한 = 비용/무료쿼터 방어선
        self.max_output_tokens: int = _env_int("MAX_OUTPUT_TOKENS", 500)
        self.ai_temperature: float = _env_float("AI_TEMPERATURE", 0.7)

        # --- Firebase ---
        # (A) JSON 문자열 통째로 (Render 배포용)  (B) 키 파일 경로 (로컬용)
        self.firebase_service_account_json: str = _env_str("FIREBASE_SERVICE_ACCOUNT_JSON")
        # 상대경로는 "명령을 실행한 폴더"가 아니라 backend/ 폴더 기준으로 해석한다.
        # (어디서 uvicorn 을 띄워도 같은 키 파일을 찾도록)
        cred_path = _env_str("GOOGLE_APPLICATION_CREDENTIALS", "./serviceAccountKey.json")
        if cred_path and not os.path.isabs(cred_path):
            cred_path = os.path.normpath(os.path.join(BACKEND_DIR, cred_path))
        self.google_application_credentials: str = cred_path
        self.data_collection: str = _env_str("DATA_COLLECTION", "data")
        self.conversation_collection: str = _env_str("CONVERSATION_COLLECTION", "conversations")

        # --- CORS ---
        # "a.com, b.com" 형태의 한 줄 문자열을 리스트로 쪼갠다.
        # 브라우저가 보내는 Origin 에는 끝 슬래시가 없으므로, 'https://x.vercel.app/' 처럼 적어도 맞도록 제거한다.
        raw_origins = _env_str("ALLOWED_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500")
        self.allowed_origins: list[str] = [
            o.strip().rstrip("/") for o in raw_origins.split(",") if o.strip().rstrip("/")
        ]

    # ---------- 상태 점검용 헬퍼 (비밀값은 절대 반환하지 않는다) ----------

    @property
    def ai_model(self) -> str:
        """현재 선택된 provider 의 모델명."""
        return self.openai_model if self.ai_provider == "openai" else self.gemini_model

    @property
    def is_ai_key_configured(self) -> bool:
        """선택된 provider 의 키가 채워져 있는지 여부만 알려준다(값은 노출하지 않음)."""
        if self.ai_provider == "openai":
            return bool(self.openai_api_key)
        return bool(self.gemini_api_key)

    @property
    def is_firebase_configured(self) -> bool:
        if self.firebase_service_account_json:
            return True
        return os.path.exists(self.google_application_credentials)

    def public_status(self) -> dict:
        """헬스체크 응답에 실어 보낼 수 있는, 비밀값이 없는 설정 요약."""
        return {
            "app_env": self.app_env,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_key_configured": self.is_ai_key_configured,
            "firebase_configured": self.is_firebase_configured,
            "allowed_origins": self.allowed_origins,
            "max_output_tokens": self.max_output_tokens,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """앱 어디서든 이 함수로 설정을 가져온다. 최초 1회만 생성되고 이후엔 캐시된 객체를 준다."""
    return Settings()
