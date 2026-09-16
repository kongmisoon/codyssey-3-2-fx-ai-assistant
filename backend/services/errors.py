"""
services/errors.py — 서비스 계층이 던지는 "예상된 실패"의 종류.

왜 필요한가?
서비스는 HTTP 를 몰라야 한다(나중에 CLI 나 MCP 서버에서도 같은 함수를 재사용할 수 있게).
그래서 서비스는 HTTPException 대신 이 예외들을 던지고,
main.py 에 등록된 핸들러가 한 곳에서 HTTP 상태코드로 바꿔준다.
→ 라우터마다 try/except 를 반복하지 않아도 된다.
"""


class ServiceError(Exception):
    """사용자에게 그대로 보여줘도 되는 메시지를 가진 예외의 부모 클래스."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """요청한 문서가 없다 → 404"""

    status_code = 404


class ConflictError(ServiceError):
    """이미 같은 키의 문서가 있다 → 409"""

    status_code = 409
