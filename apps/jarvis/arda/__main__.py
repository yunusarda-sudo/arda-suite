"""Jarvis — push-to-talk Siri-benzeri masaüstü sesli asistan.

⌘⇧A → dinler → ne istediğini anlar → yanıtlar / Mac eylemi yapar / not alır.
app.mode ile menü çubuğu app'i veya görünmez daemon olarak çalışır.
(İç paket adı tarihsel olarak `arda` kalır; kullanıcıya görünen ad: Jarvis.)
"""
from __future__ import annotations

import argparse
import atexit
import dataclasses
import logging
import os
import signal
import subprocess
import sys
import threading

from datetime import date
from pathlib import Path

from arda import __version__
from arda.constants import ASSISTANT_NAME, TTS_VOICE, WAKE_ACK
from arda.core import bootstrap_runtime_database
from arda.diagnostics import Diagnostics, default_database_path, render
from arda.logging_setup import setup_logging
from arda.platform_runtime import runtime_report, system as platform_system
from arda.permissions import permission_report

log = logging.getLogger(__name__)

_DEFAULT_VOICE = TTS_VOICE


def _load_desktop_runtime() -> None:
    """Load optional desktop-agent dependencies only for the application path.

    Read-only Core diagnostics must remain usable on a minimal Pi deployment.
    The legacy Jarvis runtime includes optional model/audio dependencies (for
    example ``ollama``), so importing them at module load time would make
    ``arda episode`` fail before it can inspect SQLite state.
    """
    global actions, agent_audit, agent_executor, agent_planner, brain, dictation
    global memory, notes, phrases, research, speak
    global AgentDeps, run_forever, AgentRunDeps, CamofoxClient
    global SkillError, select_skill, ToolContext, ConfigError, load_config
    global PendingStore, Conversation, record_barge_in, record_follow_up
    global record_until_silence, MemoryStore, ensure_ollama, Deps, process_once
    global ensure_app_environment, Controls, make_transcriber, wait_for_trigger
    global make_listener

    from arda import actions, agent_audit, agent_executor, agent_planner, brain, dictation, memory, notes, phrases, research, speak
    from arda.agent import AgentDeps, run_forever
    from arda.agent_executor import AgentRunDeps
    from arda.browser_adapter import CamofoxClient
    from arda.browser_sessions import BrowserSessionRegistry, close_revoked_browser_tabs
    from arda.agent_skills import SkillError, select_skill
    from arda.agent_tools import ToolContext
    from arda.config import ConfigError, load_config
    from arda.confirm import PendingStore
    from arda.conversation import Conversation
    from arda.listen import record_barge_in, record_follow_up, record_until_silence
    from arda.memory import MemoryStore
    from arda.ollama_boot import ensure_ollama
    from arda.pipeline import Deps, process_once
    from arda.utf8_boot import ensure_app_environment
    from arda.state import Controls
    from arda.transcribe import make_transcriber
    from arda.trigger import wait_for_trigger
    from arda.wake import make_listener


def _effective_mode(cfg: dict) -> str:
    """Select a runnable UI mode without loading macOS menubar UI on Linux."""
    requested = str(cfg.get("app", {}).get("mode", "menubar")).lower()
    if requested == "menubar" and platform_system() != "Darwin":
        log.warning("Menubar modu %s üzerinde desteklenmiyor; daemon moduna düşülüyor", platform_system())
        return "daemon"
    return requested if requested in {"menubar", "daemon"} else "daemon"


def _install_daemon_trigger(controls: Controls, mode: str) -> None:
    """Allow a headless daemon turn to be triggered with ``SIGUSR1``."""
    if mode != "daemon" or not hasattr(signal, "SIGUSR1"):
        return

    def _on_trigger(_signum, _frame):
        if controls.request_manual():
            log.info("SIGUSR1 alındı; manuel dinleme tetiklendi")
        else:
            log.info("SIGUSR1 alındı ancak ajan meşgul")

    signal.signal(signal.SIGUSR1, _on_trigger)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda _signum, _frame: controls.request_shutdown())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="arda",
        description=f"{ASSISTANT_NAME} masaüstü sesli asistanını başlatır.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{ASSISTANT_NAME} {__version__}",
    )
    commands = parser.add_subparsers(dest="diagnostic_command")

    def add_read_command(name: str) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=f"read-only {name} diagnostics")
        command.add_argument("--db", type=Path, default=default_database_path())
        command.add_argument("--json", action="store_true", dest="as_json")
        return command

    for name in ("status", "projects", "jobs", "episodes", "capabilities", "providers", "doctor"):
        add_read_command(name)
    episode = commands.add_parser("episode", help="show one read-only Episode and lifecycle events")
    episode.add_argument("episode_id")
    episode.add_argument("--db", type=Path, default=default_database_path())
    episode.add_argument("--json", action="store_true", dest="as_json")
    events = add_read_command("events")
    events.add_argument("--limit", type=int, default=20)
    github = commands.add_parser("github", help="read-only GitHub diagnostics")
    github_commands = github.add_subparsers(dest="github_command", required=True)
    github_status = github_commands.add_parser("status", help="show GitHub sync state")
    github_status.add_argument("--db", type=Path, default=default_database_path())
    github_status.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def _run_diagnostic(args: argparse.Namespace) -> int:
    with Diagnostics(args.db) as diagnostics:
        if args.diagnostic_command == "status":
            result = diagnostics.status()
        elif args.diagnostic_command == "projects":
            result = diagnostics.projects()
        elif args.diagnostic_command == "jobs":
            result = diagnostics.jobs()
        elif args.diagnostic_command == "episodes":
            result = diagnostics.episodes()
        elif args.diagnostic_command == "capabilities":
            result = diagnostics.capabilities()
        elif args.diagnostic_command == "providers":
            result = diagnostics.providers()
        elif args.diagnostic_command == "episode":
            result = diagnostics.episode(args.episode_id)
        elif args.diagnostic_command == "events":
            result = diagnostics.events(args.limit)
        elif args.diagnostic_command == "doctor":
            result = diagnostics.doctor()
        elif args.diagnostic_command == "github" and args.github_command == "status":
            result = diagnostics.github_status()
        else:  # pragma: no cover - argparse restricts command values
            raise ValueError(f"unknown diagnostic command: {args.diagnostic_command}")
    print(render(result, as_json=args.as_json))
    return 0


def _confirm_dialog(message: str, *, cancel_event: threading.Event | None = None) -> bool:
    """confirm-riskli agent adımını native macOS dialog ile onaylat (Evet/Hayır).

    osascript arg-listesiyle çalışır (shell yok); kullanıcı 'Evet'e basarsa True.
    İptal/Escape ya da hata → False (fail-closed)."""
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display dialog "{safe}" buttons {{"Hayır", "Evet"}} '
        f'default button "Evet" with title "{ASSISTANT_NAME} Agent" with icon note'
    )
    try:
        proc = subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.wait(0.05):
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False
        stdout, _stderr = proc.communicate(timeout=0.5)
        return proc.returncode == 0 and "Evet" in (stdout or "")
    except Exception:  # noqa: BLE001 — dialog kurulamadı → güvenli taraf
        return False


def _agent_research(query: str, cfg: dict) -> str:
    """web_research aracı için: research.py ara → brain.synthesize kaynaklı özet.

    Mevcut research/synthesize boru hattını agent'a köprüler (madde 3 entegrasyonu)."""
    sources = research.research(query, cfg)[0]
    if not sources:
        return ""
    return brain.synthesize(query, sources, cfg=cfg)


def _build_run_agent(cfg: dict, controls: Controls):
    """Çok-adımlı desktop agent için run_agent(request) callable'ı kur.

    Sandbox = agent.workspace (yoksa cwd). Onay = native dialog. Plan/özet = Ollama.
    web_research aracı mevcut research boru hattına bağlanır. İptal: controls.is_cancelled
    executor'a should_stop olarak verilir → uzun koşu UI'dan durdurulabilir (adım sınırında).
    """
    agent_cfg = cfg.get("agent", {})
    ws = agent_cfg.get("workspace")
    workspace = Path(ws).expanduser() if ws else Path.cwd()
    # Denetim günlüğü: agent.log_dir (yoksa vault/AgentRuns) → "Jarvis dosyalarıma ne yaptı?"
    log_dir = agent_cfg.get("log_dir") or (Path(cfg["vault"]["path"]).expanduser() / "AgentRuns")
    browser_cfg = cfg.get("browser", {})
    project_id = str(browser_cfg.get("project_id", "")).strip()
    browser_session_id = str(browser_cfg.get("session_id", "")).strip()
    browser_authorize = None
    if project_id and browser_session_id:
        def browser_authorize(url):
            database = bootstrap_runtime_database()
            try:
                return BrowserSessionRegistry(database).authorize(
                    browser_session_id, project_id, url=url
                )
            finally:
                database.close()
    browser = CamofoxClient(
        base_url=str(browser_cfg.get("base_url", "http://localhost:9377")),
        api_key=os.environ.get("CAMOFOX_API_KEY", ""),
        timeout=float(browser_cfg.get("timeout_seconds", 15)),
        egress_proxy_host=str(browser_cfg.get("egress_proxy_host", "")),
        egress_proxy_port=int(browser_cfg.get("egress_proxy_port", 3129)),
        require_egress_proxy=bool(browser_cfg.get("require_egress_proxy", False)),
        required_camofox_version=str(browser_cfg.get("required_camofox_version", "2.4.8")),
    )

    def browser_recover():
        database = bootstrap_runtime_database()
        try:
            return close_revoked_browser_tabs(BrowserSessionRegistry(database), browser)
        finally:
            database.close()

    def browser_register_tab(tab_id):
        database = bootstrap_runtime_database()
        try:
            BrowserSessionRegistry(database).track_tab(browser_session_id, project_id, tab_id)
        finally:
            database.close()

    def browser_record_tab_close(tab_id, error_class):
        database = bootstrap_runtime_database()
        try:
            BrowserSessionRegistry(database).record_tab_close(
                browser_session_id, tab_id, error_class=error_class
            )
        finally:
            database.close()

    ctx = ToolContext(
        workspace=workspace,
        test_cmd=tuple(agent_cfg.get("test_cmd") or ("pytest", "-q")),
        screenshot_enabled=bool(agent_cfg.get("screenshot_enabled", False)),
        ui_automation_enabled=bool(agent_cfg.get("ui_automation", True)),
        research_fn=lambda query: _agent_research(query, cfg),
        browser_client=browser,
        browser_session_authorize=browser_authorize,
        browser_tab_registered=browser_register_tab if project_id and browser_session_id else None,
        browser_tab_closed=browser_record_tab_close if project_id and browser_session_id else None,
        browser_session_recover=browser_recover,
    )
    deps = AgentRunDeps(
        plan=lambda req: agent_planner.plan(req, cfg=cfg),
        ctx=ctx,
        confirm=_confirm_dialog,
        max_steps=int(agent_cfg.get("max_steps", 12)),
        run_timeout_sec=float(agent_cfg.get("run_timeout_sec", 120)),
        summarize=lambda plan, logs: agent_planner.summarize_run(plan, logs, cfg=cfg),
        audit=lambda request, result: agent_audit.write_run_log(request, result, log_dir=log_dir),
        should_stop=lambda: controls.is_cancelled() or controls.is_shutdown(),
    )

    def _run(request, on_event):
        controls.clear_cancel()              # eski iptal yeni koşuyu öldürmesin
        try:
            clean_request, skill = select_skill(request)
        except SkillError as exc:
            return agent_executor.AgentRunResult(status="failed", spoken=str(exc))
        run_deps = dataclasses.replace(
            deps,
            skill=skill,
            plan=lambda req: agent_planner.plan(req, cfg=cfg, skill=skill),
        )
        # on_event pipeline'dan gelir → executor her adımda THINKING+mesaj yayınlar (menübar canlı).
        return agent_executor.run_agent(clean_request, deps=run_deps, on_event=on_event)

    return _run


def _record_cancellable(cfg: dict, controls):
    """Kayıt İPTAL-EDİLEBİLİR: her kayıttan ÖNCE eski iptal temizlenir (yoksa önceki
    'Durdur' yeni kaydı anında öldürür), kayıt sırasında 'Durdur' gelirse kayıt kesilir
    → boş transkript → pipeline sessiz no-op → standby. "Dinliyor…"da takılma olmaz."""
    controls.clear_cancel()
    return record_until_silence(
        cfg,
        should_stop=lambda: controls.is_cancelled() or controls.is_shutdown(),
    )


def _lazy_listener(cfg: dict) -> "Callable[[], object]":
    """Listener'ı TEMBEL kuran fabrika döndür (backend: oww veya whisper).

    Wake kelimesi açılana dek çağrılmaz → push-to-talk modunda
    model yüklenmez ve sürekli mikrofon hiç açılmaz."""
    holder: dict[str, object] = {}

    def provider() -> object:
        if "listener" not in holder:
            holder["listener"] = make_listener(cfg)
        return holder["listener"]

    return provider


def _voice(cfg: dict) -> str:
    # Canlı okunur → menübar'dan ses değişince anında uygulanır.
    return cfg.get("tts", {}).get("voice", _DEFAULT_VOICE)


def _build_pipeline_deps(
    cfg: dict, transcriber, conversation: Conversation, controls: Controls, mem_store: MemoryStore
) -> Deps:
    say = lambda text: speak.say(text, voice=_voice(cfg))  # noqa: E731
    # Mac eylemleri kürütörlü beyaz listeden (arda.actions); sayaç dolunca sesli haber verir.
    action_deps = actions.ActionDeps(say=say)
    # Sürekli sohbet: bitirme ifadeleri (config'le değiştirilebilir, yoksa varsayılan liste).
    stop_phrases = tuple(cfg.get("app", {}).get("stop_phrases") or phrases.STOP_PHRASES)
    # Düşük-güvenli eylemde teyit sorma: açıksa tutucu enjekte → action sessizce inbox'a
    # düşmek yerine "şunu mu demek istedin?" sorar. Kapalıysa None (eski inbox davranışı).
    app = cfg.get("app", {})
    pending = PendingStore() if app.get("confirm_low_confidence", True) else None
    confirm_timeout = float(app.get("confirm_timeout_sec", 60.0))

    def _transcribe(wav):
        """Komut yolu STT'sini ZAMAN-SINIRLI çalıştır: model/STT asılırsa (örn. eksik
        model indirmesi) UI 'Yazıya çeviriyor...'da SONSUZA kadar donmasın → süre aşınca
        pipeline catch-all'a düşüp sesli hata verir. (Dikte yolundaki korumanın komut eşi.)"""
        timeout = float(cfg.get("stt", {}).get("timeout_sec", 30.0))
        box: dict = {}

        def _run():
            try:
                box["value"] = transcriber.transcribe(wav)
            except BaseException as exc:  # noqa: BLE001 — caller'a aynen ilet
                box["error"] = exc

        th = threading.Thread(target=_run, daemon=True, name="cmd-transcribe")
        th.start()
        th.join(timeout)
        if th.is_alive():
            raise TimeoutError(f"STT zaman aşımı ({timeout:g} sn) — model inmemiş olabilir")
        if "error" in box:
            raise box["error"]
        return box.get("value", "")

    return Deps(
        transcribe=_transcribe,
        # Router'a önceki turlar bağlam olarak verilir → takip soruları/atıfları çözülür.
        classify=lambda text: brain.classify(text, cfg=cfg, history=conversation.recent()),
        # Cevap motoru router'dan AYRI: schema'sız, daha yüksek sıcaklık, doğal sohbet.
        # memories: pipeline'ın önceden hesapladığı mem0 recall sonucu (varsayılan boş).
        answer=lambda text, memories=(): brain.answer(
            text, cfg=cfg, history=conversation.recent(), memories=memories),
        # Akan cevap parçaları bellekte toplanır; stream tamamlanınca pipeline tek TTS
        # çağrısı yapar. app.stream_tts=false ise None → doğrudan tam-cevap yolu.
        answer_stream=(
            (lambda text, memories=(): brain.answer_stream(
                text, cfg=cfg, history=conversation.recent(), memories=memories))
            if cfg.get("app", {}).get("stream_tts", True)
            else None
        ),
        write_note=lambda intent, raw, date: notes.write_note(intent, raw, cfg=cfg, date=date),
        append_task=lambda intent: notes.append_task(intent, cfg=cfg),
        save_to_inbox=lambda raw, date: notes.save_to_inbox(raw, cfg=cfg, date=date),
        say=say,
        do_action=lambda name, params: actions.run_action_result(name, params, deps=action_deps),
        remember=conversation.remember,
        # Transkripsiyon bir bitirme ifadesiyse pipeline classify/act çalıştırmaz → oturum biter.
        is_stop_phrase=lambda text: phrases.is_stop_phrase(text, stop_phrases),
        # UI 'Durdur' → stream tamamlanmadan TTS başlatma; playback başladıysa speak.interrupt
        # mevcut sesi keser. Barge-in için ikinci kapı pipeline._halted içinde.
        should_stop=lambda: controls.is_cancelled() or controls.is_shutdown(),
        # Web araştırma (Faz 4): Tavily-or-DDG ara → kaynaklara dayalı sentez → kaynaklı not.
        research=lambda query: research.research(query, cfg)[0],
        synthesize=lambda query, sources: brain.synthesize(query, sources, cfg=cfg),
        write_research_note=lambda query, summary, sources: notes.write_research_note(
            query, summary, sources, cfg=cfg, date=date.today().isoformat()
        ),
        # Çok-adımlı desktop agent (Faz 5): planla → izin/onay → çalıştır → doğrula → sesli özet.
        run_agent=_build_run_agent(cfg, controls),
        # Düşük-güvenli action: yanlış işlem yerine teyit sor ('evet' ile çalışır).
        pending=pending,
        confirm_timeout_sec=confirm_timeout,
        # Ortak kalıcı hafıza (mem0 → Qdrant, Claude Code ile paylaşılan depo). Devre dışıysa
        # (config/health) MemoryStore metotları no-op döner → pipeline hiç etkilenmez.
        recall=mem_store.recall,
        recall_hits=mem_store.recall_hits,
        mem_enqueue=mem_store.enqueue,
        mem_flush=mem_store.flush,
    )


def _build_agent_deps(
    cfg: dict, pipeline_deps: Deps, listener_provider, controls: Controls
) -> AgentDeps:
    return AgentDeps(
        # Push-to-talk (wake kapalı): mic açmadan manuel tetik bekle.
        # Wake açıksa: listener tembel kurulur, wake kelimesi dinlenir.
        wait_for_wake=lambda: wait_for_trigger(controls, listener_provider, stop=controls.is_shutdown),
        record=lambda: _record_cancellable(cfg, controls),
        process=lambda wav, on_event: process_once(wav, deps=pipeline_deps, on_event=on_event),
        say=lambda text: speak.say(text, voice=_voice(cfg)),
        cleanup=lambda wav: wav.unlink(missing_ok=True),
        # Sürekli sohbet: cevaptan sonra takip dinlemesi (konuşma gelmezse None → standby).
        follow_up=lambda: record_follow_up(cfg),
        # Barge-in: TTS konuşurken araya girme desteği (config'den kapatılabilir).
        barge_in=lambda should_stop=lambda: False: record_barge_in(cfg, should_stop=should_stop) if cfg.get("app", {}).get("barge_in", True) else None,
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.diagnostic_command:
        return _run_diagnostic(args)

    _load_desktop_runtime()

    # Her şeyden ÖNCE GUI açılış ortamını onar: uygulama olarak açıldığında LANG yok
    # (open() ascii → parakeet config.json çöker) ve PATH'te Homebrew yok (ffmpeg bulunamaz).
    ensure_app_environment()

    try:
        cfg = load_config()
    except ConfigError as exc:
        sys.exit(f"Hata: {exc}")

    # Loglama İLK KURULUR: bundan sonraki her modül log'u dosyaya + konsola akar.
    # Kurulmazsa runtime hataları (STT/beyin/eylem) sessizce yutulur → hata ayıklanamaz.
    log_file = setup_logging(cfg)
    print(f"Loglar: {log_file}")
    log.info("Başlangıç sistem durumu:\n%s\n\n%s", permission_report(), runtime_report())

    # Application startup is the canonical Core bootstrap path. Diagnostics
    # stay read-only; startup creates the parent directory and applies all
    # versioned migrations before workers or memory integrations begin.
    core_db = bootstrap_runtime_database()
    atexit.register(core_db.close)
    log.info("Arda Core runtime DB hazır: %s", core_db.path)

    # Ortak kalıcı hafıza (mem0 → Qdrant, Claude Code ile paylaşılan depo). Boot healthcheck
    # hafif bir erişim dener; erişilemezse ya da config.memory.enabled=false ise MemoryStore
    # kalıcı olarak no-op'a düşer → pipeline hiçbir zaman hafıza yüzünden kırılmaz.
    mem_store = MemoryStore(cfg.get("memory", {}))
    if mem_store.enabled and mem_store.health():
        log.info("Ortak hafıza aktif (mem0 → %s)", memory.DEFAULT_COLLECTION)
    else:
        log.info("Ortak hafıza devre dışı (mem0/Qdrant erişilemedi ya da kapalı)")
    # Kapanış: uygulama nasıl biterse bitsin (Ctrl+C, menübar Çık, sinyal) kuyruktaki
    # kalıcı turları en son bir kez yazmayı dene.
    atexit.register(mem_store.flush)

    app = cfg.get("app", {})
    mode = _effective_mode(cfg)
    transcriber = make_transcriber(cfg)
    if app.get("warm_on_start", True):
        # Açılışta Ollama kapalıysa BAŞLAT (kullanıcı elle açmayı unutmasın) → sonra ısıt.
        # Arka planda: menübar ikonu Ollama başlarken beklemesin. ensure_ollama zaten açıksa
        # hızlı döner; kapalıysa Ollama.app'i açıp hazır olana dek bekler, ardından warm eder.
        def _ensure_and_warm() -> None:
            ensure_ollama(cfg)
            brain.warm(cfg)

        threading.Thread(target=_ensure_and_warm, daemon=True).start()
        if mode == "menubar":
            # Menü çubuğu ikonu hemen çıksın; STT modeli arka planda ısınsın.
            threading.Thread(target=transcriber.warm, daemon=True).start()
        else:
            print("Model ısıtılıyor (ilk açılış biraz sürebilir)...")
            transcriber.warm()
    else:
        # Isıtma kapalı olsa da Ollama'yı yine de garantiye al (beyin cevapsız kalmasın).
        threading.Thread(target=lambda: ensure_ollama(cfg), daemon=True).start()

    # Kısa süreli sohbet belleği: son N tur, idle timeout sonrası temizlenir.
    # on_expire=mem_store.flush: idle nedeniyle bağlam temizlenirken kuyruktaki kalıcı
    # turlar da o anda boşaltılır (idle flush tetiği) — kapanışı beklemek zorunda kalmaz.
    conversation = Conversation(
        max_turns=app.get("conversation_max_turns", 8),
        ttl_sec=app.get("conversation_idle_sec", 300.0),
        on_expire=mem_store.flush,
    )
    # Wake varsayılan KAPALI → push-to-talk. Listener yalnızca wake açılırsa kurulur.
    # controls pipeline'dan ÖNCE kurulur → agent koşusu iptal sinyalini (should_stop) alır.
    controls = Controls(wake_enabled=app.get("wake_enabled", False))
    _install_daemon_trigger(controls, mode)
    pipeline_deps = _build_pipeline_deps(cfg, transcriber, conversation, controls, mem_store)
    agent_deps = _build_agent_deps(cfg, pipeline_deps, _lazy_listener(cfg), controls)
    ack = app.get("wake_ack", WAKE_ACK)

    if mode == "menubar":
        from arda import menubar

        # Dikte amblemi (Wispr Flow tarzı): etkinse çok-dilli whisper transcriber'ı AYRI kur.
        # Yalnız menubar modunda (daemon'da AppKit yok). Best-effort ısıtma → ilk dikte hızlı.
        dictation_transcriber = None
        if cfg.get("dictation", {}).get("enabled", False):
            dictation_transcriber = dictation.make_dictation_transcriber(cfg)
            threading.Thread(target=dictation_transcriber.warm, daemon=True).start()

        print(f"{ASSISTANT_NAME} menü çubuğunda çalışıyor. Konuşmak için ⌘⇧A (ya da 'Dinle').")
        menubar.run(agent_deps, ack=ack, controls=controls, cfg=cfg,
                    dictation_transcriber=dictation_transcriber, mem_store=mem_store)
    else:
        print(f"{ASSISTANT_NAME} hazır (daemon). Konuşmak için ⌘⇧A / 'Dinle'. Çıkış: Ctrl+C.")
        try:
            run_forever(agent_deps, ack=ack, stop=controls.is_shutdown)
        except (KeyboardInterrupt, EOFError):
            print("\nGüle güle.")
        finally:
            speak.interrupt()
            mem_store.flush()


if __name__ == "__main__":
    main()
