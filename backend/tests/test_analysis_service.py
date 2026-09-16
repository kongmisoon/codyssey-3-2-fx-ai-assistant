"""
tests/test_analysis_service.py — compute_summary() 단위 테스트.

compute_summary 는 순수 함수라 Firestore·네트워크 없이 검증할 수 있다.
표준 라이브러리 unittest 만 사용하므로 추가 설치가 필요 없다.

실행 (backend 폴더에서):
    venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.analysis_service import compute_summary  # noqa: E402


def make_records(values, start_month=1, start_day=1):
    """values 순서대로 2025년 날짜를 붙인다. 한 달에 28일씩 채워 월 경계를 예측 가능하게 한다."""
    records = []
    for i, v in enumerate(values):
        month = start_month + (start_day - 1 + i) // 28
        day = (start_day - 1 + i) % 28 + 1
        records.append({"date": f"2025-{month:02d}-{day:02d}", "value": v})
    return records


class EmptyAndTinyInputs(unittest.TestCase):
    def test_empty(self):
        s = compute_summary([])
        self.assertEqual(s["count"], 0)
        self.assertIsNone(s["metrics"])
        self.assertIsNone(s["period"])
        self.assertEqual(s["trend_direction"], "none")
        self.assertEqual(s["trend"], "데이터 없음")
        self.assertEqual(s["monthly"], [])

    def test_single_record(self):
        s = compute_summary([{"date": "2025-01-02", "value": 1400}])
        self.assertEqual(s["count"], 1)
        m = s["metrics"]
        self.assertEqual((m["average"], m["max"], m["min"], m["range"], m["std"]), (1400, 1400, 1400, 0, 0.0))
        self.assertEqual(m["total_change_pct"], 0)
        self.assertEqual(s["trend_direction"], "insufficient")
        self.assertIsNone(s["max_daily_rise"])

    def test_invalid_rows_are_skipped(self):
        s = compute_summary(
            [
                {"date": "2025-01-01", "value": 1000},
                {"date": "2025-01-02", "value": None},
                {"date": None, "value": 1200},
                {"date": "2025-01-03", "value": "abc"},
                {"value": 1300},
                {"date": "2025-01-04", "value": "1100"},  # 숫자 문자열은 허용
            ]
        )
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["metrics"]["average"], 1050)


class BasicMetrics(unittest.TestCase):
    def test_unsorted_input_is_sorted_by_date(self):
        s = compute_summary(
            [
                {"date": "2025-01-03", "value": 1300},
                {"date": "2025-01-01", "value": 1100},
                {"date": "2025-01-02", "value": 1200},
            ]
        )
        m = s["metrics"]
        self.assertEqual(s["period"], "2025-01-01 ~ 2025-01-03")
        self.assertEqual((m["first_rate"], m["last_rate"]), (1100, 1300))
        self.assertEqual(m["total_change_pct"], 18.18)
        self.assertEqual(m["median"], 1200)
        self.assertEqual(m["std"], 100.0)  # 표본 표준편차 (n-1)
        self.assertEqual([d["date"] for d in s["recent_days"]], ["2025-01-01", "2025-01-02", "2025-01-03"])

    def test_ties_report_earliest_date(self):
        s = compute_summary(make_records([1000, 1500, 900, 1500, 900]))
        self.assertEqual(s["metrics"]["max_date"], "2025-01-02")
        self.assertEqual(s["metrics"]["min_date"], "2025-01-03")

    def test_recent_days_keeps_last_five(self):
        s = compute_summary(make_records(range(1000, 1010)))
        self.assertEqual([d["value"] for d in s["recent_days"]], [1005, 1006, 1007, 1008, 1009])

    def test_no_total_field(self):
        """환율 합계는 의미가 없으므로 제공하지 않는다."""
        s = compute_summary(make_records([1000, 1100]))
        self.assertNotIn("total", s["metrics"])


class Trend(unittest.TestCase):
    def test_up_means_won_weakening(self):
        s = compute_summary(make_records([1000] * 20 + [1010] * 20))  # +1.0%
        self.assertEqual(s["trend_direction"], "up")
        self.assertEqual(s["change_pct"], 1.0)
        self.assertEqual((s["previous_avg"], s["recent_avg"]), (1000, 1010))
        self.assertIn("원화 약세", s["trend"])
        self.assertIn("+1.00%", s["trend"])

    def test_down_means_won_strengthening(self):
        s = compute_summary(make_records([1000] * 20 + [990] * 20))  # -1.0%
        self.assertEqual(s["trend_direction"], "down")
        self.assertIn("원화 강세", s["trend"])

    def test_small_change_is_flat(self):
        s = compute_summary(make_records([1000] * 20 + [1003] * 20))  # +0.3%
        self.assertEqual(s["trend_direction"], "flat")
        self.assertIn("보합", s["trend"])

    def test_threshold_boundary_is_flat(self):
        """정확히 +0.5% 는 '초과'가 아니므로 보합."""
        s = compute_summary(make_records([1000] * 20 + [1005] * 20))
        self.assertEqual(s["change_pct"], 0.5)
        self.assertEqual(s["trend_direction"], "flat")

    def test_uses_only_last_two_windows(self):
        """앞쪽의 오래된 급등은 추세 판단에 영향을 주지 않는다."""
        s = compute_summary(make_records([5000] * 10 + [1000] * 20 + [1000] * 20))
        self.assertEqual(s["trend_direction"], "flat")
        self.assertEqual(s["trend_window"], 20)

    def test_short_series_shrinks_window(self):
        s = compute_summary(make_records([1000, 1000, 1000, 1100, 1100, 1100]))
        self.assertEqual(s["trend_window"], 3)
        self.assertEqual(s["trend_direction"], "up")
        self.assertIn("최근 3영업일", s["trend"])

    def test_odd_count_ignores_oldest(self):
        s = compute_summary(make_records([9999, 1000, 1100]))  # w=1 → 1000 vs 1100
        self.assertEqual(s["trend_window"], 1)
        self.assertEqual(s["change_pct"], 10.0)


class DailyMovesAndMonthly(unittest.TestCase):
    def test_max_daily_rise_and_fall(self):
        s = compute_summary(make_records([1000, 1020, 1010, 1060, 1000]))
        self.assertEqual(s["max_daily_rise"], {"date": "2025-01-04", "change_pct": 4.95, "value": 1060})
        self.assertEqual(s["max_daily_fall"], {"date": "2025-01-05", "change_pct": -5.66, "value": 1000})
        self.assertIsNotNone(s["daily_change_std_pct"])

    def test_daily_move_tie_keeps_earliest(self):
        s = compute_summary(make_records([1000, 1100, 1000, 1100]))  # +10% 가 두 번
        self.assertEqual(s["max_daily_rise"]["date"], "2025-01-02")

    def test_monthly_grouping_and_change(self):
        values = [1000] * 28 + [1100] * 28 + [1045] * 5
        s = compute_summary(make_records(values))
        months = s["monthly"]
        self.assertEqual([m["month"] for m in months], ["2025-01", "2025-02", "2025-03"])
        self.assertEqual([m["count"] for m in months], [28, 28, 5])
        self.assertIsNone(months[0]["change_pct"])
        self.assertEqual(months[1]["change_pct"], 10.0)
        self.assertEqual(months[2]["change_pct"], -5.0)
        self.assertEqual((months[1]["min"], months[1]["max"]), (1100, 1100))


if __name__ == "__main__":
    unittest.main()
