"""
tests/test_chat_service.py — 채팅 흐름 단위 테스트.

AI·Firestore 를 가짜(mock)로 바꿔서 "무엇을 어떤 순서로 부르고, 언제 저장하는가"만 검증한다.
실제 AI 사용량을 쓰지 않는다.

실행 (backend 폴더에서):
    venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import chat_service  # noqa: E402
from services.ai_service import AIServiceError  # noqa: E402
from services.errors import BadRequestError, NotFoundError  # noqa: E402
from services.prompt_builder import EMPTY_PROMPT  # noqa: E402

RECORDS = [{"date": f"2025-01-{d:02d}", "value": 1000 + d} for d in range(1, 29)]


def conversation(n_messages: int, cid: str = "conv1") -> dict:
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}", "timestamp": None}
        for i in range(n_messages)
    ]
    return {"id": cid, "title": "기존 대화", "message_count": n_messages, "messages": msgs}


def saved(cid: str, count: int) -> dict:
    return {"id": cid, "title": "제목", "message_count": count, "messages": []}


class ChatFlow(unittest.TestCase):
    def setUp(self):
        patches = {
            "records": patch.object(chat_service.data_service, "get_all_records", return_value=RECORDS),
            "ai": patch.object(chat_service, "generate_reply", return_value="AI 답변"),
            "get": patch.object(chat_service.conversation_service, "get_conversation"),
            "create": patch.object(chat_service.conversation_service, "create_conversation",
                                   return_value=saved("new1", 2)),
            "append": patch.object(chat_service.conversation_service, "append_messages"),
        }
        self.m = {k: p.start() for k, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)

    def test_new_conversation(self):
        result = chat_service.chat("최근 추세는?")

        system_prompt, messages = self.m["ai"].call_args.args
        self.assertIn("2025-01-01 ~ 2025-01-28", system_prompt)  # 요약이 주입됨
        self.assertEqual(messages, [{"role": "user", "content": "최근 추세는?"}])  # 과거 대화 없음
        self.m["create"].assert_called_once_with(
            [{"role": "user", "content": "최근 추세는?"}, {"role": "assistant", "content": "AI 답변"}]
        )
        self.m["append"].assert_not_called()
        self.m["get"].assert_not_called()
        self.assertEqual(result["reply"], "AI 답변")
        self.assertEqual(result["conversation_id"], "new1")
        self.assertEqual(result["context"]["count"], 28)
        self.assertEqual(result["context"]["history_messages_used"], 0)

    def test_existing_conversation_sends_last_20_messages(self):
        self.m["get"].return_value = conversation(30)
        self.m["append"].return_value = saved("conv1", 32)

        result = chat_service.chat("이어서 질문", "conv1")

        _, messages = self.m["ai"].call_args.args
        self.assertEqual(len(messages), 21)  # 과거 20 + 새 질문 1
        self.assertEqual(messages[0], {"role": "user", "content": "m10"})  # 앞의 10개는 잘림
        self.assertEqual(messages[-2], {"role": "assistant", "content": "m29"})
        self.assertEqual(messages[-1], {"role": "user", "content": "이어서 질문"})
        self.assertTrue(all(set(m) == {"role", "content"} for m in messages))  # timestamp 는 AI 에 안 보냄
        self.m["append"].assert_called_once()
        self.assertEqual(self.m["append"].call_args.args[0], "conv1")
        self.m["create"].assert_not_called()
        self.assertEqual(result["message_count"], 32)
        self.assertEqual(result["context"]["history_messages_used"], 20)

    def test_short_history_is_sent_whole(self):
        self.m["get"].return_value = conversation(4)
        self.m["append"].return_value = saved("conv1", 6)
        chat_service.chat("질문", "conv1")
        _, messages = self.m["ai"].call_args.args
        self.assertEqual(len(messages), 5)

    def test_ai_failure_saves_nothing(self):
        self.m["ai"].side_effect = AIServiceError("한도 초과", status_code=429)
        with self.assertRaises(AIServiceError):
            chat_service.chat("질문")
        self.m["create"].assert_not_called()
        self.m["append"].assert_not_called()

    def test_ai_failure_in_existing_conversation_saves_nothing(self):
        self.m["get"].return_value = conversation(2)
        self.m["ai"].side_effect = AIServiceError("시간 초과", status_code=504)
        with self.assertRaises(AIServiceError):
            chat_service.chat("질문", "conv1")
        self.m["append"].assert_not_called()

    def test_missing_conversation_fails_before_ai_call(self):
        self.m["get"].side_effect = NotFoundError("대화를 찾을 수 없습니다.")
        with self.assertRaises(NotFoundError):
            chat_service.chat("질문", "nope")
        self.m["ai"].assert_not_called()
        self.m["records"].assert_not_called()

    def test_full_conversation_fails_before_ai_call(self):
        self.m["get"].return_value = conversation(199)  # +2 하면 201 > 200
        with self.assertRaises(BadRequestError):
            chat_service.chat("질문", "conv1")
        self.m["ai"].assert_not_called()
        self.m["append"].assert_not_called()

    def test_conversation_with_room_for_exactly_one_pair(self):
        self.m["get"].return_value = conversation(198)  # +2 = 200 → 허용
        self.m["append"].return_value = saved("conv1", 200)
        chat_service.chat("마지막 질문", "conv1")
        self.m["ai"].assert_called_once()

    def test_empty_data_uses_empty_prompt(self):
        self.m["records"].return_value = []
        result = chat_service.chat("요즘 환율 어때?")
        system_prompt, _ = self.m["ai"].call_args.args
        self.assertEqual(system_prompt, EMPTY_PROMPT)
        self.assertEqual(result["context"]["count"], 0)
        self.assertEqual(result["context"]["trend_direction"], "none")


if __name__ == "__main__":
    unittest.main()
