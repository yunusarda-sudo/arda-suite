"""Yapılandırma yükleme: config.yaml (varsayılanlar) + config.local.yaml (sır/yola özel).

İki katman derin (özyinelemeli) birleştirilir: iç içe dict'ler seviye seviye
birleşir (bir alt anahtarı override etmek kardeşlerini silmez); dict olmayan
değerler (skaler, liste) doğrudan ezilir.
Doğrulama burada yapılır (vault.path zorunlu); hata durumunda ConfigError fırlatılır
— süreç sonlandırma kararı çağırana (örn. __main__) bırakılır.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
DEFAULT_BASE = _ROOT / "config.yaml"
DEFAULT_LOCAL = _ROOT / "config.local.yaml"


class ConfigError(Exception):
    """Yapılandırma dosyası eksik/geçersiz olduğunda fırlatılır."""


def _read_yaml(path: Path) -> dict:
    """YAML dosyasını dict olarak oku; boş dosya → {}. Sözlük değilse hata."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} bir eşleme (mapping) olmalı.")
    return data


def _merge(base: dict, overlay: dict) -> dict:
    """Derin (özyinelemeli) birleşme: yeni dict döndür (base'i değiştirmez).

    İç içe dict'ler seviye seviye birleşir; bir alt anahtarı override etmek
    (örn. ``vault.categories.idea``) kardeşlerini (``internship``, ``inbox``) silmez.
    Dict olmayan değerler (skaler, liste) doğrudan ezilir."""
    merged = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config(*, base: Path = DEFAULT_BASE, local: Path = DEFAULT_LOCAL) -> dict:
    """Birleştirilmiş, doğrulanmış yapılandırmayı döndür.

    Raises:
        ConfigError: base dosyası yoksa, biçimi bozuksa veya vault.path eksikse.
    """
    if not base.exists():
        raise ConfigError(
            f"Yapılandırma bulunamadı: {base}\n"
            "config.yaml repo kökünde olmalı."
        )

    cfg = _read_yaml(base)
    if local.exists():
        cfg = _merge(cfg, _read_yaml(local))

    if not isinstance(cfg.get("vault"), dict) or not cfg["vault"].get("path"):
        raise ConfigError(
            "vault.path tanımlı değil.\n"
            "config.local.yaml dosyasını oluşturup vault.path ayarını ekle "
            "(şablon: config.local.example.yaml)."
        )
    return cfg


def save_local(updates: dict, *, local: Path = DEFAULT_LOCAL) -> None:
    """Yalnızca verilen anahtarları ``config.local.yaml``'a yazar (derin-merge, atomik).

    Var olan ayarları (örn. ``vault.path``) **korur**: mevcut yerel dosyayı
    okuyup üzerine özyinelemeli birleştirir. Önce ``.tmp``'ye yazıp ``replace`` ile
    taşır → yarım/bozuk dosya bırakmaz. Çalışma-zamanı ayar değişiklikleri için
    (menubar 'Ayarlar') kullanılır.
    """
    current = _read_yaml(local) if local.exists() else {}
    merged = _merge(current, updates)
    local.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{local.name}.", suffix=".tmp", dir=local.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(local)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp.unlink(missing_ok=True)
        raise
