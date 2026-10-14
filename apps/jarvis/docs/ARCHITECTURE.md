# Arda / Jarvis — Mimari Dokümanı

macOS üzerinde çalışan, **yerel** (offline-first) sesli masaüstü asistanı.
Tetikleme → kayıt → STT → LLM yönlendirme → eylem/araştırma/not → TTS akışı ile çalışır.
Tüm beyin (LLM) **Ollama** üzerinden lokal koşar; STT Apple Silicon'da in-process (MLX).

İki ayrı "ajan" katmanı vardır ve karıştırılmamalı:

1. **Asistan ajanı** (`agent.py` + `pipeline.py`) — her-zaman-dinle / bas-konuş döngüsü, tek-turlu sesli komut işler.
2. **Çok-adımlı agent alt sistemi** (`agent_*.py`) — bir kullanıcı isteğini **planla → izin → çalıştır → doğrula → denetle** döngüsüyle yürüten otonom masaüstü ajanı (dosya/kabuk/AppleScript/araç kullanımı).

---

## 1. Katman Haritası (yüksek seviye)

```
┌──────────────────────────────────────────────────────────────────────┐
│  UI KATMANI — menubar.py (rumps)                                       │
│  ikon/başlık = AgentState · Kaydet(⌘⇧A) · wake toggle · ses · VAD · vault│
│  Worker thread → queue.Queue → ana thread (AppKit runloop) 0.2s drain  │
├──────────────────────────────────────────────────────────────────────┤
│  KONTROL KATMANI — state.py (Controls)                                 │
│  wake_enabled · request_manual() · wait_for_manual() · set_busy_for()  │
├──────────────────────────────────────────────────────────────────────┤
│  ASİSTAN DÖNGÜSÜ — agent.py (run_forever / run_once)                   │
│  trigger → record → process → follow-up oturumu → cleanup → tekrar     │
├──────────────────────────────────────────────────────────────────────┤
│  SES BORU HATTI — pipeline.py (process_once)                           │
│  1.Transcribe 2.Stop-phrase 3.Classify 4.Route 5.Speak  (asla raise etmez)│
├──────────────────────────────────────────────────────────────────────┤
│  BEYİN — brain.py (Ollama)                                             │
│  classify(router/JSON) · answer(serbest sohbet) · synthesize(araştırma) │
├──────────────────────────────────────────────────────────────────────┤
│  ÇOK-ADIMLI AGENT — agent_*.py                                         │
│  planner → permission → executor → tools → verifier → audit            │
├──────────────────────────────────────────────────────────────────────┤
│  DEPOLAMA — notes.py + vault/ (Obsidian markdown)                      │
│  write_note · write_research_note · append_task · save_to_inbox (fallback)│
├──────────────────────────────────────────────────────────────────────┤
│  KONFİG — config.yaml + config.local.yaml + constants.py               │
└──────────────────────────────────────────────────────────────────────┘
```

Dosya sayısı: `arda/` altında ~24 modül, ~4.2k satır. "Çok küçük dosya > az büyük dosya" prensibi izlenmiş.

---

## 2. Çalışma Zamanı Akışı (uçtan uca)

```
[1] TETİK   trigger.py: wait_for_trigger(controls, listener_provider)
     ├─ wake AÇIK  → listener_provider() lazy → listener.wait_for_wake(...)
     │               (oww | whisper | vosk | parakeet backend)
     └─ wake KAPALI → mikrofon kapalı, sadece controls.wait_for_manual() (bas-konuş)
          listener hatası → uyarı log → wake'i kapat → bas-konuş'a düş (graceful)

[2] KAYIT   listen.py: record(cfg) → WAV
     VAD enerji eşiği + sessizlik limiti; barge-in için record_barge_in (TTS sırasında dinler)

[3] STT     transcribe.py: Transcriber | ParakeetTranscriber → metin
     whisper (mlx-whisper, çok dil/TR) veya parakeet (mlx, EN, hızlı, paylaşımlı model)

[4] BEYİN   brain.classify(text, history, now) → Intent  (Ollama, temp=0.1, JSON şema)

[5] YÖNLENDİRME  pipeline.process_once → intent'e göre dallan:
     note      → write_note()            → "… kaydedildi"
     task      → append_task()           → "… eklendi"
     answered  → brain.answer()          → serbest sohbet cevabı (temp=0.7, şemasız)
     action    → actions.run_action()    → whitelist eylem (open_app, set_reminder…)
     research  → research()+synthesize()+write_research_note() → kaynaklı özet
     (hata)    → save_to_inbox()         → veri asla kaybolmaz + özür

[6] TTS     speak.say(text) → macOS `say -v <voice>`  (markdown temizlenir, kesilebilir)

[7] HAFIZA  conversation.remember(user, assistant, action_result)
     deque(maxlen=8), TTL=300s; 5 dk boşta kalınca bağlam silinir

[8] TEMİZLİK  wav.unlink() → [1]'e dön
```

### State makinesi (`models.AgentState`)
`IDLE → WOKE → RECORDING → TRANSCRIBING → THINKING → DONE/ERROR`, ayrıca `ENDED` (stop-phrase).
Meşgul setleri `{WOKE, RECORDING, TRANSCRIBING, THINKING}` — bu sırada yeni manuel tetik debounce edilir.

---

## 3. Ses Boru Hattı Detayı

### Tetikleme (`trigger.py`, `wake.py`)
- **Lazy listener**: `listener_provider()` yalnızca `wake_enabled()` true ise çağrılır → bas-konuş modunda sıfır donanım maliyeti.
- 4 pluggable wake backend (`make_listener(cfg)`):

| backend | sınıf | not | speaker verify |
|---|---|---|---|
| `oww` | `WakeListener` | openWakeWord, sürekli düşük maliyet | ✗ |
| `whisper` | `WhisperWakeListener` | STT tabanlı, aksandan bağımsız | ✓ |
| `vosk` | `VoskWakeListener` | offline Kaldi, düşük CPU, anahtar kelime | ✗ |
| `parakeet` | `ParakeetWakeListener` | ana STT modelini paylaşır, düşük gecikme | ✓ |

- Örnekleme: `_SAMPLERATE=16000`, `_FRAME=1280` (80 ms).

### Kayıt + VAD (`listen.py`)
- `record` / `record_follow_up` / `record_barge_in`.
- Barge-in: TTS sırasında daha yüksek eşikle (`barge_in_multiplier`, vars. ~4×) dinler ki kendi sesini tetik saymasın; kullanıcı araya girerse WAV döndürür ve TTS kesilir.

### STT (`transcribe.py`, `parakeet.py`)
- `make_transcriber(cfg)` → whisper veya parakeet.
- `parakeet.load_model(repo)` modül seviyesinde cache (`_models` dict) → reload yok.
- `transcribe_array()` wake için in-process log-mel + generate (dosya I/O'suz).

### Konuşmacı doğrulama (`speaker.py`)
- resemblyzer GE2E encoder → 256-d L2-normalize embedding.
- `verify(audio, profile, threshold=0.75)` kosinüs benzerliği.
- Enrollment: `scripts/enroll_speaker.py` → `models/speaker_profile.npy`.
- Yalnız STT tabanlı wake backend'lerinde (whisper/parakeet) aktif.

### Beyin (`brain.py`, Ollama)
| fonksiyon | amaç | sıcaklık | şema |
|---|---|---|---|
| `classify` | intent router + eylem parametre çıkarımı | 0.1 | JSON (zorunlu) |
| `answer` | serbest doğal sohbet | 0.7 | yok |
| `synthesize` | araştırma kaynaklarından `[n]` atıflı özet | 0.7 | yok |
- Geçmiş: son 8 `Turn` (`user`/`assistant`/`action_result`) mesajlara çevrilir; eylem sonuçları `[eylem: …]` olarak modele okutulur ama seslendirilmez.
- `BrainError` → pipeline fallback (asla sessiz kalmaz).

### Konuşma hafızası (`conversation.py`)
- `Conversation(max_turns=8, ttl_sec=300, clock=monotonic)`, thread-safe, boşta kalınca otomatik temizlenir.

---

## 4. Çok-Adımlı Agent Alt Sistemi (`agent_*.py`)

En kritik kısım. Bir doğal dil isteğini güvenli biçimde icra eden otonom döngü.

### Akış
```
run_agent(request, deps)
 ├─ planner.plan(request)               # Ollama + JSON şema → Plan(steps[])
 ├─ her adım (max_steps, vars. 12):
 │    risk = permission.decide(tool, params)   # AUTO | CONFIRM | DENY
 │    DENY      → çalıştırma yok (decision="denied")
 │    CONFIRM   → confirm() native dialog; hayır ise "confirmed-no"
 │    AUTO/onay → tools.run_tool(tool, params, ctx) → ToolResult
 │    verifier.verify_step(step, result, ctx)  # yazma işlemini diskten geri-oku
 │    → StepLog(decision, ok, verified, output/error)
 ├─ status = done | partial | denied | empty | failed
 ├─ spoken = planner.summarize_run(...) (LLM) ya da deterministik özet
 └─ audit.write_run_log(...) → vault/AgentRuns/agent-YYYY-MM-DD.md
```
`run_agent` **hiç raise etmez**; her adım izole — bir adım çökse bile döngü loglar ve devam eder.

### Veri modelleri (`agent_models.py`)
```python
class RiskLevel(str, Enum): AUTO; CONFIRM; DENY

@dataclass(frozen=True)
class ToolResult: ok: bool; output=""; error=""; data: dict|None=None

@dataclass(frozen=True)
class PlanStep: tool: str; params: dict; rationale: str

@dataclass(frozen=True)
class Plan: summary: str; steps: tuple[PlanStep, ...]

@dataclass(frozen=True)
class StepLog: tool; params; rationale; risk: RiskLevel; decision: str;
               ok: bool; output=""; error=""; verified=True

@dataclass(frozen=True)
class AgentRunResult: status: str; spoken: str; logs: tuple[StepLog,...]=()
```

### Araç kayıt defteri (`agent_tools.py`) — 18 araç, tek kaynak `REGISTRY`
`ToolContext`: `workspace`, enjekte edilen `run` (subprocess), clipboard fn'leri, `test_cmd`, `screenshot_enabled`, `ui_automation_enabled`, `research_fn`, `max_read_bytes`.

| araç | risk | iş | yetenek |
|---|---|---|---|
| list_dir | AUTO | dizin listele | ls |
| read_file | AUTO | dosya oku (byte limitli) | cat |
| write_file | CONFIRM | dosya yaz | fs |
| edit_file | CONFIRM | hedefli düzenle (old→new) | fs |
| search_files | AUTO | isimle dosya bul | find |
| run_tests | AUTO | pytest / `test_cmd` | subprocess |
| safe_shell | CONFIRM | kabuk (shlex, shell=False) | subprocess |
| open_app | AUTO | uygulama aç | `open -a` |
| open_url | CONFIRM | http/https aç | `open` |
| get_clipboard | CONFIRM | pano oku (parola/sır olabilir) | pbpaste |
| set_clipboard | CONFIRM | pano yaz | pbcopy |
| screenshot | CONFIRM | ekran görüntüsü (ekrandaki her şey) | screencapture |
| web_research | CONFIRM | web arama+özet | research_fn |
| activate_app | AUTO | uygulamayı öne al | AppleScript |
| ui_type | CONFIRM | odakta metin yaz | System Events keystroke |
| ui_keystroke | CONFIRM | tuş/kombinasyon | System Events key code |
| ui_click_menu | CONFIRM | menü öğesi tıkla | System Events |
| run_applescript | CONFIRM | ham AppleScript | osascript |

### Güvenlik (savunma derinliği)
- `resolve_in_workspace()` → `../` kaçışını engeller (`PathEscape`); tüm dosya işlemleri workspace kökünde.
- `is_hard_denied()` → `rm -rf`, `sudo`, `dd`, fork bomb, ham disk yazımı, `chmod 777`, `curl|sh`, `git push --force`, mail gönderimi → koşulsuz DENY.
- `sensitive_path()` → `.ssh`, `id_rsa`, `.env`, `.aws/credentials`, `.pem`, keychain, `.gnupg`, secrets → DENY.
- `permission.decide`: `safe_shell` için yıkıcı komutlar DENY, diğer tüm komutlar CONFIRM alır; runner OS sandbox değildir; `run_applescript` daima CONFIRM; argüman listesi modu (`shell=False`) → komut enjeksiyonu yok.
- Tüm handler'lar hata durumunda `ToolResult(ok=False, error=…)` döndürür, asla raise etmez.

### Doğrulayıcı (`agent_verifier.py`)
- `write_file`/`edit_file` → dosyayı diskten geri okuyup beklenen içerikle karşılaştırır.
- `read_file` → `data.content` var mı bakar.
- read-only/exec araçlar → `ok=True` yeterli.

### Denetim (`agent_audit.py`)
- `vault/AgentRuns/agent-YYYY-MM-DD.md` append-only; her koşu için zaman damgalı başlık + adım tablosu (Tool | Decision | OK | Verified | Output/Error).

---

## 5. Eylem Whitelist'i (`actions.py`)

LLM yalnız sabit listeden seçer; her eylem `subprocess` + argüman listesi (`shell=False`).

| eylem | parametre | iş |
|---|---|---|
| open_app | name | `open -a` |
| open_url | url | `open` |
| set_volume | level 0–100 | osascript |
| lock_screen | — | `pmset displaysleepnow` |
| tell_time | — | `datetime.now()` |
| search_files | query | `mdfind` (Spotlight) |
| set_timer | seconds, label? | arka plan sayaç, bitince seslenir |
| set_reminder | at (ISO8601), text | Reminders.app (yeniden başlatmaya dayanıklı) |
| delete_reminder | title, id? | Reminders.app sil |
| list_reminders | — | bekleyenleri oku |

- `ACTION_PARAMS` (tip+zorunluluk), `ACTION_SPECS` (LLM sistem prompt'una gider), `missing_params`/`coerce_params` doğrulama.
- `ActionResult(spoken, memory)` → `memory` anafora için ("az önce kurduğum hatırlatıcıyı sil").

---

## 6. Depolama (`notes.py` + `vault/`)

Obsidian markdown vault. Fonksiyonlar: `write_note`, `render_research_note`/`write_research_note`, `append_task` (→ `Tasks.md` checkbox), `save_to_inbox` (kaybetme-önleyici fallback). `slugify_tr` ve `phrases.py` Türkçe-bilinçli (`İ→i`, `I→ı`).

Kategori eşlemesi `config.vault.categories`: internship/research/idea/daily/task/inbox.

---

## 7. Konfigürasyon

`load_config()` → `config.yaml` (taban) + `config.local.yaml` (gizli/yerel, gitignore) **derin (özyinelemeli) birleştirme**: bir alt anahtarı override etmek (ör. `vault.categories.idea`) kardeşlerini silmez. `vault.path` zorunlu, yoksa `ConfigError`.

Başlıca bölümler: `brain` (model, host, think, keep_alive, answer_temperature), `stt` (backend, model, parakeet_model, language), `tts` (voice), `vad` (silence_sec, energy_threshold, max_sec), `app` (mode, wake_enabled, wake_ack, conversation_max_turns/idle_sec, follow_up_window_sec, barge_in, stop_phrases), `wake` (backend, phrase, threshold, framework, chunk_sec, speaker_verify/profile/threshold), `research` (provider auto|tavily|duckduckgo, max_results), `agent` (workspace, max_steps, test_cmd, screenshot_enabled, ui_automation, log_dir), `vault` (path, categories).

`constants.py` kod-içi varsayılanlar (tek kaynak): `ASSISTANT_NAME="Jarvis"`, `WAKE_PHRASE`, `WAKE_ACK="Efendim?"`, `TTS_VOICE="Yelda"`, `STT_MODEL`, `PARAKEET_MODEL`, `LLM_MODEL="qwen3:8b"`, `LLM_KEEP_ALIVE="10m"`, `STOP_PHRASES`.

---

## 8. App Shell & Build

- **Giriş**: `python -m arda` (console_script `arda`) veya `app_arda.py` (py2app `Jarvis.app`).
- `__main__.py`: config yükle → transcriber kur → model warm → Conversation → pipeline deps (`_build_pipeline_deps`) → agent deps (`_build_agent_deps`) → Controls → mode seç (`menubar` rumps / `daemon` headless `run_forever`).
- `menubar.py`: rumps; worker thread `run_forever` koşar, `AgentEvent`'leri `queue.Queue`'ya yazar; ana thread 0.2s'de bir drain edip ikon/başlık günceller. pynput global hotkey (⌘⇧A) ayrı thread, yalnız `Controls.request_manual()` çağırır (UI'ya dokunmaz). pynput yoksa menü "Kaydet" fallback.
- `setup.py`: bundle `com.example.jarvis`, `LSUIElement=True` (Dock'ta yok), `NSMicrophoneUsageDescription`.

### Bağımlılıklar (amaç bazlı)
- Ses: `sounddevice`, `numpy`, `webrtcvad`
- STT: `mlx-whisper`, `parakeet-mlx`
- Wake: `openwakeword`, `vosk`, (speaker) `resemblyzer`
- LLM: `ollama`
- UI: `rumps`, `pynput`
- Araştırma: `ddgs` (+ opsiyonel Tavily)
- Build/dev: `pytest`, `hf_xet`, `py2app`

---

## 9. Tasarım İlkeleri

1. **Bağımlılık enjeksiyonu** — `AgentDeps`, `pipeline.Deps`, `AgentRunDeps`, `ActionDeps`, `ToolContext.run`; tüm yan etkiler test edilebilir.
2. **Asla çökmez** — handler'lar raise etmez; pipeline ve `run_agent` her hatayı yakalar, fallback üretir (inbox/özür).
3. **Savunma derinliği** — DENY/CONFIRM kapıları + shell hard-deny + workspace sandbox + sensitive-path + `shell=False`.
4. **Tek kaynak** — `constants.py` varsayılan, `config.yaml` override, `config.local.yaml` gizli.
5. **Lazy & offline-first** — model/listener tembel yüklenir; her şey lokal (Ollama, MLX), wake kapalıyken mikrofon kapalı (gizlilik).
6. **Türkçe-bilinçli** — slug/lowercase için özel charmap.
7. **Thread güvenliği** — worker↔UI queue, `Controls`, `Conversation` kilitli.
8. **Çok küçük dosya** — feature/domain bazlı ~24 modül.

---

## 10. Bilinen kısıtlar + bilinçli kararlar

Kod denetiminde (2026-06) doğrulanan; bir kısmı düzeltildi, bir kısmı bilinçli kabul.

### Düzeltildi (Batch 0)
- **Config artık derin-merge** (`config.py` `_merge` özyinelemeli) — `vault.categories` gibi iki+ seviye nested override kardeşleri silmiyor.
- **Executor iptal token'ı** (`AgentRunDeps.should_stop`) — her adımdan önce kontrol; True → koşu `status="cancelled"` ile nazikçe durur, audit'e geçer. Hata → fail-open. (Controls'a bağlama Batch 3'te tamamlandı.)
- **Hassas okumalar CONFIRM** — `get_clipboard` + `screenshot` artık AUTO değil (exfiltrasyon ekseni: oku→dışarı-gönder zincirini kullanıcı kapısına alır).
- **Yapısal tur gözlemi** (`pipeline.Deps.observe`) — her tur tek kayıt: `status, intent, stt_ms, classify_ms, total_ms, chars, error`. Hata-kaynaklı inbox (`error` dolu) ile bilinçli inbox (unknown intent, `error=None`) ayrışır → "veri kaybolmaz" artık "sessiz başarısızlık" değil.

### Düzeltildi (Batch 1) — latency
- **Streaming response + single-shot TTS** — `brain.answer_stream()` (Ollama `stream=True`) parçaları pipeline `_answer_streamed` içinde toplar; akış tamamlanınca `speak.say()` tek kez çağrılır. Böylece cümleler arası bloklayan sentez boşluğu oluşmaz. `ProcessResult.message`/bellek tam metni alır. `app.stream_tts=false` kill-switch; akış boş/hata → tam-cevaba düşer. (Not: gerçek mic/hoparlör ile uçtan-uca latency manuel doğrulanmalı.)
- **Ayrı router modeli** — `cfg.brain.router_model` (yoksa `model`). `classify` küçük/hızlı modele alınabilir; `answer`/`synthesize` ana modelde kalır → router gecikmesi düşer. 2 sıralı roundtrip hâlâ var ama router artık ucuzlatılabilir.

### Düzeltildi (Batch 2) — doğruluk
- **Deterministik göreli-tarih** — `timeparse.resolve_when()` (saf, Türkçe-bilinçli) + `brain._resolve_action_time`: `set_reminder.at` ham metinden belirsiz-olmayan ifadelerle ezilir — ofsetler ('10 dakika sonra', '2 saat sonra') tam override; gün işareti + daypart ('yarın akşam 8') tam; gün işareti + çıplak saat → LLM saatini koru yalnız yanlış GÜNÜ düzelt (am/pm'i LLM'e bırak); tanınmazsa LLM korunur. LLM aritmetiği/biçimi artık zincirin tek-noktası değil.
- **Güven sinyali + güvenli erteleme** — router şemasına `confidence: high|low` (model belirsizlikte/geri-alınamaz eylemde "low" raporlar). Pipeline: düşük-güvenli **action** SESSİZCE çalıştırılmaz → sesli haber + inbox + `status="unclear"` (veri güvenli, yanlış yan-etki yok). Tek-turlu güvenli erteleme; tam "X mi demek istedin?" diyalogu bekleyen-onay durumu ister (Batch 3).

### Düzeltildi (Batch 3) — güvenlik + iptal
- **Adversarial güvenlik suite** (`tests/test_security_adversarial.py`) — `is_hard_denied`/`resolve_in_workspace`/`classify_shell`/`sensitive_path` için evasion testleri (path-traversal varyantları, symlink kaçışı, long-form bayraklar, sır-yolu biçimleri). **3 gerçek açık bulundu ve kapatıldı**: `rm --recursive`/`--force` (long-form), `git push -f` (short force) hard-deny'a eklendi.
- **İptal mekanizması bağlandı** — `Controls.request_cancel/is_cancelled/clear_cancel` → `__main__` executor'a `should_stop=controls.is_cancelled` verir, her koşu başında `clear_cancel`. Uzun agent koşusu UI'dan durdurulabilir (adım sınırında, `status="cancelled"`). Kalan tek hook: menübar "Durdur" öğesi `controls.request_cancel()` çağırsın.

### Bilinçli kabul (henüz açık)
- **Tam disambiguation diyalogu yok** — düşük-güven tek-turlu ertelenir; çok-turlu "X mi yoksa Y mi?" + "evet"le sürdürme bekleyen-onay state machine'i ister. `set_timer.seconds` deterministik parser'a henüz bağlı değil (yalnız `set_reminder.at`).
- **Shell son savunması = denylist + CONFIRM** — `is_hard_denied` sertleştirildi ama hâlâ denylist; takılmayan keyfi komut AUTO değil **CONFIRM**'e düşer (gerçek `sandbox-exec`/jail değil). Tek-kullanıcı tehdit modelinde kabul.
- **Verifier sığ** — `write/edit` diskten geri-okunur; exec/UI araçları (`open_url`, `run_applescript`, `ui_keystroke`, `safe_shell`) `ok=True` ile kabul edilir → yan etki anlamsal doğrulanmaz (en riskli araçlar en az doğrulanabilir).
- **Egress asimetrisi (tasarım)** — model-başlatımlı `web_research` CONFIRM; kullanıcı-başlatımlı pipeline `research()` gate'siz. Farklı initiator → savunulabilir; ileride tek egress fonksiyonunda initiator işaretle.
- **set_timer kalıcı değil** — süreç-içi `threading.Timer`; app kapanınca kaybolur. `set_reminder` (Reminders.app) kalıcı. Bilinçli (anlık vs kalıcı).
- **Paket yeniden adlandırma yapılmadı** — asistan döngüsü (`agent.py`) ↔ çok-adımlı (`agent_*.py`) düz dosyalar; `session.py`/`autopilot/` ayrımı büyük import churn (kendi commit'i olarak yapılmalı).

### Doğrulamada düzeltilen yanlış-algılar (kod aslında doğru)
- `screenshot` PNG yolu `resolve_in_workspace` ile sandbox'lı — keyfi path'e yazamaz.
- `resolve_in_workspace` `../` + mutlak + symlink kaçışını doğru blokluyor.
- Param **format** validasyonu (ISO8601) zaten deterministik; LLM'e bırakılan yalnız göreli→mutlak çözüm.

### Yapısal not
İki "agent" katmanı (asistan döngüsü `agent.py` vs çok-adımlı `agent_*.py`) düz dosyalar; isim çakışması karışıklık yaratıyor. Önerilen: asistan döngüsü → `session.py`/`loop.py`, çok-adımlı → `autopilot/` paketi (Batch 3).
