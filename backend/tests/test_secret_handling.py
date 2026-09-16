"""
tests/test_secret_handling.py — 비밀값이 로그·응답으로 새어 나가지 않는지 검증한다.

배포 대시보드에서 환경변수 칸을 헷갈려 서비스 계정 JSON 을 API 키 칸에 넣는 실수를 가정한다.

실행 (backend 폴더에서):
    venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from config import get_settings  # noqa: E402
from services import ai_service  # noqa: E402
from services.ai_service import AIServiceError  # noqa: E402

FAKE_PRIVATE = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----\n"
FAKE_JSON = '{\n  "type": "service_account",\n  "private_key": "' + FAKE_PRIVATE.replace("\n", "\\n") + '"\n}'


class RequireValidKey(unittest.TestCase):
    def test_rejects_multiline_json_before_calling_api(self):
        with patch.object(get_settings(), "gemini_api_key", FAKE_JSON), \
             patch("google.genai.Client") as client:
            with self.assertRaises(AIServiceError) as ctx:
                ai_service.generate_reply("sys", [{"role": "user", "content": "hi"}])
        client.assert_not_called()  # 외부 호출 자체가 일어나지 않음
        self.assertIn("형식이 올바르지 않습니다", ctx.exception.message)
        self.assertNotIn("service_account", ctx.exception.message)
        self.assertIsNone(ctx.exception.__cause__)  # 원인 예외에 키가 실려 있지 않음

    def test_rejects_korean_or_spaces(self):
        for bad in ("여기에 키 붙여넣기", "AIza abc", "AIzaabc\n"):
            with self.subTest(bad=bad), patch.object(get_settings(), "gemini_api_key", bad):
                with self.assertRaises(AIServiceError):
                    ai_service.generate_reply("sys", [{"role": "user", "content": "hi"}])

    def test_missing_key(self):
        with patch.object(get_settings(), "gemini_api_key", ""):
            with self.assertRaises(AIServiceError) as ctx:
                ai_service.generate_reply("sys", [{"role": "user", "content": "hi"}])
        self.assertIn("설정되지 않았습니다", ctx.exception.message)


class Redact(unittest.TestCase):
    def test_redacts_raw_repr_and_json_forms(self):
        with patch.object(get_settings(), "gemini_api_key", FAKE_JSON):
            forms = [
                f"Illegal header value {FAKE_JSON}",
                f"Illegal header value {FAKE_JSON.encode()!r}",
                f"value={FAKE_JSON!r}",
            ]
            for text in forms:
                with self.subTest(text=text[:30]):
                    out = main._redact(text)
                    self.assertNotIn("MIIEvQIBADAN", out)
                    self.assertNotIn("service_account", out)

    def test_redacts_any_private_key_block(self):
        out = main._redact(f"error near {FAKE_PRIVATE} tail")
        self.assertNotIn("MIIEvQIBADAN", out)
        self.assertIn("***PRIVATE KEY***", out)

    def test_redacts_plain_api_key(self):
        with patch.object(get_settings(), "gemini_api_key", "AQ.SECRETSECRET123"):
            self.assertEqual(main._redact("bad key AQ.SECRETSECRET123 used"), "bad key *** used")

    def test_truncates_long_messages(self):
        self.assertLessEqual(len(main._redact("x" * 5000)), 300)


class KeyCheckInHealth(unittest.TestCase):
    def test_reports_format_only(self):
        with patch.object(get_settings(), "gemini_api_key", FAKE_JSON), \
             patch.object(get_settings(), "ai_provider", "gemini"):
            check = get_settings().public_status()["ai_key_check"]
        self.assertEqual(set(check), {"length", "expected_prefix", "single_line_ascii"})
        self.assertFalse(check["expected_prefix"])
        self.assertFalse(check["single_line_ascii"])


if __name__ == "__main__":
    unittest.main()
