"""
scripts/seed_data.py — 환율 CSV 를 Firestore data 컬렉션에 적재한다.

왜 필요한가?
1) 과제 요건 "최소 100개 이상의 데이터 포인트"를 실데이터(522영업일)로 채운다.
2) API 로 522번 POST 하는 대신 Firestore batch write 로 한 번에 넣는다.
   Firestore 는 batch 하나에 최대 500건까지만 허용하므로 나눠서 커밋한다.
3) 문서 ID 를 날짜(YYYY-MM-DD)로 쓴다. 같은 날짜를 다시 넣으면 새 문서가 생기지 않고
   덮어쓰기가 되므로, 스크립트를 여러 번 실행해도 중복이 생기지 않는다.

실행 (backend 폴더에서):
    venv\\Scripts\\python.exe scripts/seed_data.py --limit 10     # 10건만 시험 적재
    venv\\Scripts\\python.exe scripts/seed_data.py                # 전체 적재
    venv\\Scripts\\python.exe scripts/seed_data.py --reset        # 전부 지우고 다시 적재
    venv\\Scripts\\python.exe scripts/seed_data.py --dry-run      # DB 에 쓰지 않고 미리보기만
"""

import argparse
import csv
import os
import sys
from datetime import datetime

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from config import get_settings  # noqa: E402

DEFAULT_CSV = os.path.join(BACKEND_DIR, "data", "usdkrw_2024_2026.csv")

# Firestore batch 한도는 500. 여유를 두고 400건씩 커밋한다.
BATCH_SIZE = 400

FILLED_MEMO = "휴장일 보간값"


def load_rows(csv_path: str) -> list[dict]:
    """CSV 를 읽어 Firestore 에 넣을 형태({date, value, memo})로 변환한다."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV 파일이 없습니다: {csv_path}")

    records = []
    # 원본 파일 맨 앞에 BOM 이 있어 utf-8-sig 로 읽어야 첫 컬럼명이 'Date' 로 정상 인식된다.
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            date_str = (row.get("Date") or "").strip()
            rate_str = (row.get("rate") or "").strip()

            # 날짜·값 검증 — 이상한 행은 건너뛰고 알려준다
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                value = round(float(rate_str), 2)
            except ValueError:
                print(f"  [건너뜀] {line_no}행: date={date_str!r}, rate={rate_str!r}")
                continue

            is_filled = (row.get("is_filled") or "").strip().lower() == "true"
            records.append(
                {
                    "date": date_str,
                    "value": value,
                    "memo": FILLED_MEMO if is_filled else "",
                }
            )

    records.sort(key=lambda r: r["date"])
    return records


def delete_all(collection) -> int:
    """컬렉션의 문서를 전부 지운다. 삭제도 batch 단위로 나눠서 한다."""
    from database import get_db

    db = get_db()
    deleted = 0
    while True:
        docs = list(collection.limit(BATCH_SIZE).stream())
        if not docs:
            return deleted
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs)
        print(f"  삭제 진행: {deleted}건")


def upload(records: list[dict], collection) -> int:
    """batch write 로 적재한다. 문서 ID = 날짜."""
    from firebase_admin import firestore

    from database import get_db

    db = get_db()
    written = 0
    for start in range(0, len(records), BATCH_SIZE):
        chunk = records[start : start + BATCH_SIZE]
        batch = db.batch()
        for rec in chunk:
            doc_ref = collection.document(rec["date"])
            batch.set(
                doc_ref,
                {
                    **rec,
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
        batch.commit()
        written += len(chunk)
        print(f"  적재 진행: {written}/{len(records)}건")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="환율 CSV → Firestore 적재")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV 파일 경로")
    parser.add_argument("--limit", type=int, default=0, help="앞에서부터 N건만 적재 (0=전체)")
    parser.add_argument("--reset", action="store_true", help="적재 전에 기존 문서 전부 삭제")
    parser.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 미리보기만")
    args = parser.parse_args()

    settings = get_settings()

    print("=" * 56)
    print(" 환율 데이터 → Firestore 적재")
    print("=" * 56)

    records = load_rows(args.csv)
    if args.limit > 0:
        records = records[: args.limit]
    if not records:
        print("[FAIL] 적재할 데이터가 없습니다.")
        return 1

    values = [r["value"] for r in records]
    filled = sum(1 for r in records if r["memo"] == FILLED_MEMO)
    print(f" 대상 컬렉션 : {settings.data_collection}")
    print(f" 건수        : {len(records)}건 (휴장일 보간값 {filled}건 포함)")
    print(f" 기간        : {records[0]['date']} ~ {records[-1]['date']}")
    print(f" 최초 / 최종 : {records[0]['value']:,.2f}원 → {records[-1]['value']:,.2f}원")
    print(f" 최저 / 최고 : {min(values):,.2f}원 / {max(values):,.2f}원")
    print()

    if args.dry_run:
        print(" [dry-run] 미리보기 3건:")
        for r in records[:3]:
            print(f"   {r}")
        print(" DB 에는 아무것도 쓰지 않았습니다.")
        return 0

    try:
        from database import get_db

        collection = get_db().collection(settings.data_collection)

        if args.reset:
            print(" 기존 문서 삭제 중...")
            print(f"  → {delete_all(collection)}건 삭제 완료\n")

        print(" 적재 중...")
        written = upload(records, collection)

        # 실제로 몇 건이 들어있는지 서버에 물어본다(집계 쿼리 1회 = 읽기 비용 최소)
        total = collection.count().get()[0][0].value
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FAIL] {type(exc).__name__}: {exc}")
        return 1

    print()
    print("=" * 56)
    print(f" [OK] {written}건 적재 완료. 현재 컬렉션 총 문서 수: {total}건")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
