# DesktopAgent — Jarvis and ARDA Core

This repository brings together a local-first desktop assistant and an experimental
agent runtime. The desktop application is named **Jarvis**; its Python package remains
`arda` for historical compatibility.

- **Jarvis desktop app** — push-to-talk voice assistant for macOS, with a Raspberry Pi/Linux daemon mode.
- **ARDA Core** — task intake, execution, verification, evaluation, and memory components for bounded agent work.
- **Hermes integration** — an experimental provider and execution bridge. It is not a claim that production multi-agent orchestration is complete.
- **Shared memory** — the optional `mem0ai` adapter connects to a separately installed local mem0/Qdrant service; that service and its data are not included here.

See the [documentation index](docs/README.md) for architecture, operational guides, and the status of experimental components.

## Project status

The repository contains code at different maturity levels. The desktop assistant is the historical base application; ARDA Core and Hermes work is evolving and should be treated as experimental unless a specific component has an acceptance report. Hardware, external providers, and local service integrations require separate setup. Tests do not by themselves verify those external paths.

The root-level `ARDA_*`, `HERMES_*`, `SPEC.md`, and related design documents preserve the development history. They are design and acceptance records, not a guarantee that every proposed capability is available in a production deployment.

For durable operational job cards backed by the Core event registry, see the
[Tracked Jobs API](TRACKED_JOBS_API.md).

---

## Jarvis desktop assistant

macOS'ta menü çubuğunda çalışan, yerel-öncelikli, **Siri-benzeri** sesli asistan.
Varsayılan **push-to-talk** (⌘⇧A): bas → konuş → Jarvis ne istediğini anlar ve

- **soruyu/sohbeti** sesli yanıtlar (kısa süreli **sohbet belleği** ile takip soruları çalışır),
- **Mac eylemi** yapar (uygulama aç, ses ayarla, sayaç, dosya ara, ekran kilitle…),
- **çok-adımlı işleri** (proje incele, dosya ara/oku/düzenle, testleri çalıştır) güvenli bir
  **desktop agent** olarak planlayıp yürütür (bkz. [Desktop Agent](#desktop-agent-çok-adımlı)),
- ya da **not/görev** olarak Obsidian vault'a yazar.

Raspberry Pi/Linux üzerinde menubar yerine headless `daemon` modu ve offline Vosk STT
kullanılabilir. Kurulum örneği için [`scripts/install_rpi_service.sh`](scripts/install_rpi_service.sh)
dosyasına bakın; fiziksel Pi doğrulaması bu kaynak paketi hazırlanırken yapılamadı.

> Varsayılan olarak **her zaman dinlemez** — mikrofon yalnızca sen tetikleyince açılır.
> İsteğe bağlı wake word (her-zaman-dinle) menüden açılabilir (sürekli mic + kendi .onnx modeli).

> **Not:** Kullanıcıya görünen ad **Jarvis**'tir; iç Python paketi tarihsel olarak
> `arda` adını korur (importlar değişmedi).

- **Plan:** `Arda-Asistan-Proje-Plani.md`
- **Teknik spec + karar günlüğü:** `SPEC.md`

## Durum
Çekirdek + Siri yönlendirici tamam (TDD, kapsamlı test paketi): push-to-talk tetik, beyin
(not/görev/yanıt/eylem router'ı), kürütörlü Mac eylem beyaz listesi (`arda/actions.py`,
keyfi shell yok), kısa süreli **sohbet belleği** (`arda/conversation.py` — son 8 tur,
5 dk idle sonrası temizlenir) ve menü çubuğu/daemon app'i. Her-zaman-dinle wake word
opsiyonel. Saf mantık + ajan döngüleri test kapsamında; ham ses/donanım/model sınırda
mock'lanır.

## Hızlı Kurulum (Yeni Mac)

Tek bir komutla Homebrew, ffmpeg, Python 3.11, sanal ortam, Ollama modelleri, Obsidian vault ve masaüstü uygulamasını otomatik kurabilirsiniz:
```bash
./install.sh
```

Sistem izinlerini (Erişilebilirlik & Mikrofon) ve servis sağlığını denetlemek için:
```bash
make doctor
```

## Manuel Geliştirme Kurulumu
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # tam kurulum
make test                              # testleri çalıştır (.venv/bin/pytest)
```

## Çalıştırma — push-to-talk (varsayılan)

Jarvis menü çubuğunda bekler; **⌘⇧A**'ya bastığında (ya da menüden "Şimdi Kaydet")
dinlemeye başlar, ne istediğini anlar ve yanıtlar/eylem yapar/not alır. Mikrofon
yalnızca tetikten sonra açılır — arka planda sürekli dinleme yok. Her-zaman-dinleyen
wake word'ü istersen menüden aç (sürekli mic + kendi `.onnx` modeli gerekir).

**Sohbet belleği:** ardışık push-to-talk konuşmaları bağlamı paylaşır → "Chrome'u aç"
dedikten sonra "YouTube'a gir" ya da "bir not al…" dedikten sonra "ona başlık ekle"
gibi takip cümleleri çözülür. Bağlam **5 dk** sessizlikten sonra kendini temizler.

Gereksinimler:
- **Ollama** çalışıyor olmalı: `ollama serve` + `ollama pull qwen3:8b`
- `config.local.yaml` içinde `vault.path` ayarlı olmalı
- İlk açılışta STT modeli iner (config'e göre: parakeet ~2.5 GB / whisper ~1.5 GB) — bir kez, sonra sıcak kalır

```bash
make run            # config'teki app.mode'a göre menü çubuğu veya daemon
```

### Çalışma modu (`config.yaml` → `app.mode`)
| mode | davranış |
|------|----------|
| `menubar` | Menü çubuğunda ikon; arka planda dinler. Mikrofon iznini düzgün tetikler. **Önerilen.** |
| `daemon`  | Görünmez arka plan. Login'de otomatik başlatmak için `deploy/com.example.jarvis.plist` (launchd) kullan. |

Linux/Raspberry Pi'de `daemon` modu systemd kullanıcı servisiyle yönetilir; macOS launchd
şablonu yerine [`deploy/jarvis.service`](deploy/jarvis.service) kullanılır. Pi bağımlılıkları
[`requirements-rpi.txt`](requirements-rpi.txt) dosyasında ayrıdır.
Dağıtım hedeflerinin özeti için [`docs/release-targets.md`](docs/release-targets.md) belgesine bakın.

Daemon'u login'de otomatik başlatma: `deploy/com.example.jarvis.plist` içindeki
yönergeleri izle.

### Dikte amblemi (Wispr Flow tarzı sesli yazma)

Ekranın alt-ortasında küçük yüzen bir **amblem** durur. **⌘⇧D**'ye bas (ya da amblem'e tıkla)
→ konuş → konuştukların **aktif uygulamadaki imlecin olduğu yere** otomatik yapışır. Mevcut
ajan akışından (⌘⇧A) ayrıdır; bu saf **dikte**: ham metin hafifçe temizlenir (noktalama,
baştaki "ee/ıı" dolgu sesleri), beyne/ajana sokulmaz.

- **Dil otomatik:** İngilizce konuşursan İngilizce, Türkçe konuşursan Türkçe yapışır
  (çok-dilli `mlx-whisper`; ajan `parakeet` kullanıyorsa dikte whisper'ı **ayrıca** yükler ~+1.5 GB RAM).
- **v1 akışı:** bas → dinler → **sustuğunda otomatik durur** ve yapıştırır. (Elle ikinci-basışla
  durdurma, akan-kayıt refactor isteyen sonraki faz.)
- **Yapıştırma için Accessibility izni gerekir** (Sistem Ayarları → Gizlilik & Güvenlik →
  Erişilebilirlik → Jarvis/terminal'e izin ver). Pano içeriğin korunur (yapıştırmadan sonra geri yüklenir).
- Amblemi menüden **"Dikte amblemi"** ile aç/kapat; sürükleyerek taşı. Ayarlar: `config.yaml → dictation`.
- Yalnız `menubar` modunda çalışır (daemon'da AppKit yok). pynput/pyobjc yoksa app çökmez —
  ilgili tetik sessizce devre dışı kalır.

### Çift tıklanan macOS app'i (`Jarvis.app`) ve DMG Dağıtımı
```bash
make app             # dist/Jarvis.app üretir (py2app, alias build)
open dist/Jarvis.app # veya Launchpad'e / Applications'a kopyala

make dmg             # dist/Jarvis.dmg üretir (sürükle-bırak DMG paketleyicisi)
open dist/Jarvis.dmg
```
- İlk açılışta macOS **mikrofon ve erişilebilirlik izni** ister (menüden `🩺 İzinleri Denetle` ile anında kontrol edebilirsiniz).
- `LSUIElement` sayesinde Dock'ta görünmez; sadece menü çubuğunda 🎙️ ikonu çıkar.
- **alias build** bu Mac'e ve bu repo'nun `.venv`'ine bağlıdır (repo'yu/`.venv`'i taşıma).
  Başka makineye taşınabilir tam-standalone paket, ML bağımlılıkları (mlx, onnxruntime,
  openWakeWord modelleri) nedeniyle ayrı bir iştir — gerekirse `setup.py` genişletilir.
- Yine **Ollama çalışıyor** olmalı ve `config.local.yaml`'da `vault.path` ayarlı olmalı.

Bu `.app/.dmg` çıktısı geliştirme Mac’ine özel alias release’tir; başka Mac’e
taşınabilir standalone paket ayrı bir release fazıdır.

### Opsiyonel uyandırma kelimesi (4 backend)
Varsayılan akış push-to-talk'tır; wake word **gerekmez**. Her-zaman-dinle istersen
menüden aç ya da `config.local.yaml` → `app.wake_enabled: true`. Backend
`config.wake.backend` ile seçilir:

| backend | ne | not |
|---------|-----|-----|
| `oww` | openWakeWord — hazır `hey jarvis` ya da kendi `.onnx` modelin | hafif, sürekli dinlemede ucuz |
| `vosk` | offline KaldiRecognizer keyword tespiti | API key yok; `models/vosk-en` gerekir (`make models-vosk`) |
| `whisper` | Whisper STT ile phrase tespiti | aksan/dil-bağımsız |
| `parakeet` | NVIDIA Parakeet STT (ana STT ile aynı modeli paylaşır) | doğru ama 0.6B → sürekli dinlemede ağır |

> **Konuşmacı doğrulama** (`wake.speaker_verify: true`) yalnızca STT-tabanlı backend'lerde
> (`whisper` / `parakeet`) uygulanır. `oww` / `vosk` ile birlikte açılırsa wake güvenli
> tarafta devre dışı kalır ve uygulama push-to-talk'a düşer.

**openWakeWord (`backend: oww`) seçenekleri:**

- **Hazır (sıfır eğitim):** openWakeWord'ün yerleşik `hey jarvis` modeli.
  ```yaml
  app:  { wake_enabled: true }
  wake: { model_path: hey_jarvis }   # yalnızca jarvis tetikler (alexa/timer/weather kapalı)
  ```
  "**hey jarvis**" de → uyanır. Tek başına "jarvis" güvenilmez (model "hey jarvis" üzerine eğitilmiş).

- **Kendi tetiğin ("Hey Arda"):** kendi `.onnx` modelini eğit → `models/hey_arda.onnx`'e koy →
  `wake.model_path`'i ona bağla. Adım adım reçete: **[`docs/wake-word-training.md`](docs/wake-word-training.md)**.
  Eğitim sonrası **`make check-wake`** ile yükleme + eşik ayarını mikrofonsuz doğrula.

## Desktop Agent (çok-adımlı)

"Jarvis, bu projeyi incele ve ne yapabildiğini söyle" ya da "testleri çalıştır ve hataları
özetle" dediğinde Jarvis tek bir Mac eylemi değil, **çok-adımlı** bir iş planlayıp yürütür:

```
istek → planlayıcı (Ollama) → her adım: izin → (gerekirse onay) → çalıştır → doğrula → logla → sesli özet
```

**Araçlar** (`arda/agent_tools.py`): `list_dir`, `read_file`, `write_file`, `edit_file`,
`search_files`, `run_tests`, `safe_shell`, `open_app`, `open_url`, `get_clipboard`,
`set_clipboard`, `screenshot`, `web_research`.

**Güvenlik — 3 risk seviyesi** (`arda/agent_permission.py`):
- **auto:** okuma/listeleme/arama/test → onaysız çalışır.
- **confirm:** dosya yazma/düzenleme, paket kurma, git commit, web araştırma → **native macOS dialog** ile onay.
- **deny:** sır/credential okuma (`.env`, `.ssh`…), `rm -rf`, `sudo`, izinsiz mail → **asla** çalışmaz.

`safe_shell` whitelist/denylist ile çalışır (yıkıcı komutlar reddedilir, `shell=False` → enjeksiyon yok).
Dosya araçları `agent.workspace` köküne **hapsedilir** (path-traversal kapalı).

**Denetim günlüğü:** her koşu (başarısız dahil) `vault/AgentRuns/agent-YYYY-MM-DD.md`'ye
markdown tablo olarak yazılır — istek + durum + her adımın tool/karar/ok/doğrulama/çıktısı.
"Jarvis dosyalarıma ne yaptı?" → bu dosyaya bak.

**Ayar** (`config.yaml` → `agent`):
```yaml
agent:
  workspace: null            # null → cwd; agent'ın çalışacağı proje kökü
  max_steps: 12              # bir koşuda en fazla adım
  test_cmd: ["pytest", "-q"] # run_tests komutu
  screenshot_enabled: false
  log_dir: null              # null → vault/AgentRuns (denetim günlüğü)
```

Teknik detay + karar günlüğü: **[`docs/agent-spec.md`](docs/agent-spec.md)**.

## Yığın
Python · STT: parakeet-mlx (macOS varsayılan) / mlx-whisper / Vosk (RPi/Linux) · Ollama/qwen3:8b (beyin + sohbet
belleği + agent planlayıcı) · wake: openWakeWord / vosk / whisper / parakeet ·
rumps (menü çubuğu) · macOS `say` (TTS) · Obsidian/Markdown
