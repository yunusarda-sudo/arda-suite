"""config.load_config testleri — gerçek dosyalarla (tmp_path), sır/yola özel mock yok."""
from __future__ import annotations

import os
import stat

import pytest

from arda.config import ConfigError, load_config, save_local

_BASE_YAML = """\
brain:
  model: "qwen3:8b"
  host: "http://localhost:11434"
stt:
  language: "tr"
vault:
  categories:
    internship: "Internship"
    inbox: "Inbox"
"""


def _write(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadConfigMerge:
    def test_base_plus_local_merges_vault_path(self, tmp_path):
        base = _write(tmp_path / "config.yaml", _BASE_YAML)
        local = _write(
            tmp_path / "config.local.yaml",
            'vault:\n  path: "/Users/x/Vault"\n',
        )
        cfg = load_config(base=base, local=local)
        # local'den path geldi, base'den categories korundu (sığ birleşme)
        assert cfg["vault"]["path"] == "/Users/x/Vault"
        assert cfg["vault"]["categories"]["internship"] == "Internship"
        assert cfg["brain"]["model"] == "qwen3:8b"

    def test_local_overrides_scalar_keys(self, tmp_path):
        base = _write(tmp_path / "config.yaml", _BASE_YAML)
        local = _write(
            tmp_path / "config.local.yaml",
            'vault:\n  path: "/v"\nbrain:\n  host: "http://remote:11434"\n',
        )
        cfg = load_config(base=base, local=local)
        assert cfg["brain"]["host"] == "http://remote:11434"
        assert cfg["brain"]["model"] == "qwen3:8b"  # base korundu

    def test_deep_merges_nested_categories(self, tmp_path):
        # İki seviye derin override: local yalnız 'idea' kategorisini ekler;
        # base'in diğer kategorileri (internship, inbox) KORUNMALI (derin birleşme).
        base = _write(tmp_path / "config.yaml", _BASE_YAML)
        local = _write(
            tmp_path / "config.local.yaml",
            'vault:\n  path: "/v"\n  categories:\n    idea: "Fikirler"\n',
        )
        cfg = load_config(base=base, local=local)
        assert cfg["vault"]["categories"]["idea"] == "Fikirler"
        assert cfg["vault"]["categories"]["internship"] == "Internship"  # kardeş korundu
        assert cfg["vault"]["categories"]["inbox"] == "Inbox"            # kardeş korundu
        assert cfg["vault"]["path"] == "/v"

    def test_works_without_local_when_base_has_vault_path(self, tmp_path):
        base = _write(
            tmp_path / "config.yaml",
            _BASE_YAML + '  path: "/only-base"\n',
        )
        cfg = load_config(base=base, local=tmp_path / "yok.yaml")
        assert cfg["vault"]["path"] == "/only-base"


class TestLoadConfigValidation:
    def test_missing_vault_path_raises_config_error(self, tmp_path):
        base = _write(tmp_path / "config.yaml", _BASE_YAML)
        with pytest.raises(ConfigError):
            load_config(base=base, local=tmp_path / "yok.yaml")

    def test_empty_base_raises_config_error_not_typeerror(self, tmp_path):
        base = _write(tmp_path / "config.yaml", "")
        with pytest.raises(ConfigError):
            load_config(base=base, local=tmp_path / "yok.yaml")

    def test_missing_base_file_raises_config_error(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(base=tmp_path / "yok.yaml", local=tmp_path / "yok2.yaml")


class TestSaveLocal:
    @pytest.mark.parametrize("existing", [False, True], ids=["create", "overwrite"])
    def test_local_config_is_private_even_with_permissive_umask(self, tmp_path, existing):
        local = tmp_path / "config.local.yaml"
        if existing:
            _write(local, 'vault:\n  path: "/keep/me"\n')
            local.chmod(0o644)

        old_umask = os.umask(0)
        try:
            save_local({"tts": {"voice": "Alice"}}, local=local)
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(local.stat().st_mode) == 0o600

    def test_creates_file_when_absent(self, tmp_path):
        local = tmp_path / "config.local.yaml"
        save_local({"tts": {"voice": "Alice"}}, local=local)
        assert local.exists()
        reloaded = load_config(
            base=_write(tmp_path / "config.yaml", _BASE_YAML + '  path: "/v"\n'),
            local=local,
        )
        assert reloaded["tts"]["voice"] == "Alice"

    def test_preserves_existing_keys(self, tmp_path):
        local = _write(
            tmp_path / "config.local.yaml",
            'vault:\n  path: "/keep/me"\n',
        )
        save_local({"tts": {"voice": "Fred"}}, local=local)
        # vault.path ezilmemeli; yeni anahtar eklenmeli.
        base = _write(tmp_path / "config.yaml", _BASE_YAML)
        cfg = load_config(base=base, local=local)
        assert cfg["vault"]["path"] == "/keep/me"
        assert cfg["tts"]["voice"] == "Fred"

    def test_shallow_merges_nested_subkeys(self, tmp_path):
        local = _write(tmp_path / "config.local.yaml", 'wake:\n  threshold: 0.5\n')
        save_local({"wake": {"enabled": False}}, local=local)
        from arda.config import _read_yaml

        data = _read_yaml(local)
        assert data["wake"]["enabled"] is False
        assert data["wake"]["threshold"] == 0.5  # mevcut alt-anahtar korundu

    def test_unicode_roundtrip(self, tmp_path):
        local = tmp_path / "config.local.yaml"
        save_local({"tts": {"voice": "Çağrı"}}, local=local)
        from arda.config import _read_yaml

        assert _read_yaml(local)["tts"]["voice"] == "Çağrı"
