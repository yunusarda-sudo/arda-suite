"""py2app paketleme — `Jarvis.app` (macOS menü çubuğu uygulaması).

Önerilen (kişisel kullanım, bu Mac):   make app        →  alias build
    Hızlı; .app bu repo'nun .venv'ine bağlanır (başka makineye taşınmaz),
    ama mikrofon iznini düzgün tetikler ve Launchpad/Finder'dan çift tıklanır.

Tam standalone (taşınabilir) build bu ML bağımlılıklarıyla (mlx, onnxruntime,
openwakeword model dosyaları) kırılgandır; gerekiyorse `packages`/`resources`
genişletilerek denenir. Varsayılan akış alias'tır.

pytest bu dosyayı toplamasın diye proje testleri tests/ altında; bu setup.py
yalnızca `python setup.py py2app` ile çağrılır.
"""
from setuptools import setup

# Kimlik tek kaynaktan (constants ağır bağımlılık içermez → build-time import güvenli).
from arda import __version__
from arda.constants import ASSISTANT_NAME

APP = ["app_arda.py"]

OPTIONS = {
    "argv_emulation": False,
    # Bundle'da bulunması gereken paketler (alias mode'da çoğu .venv'den symlink'lenir).
    "packages": ["arda", "rumps"],
    "plist": {
        "CFBundleName": ASSISTANT_NAME,
        "CFBundleDisplayName": ASSISTANT_NAME,
        "CFBundleIdentifier": f"com.example.{ASSISTANT_NAME.lower()}",
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        # Menü çubuğu app'i: Dock'ta görünme, sadece üst barda ikon.
        "LSUIElement": True,
        # Launchpad/Finder açılışında ortamda LANG YOKTUR → open() ascii olur ve
        # parakeet config.json UTF-8 baytında çöker. UTF-8'i garanti et (kod tarafındaki
        # ensure_utf8() ile birlikte defense-in-depth). Değişiklik lsregister/rebuild ister.
        "LSEnvironment": {
            "PYTHONUTF8": "1",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            # Homebrew PATH: yoksa ffmpeg bulunamaz → transkripsiyon boş döner ("cevap yok").
            "PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        # Mikrofon izni penceresi bu açıklamayla çıkar (zorunlu — yoksa erişim reddedilir).
        "NSMicrophoneUsageDescription":
            f"{ASSISTANT_NAME}, ⌘⇧A'ya bastığınızda sizi dinleyip yanıtlamak için mikrofona erişir.",
        # AppleEvents izni: dikte metnini imlece yapıştırmak için System Events kontrolü.
        "NSAppleEventsUsageDescription":
            f"{ASSISTANT_NAME}, dikte metnini imlece yapıştırmak ve sistem komutlarını yürütmek için AppleEvents kullanır.",
    },
}

setup(
    app=APP,
    name=ASSISTANT_NAME,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
