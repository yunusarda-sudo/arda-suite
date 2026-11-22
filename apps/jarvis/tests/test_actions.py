"""actions testleri — kürütörlü beyaz liste. Gerçek subprocess/timer mock'lu.

Güvenlik garantileri burada doğrulanır: keyfi shell yok (hep arg listesi),
bilinmeyen eylem güvenli mesaja düşer, girdiler sınırlanır/doğrulanır.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from arda import actions
from arda.actions import ActionDeps, run_action


@pytest.fixture(autouse=True)
def _macos_action_platform(monkeypatch):
    """These tests assert the macOS command adapter, independent of CI host OS."""
    monkeypatch.setattr("arda.platform_runtime.system", lambda: "Darwin")


def _ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fake_deps(stdout="", returncode=0):
    calls = {"run": [], "schedule": [], "say": []}

    def _run(args):
        calls["run"].append(args)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    def _schedule(seconds, callback):
        calls["schedule"].append((seconds, callback))

    deps = ActionDeps(
        run=_run,
        schedule=_schedule,
        say=lambda t: calls["say"].append(t),
        now=lambda: datetime(2026, 6, 19, 14, 30),
    )
    return deps, calls


class TestRunActionDispatch:
    def test_unknown_action_is_safe(self):
        msg = run_action("rm_rf_everything", {}, deps=_fake_deps()[0])
        assert "yapamıyorum" in msg.lower()

    def test_handler_exception_is_swallowed(self):
        def _boom(_args):
            raise RuntimeError("subprocess çöktü")

        deps = ActionDeps(run=_boom)
        msg = run_action("open_app", {"name": "Safari"}, deps=deps)
        assert "sorun" in msg.lower()  # raise etmedi, güvenli mesaj döndü


class TestOpenApp:
    def test_opens_with_arg_list_no_shell(self):
        deps, calls = _fake_deps()
        run_action("open_app", {"name": "Safari"}, deps=deps)
        assert calls["run"] == [["open", "-a", "Safari"]]  # arg listesi → shell yok

    def test_missing_name_asks(self):
        deps, calls = _fake_deps()
        msg = run_action("open_app", {}, deps=deps)
        assert "uygulama" in msg.lower()
        assert calls["run"] == []

    def test_nonzero_returncode_reports_not_found(self):
        deps, _ = _fake_deps(returncode=1)
        msg = run_action("open_app", {"name": "Yokuygulama"}, deps=deps)
        assert "bulamadım" in msg.lower()

    def test_resolves_common_alias_like_chrome(self):
        deps, calls = _fake_deps()
        msg = run_action("open_app", {"name": "chrome"}, deps=deps)
        assert calls["run"] == [["open", "-a", "Google Chrome"]]
        assert "Google Chrome açılıyor." in msg

    def test_resolves_vscode_alias(self):
        deps, calls = _fake_deps()
        msg = run_action("open_app", {"name": "vscode"}, deps=deps)
        assert calls["run"] == [["open", "-a", "Visual Studio Code"]]
        assert "Visual Studio Code açılıyor." in msg

    def test_falls_back_to_web_url_for_known_services_when_app_not_installed(self):
        deps, calls = _fake_deps(returncode=1)
        msg = run_action("open_app", {"name": "youtube"}, deps=deps)
        assert ["open", "https://www.youtube.com"] in calls["run"]
        assert "web sitesi açılıyor" in msg.lower()


class TestOpenUrl:
    def test_rejects_non_http_scheme(self):
        deps, calls = _fake_deps()
        msg = run_action("open_url", {"url": "file:///etc/passwd"}, deps=deps)
        assert calls["run"] == []          # güvenlik: file:// açılmaz
        assert "web" in msg.lower()

    def test_opens_https(self):
        deps, calls = _fake_deps()
        run_action("open_url", {"url": "https://example.com"}, deps=deps)
        assert calls["run"] == [["open", "https://example.com"]]


class TestSetVolume:
    def test_clamps_above_100(self):
        deps, calls = _fake_deps()
        run_action("set_volume", {"level": "250"}, deps=deps)
        assert calls["run"] == [["osascript", "-e", "set volume output volume 100"]]

    def test_clamps_below_0(self):
        deps, calls = _fake_deps()
        run_action("set_volume", {"level": "-5"}, deps=deps)
        assert calls["run"] == [["osascript", "-e", "set volume output volume 0"]]

    def test_invalid_level_reports(self):
        deps, calls = _fake_deps()
        msg = run_action("set_volume", {"level": "yüksek"}, deps=deps)
        assert calls["run"] == []
        assert "anlayamadım" in msg.lower()

    def test_extracts_digits_from_formatted_string(self):
        deps, calls = _fake_deps()
        run_action("set_volume", {"level": "%50"}, deps=deps)
        assert calls["run"] == [["osascript", "-e", "set volume output volume 50"]]

    def test_handles_mute_keyword(self):
        deps, calls = _fake_deps()
        run_action("set_volume", {"level": "sessiz"}, deps=deps)
        assert calls["run"] == [["osascript", "-e", "set volume output volume 0"]]


class TestTellTime:
    def test_speaks_injected_clock(self):
        deps, _ = _fake_deps()
        assert run_action("tell_time", {}, deps=deps) == "Saat 14:30."


class TestSearchFiles:
    def test_counts_hits(self):
        deps, calls = _fake_deps(stdout="/a/rapor.pdf\n/b/rapor2.pdf\n")
        msg = run_action("search_files", {"query": "rapor"}, deps=deps)
        assert "2 dosya" in msg
        assert calls["run"][0][0] == "mdfind"

    def test_no_hits(self):
        deps, _ = _fake_deps(stdout="")
        msg = run_action("search_files", {"query": "yok"}, deps=deps)
        assert "bulamadım" in msg.lower()


class TestSetTimer:
    def test_schedules_and_fires_say(self):
        deps, calls = _fake_deps()
        msg = run_action("set_timer", {"seconds": "300", "label": "çay"}, deps=deps)
        assert "5 dakika" in msg
        assert len(calls["schedule"]) == 1
        seconds, callback = calls["schedule"][0]
        assert seconds == 300
        callback()                                   # sayaç dolunca
        assert calls["say"] == ["çay sayacı doldu."]

    def test_rejects_nonpositive(self):
        deps, calls = _fake_deps()
        msg = run_action("set_timer", {"seconds": "0"}, deps=deps)
        assert calls["schedule"] == []
        assert "geçersiz" in msg.lower()

    def test_rejects_too_long(self):
        deps, calls = _fake_deps()
        run_action("set_timer", {"seconds": str(48 * 3600)}, deps=deps)
        assert calls["schedule"] == []


class TestParamValidation:
    """Zorunlu param eksikse IO'ya gidilmez, kibarca sorulur (issue #3)."""

    def test_missing_required_param_asks_and_skips_io(self):
        deps, calls = _fake_deps()
        msg = run_action("set_volume", {}, deps=deps)   # level zorunlu, yok
        assert calls["run"] == []                        # IO'ya hiç gitmedi
        assert "?" in msg                                # kibar soru

    def test_missing_params_lists_required_gaps(self):
        assert actions.missing_params("open_app", {}) == ["name"]
        assert actions.missing_params("open_app", {"name": "  "}) == ["name"]  # boşluk = eksik
        assert actions.missing_params("open_app", {"name": "Safari"}) == []

    def test_optional_param_not_required(self):
        # set_timer.label opsiyonel → yokken eksik sayılmaz (seconds zorunlu).
        assert actions.missing_params("set_timer", {"seconds": "60"}) == []

    def test_present_but_wrong_type_is_rejected_before_io(self):
        # "yüksek" dolu (eksik değil) ama sayı değil → coerce reddeder, IO'ya gitmez.
        deps, calls = _fake_deps()
        msg = run_action("set_volume", {"level": "yüksek"}, deps=deps)
        assert "anlayamadım" in msg.lower()
        assert calls["run"] == []


class TestCoerceParams:
    """Tip dönüşümü/doğrulaması tek yerde (declarative, ParamSpec.type ile)."""

    def test_coerces_int_to_real_int(self):
        typed, err = actions.coerce_params("set_volume", {"level": "40"})
        assert err is None
        assert typed == {"level": 40} and isinstance(typed["level"], int)

    def test_int_failure_returns_friendly_error(self):
        typed, err = actions.coerce_params("set_volume", {"level": "loud"})
        assert typed == {} and "anlayamadım" in err.lower()

    def test_drops_params_not_in_schema(self):
        # Güvenlik: model şemada olmayan bir param sızdıramaz → düşürülür.
        typed, err = actions.coerce_params("open_app", {"name": "Safari", "evil": "rm -rf /"})
        assert err is None and typed == {"name": "Safari"}

    def test_validates_url_scheme(self):
        _, err = actions.coerce_params("open_url", {"url": "ftp://x"})
        assert err and "web" in err.lower()

    def test_clean_url_passes_through(self):
        typed, err = actions.coerce_params("open_url", {"url": "https://a.com"})
        assert err is None and typed == {"url": "https://a.com"}

    def test_optional_absent_param_is_skipped(self):
        typed, err = actions.coerce_params("set_timer", {"seconds": "60"})
        assert err is None and typed == {"seconds": 60}  # label yok → atlandı

    def test_coerces_datetime_type(self):
        typed, err = actions.coerce_params(
            "set_reminder", {"at": "2026-06-20T08:00", "text": "ders"}
        )
        assert err is None
        assert typed["at"] == datetime(2026, 6, 20, 8, 0)
        assert typed["text"] == "ders"

    def test_bad_datetime_returns_friendly_error(self):
        _, err = actions.coerce_params("set_reminder", {"at": "yarın 8", "text": "x"})
        assert err and "zaman" in err.lower()


class TestSetReminder:
    """Kalıcı macOS hatırlatıcısı (Reminders.app, osascript). IO mock'lu."""

    def test_creates_reminder_with_relative_delta(self):
        deps, calls = _fake_deps()  # now = 2026-06-19 14:30
        msg = run_action("set_reminder", {"at": "2026-06-19T15:30", "text": "toplantı"}, deps=deps)
        assert len(calls["run"]) == 1
        cmd = calls["run"][0]
        assert cmd[0] == "osascript"
        script = cmd[2]
        assert "(current date) + 3600" in script   # 1 saat sonra → 3600 sn
        assert '"toplantı"' in script
        assert "15:30" in msg

    def test_past_time_rejected_without_io(self):
        deps, calls = _fake_deps()
        msg = run_action("set_reminder", {"at": "2026-06-19T08:00", "text": "x"}, deps=deps)
        assert calls["run"] == []
        assert "geçti" in msg.lower()

    def test_missing_text_asks(self):
        deps, calls = _fake_deps()
        msg = run_action("set_reminder", {"at": "2026-06-19T15:30"}, deps=deps)
        assert calls["run"] == []
        assert "?" in msg

    def test_quotes_in_text_are_escaped(self):
        deps, calls = _fake_deps()
        run_action("set_reminder", {"at": "2026-06-19T15:30", "text": 'de "merhaba"'}, deps=deps)
        script = calls["run"][0][2]
        assert '\\"merhaba\\"' in script   # AppleScript string'inden kaçış engellendi

    def test_nonzero_returncode_reports_failure(self):
        deps, _ = _fake_deps(returncode=1)
        msg = run_action("set_reminder", {"at": "2026-06-19T15:30", "text": "x"}, deps=deps)
        assert "kuramadım" in msg.lower()


class TestRegistryConsistency:
    def test_every_action_has_a_spec(self):
        # Beyin'in prompt'u ACTION_SPECS'ten üretilir → her handler'ın açıklaması olmalı.
        assert set(actions.ACTION_NAMES) == set(actions.ACTION_SPECS)

    def test_every_action_has_param_spec(self):
        # Doğrulama ACTION_PARAMS'tan beslenir → her eylemin param sözleşmesi olmalı.
        assert set(actions.ACTION_NAMES) == set(actions.ACTION_PARAMS)


class TestActionResultStructured:
    """ADIM 2: run_action geriye uyumlu str döndürür; run_action_result yapılandırılmış
    sonucu (sesli metin + bellek payload'ı) verir. set_reminder reminder id'sini yakalar."""

    def test_run_action_still_returns_str(self):
        deps, _ = _fake_deps(stdout="x-apple-reminder://ABC")
        msg = run_action("set_reminder", {"at": "2026-06-19T15:30", "text": "toplantı"}, deps=deps)
        assert isinstance(msg, str) and "15:30" in msg

    def test_run_action_result_returns_memory_with_id(self):
        from arda.actions import run_action_result

        deps, _ = _fake_deps(stdout="x-apple-reminder://ABC\n")
        res = run_action_result("set_reminder", {"at": "2026-06-19T15:30", "text": "toplantı"}, deps=deps)
        assert res.spoken.startswith("Tamam")
        assert res.memory["action"] == "set_reminder"
        assert res.memory["success"] is True
        assert res.memory["text"] == "toplantı"
        assert res.memory["id"] == "x-apple-reminder://ABC"

    def test_set_reminder_script_captures_id(self):
        deps, calls = _fake_deps(stdout="x-1")
        run_action("set_reminder", {"at": "2026-06-19T15:30", "text": "toplantı"}, deps=deps)
        script = calls["run"][0][2]
        assert "return id of" in script                 # id yakalamak için referans döndürülür
        assert "(current date) + 3600" in script        # delta hesabı korunur

    def test_failed_action_marks_success_false(self):
        from arda.actions import run_action_result

        deps, _ = _fake_deps(returncode=1)
        res = run_action_result("set_reminder", {"at": "2026-06-19T15:30", "text": "x"}, deps=deps)
        assert res.memory["success"] is False

    def test_simple_action_has_no_memory(self):
        from arda.actions import run_action_result

        deps, _ = _fake_deps()
        res = run_action_result("open_app", {"name": "Safari"}, deps=deps)
        assert res.memory is None                        # düz eylemler bağlamı kirletmez


class TestDeleteReminder:
    """ADIM 3: hatırlatıcı sil (Reminders.app, osascript, arg-list). IO mock'lu."""

    def test_deletes_by_title_arg_list_no_shell(self):
        deps, calls = _fake_deps(stdout="1")
        msg = run_action("delete_reminder", {"title": "toplantı"}, deps=deps)
        assert "sildim" in msg.lower()
        assert calls["run"][0][0] == "osascript"          # arg listesi → shell yok
        assert 'name is "toplantı"' in calls["run"][0][2]

    def test_not_found_reports(self):
        deps, _ = _fake_deps(stdout="0")
        msg = run_action("delete_reminder", {"title": "yok"}, deps=deps)
        assert "bulamadım" in msg.lower()

    def test_permission_error_reports(self):
        deps, _ = _fake_deps(returncode=1)
        msg = run_action("delete_reminder", {"title": "x"}, deps=deps)
        assert "silemedim" in msg.lower()

    def test_missing_title_asks_and_skips_io(self):
        deps, calls = _fake_deps()
        msg = run_action("delete_reminder", {}, deps=deps)
        assert "?" in msg
        assert calls["run"] == []

    def test_quotes_in_title_escaped(self):
        deps, calls = _fake_deps(stdout="1")
        run_action("delete_reminder", {"title": 'de "x"'}, deps=deps)
        assert '\\"x\\"' in calls["run"][0][2]            # AppleScript string kaçışı

    def test_id_takes_precedence_over_title(self):
        deps, calls = _fake_deps(stdout="1")
        run_action("delete_reminder", {"title": "toplantı", "id": "x-apple://A"}, deps=deps)
        assert 'id is "x-apple://A"' in calls["run"][0][2]   # id varsa kesin eşleşme

    def test_records_structured_memory(self):
        from arda.actions import run_action_result

        deps, _ = _fake_deps(stdout="1")
        res = run_action_result("delete_reminder", {"title": "toplantı"}, deps=deps)
        assert res.memory["action"] == "delete_reminder" and res.memory["success"] is True


class TestListReminders:
    """ADIM 3: bekleyen hatırlatıcıları oku ve answered-tarzı sesle özetle. IO mock'lu."""

    def test_counts_incomplete_via_arg_list(self):
        deps, calls = _fake_deps(stdout="süt al, panel, fatura")
        msg = run_action("list_reminders", {}, deps=deps)
        assert "3 hatırlatıcın" in msg
        assert calls["run"][0][0] == "osascript"
        assert "completed is false" in calls["run"][0][2]

    def test_single_reminder(self):
        deps, _ = _fake_deps(stdout="süt al")
        msg = run_action("list_reminders", {}, deps=deps)
        assert "süt al" in msg and "bir" in msg.lower()

    def test_empty_list(self):
        deps, _ = _fake_deps(stdout="")
        msg = run_action("list_reminders", {}, deps=deps)
        assert "yok" in msg.lower()

    def test_permission_error_reports(self):
        deps, _ = _fake_deps(returncode=1)
        msg = run_action("list_reminders", {}, deps=deps)
        assert "okuyamadım" in msg.lower()
