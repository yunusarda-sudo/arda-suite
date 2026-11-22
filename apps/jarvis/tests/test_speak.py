"""speak testleri — render (say → WAV) mock'lu, gerçek ses donanımı yok.

Yeni mimari: metin `say` ile WAV'e RENDER edilir, TEK kalıcı sounddevice akışında
çalınır. Testler render subprocess'ini ve _play/_get_stream'i mock'lar."""
import subprocess
from unittest.mock import MagicMock, patch

import numpy as np

import arda.speak as speak
from arda.speak import say, interrupt, is_speaking


def _fake_render_ok(monkeypatch_target="arda.speak._render"):
    """Sabit kısa int16 sinyal döndüren _render yaması bağlamı."""
    samples = (np.ones(2000, dtype=np.int16) * 1000)
    return patch(monkeypatch_target, return_value=samples)


def _silence_play():
    """Gerçek ses cihazına yazmayı engelle."""
    return patch("arda.speak._play")


# --- render komutu ('say' çağrısı) ---

def test_say_renders_then_plays():
    with patch("arda.speak._render", return_value=np.ones(100, np.int16)) as rnd, \
         _silence_play() as play:
        say("Merhaba")
    rnd.assert_called_once()
    assert rnd.call_args.args[0] == "Merhaba"
    assert rnd.call_args.args[1] == "Yelda"  # varsayılan ses
    play.assert_called_once()


def test_render_builds_say_wav_command():
    """_render 'say ... --file-format=WAVE --data-format=LEI16@22050 -o <wav>' kurmalı."""
    proc = MagicMock(returncode=0, stderr=b"")
    fake_wav = (np.ones(50, np.int16), speak._SR)
    with patch("arda.platform_runtime.system", return_value="Darwin"), \
         patch("arda.speak.subprocess.run", return_value=proc) as run, \
         patch("soundfile.read", return_value=fake_wav):
        out = speak._render("Not kaydedildi.", "Yelda")
    cmd = run.call_args.args[0]
    assert cmd[0] == "say"
    assert "Yelda" in cmd
    assert "Not kaydedildi." in cmd
    assert "--file-format=WAVE" in cmd
    assert any("LEI16@" in c for c in cmd)
    assert out is not None and len(out) == 50


def test_render_returns_none_on_failure():
    proc = MagicMock(returncode=1, stderr=b"boom")
    with patch("arda.speak.subprocess.run", return_value=proc):
        assert speak._render("metin", "Yelda") is None


def test_say_uses_custom_voice():
    with patch("arda.speak._render", return_value=np.ones(100, np.int16)) as rnd, \
         _silence_play():
        say("Hello", voice="Samantha")
    assert rnd.call_args.args[1] == "Samantha"


def test_say_falls_back_to_legacy_when_render_fails():
    """Render None dönerse düz `say` ile yine konuşulmalı (asistan sessiz kalmasın)."""
    with patch("arda.speak._render", return_value=None), \
         patch("arda.speak._legacy_say") as legacy, \
         _silence_play():
        say("metin")
    legacy.assert_called_once()
    assert legacy.call_args.args[0] == "metin"


def test_say_strips_markdown_before_render():
    with patch("arda.speak._render", return_value=np.ones(100, np.int16)) as rnd, \
         _silence_play():
        say("**Merhaba** ve *hoş geldin*")
    text_arg = rnd.call_args.args[0]
    assert "*" not in text_arg
    assert "Merhaba" in text_arg


def test_say_empty_text_is_noop():
    with patch("arda.speak._render") as rnd, _silence_play():
        say("   ")
    rnd.assert_not_called()


def test_interrupt_reports_speaking_state_and_sets_stop():
    speak._stop.clear()
    speak._speaking.set()
    try:
        assert interrupt() is True          # konuşuyordu
        assert speak._stop.is_set()         # kesme sinyali kondu
    finally:
        speak._speaking.clear()
    speak._stop.clear()
    assert interrupt() is False             # boştaydı


def test_is_speaking_reflects_event():
    speak._speaking.clear()
    assert is_speaking() is False
    speak._speaking.set()
    try:
        assert is_speaking() is True
    finally:
        speak._speaking.clear()


def test_shape_limits_peak_and_fades():
    """_shape tepe seviyeyi sınırlar (clipping önler) ve uçlara fade uygular."""
    loud = (np.ones(1000, dtype=np.int16) * 32767)
    out = speak._shape(loud)
    assert int(np.abs(out).max()) <= int(speak._PEAK_LIMIT * 32767) + 1
    assert out[0] == 0                      # giriş fade → ilk örnek ~0
    assert out[-1] == 0                     # çıkış fade → son örnek ~0


class TestIterSentences:
    """Akan LLM token'larını TAM cümlelere böler → ilk-sese-kadar gecikmeyi düşürür."""

    def test_single_sentence_without_terminator_yields_at_end(self):
        from arda.speak import iter_sentences
        assert list(iter_sentences(["Merhaba", " dünya"])) == ["Merhaba dünya"]

    def test_splits_two_sentences_across_chunks(self):
        from arda.speak import iter_sentences
        out = list(iter_sentences(["Mer", "haba. ", "Nasıl", "sın?"]))
        assert out == ["Merhaba.", "Nasılsın?"]

    def test_multiple_sentences_in_one_chunk(self):
        from arda.speak import iter_sentences
        assert list(iter_sentences(["Bir. İki! Üç?"])) == ["Bir.", "İki!", "Üç?"]

    def test_decimal_number_is_not_split(self):
        from arda.speak import iter_sentences
        # '3.14' içindeki nokta rakamla devam ediyor → cümle sonu DEĞİL.
        assert list(iter_sentences(["Pi ", "3.14 ", "civarında."])) == ["Pi 3.14 civarında."]

    def test_empty_chunks_ignored(self):
        from arda.speak import iter_sentences
        assert list(iter_sentences(["", "Selam.", "", " Naber?"])) == ["Selam.", "Naber?"]

    def test_empty_stream_yields_nothing(self):
        from arda.speak import iter_sentences
        assert list(iter_sentences([])) == []

    def test_whitespace_only_tail_not_yielded(self):
        from arda.speak import iter_sentences
        assert list(iter_sentences(["Bitti.", "   \n"])) == ["Bitti."]


class TestBargeInterruptLifecycle:
    """Barge-in / 'Durdur' bayrağının tur boyunca YAŞAMASI.

    Kök neden (regresyon): say() her çağrısında interrupt()+_stop.clear() yapıyordu.
    Barge-in bir cümleyi kesiyor, ama pipeline'ın SONRAKİ cümlesi say() ile bayrağı
    silip konuşmaya devam ediyordu → 'sözünü kesince susmuyor'."""

    def teardown_method(self):
        speak.reset_interrupt()

    def test_say_preserves_interrupt_flag(self):
        """interrupt() sonrası say() bayrağı SİLMEZ → tur kesik kalır."""
        speak.reset_interrupt()
        speak.interrupt()
        with _fake_render_ok(), _silence_play():
            say("Yeni cümle.")
        assert speak.was_interrupted() is True

    def test_say_is_noop_while_interrupted(self):
        """Kesilmiş turda say() hiç render/oynatma yapmaz (yarış koruması)."""
        speak.reset_interrupt()
        speak.interrupt()
        with _fake_render_ok() as render, _silence_play():
            say("Konuşmamalı.")
        render.assert_not_called()

    def test_normal_say_does_not_set_interrupt_flag(self):
        """say()'in 'önceki sesi kes' davranışı barge bayrağını KİRLETMEZ."""
        speak.reset_interrupt()
        with _fake_render_ok(), _silence_play():
            say("Bir.")
        assert speak.was_interrupted() is False

    def test_reset_interrupt_clears_both_flags(self):
        speak.interrupt()
        speak.reset_interrupt()
        assert speak.was_interrupted() is False
        assert speak._stop.is_set() is False


class TestStreamSession:
    """Akan cevapta cümleler arası barge-in penceresi.

    Kök neden: cümleler arası _speaking clear oluyor ve sonraki cümlenin `say` render'ı
    ~1s sürüyor. record_barge_in `while is_speaking()` ile o boşlukta çıkıp ölüyordu."""

    def teardown_method(self):
        speak.end_stream()

    def test_is_active_true_between_sentences(self):
        speak.begin_stream()
        assert speak.is_speaking() is False   # şu an ses çalmıyor (render boşluğu)
        assert speak.is_active() is True      # ama akış oturumu sürüyor

    def test_is_active_false_after_stream_ends(self):
        speak.begin_stream()
        speak.end_stream()
        assert speak.is_active() is False

    def test_is_active_true_while_speaking_without_session(self):
        """Akış dışı tek say() da aktif sayılır (barge-in yine mümkün)."""
        speak._speaking.set()
        try:
            assert speak.is_active() is True
        finally:
            speak._speaking.clear()
