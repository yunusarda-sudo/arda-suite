# Bilgisayar Kontrolü — Geniş Erişim Kurulumu

Jarvis agent'ı (Faz 5) "Siri gibi" davranır ama daha güçlüdür: tüm ev klasörüne erişir,
her uygulamayı yazı/tuş/menü ile kontrol eder, kabuk komutu çalıştırır. **Güç = sorumluluk:**
riskli her işlem native onay dialog'u ister ve her koşu denetim günlüğüne yazılır.

## Güvenlik modeli (özet)

| Katman | Davranış |
|--------|----------|
| **Workspace** | `config.yaml` → `agent.workspace: "~"` (tüm ev klasörü). Dosya araçları bu köke hapsedilir. |
| **Sır koruması** | `.ssh`, `.env`, `.aws/credentials`, keychain, `*.pem/*.key` vb. yolları **her zaman REDDEDİLİR** (geniş erişimde bile). |
| **Onay kapısı** | `write_file`, `edit_file`, `safe_shell`, tüm `ui_*`, `run_applescript` → "Evet/Hayır" dialog'u (fail-closed). |
| **Kabuk denylist** | `rm -rf`, `sudo`, `dd`, `mkfs`, fork bomb, `curl\|sh`, `--force push` → çalıştırılmadan reddedilir. |
| **AppleScript bypass** | `do shell script` içinde yıkıcı komut → DENY (kabuk sandbox'ını atlatamaz). |
| **Denetim** | Her koşu `vault/AgentRuns/agent-YYYY-MM-DD.md` dosyasına loglanır. |

Tam otonom (onaysız) İSTEMEDİK — yanlış bir transkripsiyon/yorum geri alınamaz hasar verebilir.

## Proje kapsamlı, süreli browser oturumu

Camofox oturumu artık tek bir global `jarvis` profiline bağlanmamalı. Browser araçları
yalnızca Core'da proje kimliği, açık domain listesi, oluşturma zamanı ve sona erme zamanı
bulunan bir grant ile çalışır. Grant geri alınabilir ve domain listesi sonradan değiştirilemez.
Grant yoksa browser health dışındaki işlemler fail-closed olur.

Bir grant'i operatör olarak oluşturmak için uygulama sanal ortamında:

```python
from arda.core import bootstrap_runtime_database
from arda.browser_sessions import BrowserSessionRegistry

db = bootstrap_runtime_database()
try:
    grant = BrowserSessionRegistry(db).create(
        "opvia", ["github.com", "docs.github.com"],
        created_by="operator", ttl_seconds=3600,
    )
    print(grant["session_id"], grant["expires_at"])
finally:
    db.close()
```

`config.local.yaml` içindeki `browser.project_id` ve `browser.session_id` alanlarını grant
ile eşleştir. Grant kimliğini yalnız ilgili cihazın yerel config'inde tut. Grant'i geri almak
için aynı sanal ortamda şu kodu çalıştır:

```python
from arda.core import bootstrap_runtime_database
from arda.browser_sessions import BrowserSessionRegistry

db = bootstrap_runtime_database()
try:
    BrowserSessionRegistry(db).revoke("bs_...", revoked_by="operator")
finally:
    db.close()
```

Sonraki browser araç çağrısı reddedilir. ARDA açtığı Camofox sekmelerini Core SQLite'ta
grant'e bağlı olarak kaydeder; run sonunda kapatmayı dener. Grant iptal edildiyse veya
süresi dolduysa sonraki browser aracı önce kalan sekmeleri kapatmayı dener. Camofox
ulaşılamazsa browser kullanımı fail-closed kalır ve close kaydı sonraki çağrıda yeniden
denenir. Bu, browser sürecini sonlandırmaz ve fiziksel egress izolasyonu sağlamaz; gerçek
unattended kullanım için process supervisor ile OS/container egress firewall hâlâ gerekir.

Domain allowlist'i uygulama katmanında ilk URL'yi doğrular ve navigasyon/snapshot sonunda
gözlenen URL'yi tekrar denetler. Tarayıcı redirect'i yanıt alınmadan önce izlemiş olabilir;
bu nedenle bu kontrol egress sandbox'ı değildir. Dış sitelerle unattended kullanım için
Camofox worker'ının kendisi redirect/DNS-rebinding dahil network egress ACL'li izole bir
host/container içinde çalışmalı. Bu repo kodu o fiziksel ağ izolasyonunu kurmuş veya
doğrulamış sayılmaz.

## macOS izinleri (UI otomasyonu için ŞART)

`ui_type` / `ui_keystroke` / `ui_click_menu` / `run_applescript` System Events kullanır.
İzin verilmezse araçlar `ToolResult(ok=False)` döner (çökmez), ama iş yapmaz.

Jarvis'i çalıştıran uygulamaya (geliştirmede **Terminal** / **iTerm**; pakette **Jarvis.app**)
şu izinleri ver — **Sistem Ayarları → Gizlilik ve Güvenlik**:

1. **Erişilebilirlik (Accessibility)** — tuş gönderme, menü tıklama (en kritik).
2. **Otomasyon (Automation)** — diğer uygulamaları AppleScript ile kontrol (ilk denemede macOS sorar → İzin Ver).
3. **Ekran Kaydı (Screen Recording)** — yalnızca `agent.screenshot_enabled: true` ise.
4. **Tam Disk Erişimi (Full Disk Access)** — ev klasörü dışındaki korumalı alanlar (Mail/Mesajlar DB'leri vb.) gerekiyorsa; çoğu iş için gerekmez.

> İzni verdikten sonra uygulamayı yeniden başlat.

## Kapatma / daraltma

- UI otomasyonunu kapat: `config.yaml` → `agent.ui_automation: false`.
- Erişimi tek projeye daralt: `agent.workspace: "/Users/.../proje"` (veya `null` → cwd).
- Onay dialog'u her zaman açık; kapatılması desteklenmez (bilinçli tasarım).

## Hızlı test

Jarvis'e (İngilizce, Parakeet STT) şunu söyle:
- "Open Safari and go to apple.com" → `activate_app` + `open_url` (onay çıkar).
- "In TextEdit, type hello and save it" → `ui_type` + `ui_keystroke cmd+s` (her adım onay).
