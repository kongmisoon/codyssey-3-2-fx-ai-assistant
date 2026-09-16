"""
scripts/check_setup.py — Phase 1 점검 스크립트.

서버를 띄우기 전에 "환경변수 / Firestore / AI 키" 세 가지가 실제로 동작하는지 먼저 확인한다.
여기서 초록불이 다 켜지고 나서 Phase 2 로 넘어가야, 나중에 에러가 났을 때
원인이 인증인지 로직인지 헷갈리지 않는다.

실행:
    cd backend
    python scripts/check_setup.py
"""

import os
import sys

# backend/ 를 import 경로에 추가 (scripts/ 하위에서 실행해도 config, database 를 찾도록)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402

OK = "  [OK]  "
NG = "  [FAIL]"


def check_env() -> bool:
    print("\n1) 환경변수 확인")
    s = get_settings()
    status = s.public_status()

    print(f"     AI provider      : {status['ai_provider']}")
    print(f"     AI model         : {status['ai_model']}")
    print(f"     CORS 허용 origin : {', '.join(status['allowed_origins'])}")
    print(f"     최대 출력 토큰   : {status['max_output_tokens']}")

    ok = True
    if status["ai_key_configured"]:
        print(f"{OK} AI API 키가 설정되어 있습니다.")
    else:
        key_name = "OPENAI_API_KEY" if s.ai_provider == "openai" else "GEMINI_API_KEY"
        print(f"{NG} {key_name} 가 비어 있습니다. .env 를 확인하세요.")
        ok = False

    if status["firebase_configured"]:
        print(f"{OK} Firebase 인증 정보를 찾았습니다.")
    else:
        print(f"{NG} Firebase 키 파일도, FIREBASE_SERVICE_ACCOUNT_JSON 도 찾지 못했습니다.")
        ok = False

    return ok


def check_firestore() -> bool:
    print("\n2) Firestore 연결 확인")
    try:
        import database

        result = database.ping()
        if result["connected"]:
            print(f"{OK} Firestore 에 연결되었습니다.")
            return True
        print(f"{NG} 연결 실패: {result['error']}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"{NG} 연결 실패: {type(exc).__name__}: {exc}")
        return False


def check_ai() -> bool:
    print("\n3) AI 호출 확인 (짧은 테스트 요청 1회)")
    try:
        from services.ai_service import AIServiceError, generate_reply

        reply = generate_reply(
            system_prompt="당신은 테스트 응답기입니다. 반드시 '연결 성공'이라고만 답하세요.",
            messages=[{"role": "user", "content": "테스트"}],
        )
        print(f"{OK} AI 응답: {reply[:60]}")
        return True
    except AIServiceError as exc:
        print(f"{NG} {exc.message}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"{NG} {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    print("=" * 56)
    print(" 환율 AI 비서 — Phase 1 환경 점검")
    print("=" * 56)

    results = [check_env(), check_firestore(), check_ai()]

    print("\n" + "=" * 56)
    if all(results):
        print(" 모든 점검을 통과했습니다. Phase 2 로 진행하세요.")
        print("=" * 56)
        return 0

    print(" 실패한 항목이 있습니다. 위 [FAIL] 메시지를 먼저 해결하세요.")
    print("=" * 56)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
