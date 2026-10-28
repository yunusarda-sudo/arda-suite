# Arda — Masaüstü AI Asistanı
### Profesyonel Proje Planı ve Yol Haritası

**Sürüm:** 1.0
**Tarih:** 16 Haziran 2026
**Platform:** macOS (Apple Silicon)
**Durum:** Planlama → Geliştirme

---

## 1. Proje Özeti

Arda, macOS üzerinde arka planda çalışan, kendi adıyla çağrıldığında uyanan kişisel bir sesli asistandır. Temel görevi; konuşmayı dinlemek, yazıya çevirmek, içeriği akıllıca düzenleyip not olarak kaydetmek, gerektiğinde web araştırması yapmak ve kullanıcıya sesli geri bildirim vermektir. Notlar standart Markdown dosyaları olarak yerel bir Obsidian vault klasöründe tutulur; böylece veri tamamen kullanıcının kontrolünde ve dışa bağımlılıktan uzak kalır.

**Tek cümlelik hedef:** "Arda" de, konuş, ve düzgün biçimlendirilmiş bir not Obsidian'a düşsün.

---

## 2. Hedefler ve Kapsam

### 2.1 Kapsam Dahilinde (bu projede yapılacaklar)
- Özel uyandırma kelimesi ("Arda") ile sesli aktivasyon
- Yerel konuşma-metin (STT) dönüşümü
- Komut sınıflandırma: not / araştırma / görev / soru
- Akıllı not biçimlendirme (ham metni başlıklı, etiketli, yapılandırılmış nota dönüştürme)
- Obsidian/Markdown vault'a otomatik kayıt
- Web araştırması ve kaynaklı çıktı
- macOS `say` üzerinden sesli geri bildirim
- Terminal → menü çubuğu uygulaması evrimi

### 2.2 Kapsam Dışında (bu sürümde yapılmayacaklar)
- Mobil/Windows/Linux desteği
- Bulut senkronizasyonu veya çok cihazlı altyapı
- Çok kullanıcılı / hesap sistemi
- Görüntü/video işleme
- Üçüncü parti entegrasyonlar (takvim, mail) — V4'e ertelenmiştir

### 2.3 Tasarım İlkeleri
1. **Yerel öncelikli (local-first):** Veri ve işlem mümkün olduğunca cihazda kalır, gizlilik korunur.
2. **Aşamalı teslim:** Her faz tek başına çalışan ve test edilebilir bir ürün üretir. "Komple Jarvis"i tek seferde yapmaya çalışmayız.
3. **Modülerlik:** Her yetenek (wake, STT, beyin, not, araştırma, ses) ayrı modül; biri değişse diğeri etkilenmez.
4. **Sade arayüz:** Önce terminal, sonra menü çubuğu. Görsel karmaşıklık en sona bırakılır.

---

## 3. Sistem Mimarisi

```
┌─────────────────────────────────────────────────────────┐
│                    macOS Arka Plan Süreci                 │
└─────────────────────────────────────────────────────────┘

   Mikrofon
      │
      ▼
┌──────────────┐   "Arda" algılandı
│  Wake Word   │ ───────────────────►  (uyku → aktif)
│  (Porcupine) │
└──────────────┘
      │
      ▼
┌──────────────┐   VAD (sessizlik algılama) ile
│  Dinleme     │   konuşma bitince durdurur
│  (sounddevice│
│   + VAD)     │
└──────────────┘
      │  ses kaydı
      ▼
┌──────────────┐
│     STT      │   konuşma → metin
│ (whisper.cpp)│
└──────────────┘
      │  ham metin
      ▼
┌──────────────┐   niyet sınıflandırma + biçimlendirme
│   AI Beyin   │   JSON çıktı: {intent, content, tags...}
│   (Ollama)   │
└──────────────┘
      │
      ├──► [note]      ─► Not Modülü   ─► Obsidian/Markdown vault
      ├──► [research]  ─► Araştırma    ─► Tavily API → kaynaklı not
      ├──► [task]      ─► Görev Modülü ─► Tasks.md
      └──► [question]  ─► Doğrudan cevap
      │
      ▼
┌──────────────┐
│  Sesli Cevap │   "Not kaydedildi."
│ (macOS say)  │
└──────────────┘
```

**Akışın özeti:** Dinle → yazıya çevir → niyeti anla → ilgili modüle yönlendir → kaydet/araştır → sesli onay ver.

---

## 4. Teknoloji Yığını (2026 Güncel)

| Katman | Seçim | Gerekçe |
|--------|-------|---------|
| **Dil** | Python 3.11+ | Tüm ses/AI kütüphaneleri için en olgun ekosistem |
| **Wake Word** | Picovoice Porcupine | Özel "Arda" kelimesi `.ppn` dosyasıyla cihazda çalışır; Apple Silicon'da sorunsuz |
| **STT** | whisper.cpp (large-v3 / turbo) | Mac'te yerel transkripsiyon doğruluğunda altın standart; Apple Silicon'da hızlı |
| **STT (alternatif)** | Parakeet V3 (yerel) | 2026'da öne çıkan, düşük gecikmeli yerel ASR alternatifi |
| **AI Beyin** | Ollama 0.19+ | Apple Silicon arka ucu artık MLX; `localhost:11434` üzerinden HTTP API |
| **LLM Modeli** | Qwen 3 ailesi (RAM'e göre) | 2026'nın varsayılan yerel modeli; kalite/hız dengesi iyi |
| **Not Sistemi** | Obsidian vault (Markdown) | Notlar yerel `.md` dosyaları; dışa bağımlılık yok, taşınabilir |
| **Araştırma** | Tavily API | AI agent'lar için arama/extract; Python SDK mevcut |
| **Sesli Cevap** | macOS `say` (V1) → Piper TTS (V3) | `say` sıfır kurulumla çalışır; Piper daha doğal ses için |
| **Arayüz** | Terminal (V1) → `rumps` menü çubuğu (V2) → PyQt (V3) | Aşamalı karmaşıklık |

### 4.1 RAM'e Göre Model Önerisi
Kesin model seçimi Mac'in birleşik belleğine (unified memory) bağlıdır. Pratik kural: belleğin yaklaşık %70'i model ağırlıkları için kullanılabilir.

| Mac RAM | Önerilen Model | Not |
|---------|----------------|-----|
| 8 GB | 7-8B Q4 (örn. Qwen 3 8B Q4) | Çalışır ama sınırda; kısa bağlamla |
| 16 GB | Qwen 3 8B (Q4/Q5) | Tatlı nokta; günlük kullanım için ideal |
| 32 GB+ | Qwen 3 MoE (örn. 30B-A3B) | Sadece ~3B aktif parametre → hızlı, bağlama yer kalır |
| 48 GB+ | Qwen 3 27B dense (Q6/Q8) | En yüksek kalite |

> **Not:** Asistanın işi (niyet sınıflandırma + metin düzenleme) ağır bir görev değildir. 8B'lik bir model bile V1 için fazlasıyla yeterlidir. Büyük modele ihtiyaç V3'teki gelişmiş özetleme/araştırma sentezinde doğar.

---

## 5. Yol Haritası ve Fazlar

Her faz, kendi başına çalışan ve test edilebilir bir teslimattır. Bir sonraki faza ancak "Kabul Kriterleri" sağlandığında geçilir.

### Faz 0 — Ortam Kurulumu *(yarım gün)*
**Amaç:** Geliştirme ortamını hazırlamak.
- Homebrew bağımlılıkları (`portaudio`, `ollama`)
- Python sanal ortamı ve bağımlılıklar
- Ollama'da model indirme ve `localhost:11434` testi
- Mikrofon iznini macOS Gizlilik ayarlarından verme

**Kabul Kriteri:** `ollama run` ile modele soru sorulabiliyor; Python'dan mikrofon okunabiliyor.

---

### Faz 1 — Çalışan Çekirdek (Terminal MVP) *(öncelikli hedef)*
**Amaç:** Sesli komuttan Obsidian notuna kadar tam zinciri terminalde çalıştırmak.

| # | Görev | Modül |
|---|-------|-------|
| 1.1 | Mikrofonu dinleyip ses kaydetme (VAD ile otomatik durdurma) | `listen.py` |
| 1.2 | whisper.cpp ile konuşmayı metne çevirme | `transcribe.py` |
| 1.3 | Ollama'ya istek atıp niyet sınıflandırma + biçimlendirme (JSON çıktı) | `brain.py` |
| 1.4 | Yapılandırılmış notu Markdown olarak vault'a yazma | `notes.py` |
| 1.5 | `say` ile sesli onay | `speak.py` |
| 1.6 | Hepsini bağlayan ana döngü | `main.py` |

**Kabul Kriteri:** Terminalde program çalışıyor; konuşulan bir staj notu, biçimlendirilmiş `.md` olarak doğru klasöre kaydediliyor ve "Not kaydedildi" sesli yanıtı geliyor. *(Bu aşamada wake word henüz yok — tetikleme tuşla/Enter ile yapılır.)*

---

### Faz 2 — Wake Word ("Arda") *(2-3 gün)*
**Amaç:** Elle tetiklemeyi kaldırıp sesli aktivasyona geçmek.
- Picovoice Console'dan "Arda" / "Hey Arda" için macOS `.ppn` dosyası üretme
- Porcupine'i ana döngüye entegre etme (uyku ↔ aktif durum yönetimi)
- Yanlış tetikleme (false positive) hassasiyet ayarı

**Kabul Kriteri:** Program arka planda beklerken "Arda" denince uyanıyor, komutu alıyor, işini yapıyor, tekrar uyku moduna dönüyor.

---

### Faz 3 — Menü Çubuğu Uygulaması *(3-5 gün)*
**Amaç:** Terminali kapatıp Mac üst menüsünde yaşayan bir uygulamaya dönüşmek.
- `rumps` ile menü çubuğu ikonu
- Menü öğeleri: Aktif/Pasif geçişi · Mikrofon durumu · Son notlar · Vault klasörünü aç · Ayarlar
- Durum göstergesi (dinliyor / uyuyor / işliyor)
- Arka planda otomatik başlatma (login item)

**Kabul Kriteri:** Uygulama menü çubuğundan açılıp kapatılabiliyor; terminal gerekmiyor.

---

### Faz 4 — Gelişmiş Yetenekler *(devam eden)*
**Amaç:** Asistanı gerçek bir kişisel araca dönüştürmek.
- Web araştırma modunu (Tavily) kaynaklı not çıktısıyla tamamlama
- Mevcut notlara ekleme yapma ("bunu MODÜ-GRID fikrine ekle")
- Günlük not özetleme ve görev listesi çıkarma
- Piper TTS ile daha doğal sesli yanıt
- (İsteğe bağlı) Takvim okuma, mail taslağı, PDF/döküman okuma

**Kabul Kriteri:** Asistan araştırma yapıp kaynaklarıyla not kaydedebiliyor; mevcut notları güncelleyebiliyor.

---

### Faz 5 — Proje Hafızası ve Otomasyon *(ileri vizyon)*
- Proje bazlı hafıza (hangi notlar hangi projeye ait)
- GitHub repo analizi
- Claude Code / Codex'e görev devretme
- Notlar arası bağlam ve geri çağırma

---

## 6. Proje Klasör Yapısı

```
arda-assistant/
│
├── main.py              # Ana uygulama döngüsü
├── wake.py              # "Arda" wake word (Porcupine)
├── listen.py            # Mikrofon dinleme + VAD
├── transcribe.py        # Konuşma → metin (whisper.cpp)
├── brain.py             # Niyet sınıflandırma + biçimlendirme (Ollama)
├── notes.py             # Markdown not yazma
├── research.py          # Web araştırma (Tavily)
├── speak.py             # Sesli cevap (say / Piper)
│
├── config.yaml          # Ayarlar (model, vault yolu, wake hassasiyeti)
├── requirements.txt
├── assets/
│   └── arda_mac.ppn     # Özel wake word dosyası
│
└── vault/               # Obsidian vault (notların tutulduğu yer)
    ├── Daily/
    ├── Internship/      # Staj notları
    ├── Research/        # Araştırma çıktıları
    ├── Ideas/           # Proje fikirleri
    └── Tasks.md
```

---

## 7. Kurulum (Faz 0 Komutları)

```bash
# Sistem bağımlılıkları
brew install portaudio
brew install ollama

# whisper.cpp (yerel STT)
brew install whisper-cpp        # veya kaynaktan derleme

# Python ortamı
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# requirements: sounddevice, pyyaml, requests, pvporcupine,
#               numpy, webrtcvad, tavily-python, rumps

# Yerel model (RAM'e göre seçilir)
ollama pull qwen3:8b
```

**macOS izni:** İlk çalıştırmada *Sistem Ayarları → Gizlilik ve Güvenlik → Mikrofon* altından Terminal/uygulamaya izin verilmelidir.

---

## 8. Veri Modeli — Akıllı Not Formatı

Asistan ham metni doğrudan kaydetmez; düzenler, başlıklandırır, etiketler ve eksik noktaları "araştırılacaklar" olarak çıkarır. Her not Obsidian uyumlu YAML başlığı (frontmatter) içerir.

**Örnek girdi (konuşma):**
> "Arda, bugün ORS'de rulman üretim hattında taşlama ve kalite kontrol süreçlerini gördük, bunu staj notu olarak kaydet."

**Örnek çıktı (`vault/Internship/2026-06-16-ORS-rulman.md`):**

```markdown
---
type: internship
date: 2026-06-16
tags: [staj, ORS, rulman, üretim, kalite-kontrol]
---

# ORS Staj Notu — 16 Haziran 2026

## Ham Not
Bugün ORS'de rulman üretim hattında taşlama ve kalite kontrol süreçleri görüldü.

## Düzenlenmiş Not
Üretim hattında rulmanların taşlama aşaması ve sonrasında uygulanan
kalite kontrol süreçleri incelendi. Taşlama, rulman yüzey hassasiyetini
artırmak için kullanılırken; kalite kontrol aşamasında ölçüm ve uygunluk
kontrolleri yapıldı.

## Anahtar Kelimeler
#staj #ORS #rulman #üretim #kalite-kontrol

## Sonradan Araştırılacaklar
- Rulman taşlama makineleri
- Yüzey pürüzlülüğü ölçümü (Ra/Rz)
- Rulman kalite kontrol standartları (ISO)
```

---

## 9. Komut / Niyet Tasarımı

Beyin modülü her ham metni aşağıdaki niyetlerden birine sınıflandırır ve sabit bir JSON sözleşmesiyle döner. Bu, kodun çıktıyı güvenle ayrıştırmasını sağlar.

| Niyet | Tetikleyici örnek | Sonuç |
|-------|-------------------|-------|
| `note` | "...not al / staj notu olarak kaydet" | Yapılandırılmış not → ilgili klasör |
| `research` | "...araştır / kaynaklarıyla kaydet" | Tavily araması → kaynaklı not |
| `task` | "...görev listesi çıkar / yapılacak" | `Tasks.md`'e madde ekleme |
| `question` | "...nedir / nasıl çalışır" | Doğrudan sesli/yazılı cevap |
| `append` | "...şu fikre ekle / şu nota ekle" | Mevcut `.md` dosyasını güncelleme |

**LLM'den beklenen çıktı sözleşmesi (örnek):**
```json
{
  "intent": "note",
  "category": "internship",
  "title": "ORS Staj Notu",
  "clean_text": "...",
  "tags": ["staj", "ORS", "rulman"],
  "follow_up": ["Yüzey pürüzlülüğü ölçümü"]
}
```
> Beyin promptunda model açıkça **yalnızca JSON döndürmesi** için yönlendirilir (markdown çiti, açıklama vb. olmadan). Kod tarafında güvenli `try/except` ile ayrıştırılır.

---

## 10. Riskler ve Önlemler

| Risk | Etki | Önlem |
|------|------|-------|
| Wake word yanlış tetikleme | Asistan gereksiz uyanır | Porcupine hassasiyetini ayarla; gerekirse "Hey Arda" gibi iki heceli kelime |
| STT doğruluğu (teknik terimler) | Yanlış not | large-v3/turbo model; gerekirse domain sözlüğü; düşük RAM'de model boyutunu dengele |
| LLM bozuk JSON döndürmesi | Kayıt hatası | Katı prompt + `try/except` + tekrar deneme; başarısızsa ham metni "İşlenmemiş" klasörüne kaydet |
| Düşük RAM'de yavaşlık | Kötü deneyim | RAM tierine uygun model seç; Ollama 0.19+ (MLX) kullan |
| Mikrofon izni reddi | Çalışmaz | İlk çalıştırmada net yönlendirme; izin kontrolü ekle |
| Veri kaybı | Not kaybı | Vault'u Git/iCloud ile yedekle (kullanıcı tercihi) |

---

## 11. Başarı Kriterleri

**V1 (MVP) başarılı sayılır eğer:**
- "Arda" (veya tuş) ile tetiklenip 1 dakika içinde konuşmadan biçimli nota geçiş tamamlanıyorsa
- Notlar doğru klasöre, doğru etiketlerle kaydediliyorsa
- Sesli onay çalışıyorsa
- Yanlış tetikleme günde birkaç defadan az ise

**Olgun ürün (V3+) başarılı sayılır eğer:**
- Asistan araştırma yapıp kaynaklı not üretebiliyorsa
- Mevcut notlara doğru ekleme yapabiliyorsa
- Terminal olmadan menü çubuğundan günlük olarak kullanılabiliyorsa

---

## 12. İlk Somut Adım

Önerilen başlangıç noktası **Faz 1'in çekirdeği**dir; wake word'ü en sona bırakırız çünkü o olmadan da tüm zincir test edilebilir:

> **Mikrofon → yazıya çevir → AI ile düzenle → Markdown olarak kaydet**

Bu dört adım sağlam kurulduğunda, üzerine "Arda" wake word'ünü (Faz 2) ve araştırma modunu (Faz 4) eklemek görece kolaydır. Sıradaki teslimat: Faz 0 ortam kurulumu + Faz 1.1–1.2 (dinleme ve transkripsiyon) iskeleti.
