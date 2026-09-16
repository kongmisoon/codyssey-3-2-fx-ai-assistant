"""
scripts/set_key.py — API 키를 .env 에 안전하게 써 넣는 헬퍼.

메모장으로 .env 를 직접 고치면 붙여넣기 실패·확장자 오류·따옴표 혼입 같은 사고가 잦다.
이 스크립트는 키를 입력받아 형식을 검사한 뒤 .env 의 해당 줄만 정확히 교체한다.
(윈도우 콘솔에서는 숨김 입력(getpass)에 붙여넣기가 잘 안 되므로 보이는 입력을 쓴다.)

실행:
    cd backend
    venv\\Scripts\\python.exe scripts/set_key.py            # 기본: GEMINI_API_KEY
    venv\\Scripts\\python.exe scripts/set_key.py OPENAI_API_KEY
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BACKEND_DIR, ".env")

# 키 이름별 기대하는 접두사 (오타/잘못된 값 조기 발견용)
EXPECTED_PREFIX = {
    "GEMINI_API_KEY": ("AIza", "AQ."),   # 구형 AIza..., 신형 AQ....
    "OPENAI_API_KEY": ("sk-",),
}


def clean(value: str) -> str:
    """복붙 과정에서 흔히 섞여 들어오는 공백·따옴표·개행을 제거한다."""
    return value.strip().strip('"').strip("'").replace("\r", "").replace("\n", "")


def write_key(key_name: str, key_value: str) -> None:
    """.env 안의 해당 줄만 교체한다. 없으면 파일 끝에 추가한다."""
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    replaced = False
    for i, line in enumerate(lines):
        # 주석(#)이 아닌, 해당 키의 할당 줄을 찾는다
        if line.lstrip().startswith(f"{key_name}="):
            lines[i] = f"{key_name}={key_value}\n"
            replaced = True
            break

    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key_name}={key_value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main() -> int:
    key_name = sys.argv[1] if len(sys.argv) > 1 else "GEMINI_API_KEY"

    if not os.path.exists(ENV_PATH):
        print(f"[FAIL] .env 파일이 없습니다: {ENV_PATH}")
        print("       먼저 'copy .env.example .env' 를 실행하세요.")
        return 1

    print("=" * 56)
    print(f" {key_name} 설정")
    print("=" * 56)
    print(" 키를 붙여넣고 Enter 를 누르세요.")
    print(" 붙여넣기: 창 안에서 마우스 오른쪽 클릭 (또는 Ctrl+V)\n")

    value = clean(input(f" {key_name} = "))

    if not value:
        print("\n[FAIL] 입력된 값이 없습니다.")
        print("       클립보드가 비어 있을 수 있습니다. 키를 다시 복사한 뒤 재실행하세요.")
        return 1

    prefixes = EXPECTED_PREFIX.get(key_name)
    if prefixes and not value.startswith(prefixes):
        print(f"\n[경고] 보통 {key_name} 는 {' 또는 '.join(prefixes)} 로 시작합니다.")
        print(f"       입력된 값은 '{value[:4]}...' 로 시작합니다.")
        if input("       그래도 저장할까요? (y/N): ").strip().lower() != "y":
            print("       취소했습니다.")
            return 1

    write_key(key_name, value)

    print(f"\n[OK] .env 에 저장했습니다. (길이 {len(value)}자, 앞 4자 '{value[:4]}')")
    print("     이어서 아래 명령으로 점검하세요:")
    print("     venv\\Scripts\\python.exe scripts/check_setup.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
