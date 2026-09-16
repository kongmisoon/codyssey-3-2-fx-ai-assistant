"""
scripts/copy_firebase_json.py — Firebase 서비스 계정 키를 "한 줄 JSON"으로 클립보드에 복사한다.

Render 대시보드의 환경변수 FIREBASE_SERVICE_ACCOUNT_JSON 에 붙여넣기 위한 도구.
- 키 내용은 화면에 출력하지 않는다 (프로젝트 ID 와 길이만 보여준다).
- 파일에 적힌 형식이 올바른지(서비스 계정 키인지) 먼저 확인한다.

실행 (backend 폴더에서, 또는 copy_firebase_json.bat 더블클릭):
    venv\\Scripts\\python.exe scripts/copy_firebase_json.py
    venv\\Scripts\\python.exe scripts/copy_firebase_json.py --dry-run     # 복사하지 않고 확인만
"""

import argparse
import json
import os
import subprocess
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KEY = os.path.join(BACKEND_DIR, "serviceAccountKey.json")
REQUIRED_FIELDS = ("type", "project_id", "private_key", "client_email")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", default=DEFAULT_KEY, help="서비스 계정 키 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="클립보드에 복사하지 않고 확인만")
    args = parser.parse_args()

    print("=" * 56)
    print(" Firebase 키 → Render 환경변수용 한 줄 JSON")
    print("=" * 56)

    if not os.path.exists(args.key):
        print(f"[FAIL] 키 파일이 없습니다: {args.key}")
        return 1

    try:
        with open(args.key, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[FAIL] 키 파일을 JSON 으로 읽을 수 없습니다: {type(exc).__name__}")
        return 1

    missing = [k for k in REQUIRED_FIELDS if not info.get(k)]
    if missing or info.get("type") != "service_account":
        print(f"[FAIL] 서비스 계정 키 형식이 아닙니다. (누락: {', '.join(missing) or 'type != service_account'})")
        return 1

    one_line = json.dumps(info, separators=(",", ":"), ensure_ascii=True)
    print(f" 프로젝트 ID : {info['project_id']}")
    print(f" 길이        : {len(one_line):,}자 (한 줄)")

    if args.dry_run:
        print("\n[OK] 형식 확인 완료 (--dry-run 이라 복사하지 않았습니다)")
        return 0

    if sys.platform != "win32":
        print("[FAIL] 이 도구는 Windows 클립보드(clip)만 지원합니다.")
        return 1
    try:
        subprocess.run(["clip"], input=one_line.encode("ascii"), check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] 클립보드 복사 실패: {exc}")
        return 1

    print("\n[OK] 클립보드에 복사했습니다.")
    print("     Render → Environment → FIREBASE_SERVICE_ACCOUNT_JSON 의 Value 칸에 Ctrl+V 하세요.")
    print("     (붙여넣은 뒤에는 클립보드에 다른 글자를 한 번 복사해 두면 더 안전합니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
