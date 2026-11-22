#!/usr/bin/env python3
"""Unit tests for macOS Desktop App bundle (SharedBrain.app)."""

import os
import plistlib
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
APP_BUNDLE = HERE / "dist" / "SharedBrain.app"
USER_APP_BUNDLE = Path.home() / "Applications" / "SharedBrain.app"


class TestMacOSApp(unittest.TestCase):

    @unittest.skipUnless(sys.platform == "darwin" and APP_BUNDLE.is_dir(),
                         "requires a built macOS SharedBrain.app artifact")
    def test_app_bundle_structure(self):
        self.assertTrue(APP_BUNDLE.is_dir(), f"Bundle directory {APP_BUNDLE} missing")
        contents = APP_BUNDLE / "Contents"
        self.assertTrue(contents.is_dir())
        self.assertTrue((contents / "Info.plist").is_file())
        self.assertTrue((contents / "PkgInfo").is_file())
        self.assertTrue((contents / "MacOS" / "SharedBrain").is_file())
        self.assertTrue((contents / "Resources" / "AppIcon.icns").is_file())

    @unittest.skipUnless(sys.platform == "darwin" and APP_BUNDLE.is_dir(),
                         "requires a built macOS SharedBrain.app artifact")
    def test_launcher_permissions(self):
        launcher = APP_BUNDLE / "Contents" / "MacOS" / "SharedBrain"
        self.assertTrue(os.access(launcher, os.X_OK), "SharedBrain launcher must be executable")

        # Verify launcher script contains health checks and target URL
        content = launcher.read_text(encoding="utf-8")
        self.assertIn("6333", content, "Should check Qdrant port")
        self.assertIn("11434", content, "Should check Ollama port")
        self.assertIn("8899", content, "Should check Dashboard port")
        self.assertIn("#harness", content, "Should open direct to Harness Studio")

    @unittest.skipUnless(sys.platform == "darwin" and APP_BUNDLE.is_dir(),
                         "requires a built macOS SharedBrain.app artifact")
    def test_info_plist_validity(self):
        plist_path = APP_BUNDLE / "Contents" / "Info.plist"
        with open(plist_path, "rb") as f:
            pl = plistlib.load(f)

        self.assertEqual(pl.get("CFBundleExecutable"), "SharedBrain")
        self.assertEqual(pl.get("CFBundleIdentifier"), "com.mem0.sharedbrain")
        self.assertEqual(pl.get("CFBundlePackageType"), "APPL")
        self.assertEqual(pl.get("CFBundleIconFile"), "AppIcon")
        self.assertEqual(pl.get("CFBundleName"), "Shared Brain")

    @unittest.skipUnless(sys.platform == "darwin" and APP_BUNDLE.is_dir(),
                         "requires a built macOS SharedBrain.app artifact")
    def test_app_icon_exists_and_non_empty(self):
        icon_path = APP_BUNDLE / "Contents" / "Resources" / "AppIcon.icns"
        self.assertTrue(icon_path.stat().st_size > 1000, "AppIcon.icns should be a valid icon file (>1KB)")

    def test_installed_user_app(self):
        try:
            if USER_APP_BUNDLE.exists():
                launcher = USER_APP_BUNDLE / "Contents" / "MacOS" / "SharedBrain"
                if os.access(launcher, os.R_OK):
                    self.assertTrue(os.access(launcher, os.X_OK))
        except (PermissionError, OSError):
            pass


if __name__ == "__main__":
    unittest.main()
