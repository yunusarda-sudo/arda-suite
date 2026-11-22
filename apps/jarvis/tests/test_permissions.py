"""permissions testleri — platform denetimi ve rehber üretimi."""
from unittest.mock import MagicMock, patch

from arda import permissions


def test_is_macos():
    with patch("platform.system", return_value="Darwin"):
        assert permissions.is_macos() is True
    with patch("platform.system", return_value="Linux"):
        assert permissions.is_macos() is False


def test_is_accessibility_granted_non_darwin():
    with patch("arda.permissions.is_macos", return_value=False):
        assert permissions.is_accessibility_granted() is True


def test_is_accessibility_granted_darwin_mock():
    with patch("arda.permissions.is_macos", return_value=True), \
         patch("ctypes.util.find_library", return_value="/mock/path"), \
         patch("ctypes.cdll.LoadLibrary") as mock_load:
        mock_lib = MagicMock()
        mock_lib.AXIsProcessTrusted.return_value = True
        mock_load.return_value = mock_lib
        assert permissions.is_accessibility_granted() is True


def test_is_microphone_available():
    with patch("sounddevice.query_devices", return_value={"max_input_channels": 2}):
        assert permissions.is_microphone_available() is True

    with patch("sounddevice.query_devices", return_value=None):
        assert permissions.is_microphone_available() is False


def test_permission_report():
    with patch("arda.permissions.is_macos", return_value=True), \
         patch("arda.permissions.check_all_permissions", return_value={"accessibility": True, "microphone": True}):
        rep = permissions.permission_report()
        assert "✅ Verildi" in rep

    with patch("arda.permissions.is_macos", return_value=True), \
         patch("arda.permissions.check_all_permissions", return_value={"accessibility": False, "microphone": False}):
        rep = permissions.permission_report()
        assert "Erişilebilirlik izni vermek için" in rep
        assert "Mikrofon izni vermek için" in rep


def test_permission_report_non_macos_is_not_misleading():
    with patch("arda.permissions.is_macos", return_value=False), \
         patch("platform.system", return_value="Linux"), \
         patch("arda.permissions.check_all_permissions", return_value={"accessibility": True, "microphone": True}):
        rep = permissions.permission_report()
        assert "Linux İzin Durumu" in rep
        assert "macOS İzin Durumu" not in rep
        assert "Erişilebilirlik: ✅ Gerekli değil" in rep
