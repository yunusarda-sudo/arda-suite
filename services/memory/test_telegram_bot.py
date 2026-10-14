import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import telegram_bot


class TestTelegramBot(unittest.TestCase):

    def setUp(self):
        telegram_bot.CHAT_SCOPES.clear()
        telegram_bot.CHAT_HISTORY.clear()
        self.allowed_users_patcher = patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "1"})
        self.allowed_users_patcher.start()

    def tearDown(self):
        self.allowed_users_patcher.stop()

    def test_empty_or_unset_whitelist_denies_users(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": ""}):
            self.assertFalse(telegram_bot.is_user_allowed(999, "anybody"))

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(telegram_bot.is_user_allowed(999, "anybody"))

    def test_user_authorization_whitelist(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "123456,example_user,alice"}):
            self.assertTrue(telegram_bot.is_user_allowed(123456, None))
            self.assertTrue(telegram_bot.is_user_allowed(999, "example_user"))
            self.assertTrue(telegram_bot.is_user_allowed(999, "@Example_User"))
            self.assertFalse(telegram_bot.is_user_allowed(999, "mallory"))

    def test_authorization_denies_unlisted_id_and_allows_listed_id(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "123456"}):
            self.assertFalse(telegram_bot.is_user_allowed(999999, "intruder"))
            self.assertTrue(telegram_bot.is_user_allowed(123456, None))

    def test_unauthorized_user_message_rejected(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "123456"}):
            resp = telegram_bot.handle_message("Hello", user_id=999999, username="intruder", chat_id=10)
            self.assertIn("Yetkisiz erişim", resp)

    def test_unauthorized_voice_update_is_rejected_before_audio_processing(self):
        updates = {
            "ok": True,
            "result": [{
                "update_id": 1,
                "message": {
                    "voice": {"file_id": "private-audio-file"},
                    "chat": {"id": 10},
                    "from": {"id": 999999, "username": "intruder"},
                },
            }],
        }

        with (
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_ALLOWED_USERS": "123456"}),
            patch.object(telegram_bot, "call_telegram_api", side_effect=[updates, KeyboardInterrupt]),
            patch.object(telegram_bot, "download_telegram_file") as download,
            patch.object(telegram_bot, "transcribe_audio_bytes") as transcribe,
            patch.object(telegram_bot, "send_message") as send,
        ):
            with self.assertRaises(KeyboardInterrupt):
                telegram_bot.run_polling_loop()

        download.assert_not_called()
        transcribe.assert_not_called()
        send.assert_called_once()
        self.assertIn("Yetkisiz erişim", send.call_args.args[1])

    def test_start_and_help_commands(self):
        resp = telegram_bot.handle_message("/start", user_id=1, username="test", chat_id=10)
        self.assertIn("Shared Brain", resp)
        self.assertIn("Komutlar", resp)

    def test_scope_switching(self):
        # Initial default
        resp = telegram_bot.handle_message("/scope", user_id=1, username="test", chat_id=10)
        self.assertIn("Mevcut aktif kapsam: `personal`", resp)

        # Switch scope
        resp = telegram_bot.handle_message("/scope my-project", user_id=1, username="test", chat_id=10)
        self.assertIn("Aktif kapsam değiştirildi: `my-project`", resp)
        self.assertEqual(telegram_bot.get_chat_scope(10), "my-project")

    def test_clear_history(self):
        telegram_bot.CHAT_HISTORY[10] = [{"role": "user", "content": "hi"}]
        resp = telegram_bot.handle_message("/clear", user_id=1, username="test", chat_id=10)
        self.assertIn("Sohbet geçmişi temizlendi", resp)
        self.assertEqual(telegram_bot.CHAT_HISTORY[10], [])

    @patch("rules.list_rules")
    def test_rules_command(self, mock_rules):
        mock_rules.return_value = [{"id": "R01", "rule": "Always check rules"}]
        resp = telegram_bot.handle_message("/rules", user_id=1, username="test", chat_id=10)
        self.assertIn("R01", resp)
        self.assertIn("Always check rules", resp)

    @patch("ide_bridge.evaluate_health_score")
    def test_health_command(self, mock_health):
        mock_health.return_value = {
            "health_score": 92,
            "metrics": {"total": 50, "active": 48, "stale_file_references": 0, "graph_nodes_count": 12},
            "recommendations": ["No issues detected."],
        }
        resp = telegram_bot.handle_message("/health", user_id=1, username="test", chat_id=10)
        self.assertIn("92/100", resp)
        self.assertIn("50", resp)

    @patch("config.get_memory")
    def test_add_command(self, mock_get_mem):
        mock_mem = MagicMock()
        mock_get_mem.return_value = mock_mem

        resp = telegram_bot.handle_message("/add We switched to Qdrant ARM64", user_id=1, username="test", chat_id=10)
        self.assertIn("Yeni hafıza", resp)
        self.assertIn("We switched to Qdrant ARM64", resp)
        mock_mem.add.assert_called_once()

    @patch("assistant.recall_context")
    def test_search_command(self, mock_recall):
        mock_recall.return_value = {
            "memories": [
                {"scope": "personal", "score": 0.88, "memory": "Qdrant vector database is running"}
            ]
        }
        resp = telegram_bot.handle_message("/search qdrant", user_id=1, username="test", chat_id=10)
        self.assertIn("Qdrant vector database", resp)

    @patch("assistant.ask_brain")
    def test_ask_command_and_direct_chat(self, mock_ask):
        mock_ask.return_value = {
            "answer": "Qdrant port is 6333.",
            "memories_used": [{"scope": "personal", "memory": "Port 6333"}],
        }

        # Via /ask
        resp = telegram_bot.handle_message("/ask What is the Qdrant port?", user_id=1, username="test", chat_id=10)
        self.assertIn("Qdrant port is 6333", resp)
        self.assertIn("1 hafıza kaydına dayandırıldı", resp)

        # Direct conversation without /ask prefix
        resp2 = telegram_bot.handle_message("What is the Qdrant port?", user_id=1, username="test", chat_id=10)
        self.assertIn("Qdrant port is 6333", resp2)

    def test_transcribe_audio_no_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENAI_API_KEY": ""}, clear=True):
            res = telegram_bot.transcribe_audio_bytes(b"dummy_bytes")
            self.assertIsNone(res)

    @patch("urllib.request.urlopen")
    def test_transcribe_audio_mocked(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"text": "Python 3.14 mimari kararı"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            text = telegram_bot.transcribe_audio_bytes(b"fake_ogg_bytes")
            self.assertEqual(text, "Python 3.14 mimari kararı")


if __name__ == "__main__":
    unittest.main()
