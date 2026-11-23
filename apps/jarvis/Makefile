# Jarvis — geliştirici komutları (npm run benzeri)
# Kullanım: make <hedef>   (örn. make test)

PY := .venv/bin/python

# HF indirmelerini Xet ile yüksek performanslı çek (huggingface_hub >=1.x)
export HF_XET_HIGH_PERFORMANCE := 1
# İsteğe bağlı: HF_TOKEN'ı gitignore'lu .env'den oku (rate-limit kalkar, hız artar)
-include .env
export

WHISPER_MODEL  := mlx-community/whisper-large-v3-turbo
PARAKEET_MODEL := mlx-community/parakeet-tdt-0.6b-v3
OLLAMA_MODEL   := qwen3:8b
# Vosk wake backend modeli (offline). Standart küçük İngilizce model; istersen değiştir.
VOSK_URL       := https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip

.DEFAULT_GOAL := help

.PHONY: help test test-v run compile install clean setup models models-vosk app app-clean dmg release-macos check-wake doctor rpi-check rpi-e2e-check install-rpi-service rpi-status rpi-logs rpi-start rpi-stop rpi-restart rpi-listen

doctor:  ## platform, ses, STT ve Ollama durumunu denetle
	@$(PY) -c "from arda.permissions import permission_report; print(permission_report())"
	@$(PY) -c "from arda.platform_runtime import runtime_report; print(runtime_report())"
	@command -v ollama >/dev/null && echo "  - Ollama CLI: ✅ Kurulu" || echo "  - Ollama CLI: ⚠️ Bulunamadı"
	@curl -s http://localhost:11434/api/tags >/dev/null && echo "  - Ollama Servisi: ✅ Çalışıyor" || echo "  - Ollama Servisi: ⚠️ Kapalı"

install-rpi-service:  ## Linux/RPi systemd kullanıcı servisini kur ve başlat
	./scripts/install_rpi_service.sh

rpi-check:  ## Linux/RPi kurulum ön koşullarını değişiklik yapmadan denetle
	./scripts/rpi_preflight.sh

rpi-e2e-check:  ## Linux/RPi servis + Ollama + Qdrant smoke kontrolü
	./scripts/rpi_e2e_smoke.sh

rpi-status:  ## Linux/RPi Jarvis servis durumunu göster
	./scripts/jarvisctl status

rpi-logs:  ## Linux/RPi Jarvis loglarını canlı izle
	./scripts/jarvisctl logs

rpi-start:  ## Linux/RPi Jarvis servisini başlat
	./scripts/jarvisctl start

rpi-stop:  ## Linux/RPi Jarvis servisini durdur
	./scripts/jarvisctl stop

rpi-restart:  ## Linux/RPi Jarvis servisini yeniden başlat
	./scripts/jarvisctl restart

rpi-listen:  ## Linux/RPi daemon'a tek seferlik dinleme tetiği gönder
	./scripts/jarvisctl listen

help:  ## Komut listesini göster
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

test:  ## Testleri çalıştır
	$(PY) -m pytest

test-v:  ## Testleri detaylı çalıştır
	$(PY) -m pytest -v

run:  ## Uygulamayı başlat (mikrofon + Ollama + STT modeli: parakeet/whisper gerektirir)
	$(PY) -m arda

compile:  ## Tüm modüllerin derlendiğini doğrula
	$(PY) -m compileall -q arda

install:  ## Paketi düzenlenebilir kur (sonra `arda` komutu kullanılabilir)
	$(PY) -m pip install -e .

setup: install models  ## Tam ilk kurulum (paket + tüm modeller)

models:  ## STT + LLM + wake-word modellerini önceden indir (run'ı bloke etmesin)
	$(PY) -m pip install -q -r requirements.txt
	$(PY) -c "from huggingface_hub import snapshot_download; snapshot_download('$(WHISPER_MODEL)')"
	$(PY) -c "from huggingface_hub import snapshot_download; snapshot_download('$(PARAKEET_MODEL)')" || echo "parakeet modeli atlandı"
	$(PY) -c "import openwakeword.utils as u; u.download_models()" || echo "openWakeWord modelleri atlandı"
	$(MAKE) models-vosk
	@command -v ollama >/dev/null && ollama pull $(OLLAMA_MODEL) || echo "ollama yok — qwen3 atlandı"

models-vosk:  ## Vosk wake modelini indir → models/vosk-en (varsa atlar; mevcut kurulumu EZMEZ)
	@if [ -d models/vosk-en ]; then \
		echo "models/vosk-en zaten var — atlandı"; \
	else \
		echo "Vosk modeli indiriliyor ($(VOSK_URL))..."; \
		curl -fsSL -o /tmp/vosk-en.zip "$(VOSK_URL)" \
			&& unzip -q /tmp/vosk-en.zip -d models/ \
			&& mv models/vosk-model-small-en-us-0.15 models/vosk-en \
			&& rm -f /tmp/vosk-en.zip \
			&& echo "✅ models/vosk-en hazır" \
			|| echo "⚠️  Vosk indirme başarısız — elle indir: $(VOSK_URL)"; \
	fi

check-wake:  ## Wake modelini doğrula: make check-wake [WAV=klip.wav] [MODEL=models/hey_arda.onnx]
	$(PY) scripts/check_wake_model.py $(if $(MODEL),--model $(MODEL)) $(if $(WAV),--wav $(WAV))

app:  ## Jarvis.app paketle (alias build — bu Mac'e özel; Launchpad'de çift tıkla-aç)
	$(PY) -m pip install -q py2app
	rm -rf build dist
	$(PY) setup.py py2app -A
	@echo "✅ dist/Jarvis.app hazır. Aç:  open dist/Jarvis.app   (veya Launchpad'e kopyala)"

dmg: app  ## Jarvis.dmg paketleyici üret (Applications sürükle-bırak DMG)
	@echo "DMG imajı oluşturuluyor..."
	@rm -rf /tmp/jarvis-dmg dist/Jarvis.dmg
	@mkdir -p /tmp/jarvis-dmg
	@cp -R dist/Jarvis.app /tmp/jarvis-dmg/
	@ln -s /Applications /tmp/jarvis-dmg/Applications
	@hdiutil create -volname "Jarvis" -srcfolder /tmp/jarvis-dmg -ov -format UDZO dist/Jarvis.dmg
	@rm -rf /tmp/jarvis-dmg
	@echo "✅ dist/Jarvis.dmg hazır! Aç: open dist/Jarvis.dmg"

release-macos: compile test dmg  ## testleri geçerse macOS Jarvis.app + Jarvis.dmg üret
	@dist/Jarvis.app/Contents/MacOS/Jarvis --version
	@hdiutil verify dist/Jarvis.dmg >/dev/null
	@echo "✅ macOS release doğrulandı: dist/Jarvis.app ve dist/Jarvis.dmg"

app-clean:  ## .app build çıktısını temizle
	rm -rf build dist

clean:  ## __pycache__ ve geçici dosyaları temizle
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache *.egg-info
