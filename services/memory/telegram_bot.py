#!/usr/bin/env python3
"""
Telegram Bot Bridge for mem0-local (Raspberry Pi 5 Shared Brain).

Runs 100% via outbound long-polling (NO open ports, NO port forwarding, NO ngrok).
Allows chatting with your Shared Brain, recalling facts, adding new memories,
checking rules, and auditing system health directly from your mobile phone.

Uses ONLY Python standard library (urllib, json, logging). Zero external pip dependencies!
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

MEM0_ROOT = os.path.dirname(os.path.abspath(__file__))
if MEM0_ROOT not in sys.path:
    sys.path.insert(0, MEM0_ROOT)

import assistant
import config
import rules as rules_store

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_bot")

TELEGRAM_API_BASE = "https://api.telegram.org/bot"
UNAUTHORIZED_MESSAGE = "⛔ Yetkisiz erişim. Bu bot yalnızca yetkili kullanıcılar için yapılandırılmıştır."

# In-memory per-chat active scopes: chat_id -> scope
CHAT_SCOPES: Dict[int, str] = {}
# In-memory per-chat conversation history: chat_id -> list of {"role": "user"|"assistant", "content": str}
CHAT_HISTORY: Dict[int, List[Dict[str, str]]] = {}


def get_token() -> str:
    """Retrieve Telegram Bot token from environment."""
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def get_allowed_users() -> List[str]:
    """Retrieve comma-separated whitelist of allowed usernames or user IDs."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if not raw:
        return []
    return [u.strip().lower().lstrip("@") for u in raw.split(",") if u.strip()]


def is_user_allowed(user_id: int, username: Optional[str]) -> bool:
    """Check if the requesting Telegram user is in the whitelist."""
    allowed = get_allowed_users()
    if not allowed:
        # Missing configuration must never grant access to the shared memory.
        return False
    if str(user_id) in allowed:
        return True
    if username and username.lower().lstrip("@") in allowed:
        return True
    return False


def call_telegram_api(method: str, payload: Dict[str, Any], token: Optional[str] = None, timeout: int = 35) -> Dict[str, Any]:
    """Send an HTTP POST request to Telegram Bot API."""
    bot_token = token or get_token()
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    url = f"{TELEGRAM_API_BASE}{bot_token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "mem0-telegram-bridge/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res_data = resp.read().decode("utf-8")
        return json.loads(res_data)


def download_telegram_file(file_id: str, token: Optional[str] = None) -> Optional[bytes]:
    """Download audio/voice file bytes from Telegram servers."""
    bot_token = token or get_token()
    meta = call_telegram_api("getFile", {"file_id": file_id}, token=bot_token)
    if not meta.get("ok"):
        logger.error("Failed to get file info from Telegram: %s", meta)
        return None
    file_path = meta.get("result", {}).get("file_path")
    if not file_path:
        return None
    file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    req = urllib.request.Request(file_url, headers={"User-Agent": "mem0-telegram-bridge/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def transcribe_audio_bytes(audio_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
    """Transcribe voice audio bytes using Groq Whisper-large-v3 (free) or OpenAI Whisper API."""
    api_key = os.environ.get("GROQ_API_KEY")
    endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"
    model = "whisper-large-v3"

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")
        endpoint = "https://api.openai.com/v1/audio/transcriptions"
        model = "whisper-1"

    if not api_key:
        return None

    boundary = f"----WebKitFormBoundary{int(time.time()*1000)}"
    parts = []

    # Model parameter
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode("utf-8"))

    # File payload
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: audio/ogg\r\n\r\n".encode("utf-8")
        + audio_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    payload = b"".join(parts)

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "mem0-telegram-bridge/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return (res.get("text") or "").strip()


def send_message(chat_id: int, text: str, token: Optional[str] = None, parse_mode: Optional[str] = None) -> bool:
    """Send a message to a Telegram chat."""
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        call_telegram_api("sendMessage", payload, token=token)
        return True
    except urllib.error.HTTPError as ex:
        # If Markdown parsing fails due to unmatched characters, retry without parse_mode
        if parse_mode and ex.code == 400:
            payload.pop("parse_mode", None)
            try:
                call_telegram_api("sendMessage", payload, token=token)
                return True
            except Exception as retry_ex:
                logger.error("Failed to send message on plain retry: %s", retry_ex)
        logger.error("HTTP error sending message to chat %s: %s", chat_id, ex)
        return False
    except Exception as ex:
        logger.error("Error sending message to chat %s: %s", chat_id, ex)
        return False


def get_chat_scope(chat_id: int) -> str:
    """Get active scope for the given chat."""
    return CHAT_SCOPES.get(chat_id, os.environ.get("TELEGRAM_DEFAULT_SCOPE", "personal"))


def handle_message(text: str, user_id: int, username: Optional[str], chat_id: int) -> str:
    """
    Process incoming user message text and return assistant response.
    Pure functional handler suitable for direct unit testing.
    """
    text = (text or "").strip()
    if not text:
        return "Boş mesaj alındı."

    if not is_user_allowed(user_id, username):
        logger.warning("Unauthorized access attempt from user_id=%s username=%s", user_id, username)
        return UNAUTHORIZED_MESSAGE

    scope = get_chat_scope(chat_id)

    # 1. /start & /help
    if text.startswith("/start") or text.startswith("/help"):
        return (
            "🧠 *Shared Brain — Raspberry Pi Asistanı*\n\n"
            f"📍 *Aktif Kapsam (Scope):* `{scope}`\n\n"
            "*Komutlar:*\n"
            "• `/ask <soru>` veya doğrudan mesaj yaz: Hafızaya dayalı cevap al\n"
            "• `/add <not/bilgi>`: Beyne kalıcı yeni bir hafıza kaydet\n"
            "• `/search <kelime>`: Anlamsal hafıza araması yap\n"
            "• `/rules`: Bu kapsamdaki aktif mimari kuralları listele\n"
            "• `/scope <ad>`: Aktif kapsamı değiştir (örn: `/scope mem0-local`)\n"
            "• `/health`: Hafıza sağlık ve konsolidasyon durumunu gör\n"
            "• `/clear`: Sohbet geçmişini temizle\n"
        )

    # 2. /scope
    if text.startswith("/scope"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            new_scope = parts[1].strip()
            CHAT_SCOPES[chat_id] = new_scope
            return f"✅ Aktif kapsam değiştirildi: `{new_scope}`"
        return f"📍 Mevcut aktif kapsam: `{scope}`\nDeğiştirmek için: `/scope <ad>`"

    # 3. /clear
    if text.startswith("/clear"):
        CHAT_HISTORY[chat_id] = []
        return "🧹 Sohbet geçmişi temizlendi."

    # 4. /rules
    if text.startswith("/rules"):
        active_rules = rules_store.list_rules(scope=scope)
        if not active_rules:
            return f"ℹ `{scope}` kapsamında tanımlı herhangi bir kural bulunamadı."
        lines = [f"📋 *{scope} Kuralları ({len(active_rules)} adet):*"]
        for r in active_rules:
            lines.append(f"• `[{r.get('id')}]` {r.get('rule')}")
        return "\n".join(lines)

    # 5. /health
    if text.startswith("/health"):
        try:
            import ide_bridge
            h = ide_bridge.evaluate_health_score(scope=scope)
            score = h.get("health_score", 0)
            metrics = h.get("metrics", {})
            recs = h.get("recommendations", [])
            badge = "🟢 İYİ" if score >= 80 else ("🟡 ORTA" if score >= 60 else "🔴 DİKKAT")
            rec_text = "\n".join([f"  - {r}" for r in recs]) if recs else "  - Sorun tespit edilmedi."
            return (
                f"🧠 *Hafıza Sağlık Skoru:* {score}/100 [{badge}]\n"
                f"• Kapsam: `{scope}`\n"
                f"• Toplam Hafıza: {metrics.get('total', 0)} (Aktif: {metrics.get('active', 0)})\n"
                f"• Bayat Referans: {metrics.get('stale_file_references', 0)}\n"
                f"• Graf Düğümleri: {metrics.get('graph_nodes_count', 0)}\n\n"
                f"*Öneriler:*\n{rec_text}"
            )
        except Exception as ex:
            return f"❌ Sağlık skoru hesaplanamadı: {ex}"

    # 6. /add <fact>
    if text.startswith("/add"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "Kullanım: `/add <kaydedilecek bilgi veya karar>`"
        fact = parts[1].strip()
        try:
            mem = config.get_memory()
            user_id_owner = os.environ.get("MEM0_OWNER", "default")
            mem.add(fact, user_id=user_id_owner, run_id=scope, metadata={"agent_id": "telegram", "source": "telegram_bot"})
            return f"✅ Yeni hafıza `{scope}` kapsamına kaydedildi:\n\n_{fact}_"
        except Exception as ex:
            return f"❌ Hafıza eklenirken hata oluştu: {ex}"

    # 7. /search <query>
    if text.startswith("/search"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "Kullanım: `/search <aranacak terim>`"
        query = parts[1].strip()
        try:
            ctx = assistant.recall_context(query, scope=scope, top_k=5)
            hits = ctx.get("memories", [])
            if not hits:
                return f"🔍 `{query}` için ilgili hafıza bulunamadı."
            lines = [f"🔍 *'{query}' İçin Bulunan Hafızalar:*"]
            for h in hits:
                score_str = f"({h.get('score', 0):.2f})"
                lines.append(f"• `{h.get('scope')}` {score_str}: {h.get('memory')}")
            return "\n".join(lines)
        except Exception as ex:
            return f"❌ Arama sırasında hata oluştu: {ex}"

    # 8. /ask <query> or general conversation
    question = text
    if text.startswith("/ask"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            question = parts[1].strip()
        else:
            return "Kullanım: `/ask <sorunuz>` veya doğrudan mesaj yazabilirsiniz."

    history = CHAT_HISTORY.get(chat_id, [])
    try:
        res = assistant.ask_brain(question, scope=scope, history=history, top_k=5)
        answer = res.get("answer", "Cevap üretilemedi.")
        memories = res.get("memories_used", [])

        grounding_note = ""
        if memories:
            m_count = len(memories)
            grounding_note = f"\n\n_🧠 {m_count} hafıza kaydına dayandırıldı._"

        # Update history
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        if len(history) > 10:
            history = history[-10:]
        CHAT_HISTORY[chat_id] = history

        return f"{answer}{grounding_note}"
    except Exception as ex:
        return f"⚠️ Soru yanıtlanırken hata oluştu: {ex}"


def run_polling_loop():
    """Main Telegram long-polling daemon loop."""
    token = get_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not configured! Exiting.")
        sys.exit(1)

    logger.info("Starting Telegram Bot long-polling daemon...")
    allowed_users = get_allowed_users()
    if allowed_users:
        logger.info("Whitelist enabled for: %s", ", ".join(allowed_users))
    else:
        logger.warning("No TELEGRAM_ALLOWED_USERS specified; all incoming messages will be denied.")

    offset = 0
    consecutive_errors = 0

    while True:
        try:
            updates = call_telegram_api(
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
                token=token,
                timeout=30,
            )

            if not updates.get("ok"):
                logger.error("Telegram API returned non-ok: %s", updates)
                time.sleep(3)
                continue

            consecutive_errors = 0
            results = updates.get("result", [])

            for update in results:
                update_id = update.get("update_id", 0)
                offset = max(offset, update_id + 1)

                msg = update.get("message")
                if not msg:
                    continue

                text = msg.get("text", "")
                voice = msg.get("voice") or msg.get("audio")
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                from_user = msg.get("from", {})
                user_id = from_user.get("id")
                username = from_user.get("username")

                if not chat_id:
                    continue

                # Reject unauthorized updates before downloading or transcribing voice messages.
                if not is_user_allowed(user_id, username):
                    logger.warning("Unauthorized access attempt from user_id=%s username=%s", user_id, username)
                    send_message(chat_id, UNAUTHORIZED_MESSAGE, token=token)
                    continue

                # Voice note transcription support
                if not text and voice:
                    file_id = voice.get("file_id")
                    if file_id:
                        send_message(chat_id, "🎙️ _Sesli mesaj dinleniyor ve metne aktarılıyor..._", token=token, parse_mode="Markdown")
                        audio_data = download_telegram_file(file_id, token=token)
                        if audio_data:
                            transcribed = transcribe_audio_bytes(audio_data)
                            if transcribed:
                                text = transcribed
                                logger.info("Transcribed voice note: %s", text[:80])
                            else:
                                send_message(
                                    chat_id,
                                    "⚠️ Sesli mesaj metne dönüştürülemedi. Lütfen ~/.mem0/config.env içinde GROQ_API_KEY veya OPENAI_API_KEY tanımlı olduğundan emin olun.",
                                    token=token,
                                )
                                continue
                        else:
                            send_message(chat_id, "❌ Ses dosyası indirilemedi.", token=token)
                            continue

                if not text:
                    continue

                logger.info("Message from %s (%s): %s", username or user_id, chat_id, text[:60])
                reply = handle_message(text, user_id=user_id, username=username, chat_id=chat_id)
                send_message(chat_id, reply, token=token, parse_mode="Markdown")

        except urllib.error.URLError as ex:
            consecutive_errors += 1
            wait_time = min(30, 2 ** consecutive_errors)
            logger.warning("Network connection issue: %s. Retrying in %ss...", ex, wait_time)
            time.sleep(wait_time)
        except Exception as ex:
            consecutive_errors += 1
            wait_time = min(30, 2 ** consecutive_errors)
            logger.exception("Unexpected error in polling loop: %s. Retrying in %ss...", ex, wait_time)
            time.sleep(wait_time)


if __name__ == "__main__":
    run_polling_loop()
