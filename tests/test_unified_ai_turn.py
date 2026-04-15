"""Тесты разбора JSON unified AI."""

import unittest

from src.jarvis.unified_ai_turn import parse_unified_model_output


class TestParseUnifiedModelOutput(unittest.TestCase):
    def test_reply_plain(self):
        p = parse_unified_model_output('{"mode":"reply","message":"Привет."}')
        self.assertEqual(p, {"mode": "reply", "message": "Привет."})

if __name__ == "__main__":
    unittest.main()
