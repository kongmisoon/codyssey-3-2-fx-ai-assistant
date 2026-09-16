"""
services/analysis_service.py — 환율 레코드 목록으로 "요약 정보"를 계산한다.

왜 순수 함수인가?
compute_summary() 는 DB 도, HTTP 도, 현재 시각도 직접 건드리지 않는다. 리스트를 받아 dict 를 돌려줄 뿐이다.
→ Firestore 없이 단위 테스트할 수 있고(tests/test_analysis_service.py),
  같은 입력이면 항상 같은 결과가 나오므로 AI 답변의 근거 수치를 믿을 수 있다.

이 요약이 쓰이는 곳
- GET /api/data/summary   : 프론트 요약 카드
- POST /api/chat (Phase 6): 시스템 프롬프트에 주입되는 "AI 가 아는 내 데이터"의 전부

환율 데이터에 맞춘 판단
1) 합계(total)는 계산하지 않는다. 환율을 더한 값은 아무 의미가 없다.
   대신 변동폭(range) · 표준편차(std) · 기간 변화율(total_change_pct)을 제공한다.
2) 추세는 "최근 20영업일 평균 vs 직전 20영업일 평균"으로 본다.
   환율은 주말·공휴일에 값이 없어 '7일' 같은 달력 기준보다 '영업일 개수' 기준이 공정하다.
3) 추세 판정 기준은 ±0.5% 다. 원/달러 환율의 일간 변동 표준편차는 0.5% 안팎이라
   ±5% 같은 기준을 쓰면 거의 항상 '보합'이 나와 정보가 없다.
4) 환율 상승 = 원화 약세, 환율 하락 = 원화 강세. 라벨에 둘 다 적어 AI 가 혼동하지 않게 한다.
"""

from collections import OrderedDict
from statistics import mean, median, stdev

TREND_WINDOW = 20  # 비교 구간 (영업일)
TREND_THRESHOLD_PCT = 0.5  # 이 비율을 넘어야 상승/하락으로 판정
RECENT_DAYS = 5  # 요약에 함께 싣는 최근 N영업일
UNIT = "KRW/USD"

TREND_LABELS = {
    "up": "원화 약세 (환율 상승)",
    "down": "원화 강세 (환율 하락)",
    "flat": "보합",
    "insufficient": "데이터 부족",
    "none": "데이터 없음",
}


def _r(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _pct_change(new: float, old: float) -> float:
    return (new - old) / old * 100 if old else 0.0


def _clean_rows(records: list[dict]) -> list[tuple[str, float]]:
    """(date, value) 튜플로 정리하고 날짜순 정렬. 값이 비었거나 숫자가 아닌 행은 버린다."""
    rows = []
    for rec in records:
        date, value = rec.get("date"), rec.get("value")
        if not date or value is None:
            continue
        try:
            rows.append((str(date), float(value)))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def _trend(values: list[float], window: int, threshold: float) -> dict:
    """최근 window 개 평균과 그 직전 window 개 평균을 비교한다."""
    n = len(values)
    if n < 2:
        return {
            "recent_avg": None,
            "previous_avg": None,
            "change_pct": None,
            "trend_direction": "insufficient",
            "trend_window": 0,
            "trend": TREND_LABELS["insufficient"],
        }

    # 데이터가 40개(20+20) 미만이면 가진 만큼 반씩 나눠 비교한다.
    w = min(window, n // 2)
    recent = mean(values[-w:])
    previous = mean(values[-2 * w : -w])
    change = _pct_change(recent, previous)

    if change > threshold:
        direction = "up"
    elif change < -threshold:
        direction = "down"
    else:
        direction = "flat"

    text = (
        f"{TREND_LABELS[direction]} — 최근 {w}영업일 평균 {recent:,.2f}원, "
        f"직전 {w}영업일 평균 {previous:,.2f}원 대비 {change:+.2f}%"
    )
    return {
        "recent_avg": _r(recent),
        "previous_avg": _r(previous),
        "change_pct": _r(change),
        "trend_direction": direction,
        "trend_window": w,
        "trend": text,
    }


def _daily_moves(rows: list[tuple[str, float]]) -> dict:
    """전일 대비 변화율 통계. 최대 상승일·하락일은 동률이면 더 이른 날짜를 택한다."""
    if len(rows) < 2:
        return {"daily_change_std_pct": None, "max_daily_rise": None, "max_daily_fall": None}

    moves = [
        (rows[i][0], _pct_change(rows[i][1], rows[i - 1][1]), rows[i][1]) for i in range(1, len(rows))
    ]
    # moves 는 날짜순이고 '엄격히 클/작을 때만' 교체하므로, 동률이면 먼저 나온(이른) 날짜가 남는다.
    rise = fall = moves[0]
    for move in moves[1:]:
        if move[1] > rise[1]:
            rise = move
        if move[1] < fall[1]:
            fall = move
    pcts = [m[1] for m in moves]
    return {
        "daily_change_std_pct": _r(stdev(pcts), 3) if len(pcts) >= 2 else 0.0,
        "max_daily_rise": {"date": rise[0], "change_pct": _r(rise[1]), "value": _r(rise[2])},
        "max_daily_fall": {"date": fall[0], "change_pct": _r(fall[1]), "value": _r(fall[2])},
    }


def _monthly(rows: list[tuple[str, float]]) -> list[dict]:
    """월별 평균·최저·최고와 전월 평균 대비 변화율."""
    groups: "OrderedDict[str, list[float]]" = OrderedDict()
    for date, value in rows:
        groups.setdefault(date[:7], []).append(value)

    result, prev_avg = [], None
    for month, vals in groups.items():
        avg = mean(vals)
        result.append(
            {
                "month": month,
                "average": _r(avg),
                "min": _r(min(vals)),
                "max": _r(max(vals)),
                "count": len(vals),
                "change_pct": None if prev_avg is None else _r(_pct_change(avg, prev_avg)),
            }
        )
        prev_avg = avg
    return result


def compute_summary(
    records: list[dict],
    window: int = TREND_WINDOW,
    threshold: float = TREND_THRESHOLD_PCT,
) -> dict:
    """
    Args:
        records: [{"date": "YYYY-MM-DD", "value": 1350.5, ...}, ...]  (순서 무관)
    Returns:
        요약 dict (models.schemas.SummaryResponse 와 같은 모양)
    """
    rows = _clean_rows(records)
    n = len(rows)

    if n == 0:
        return {
            "period": None,
            "start_date": None,
            "end_date": None,
            "count": 0,
            "unit": UNIT,
            "metrics": None,
            "recent_avg": None,
            "previous_avg": None,
            "change_pct": None,
            "trend_direction": "none",
            "trend_window": 0,
            "trend": TREND_LABELS["none"],
            "daily_change_std_pct": None,
            "max_daily_rise": None,
            "max_daily_fall": None,
            "recent_days": [],
            "monthly": [],
        }

    dates = [d for d, _ in rows]
    values = [v for _, v in rows]

    # 최고·최저가 여러 날이면 가장 이른 날짜를 보고한다 (rows 가 날짜순이므로 index() 가 첫 번째를 준다)
    max_value, min_value = max(values), min(values)
    max_date = dates[values.index(max_value)]
    min_date = dates[values.index(min_value)]

    metrics = {
        "average": _r(mean(values)),
        "median": _r(median(values)),
        "max": _r(max_value),
        "max_date": max_date,
        "min": _r(min_value),
        "min_date": min_date,
        "range": _r(max_value - min_value),
        # 표본 표준편차(n-1). pandas .std() 와 같은 기준이라 기존 분석 결과와 비교 가능하다.
        "std": _r(stdev(values)) if n >= 2 else 0.0,
        "first_date": dates[0],
        "first_rate": _r(values[0]),
        "last_date": dates[-1],
        "last_rate": _r(values[-1]),
        "total_change_pct": _r(_pct_change(values[-1], values[0])),
    }

    return {
        "period": f"{dates[0]} ~ {dates[-1]}",
        "start_date": dates[0],
        "end_date": dates[-1],
        "count": n,
        "unit": UNIT,
        "metrics": metrics,
        **_trend(values, window, threshold),
        **_daily_moves(rows),
        "recent_days": [{"date": d, "value": _r(v)} for d, v in rows[-RECENT_DAYS:]],
        "monthly": _monthly(rows),
    }
