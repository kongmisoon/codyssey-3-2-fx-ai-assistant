"""
services/ai_service.py — AI 모델 호출을 담당하는 유일한 지점.

왜 이렇게 만드는가?
1) 라우터는 "무슨 모델을 쓰는지" 몰라야 한다. 라우터는 generate_reply() 만 부르고,
   Gemini 인지 GPT 인지는 이 파일과 환경변수(AI_PROVIDER)만 안다.
   → 개발 중에는 무료인 Gemini, 제출/시연 때는 GPT. 코드 수정 없이 .env 한 줄로 전환된다.
2) 두 SDK 는 메시지 형식이 다르다(OpenAI 는 role="assistant", Gemini 는 role="model",
   시스템 프롬프트를 넣는 위치도 다르다). 그 차이를 이 파일 안에 가둔다.
3) 외부 API 는 반드시 실패한다(쿼터 초과, 키 오류, 타임아웃). 그 예외를 사용자에게 보여줄
   한국어 메시지로 번역하는 것도 여기서 한 번만 한다.

사용법:
    reply = generate_reply(system_prompt="당신은 ...", messages=[{"role":"user","content":"안녕"}])
"""

from config import get_settings


class AIServiceError(Exception):
    """AI 호출 실패. status_code 는 라우터가 HTTP 응답으로 그대로 쓸 수 있는 값."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------- Gemini


def _generate_with_gemini(system_prompt: str, messages: list[dict]) -> str:
    # 지연 임포트: openai 만 쓰는 사람이 google-genai 미설치로 앱이 죽는 일을 막는다.
    from google import genai

    settings = get_settings()
    if not settings.gemini_api_key:
        raise AIServiceError("GEMINI_API_KEY 가 설정되지 않았습니다.", status_code=500)

    # OpenAI 형식 → Gemini 형식 변환
    #   role: "assistant" → "model",  content → parts[{text}]
    contents = [
        {
            "role": "model" if m.get("role") == "assistant" else "user",
            "parts": [{"text": m.get("content", "")}],
        }
        for m in messages
        if m.get("content")
    ]
    if not contents:
        raise AIServiceError("보낼 메시지가 비어 있습니다.", status_code=400)

    config = {
        # Gemini 는 시스템 프롬프트를 messages 가 아니라 별도 필드로 받는다.
        "system_instruction": system_prompt,
        "max_output_tokens": settings.max_output_tokens,
        "temperature": settings.ai_temperature,
        # 2.5 계열은 기본으로 '사고(thinking)'에 출력 토큰을 소모한다.
        # max_output_tokens 를 500 으로 묶어둔 상태에서 사고까지 켜두면
        # 정작 답변이 잘려 빈 문자열이 돌아오는 일이 생기므로 꺼둔다.
        "thinking_config": {"thinking_budget": 0},
    }

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate_error(exc) from exc

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise AIServiceError(
            "AI 가 빈 응답을 반환했습니다. 질문을 조금 더 구체적으로 바꿔 보세요.",
            status_code=502,
        )
    return text


# ---------------------------------------------------------------- OpenAI


def _generate_with_openai(system_prompt: str, messages: list[dict]) -> str:
    from openai import OpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise AIServiceError("OPENAI_API_KEY 가 설정되지 않았습니다.", status_code=500)

    # OpenAI 는 시스템 프롬프트를 messages 맨 앞에 넣는다.
    payload = [{"role": "system", "content": system_prompt}] + [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in messages
        if m.get("content")
    ]

    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=30.0)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=payload,
            max_tokens=settings.max_output_tokens,
            temperature=settings.ai_temperature,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate_error(exc) from exc

    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise AIServiceError("AI 가 빈 응답을 반환했습니다.", status_code=502)
    return text


# ---------------------------------------------------------------- 공통


def _translate_error(exc: Exception) -> AIServiceError:
    """
    SDK 예외를 사용자에게 보여줄 한국어 메시지로 바꾼다.
    예외 클래스 이름으로 판별하는 이유: provider 마다 예외 타입이 달라
    일일이 import 하면 결합이 생기기 때문.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if "authentication" in name or "permissiondenied" in name or "api key" in text or "401" in text:
        return AIServiceError(
            "AI API 키가 올바르지 않습니다. .env 의 키 값을 다시 확인해 주세요.", status_code=500
        )
    if "ratelimit" in name or "resourceexhausted" in name or "429" in text or "quota" in text:
        return AIServiceError(
            "AI 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.", status_code=429
        )
    if "timeout" in name or "deadline" in name or "timed out" in text:
        return AIServiceError(
            "AI 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.", status_code=504
        )
    if "notfound" in name or "model" in text and "not found" in text:
        return AIServiceError(
            "설정한 AI 모델명을 찾을 수 없습니다. .env 의 모델 이름을 확인해 주세요.",
            status_code=500,
        )
    return AIServiceError("AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.", status_code=502)


def generate_reply(system_prompt: str, messages: list[dict]) -> str:
    """
    provider 에 관계없이 동일한 방식으로 AI 답변을 얻는다.

    Args:
        system_prompt: 데이터 요약이 주입된 시스템 프롬프트
        messages: [{"role": "user"|"assistant", "content": "..."}] 형태의 대화 이력
    Returns:
        AI 답변 문자열
    Raises:
        AIServiceError: 키 오류 / 쿼터 초과 / 타임아웃 등
    """
    provider = get_settings().ai_provider
    if provider == "openai":
        return _generate_with_openai(system_prompt, messages)
    if provider == "gemini":
        return _generate_with_gemini(system_prompt, messages)
    raise AIServiceError(
        f"알 수 없는 AI_PROVIDER 값입니다: '{provider}'. gemini 또는 openai 중 하나여야 합니다.",
        status_code=500,
    )
