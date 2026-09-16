"""
scripts/smoke_data_api.py — /api/data CRUD 동작을 실제 Firestore 에 대고 한 번에 검증한다.

실데이터(2024~2026)를 건드리지 않도록 2099년 날짜만 사용하고, 끝나면 반드시 지운다.

실행 (backend 폴더에서):
    venv\\Scripts\\python.exe scripts/smoke_data_api.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from services import data_service  # noqa: E402
from services.errors import NotFoundError  # noqa: E402

A, B = "2099-01-01", "2099-01-02"
REAL = "2026-09-04"  # 실데이터에 존재하는 날짜 (충돌 테스트용, 수정되지 않아야 함)

client = TestClient(app, raise_server_exceptions=False)
results: list[tuple[bool, str]] = []


def check(name: str, resp, expected_status: int, extra=None):
    ok = resp.status_code == expected_status
    detail = ""
    if ok and extra is not None:
        try:
            ok = bool(extra(resp.json()))
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f" (검사 오류: {exc})"
    if not ok:
        detail = detail or f" → 실제 {resp.status_code}: {resp.text[:160]}"
    results.append((ok, name))
    print(f"  [{'OK' if ok else 'FAIL'}] {name}{detail}")


def cleanup():
    for d in (A, B):
        try:
            data_service.delete_record(d)
        except NotFoundError:
            pass


def main() -> int:
    cleanup()
    real_before = client.get("/api/data", params={"start_date": REAL, "end_date": REAL}).json()

    try:
        print("\n[요약 GET /summary]")
        base = client.get("/api/data/summary", params={"refresh": True})
        check("요약 조회 → 200 (라우트 순서 정상)", base, 200, lambda j: j["count"] > 0 and j["metrics"])
        base_count = base.json()["count"]

        print("\n[생성 POST]")
        check("정상 생성 → 201", client.post("/api/data", json={"date": A, "value": 1350.456, "memo": "  테스트  "}), 201,
              lambda j: j["id"] == A and j["value"] == 1350.46 and j["memo"] == "테스트" and j["created_at"])
        check("같은 날짜 재생성 → 409", client.post("/api/data", json={"date": A, "value": 1300}), 409)
        check("없는 날짜 2099-02-30 → 422", client.post("/api/data", json={"date": "2099-02-30", "value": 1300}), 422)
        check("형식 오류 2099/01/03 → 422", client.post("/api/data", json={"date": "2099/01/03", "value": 1300}), 422)
        check("범위 밖 value=10 → 422", client.post("/api/data", json={"date": B, "value": 10}), 422)
        check("숫자 아님 value='abc' → 422", client.post("/api/data", json={"date": B, "value": "abc"}), 422)
        check("memo 201자 → 422", client.post("/api/data", json={"date": B, "value": 1300, "memo": "가" * 201}), 422)
        check("정의 안 된 필드 → 422", client.post("/api/data", json={"date": B, "value": 1300, "rate": 1}), 422)
        check("date 누락 → 422", client.post("/api/data", json={"value": 1300}), 422)
        check("생성 직후 요약에 즉시 반영 (캐시 무효화)", client.get("/api/data/summary"), 200,
              lambda j: j["count"] == base_count + 1 and j["end_date"] == A and j["metrics"]["last_rate"] == 1350.46)

        print("\n[조회 GET]")
        check("기간 조회 → A 포함", client.get("/api/data", params={"start_date": "2099-01-01", "end_date": "2099-12-31"}), 200,
              lambda j: [r["id"] for r in j] == [A])
        check("최신순 limit=3 → 3건, 내림차순", client.get("/api/data", params={"order": "desc", "limit": 3}), 200,
              lambda j: len(j) == 3 and j[0]["date"] >= j[1]["date"] >= j[2]["date"])
        check("실데이터 기간 조회 2025-01 → 20건 이상", client.get("/api/data", params={"start_date": "2025-01-01", "end_date": "2025-01-31"}), 200,
              lambda j: len(j) >= 20 and all("2025-01-01" <= r["date"] <= "2025-01-31" for r in j))
        check("시작일 > 종료일 → 400", client.get("/api/data", params={"start_date": "2099-12-31", "end_date": "2099-01-01"}), 400)
        check("limit=0 → 422", client.get("/api/data", params={"limit": 0}), 422)
        check("order=abc → 422", client.get("/api/data", params={"order": "abc"}), 422)

        print("\n[수정 PUT]")
        check("value·memo 수정 → 200", client.put(f"/api/data/{A}", json={"value": 1400, "memo": "수정됨"}), 200,
              lambda j: j["value"] == 1400 and j["memo"] == "수정됨" and j["date"] == A)
        check("수정 직후 요약에 즉시 반영", client.get("/api/data/summary"), 200,
              lambda j: j["metrics"]["last_rate"] == 1400)
        check("memo=null → 빈 문자열로 비우기", client.put(f"/api/data/{A}", json={"memo": None}), 200,
              lambda j: j["memo"] == "" and j["value"] == 1400)
        check("빈 본문 {} → 422", client.put(f"/api/data/{A}", json={}), 422)
        check("value=null 만 → 422", client.put(f"/api/data/{A}", json={"value": None}), 422)
        check("없는 문서 수정 → 404", client.put("/api/data/2099-12-25", json={"value": 1300}), 404)
        check("잘못된 ID 형식 → 422", client.put("/api/data/abc", json={"value": 1300}), 422)
        check("날짜 변경 A→B → 200 (문서 이동)", client.put(f"/api/data/{A}", json={"date": B}), 200,
              lambda j: j["id"] == B and j["value"] == 1400)
        check("이동 후 옛 날짜 A 는 사라짐", client.get("/api/data", params={"start_date": A, "end_date": A}), 200,
              lambda j: j == [])
        check("이미 있는 날짜로 이동 → 409", client.put(f"/api/data/{B}", json={"date": REAL}), 409)
        check("409 뒤에도 B 는 그대로 남아 있음", client.get("/api/data", params={"start_date": B, "end_date": B}), 200,
              lambda j: len(j) == 1 and j[0]["value"] == 1400)

        print("\n[삭제 DELETE]")
        check("삭제 → 200", client.delete(f"/api/data/{B}"), 200, lambda j: j["id"] == B)
        check("다시 삭제 → 404", client.delete(f"/api/data/{B}"), 404)
        check("삭제 직후 요약이 원래대로 복귀", client.get("/api/data/summary"), 200,
              lambda j: j["count"] == base_count and j["end_date"] != B)

        print("\n[실데이터 보호]")
        real_after = client.get("/api/data", params={"start_date": REAL, "end_date": REAL}).json()
        results.append((real_before == real_after, "실데이터(2026-09-04)가 테스트 중 변경되지 않음"))
        print(f"  [{'OK' if real_before == real_after else 'FAIL'}] 실데이터(2026-09-04)가 테스트 중 변경되지 않음")
    finally:
        cleanup()

    passed = sum(ok for ok, _ in results)
    print(f"\n결과: {passed}/{len(results)} 통과")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
