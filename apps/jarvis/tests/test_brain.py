"""brain.classify testleri — ollama.Client mock'lu (gerçek LLM çağrısı yok)."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from arda import brain
from arda.brain import classify
from arda.models import Intent

CFG = {
    "brain": {
        "model": "qwen3:8b",
        "host": "http://localhost:11434",
        "think": False,
        "keep_alive": "10m",
    }
}

_VALID_PAYLOAD = {
    "intent": "note",
    "category": "internship",
    "title": "ORS Staj Notu",
    "clean_text": "Üretim hattında rulman taşlama incelendi.",
    "tags": ["staj", "ORS"],
    "follow_up": ["Yüzey pürüzlülüğü"],
}


def _mock_response(payload: dict) -> SimpleNamespace:
    msg = SimpleNamespace(content=json.dumps(payload))
    return SimpleNamespace(message=msg)


def _patch_client(chat_return=None, chat_side_effect=None):
    """arda.brain.ollama.Client'i mock'lar; (ctx, Client_mock, chat_mock) döner."""
    client_instance = MagicMock()
    if chat_side_effect is not None:
        client_instance.chat.side_effect = chat_side_effect
    else:
        client_instance.chat.return_value = chat_return
    client_cls = MagicMock(return_value=client_instance)
    return patch("arda.brain.ollama.Client", client_cls), client_cls, client_instance.chat


class TestClassifyHappyPath:
    def test_returns_intent_on_valid_response(self):
        ctx, _, _ = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            result = classify("bugün ORS'de rulman gördük", cfg=CFG)
        assert isinstance(result, Intent)
        assert result.intent == "note"
        assert result.category == "internship"
        assert result.title == "ORS Staj Notu"

    def test_passes_model_from_cfg(self):
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=CFG)
        assert chat.call_args.kwargs["model"] == "qwen3:8b"

    def test_passes_think_false_from_cfg(self):
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=CFG)
        assert chat.call_args.kwargs.get("think") is False

    def test_format_schema_is_dict(self):
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=CFG)
        fmt = chat.call_args.kwargs.get("format")
        assert isinstance(fmt, dict)
        assert "properties" in fmt

    def test_honors_configured_host(self):
        """cfg.brain.host, Ollama Client'a iletilmeli (uzak/özel sunucu desteği)."""
        ctx, client_cls, _ = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=CFG)
        assert client_cls.call_args.kwargs.get("host") == "http://localhost:11434"


class TestRouterModel:
    """Router (classify) ayrı, küçük/hızlı modele alınabilir (cfg.brain.router_model);
    sohbet (answer) ve sentez ANA modelde kalır → router gecikmesi düşer (Batch 1)."""

    def test_classify_uses_router_model_when_set(self):
        cfg = {"brain": dict(CFG["brain"], router_model="qwen3:1.7b")}
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=cfg)
        assert chat.call_args.kwargs["model"] == "qwen3:1.7b"

    def test_classify_falls_back_to_model_without_router_model(self):
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=CFG)
        assert chat.call_args.kwargs["model"] == "qwen3:8b"

    def test_answer_ignores_router_model(self):
        from arda.brain import answer

        cfg = {"brain": dict(CFG["brain"], router_model="qwen3:1.7b")}
        ctx, _, chat = _patch_client(SimpleNamespace(message=SimpleNamespace(content="selam!")))
        with ctx:
            answer("selam", cfg=cfg)
        assert chat.call_args.kwargs["model"] == "qwen3:8b"   # sohbet ANA modelde kalır


class TestSiriRouting:
    def test_action_payload_becomes_action_intent(self):
        payload = dict(
            _VALID_PAYLOAD, intent="action", action="open_app", params={"name": "Safari"}
        )
        ctx, _, _ = _patch_client(_mock_response(payload))
        with ctx:
            result = classify("Safari'yi aç", cfg=CFG)
        assert result.intent == "action"
        assert result.action == "open_app"
        assert result.params == {"name": "Safari"}

    def test_schema_exposes_action_enum_and_params(self):
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("metin", cfg=CFG)
        props = chat.call_args.kwargs["format"]["properties"]
        assert "action" in props and "params" in props
        assert "action" in props["intent"]["enum"]   # router 'action'a yönlendirebilir


class TestConversationContext:
    def test_history_is_sent_as_prior_messages(self):
        from arda.conversation import Turn

        history = (
            Turn(user="Chrome'u aç", assistant="Chrome açılıyor."),
        )
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("YouTube'a gir", cfg=CFG, history=history)
        messages = chat.call_args.kwargs["messages"]
        # system → önceki user → önceki assistant → güncel user
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "Chrome'u aç"}
        assert messages[2] == {"role": "assistant", "content": "Chrome açılıyor."}
        assert messages[-1] == {"role": "user", "content": "YouTube'a gir"}

    def test_no_history_matches_single_turn_shape(self):
        # Geçmiş yoksa davranış eski tek-tur haliyle birebir aynı (geriye uyumlu).
        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("merhaba", cfg=CFG)
        messages = chat.call_args.kwargs["messages"]
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[-1]["content"] == "merhaba"


class TestAnswerEngine:
    """answer() — router'dan AYRI serbest sohbet motoru (schema yok, yüksek temp)."""

    def _resp(self, content):
        return SimpleNamespace(message=SimpleNamespace(content=content))

    def test_returns_stripped_content(self):
        from arda.brain import answer

        ctx, _, _ = _patch_client(self._resp("  Saat şu an 14:30.  "))
        with ctx:
            result = answer("saat kaç?", cfg=CFG)
        assert result == "Saat şu an 14:30."

    def test_does_not_force_schema_and_uses_higher_temp(self):
        from arda.brain import answer

        cfg = {"brain": {"model": "qwen3:8b", "answer_temperature": 0.7}}
        ctx, _, chat = _patch_client(self._resp("merhaba!"))
        with ctx:
            answer("selam", cfg=cfg)
        kwargs = chat.call_args.kwargs
        assert "format" not in kwargs                      # serbest → schema YOK
        assert kwargs["options"]["temperature"] == 0.7     # doğallık için yüksek temp

    def test_uses_answer_system_prompt_and_history(self):
        from arda.brain import answer
        from arda.conversation import Turn

        ctx, _, chat = _patch_client(self._resp("Evet, yarın da var."))
        with ctx:
            answer("peki yarın?", cfg=CFG, history=(Turn("bugün dersim var mı?", "Evet"),))
        messages = chat.call_args.kwargs["messages"]
        assert "DOĞAL" in messages[0]["content"]           # answer system prompt'u
        assert messages[1] == {"role": "user", "content": "bugün dersim var mı?"}
        assert messages[-1]["content"] == "peki yarın?"

    def test_empty_content_raises_brain_error(self):
        from arda.brain import BrainError, answer

        ctx, _, _ = _patch_client(self._resp("   "))
        with ctx:
            with pytest.raises(BrainError):
                answer("selam", cfg=CFG)

    def test_ollama_error_raises_brain_error(self):
        import ollama

        from arda.brain import BrainError, answer

        ctx, _, _ = _patch_client(chat_side_effect=ollama.ResponseError("down"))
        with ctx:
            with pytest.raises(BrainError):
                answer("selam", cfg=CFG)


class TestAnswerStream:
    """answer_stream() — answer() gibi ama AKAN: token parçalarını yield eder →
    çağıran cümle cümle seslendirip ilk-sese-kadar gecikmeyi düşürür (Batch 1)."""

    def _parts(self, *contents):
        return [SimpleNamespace(message=SimpleNamespace(content=c)) for c in contents]

    def test_yields_content_pieces(self):
        from arda.brain import answer_stream

        ctx, _, _ = _patch_client(self._parts("Mer", "haba", "."))
        with ctx:
            out = list(answer_stream("selam", cfg=CFG))
        assert "".join(out) == "Merhaba."

    def test_requests_streaming_and_no_schema(self):
        from arda.brain import answer_stream

        ctx, _, chat = _patch_client(self._parts("x"))
        with ctx:
            list(answer_stream("selam", cfg=CFG))
        assert chat.call_args.kwargs["stream"] is True
        assert "format" not in chat.call_args.kwargs        # serbest → schema YOK

    def test_uses_main_model_not_router(self):
        from arda.brain import answer_stream

        cfg = {"brain": dict(CFG["brain"], router_model="qwen3:1.7b")}
        ctx, _, chat = _patch_client(self._parts("x"))
        with ctx:
            list(answer_stream("selam", cfg=cfg))
        assert chat.call_args.kwargs["model"] == "qwen3:8b"  # sohbet ANA modelde

    def test_connection_error_raises_brain_error(self):
        import ollama

        from arda.brain import BrainError, answer_stream

        ctx, _, _ = _patch_client(chat_side_effect=ollama.ResponseError("down"))
        with ctx:
            with pytest.raises(BrainError):
                list(answer_stream("selam", cfg=CFG))


class TestDeterministicReminderTime:
    """set_reminder.at göreli ifadeyle DETERMİNİSTİK düzeltilir (LLM aritmetiğine güvenme)."""

    def _reminder(self, at):
        return dict(
            _VALID_PAYLOAD, intent="action", action="set_reminder",
            params={"at": at, "text": "toplantı"},
        )

    def test_overrides_offset_from_raw_text(self):
        from datetime import datetime

        # LLM yanlış hesaplamış olsun; '10 dakika sonra' deterministik ezsin.
        ctx, _, _ = _patch_client(_mock_response(self._reminder("2026-06-20T15:00")))
        with ctx:
            result = classify("10 dakika sonra ilaç hatırlat", cfg=CFG,
                              now=lambda: datetime(2026, 6, 20, 14, 30))
        assert result.params["at"] == "2026-06-20T14:40"

    def test_keeps_llm_at_when_no_relative_expression(self):
        from datetime import datetime

        ctx, _, _ = _patch_client(_mock_response(self._reminder("2026-06-25T09:00")))
        with ctx:
            result = classify("doktor randevusunu hatırlat", cfg=CFG,
                              now=lambda: datetime(2026, 6, 20, 14, 30))
        assert result.params["at"] == "2026-06-25T09:00"   # göreli ifade yok → LLM korunur

    def test_non_reminder_action_untouched(self):
        payload = dict(_VALID_PAYLOAD, intent="action", action="open_app",
                       params={"name": "Safari"})
        ctx, _, _ = _patch_client(_mock_response(payload))
        with ctx:
            result = classify("yarın Safari'yi aç", cfg=CFG)
        assert result.params == {"name": "Safari"}         # set_reminder değil → dokunulmaz


class TestRouterTimeInjection:
    def test_classify_injects_current_time_for_reminders(self):
        from datetime import datetime

        ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
        with ctx:
            classify("yarın 8'de hatırlat", cfg=CFG, now=lambda: datetime(2026, 6, 19, 14, 30))
        system = chat.call_args.kwargs["messages"][0]["content"]
        assert "2026-06-19T14:30" in system     # model göreli zamanı buna göre çözer
        assert "set_reminder" in system


class TestHistoryWindow:
    def test_only_last_8_turns_sent(self):
        from arda.brain import _MAX_HISTORY_TURNS, _build_messages
        from arda.conversation import Turn

        history = tuple(Turn(f"u{i}", f"a{i}") for i in range(20))
        messages = _build_messages("şimdi", history)
        # system + (8 tur * 2) + güncel = 18 mesaj; en eski turlar kesilir.
        assert len(messages) == 1 + _MAX_HISTORY_TURNS * 2 + 1
        assert messages[1]["content"] == "u12"   # ilk 12 tur düştü
        assert messages[-1]["content"] == "şimdi"


class TestClassifyErrorHandling:
    def test_raises_on_invalid_json_content(self):
        bad = SimpleNamespace(message=SimpleNamespace(content="bu json değil {{{"))
        ctx, _, _ = _patch_client(bad)
        with ctx:
            with pytest.raises(Exception):
                classify("metin", cfg=CFG)

    def test_raises_on_missing_fields_in_response(self):
        ctx, _, _ = _patch_client(_mock_response({"intent": "note"}))
        with ctx:
            with pytest.raises(ValueError):
                classify("metin", cfg=CFG)

    def test_raises_on_ollama_error(self):
        import ollama

        ctx, _, _ = _patch_client(chat_side_effect=ollama.ResponseError("model bulunamadı"))
        with ctx:
            with pytest.raises(ollama.ResponseError):
                classify("metin", cfg=CFG)


class TestActionMemoryInMessages:
    """ADIM 2: turn.action_result _build_messages'ta modele okunabilir biçimde iletilir.
    Hem router hem answer aynı _build_messages'ı kullandığı için ikisi de görür."""

    def test_action_result_rendered_into_assistant_message(self):
        from arda.brain import _build_messages
        from arda.conversation import Turn

        ar = {"action": "set_reminder", "success": True,
              "text": "panelde MT3608", "at": "2026-06-20T08:00", "id": "x-1"}
        turn = Turn("yarın 8'de panelde MT3608 hatırlat", "Tamam, 08:00 için kurdum.", ar)
        messages = _build_messages("şimdi", (turn,))
        assistant = messages[2]
        assert assistant["role"] == "assistant"
        assert "set_reminder" in assistant["content"]
        assert "panelde MT3608" in assistant["content"]   # başlık modele iletildi → atıf çözülebilir

    def test_no_action_result_leaves_plain_assistant_message(self):
        from arda.brain import _build_messages
        from arda.conversation import Turn

        messages = _build_messages("şimdi", (Turn("selam", "merhaba"),))
        assert messages[2]["content"] == "merhaba"        # eylem yoksa düz metin (geriye uyumlu)

    def test_failed_action_marked_in_message(self):
        from arda.brain import _build_messages
        from arda.conversation import Turn

        ar = {"action": "set_reminder", "success": False, "text": "x"}
        messages = _build_messages("şimdi", (Turn("hatırlat", "kuramadım", ar),))
        assert "BAŞARISIZ" in messages[2]["content"]      # model 'kuramamıştım' bağlamını bilir


class TestDeleteFromMemory:
    """ADIM 3: 'az önce kurduğum hatırlatıcıyı sil' → router geçmişteki set_reminder
    bağlamını görür. Modelin asıl niyet kararı LLM'e ait (mock'lu); burada başlık/id'nin
    router'a İLETİLDİĞİ ve delete_reminder Intent'inin doğru taşındığı doğrulanır."""

    def test_prior_reminder_title_reaches_router_and_intent_round_trips(self):
        from arda.conversation import Turn

        ar = {"action": "set_reminder", "success": True,
              "text": "panelde MT3608", "at": "2026-06-20T08:00", "id": "x-1"}
        history = (Turn("yarın 8'de panelde MT3608 hatırlat", "Tamam, kurdum.", ar),)
        payload = dict(_VALID_PAYLOAD, intent="action", action="delete_reminder",
                       params={"title": "panelde MT3608"})
        ctx, _, chat = _patch_client(_mock_response(payload))
        with ctx:
            result = classify("az önce kurduğumu sil", cfg=CFG, history=history)
        joined = " ".join(m["content"] for m in chat.call_args.kwargs["messages"])
        assert "panelde MT3608" in joined and "set_reminder" in joined   # bağlam router'a iletildi
        assert result.action == "delete_reminder"
        assert result.params["title"] == "panelde MT3608"


class _CapClient:
    """ollama.Client yerine geçer; gönderilen messages'ı yakalar."""
    last_messages = None

    def __init__(self, *a, **k):
        pass

    def chat(self, *, model, messages, **kw):
        _CapClient.last_messages = messages
        class _R:
            class message:
                content = "cevap"
        return _R()


def test_answer_injects_memories_into_system(monkeypatch):
    monkeypatch.setattr(brain.ollama, "Client", _CapClient)
    out = brain.answer("selam", cfg={}, memories=("ben example-user", "kardeşim ayşe"))
    assert out == "cevap"
    sys_msg = _CapClient.last_messages[0]
    assert sys_msg["role"] == "system"
    assert "ben example-user" in sys_msg["content"]
    assert "kardeşim ayşe" in sys_msg["content"]


def test_answer_without_memories_has_no_recall_block(monkeypatch):
    monkeypatch.setattr(brain.ollama, "Client", _CapClient)
    brain.answer("selam", cfg={})
    assert "Hatırladıkların" not in _CapClient.last_messages[0]["content"]


def test_answer_injects_profile_about(monkeypatch):
    monkeypatch.setattr(brain.ollama, "Client", _CapClient)
    brain.answer("selam", cfg={"profile": {"about": "Adı Example User."}})
    assert "Adı Example User." in _CapClient.last_messages[0]["content"]


def test_classify_injects_profile_about_and_commands():
    ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
    cfg = {**CFG, "profile": {
        "about": "Adı Example User.",
        "commands": [{"say": "not düş", "intent": "note"}],
    }}
    with ctx:
        classify("metin", cfg=cfg)
    system_msg = chat.call_args.kwargs["messages"][0]["content"]
    assert "Adı Example User." in system_msg
    assert '- "not düş" -> note' in system_msg


def test_empty_profile_leaves_router_system_unchanged():
    ctx, _, chat = _patch_client(_mock_response(_VALID_PAYLOAD))
    with ctx:
        classify("metin", cfg=CFG)          # CFG'de profile yok
    system_msg = chat.call_args.kwargs["messages"][0]["content"]
    assert "Kullanıcı hakkında" not in system_msg
    assert "sık komutları" not in system_msg


def _ollama_up() -> bool:
    """Canlı testler için Ollama erişilebilir mi (localhost:11434)."""
    import socket
    from pathlib import Path

    # These live tests call load_config(); a listening Ollama without the
    # developer's ignored local vault configuration is not a runnable setup.
    if not (Path(__file__).resolve().parents[1] / "config.local.yaml").is_file():
        return False

    try:
        with socket.create_connection(("localhost", 11434), timeout=0.5):
            return True
    except OSError:
        return False


_OLLAMA = pytest.mark.skipif(not _ollama_up(), reason="canlı test: Ollama (localhost:11434) gerekli")


class TestAnsweredVsNote:
    """Bug regresyon guard'ı: sohbet/his bildiren girdiler ARŞİVLENMEMELİ (answered);
    note/task YALNIZCA açık kayıt fiiliyle. Davranış canlı modele bağlı → Ollama yoksa
    canlı testler skip; prompt'un açık-kayıt kuralını içerdiği deterministik doğrulanır."""

    def test_prompt_prioritizes_answered_and_requires_explicit_save(self):
        # Deterministik (Ollama gerekmez): kök-neden düzeltmesi prompt'ta duruyor mu.
        from arda.brain import _INTENTS, _SYSTEM_PROMPT

        assert _INTENTS[0] == "answered"            # enum'da sohbet önce → arşive meyil azalır
        assert "ÖNCELİK" in _SYSTEM_PROMPT           # belirsizlikte answered kuralı
        assert "YALNIZCA" in _SYSTEM_PROMPT          # note/task açık kayıt şartı

    @_OLLAMA
    def test_chat_inputs_are_not_archived(self):
        from arda.brain import classify
        from arda.config import load_config

        cfg = load_config()
        for text in ["naber", "bugün çok yorgunum", "film izlemek istiyorum", "şu an canım sıkkın"]:
            intent = classify(text, cfg=cfg).intent
            assert intent not in ("note", "task"), f"sohbet arşivlendi: {text!r} -> {intent}"

    @_OLLAMA
    def test_explicit_save_is_note(self):
        from arda.brain import classify
        from arda.config import load_config

        assert classify("şunu not al: süt almayı unutma", cfg=load_config()).intent == "note"

    @_OLLAMA
    def test_explicit_task_is_task(self):
        from arda.brain import classify
        from arda.config import load_config

        assert classify("bunu yapılacaklara ekle: sunumu bitir", cfg=load_config()).intent == "task"


class TestResearchRouting:
    """research intent: açık araştırma isteği veya güncel/web bilgisi → research; düz sohbet değil."""

    def test_research_in_intent_enum_and_prompt(self):
        from arda.brain import _INTENTS, _SYSTEM_PROMPT

        assert "research" in _INTENTS
        assert "research" in _SYSTEM_PROMPT and "araştır" in _SYSTEM_PROMPT.lower()

    @_OLLAMA
    def test_explicit_research_request_routes_to_research(self):
        from arda.brain import classify
        from arda.config import load_config

        intent = classify("yapay zeka haberlerini araştır", cfg=load_config())
        assert intent.intent == "research"
        assert intent.clean_text.strip()        # arama sorgusu dolu

    @_OLLAMA
    def test_plain_chat_is_not_research(self):
        from arda.brain import classify
        from arda.config import load_config

        assert classify("naber", cfg=load_config()).intent != "research"


class TestSynthesize:
    """brain.synthesize — kaynak-kısıtlı araştırma sentezi (schema yok, kaynaklar bağlam)."""

    def _resp(self, content):
        return SimpleNamespace(message=SimpleNamespace(content=content))

    def _sources(self):
        from arda.research import Source

        return [
            Source("Python", "https://python.org", "Python bir dildir.", "tavily"),
            Source("Docs", "https://docs.python.org", "Resmi dokümanlar.", "tavily"),
        ]

    def test_sources_are_numbered_in_user_message(self):
        from arda.brain import synthesize

        ctx, _, chat = _patch_client(self._resp("Python bir dildir [1]."))
        with ctx:
            synthesize("python nedir", self._sources(), cfg=CFG)
        messages = chat.call_args.kwargs["messages"]
        user = messages[-1]["content"]
        assert "https://python.org" in user and "[1]" in user and "[2]" in user

    def test_source_constrained_system_prompt(self):
        from arda.brain import synthesize

        ctx, _, chat = _patch_client(self._resp("cevap"))
        with ctx:
            synthesize("q", self._sources(), cfg=CFG)
        assert "kaynak" in chat.call_args.kwargs["messages"][0]["content"].lower()

    def test_no_schema_forced(self):
        from arda.brain import synthesize

        ctx, _, chat = _patch_client(self._resp("cevap"))
        with ctx:
            synthesize("q", self._sources(), cfg=CFG)
        assert "format" not in chat.call_args.kwargs        # serbest sentez → schema yok

    def test_returns_stripped_content(self):
        from arda.brain import synthesize

        ctx, _, _ = _patch_client(self._resp("  Cevap [1].  "))
        with ctx:
            assert synthesize("q", self._sources(), cfg=CFG) == "Cevap [1]."

    def test_empty_content_raises_brain_error(self):
        from arda.brain import BrainError, synthesize

        ctx, _, _ = _patch_client(self._resp("   "))
        with ctx:
            with pytest.raises(BrainError):
                synthesize("q", self._sources(), cfg=CFG)
