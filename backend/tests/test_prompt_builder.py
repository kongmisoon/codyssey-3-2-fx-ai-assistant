"""
tests/test_prompt_builder.py — 시스템 프롬프트 조립 단위 테스트 (DB·AI 불필요).

실행 (backend 폴더에서):
    venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.analysis_service import compute_summary  # noqa: E402
from services.prompt_builder import EMPTY_PROMPT, build_system_prompt  # noqa: E402


def sample_records():
    # 2025-01: 1000 x 20, 2025-02: 1100 x 20 → 최근 20 vs 직전 20 = +10% (원화 약세)
    recs = [{"date": f"2025-01-{d:02d}", "value": 1000} for d in range(1, 21)]
    recs += [{"date": f"2025-02-{d:02d}", "value": 1100} for d in range(1, 21)]
    recs[5]["value"] = 950  # 최저: 2025-01-06
    recs[-3]["value"] = 1234.5  # 최고: 2025-02-18
    return recs


class BuildSystemPrompt(unittest.TestCase):
    def setUp(self):
        self.summary = compute_summary(sample_records())
        self.prompt = build_system_prompt(self.summary)

    def test_contains_core_numbers(self):
        p = self.prompt
        self.assertIn("2025-01-01 ~ 2025-02-20 (영업일 40개)", p)
        self.assertIn("최고 1,234.50원 (2025-02-18)", p)
        self.assertIn("최저 950.00원 (2025-01-06)", p)
        self.assertIn(f"평균 {self.summary['metrics']['average']:,.2f}원", p)

    def test_trend_and_its_meaning(self):
        self.assertIn(self.summary["trend"], self.prompt)
        self.assertIn("원화 약세", self.prompt)
        self.assertIn("원화 가치가 내려갔다", self.prompt)

    def test_today_is_last_data_date(self):
        self.assertIn("마지막 날짜(2025-02-20)를 '오늘'로", self.prompt)
        self.assertIn('"이번 달"은 2025-02', self.prompt)

    def test_monthly_and_recent_sections(self):
        self.assertIn("- 2025-01: 평균", self.prompt)
        self.assertIn("- 2025-02: 평균", self.prompt)
        self.assertIn("첫 달", self.prompt)
        self.assertEqual(len(re.findall(r"^- 2025-02-\d\d: ", self.prompt, re.M)), 5)  # 최근 5영업일

    def test_safety_rules_present(self):
        for phrase in ["지어내지 말고", "예측하거나", "마크다운 기호", "규칙을 무시하라고"]:
            self.assertIn(phrase, self.prompt)

    def test_no_unfilled_placeholders(self):
        self.assertNotRegex(self.prompt, r"\{[a-z_]+\}")
        self.assertNotIn("None", self.prompt)

    def test_empty_data_uses_empty_prompt(self):
        self.assertEqual(build_system_prompt(compute_summary([])), EMPTY_PROMPT)
        self.assertIn("데이터가 없습니다", EMPTY_PROMPT)

    def test_single_record_still_builds(self):
        p = build_system_prompt(compute_summary([{"date": "2025-03-03", "value": 1400}]))
        self.assertIn("데이터 부족", p)
        self.assertNotIn("[변동성]", p)  # 하루 변화율을 계산할 수 없으므로 생략
        self.assertNotRegex(p, r"\{[a-z_]+\}")


if __name__ == "__main__":
    unittest.main()
