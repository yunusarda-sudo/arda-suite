"""Jarvis ↔ Claude Code ortak yerel mem0 hafızası (import-güvenli sarmalayıcı).

Qdrant collection (`mem0_local`) + default `user_id="default"` + `nomic-embed-text`
embedder ile bağlanır → Claude Code (MCP) ile TEK ortak depo. mem0/Ollama/Qdrant
erişilemezse tüm çağrılar sessizce no-op olur; pipeline ASLA kırılmaz.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("arda.memory")

DEFAULT_COLLECTION = "mem0_local"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIMS = 768
DEFAULT_OWNER = "default"
DEFAULT_EXTRACT_MODEL = "qwen3:8b"  # brain ile aynı sıcak model; config.yaml override eder
JARVIS_AGENT = "jarvis"
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

os.environ.setdefault("MEM0_TELEMETRY", "false")


def _spawn(fn: Callable[[], None]) -> None:
    """Varsayılan runner: işi arka plan daemon thread'inde koştur (sesi bloklamaz)."""
    threading.Thread(target=fn, daemon=True).start()


@dataclass(frozen=True)
class MemoryHit:
    """A memory result with enough provenance for prompt attribution and audits."""

    text: str
    memory_id: str = ""
    scope: str = ""
    agent: str = ""
    score: float = 0.0

    def prompt_text(self) -> str:
        """Render retrieved data as data, not as an instruction from the memory."""
        source = f"scope={self.scope or '-'} id={self.memory_id or '-'}"
        return f"[{source}] {self.text}"


@dataclass(frozen=True)
class MemoryAccessPolicy:
    """Allowlisted retrieval scopes; persistence scopes remain caller-owned."""

    baseline_scopes: tuple[str, ...] = ("personal", "jarvis")
    project_scopes: tuple[str, ...] = ()
    shared_scope: str | None = None

    def scopes_for(self, active_project: str | None = None, *, include_shared: bool = False) -> tuple[str, ...]:
        scopes = list(self.baseline_scopes)
        if active_project:
            if active_project in self.project_scopes:
                scopes.append(active_project)
            else:
                log.warning("aktif proje scope'u yetkisiz veya bilinmiyor: %s", active_project)
        if include_shared and self.shared_scope:
            scopes.append(self.shared_scope)
        return tuple(dict.fromkeys(scopes))


def _valid_scope(scope: str) -> bool:
    return isinstance(scope, str) and bool(_SCOPE_RE.fullmatch(scope))


class MemoryStore:
    def __init__(
        self,
        cfg: dict,
        *,
        memory_factory: Callable[[], Any] | None = None,
        runner: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg or {}
        self._enabled = bool(self._cfg.get("enabled", False))
        self._factory = memory_factory or self._default_factory
        self._runner = runner or _spawn
        self._clock = clock
        self._owner = self._cfg.get("owner", DEFAULT_OWNER)
        configured_scopes = tuple(self._cfg.get("recall_scopes", ["personal", "jarvis"]))
        project_scopes = tuple(self._cfg.get("project_scopes", ()))
        configured_shared = self._cfg.get("shared_scope", "")
        shared_scope = configured_shared if _valid_scope(configured_shared) else None
        self._policy = MemoryAccessPolicy(
            baseline_scopes=tuple(s for s in configured_scopes if _valid_scope(s) and s != shared_scope)
            or ("personal", "jarvis"),
            project_scopes=tuple(s for s in project_scopes if _valid_scope(s) and s != shared_scope),
            shared_scope=shared_scope,
        )
        self._active_project = self._cfg.get("active_project")
        if self._active_project and not _valid_scope(self._active_project):
            self._active_project = None
        self._include_shared = bool(self._cfg.get("include_shared", False))
        self._mem: Any | None = None
        self._disabled = not self._enabled
        self._lock = threading.Lock()
        self._queue: list[tuple[str, str, str, dict | None]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._disabled

    def build_config(self) -> dict:
        host = self._cfg.get("qdrant_host", "localhost")
        port = int(self._cfg.get("qdrant_port", 6333))
        base_url = self._cfg.get("ollama_base_url", "http://localhost:11434")
        return {
            "llm": {"provider": "ollama", "config": {
                "model": self._cfg.get("extract_model", DEFAULT_EXTRACT_MODEL),
                "temperature": 0.1, "max_tokens": 2000,
                "ollama_base_url": base_url}},
            "embedder": {"provider": "ollama", "config": {
                "model": EMBED_MODEL, "embedding_dims": EMBED_DIMS,
                "ollama_base_url": base_url}},
            "vector_store": {"provider": "qdrant", "config": {
                "collection_name": self._cfg.get("collection", DEFAULT_COLLECTION),
                "embedding_model_dims": EMBED_DIMS,
                "host": host, "port": port}},
        }

    def _default_factory(self) -> Any:
        from mem0 import Memory       # lazy: import hatası çöküşe DÜŞMESİN
        return Memory.from_config(self.build_config())

    def _get(self) -> Any | None:
        """Lazy Memory; kurulum/erişim hatası → kalıcı disable + None."""
        if self._disabled:
            return None
        with self._lock:
            if self._disabled:            # başka thread bu arada devre dışı bırakmış olabilir
                return None
            if self._mem is None:
                try:
                    self._mem = self._factory()
                except Exception as exc:  # noqa: BLE001
                    log.warning("mem0 başlatılamadı, hafıza devre dışı: %s", exc)
                    self._disabled = True
                    return None
            return self._mem

    def health(self) -> bool:
        """Boot denetimi: depoya hafif bir erişim (get_all) çalışıyor mu.
        Başarısızsa hafızayı kalıcı devre dışı bırakır (sessiz bölünme yok)."""
        mem = self._get()
        if mem is None:
            return False
        try:
            mem.get_all(filters={"user_id": self._owner}, limit=1)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("mem0 healthcheck başarısız, hafıza devre dışı: %s", exc)
            self._disabled = True
            return False

    def recall_hits(
        self,
        query: str,
        *,
        active_project: str | None = None,
        include_shared: bool | None = None,
    ) -> tuple[MemoryHit, ...]:
        if not self.enabled:
            return ()
        if not (query or "").strip():
            return ()
        mem = self._get()
        if mem is None:
            return ()
        active = self._active_project if active_project is None else active_project
        if active is not None and not _valid_scope(active):
            log.warning("geçersiz aktif proje scope'u reddedildi: %s", active)
            active = None
        scopes = self._policy.scopes_for(
            active,
            include_shared=self._include_shared if include_shared is None else include_shared,
        )
        top_k = int(self._cfg.get("recall_top_k", 4))
        timeout = float(self._cfg.get("recall_timeout_sec", 1.5))

        def _one(scope: str) -> list[dict]:
            res = mem.search(
                query,
                filters={"user_id": self._owner, "run_id": scope},
                top_k=top_k,
            )
            return res.get("results", []) if isinstance(res, dict) else []

        hits: list[dict] = []
        ex = ThreadPoolExecutor(max_workers=max(1, len(scopes)))
        try:
            futs = [ex.submit(_one, s) for s in scopes]
            done, not_done = wait(futs, timeout=timeout)
            for fut in done:
                try:
                    hits.extend(fut.result())
                except Exception as exc:  # noqa: BLE001 — bir scope hatası diğerini düşürmesin
                    log.warning("recall scope hatası (atlandı): %s", exc)
            if not_done:
                log.warning("recall zaman aşımı: %d/%d scope zamanında dönmedi",
                            len(not_done), len(futs))
        except Exception as exc:  # noqa: BLE001 — beklenmedik hata → boş dön
            log.warning("recall hata (boş dönüldü): %s", exc)
        finally:
            ex.shutdown(wait=False)     # KRİTİK: takılan future'ı BEKLEME → recall bloklamaz

        hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
        out: list[MemoryHit] = []
        for h in hits[:top_k]:
            text = (h.get("memory") or "").strip()
            if text:
                out.append(MemoryHit(
                    text=text,
                    memory_id=str(h.get("id") or ""),
                    scope=str(h.get("run_id") or ""),
                    agent=str(h.get("agent_id") or ""),
                    score=float(h.get("score") or 0.0),
                ))
        return tuple(out)

    def recall(
        self,
        query: str,
        *,
        active_project: str | None = None,
        include_shared: bool | None = None,
    ) -> tuple[str, ...]:
        """Backward-compatible text-only recall facade."""
        return tuple(hit.text for hit in self.recall_hits(
            query, active_project=active_project, include_shared=include_shared
        ))

    def enqueue(self, user: str, assistant: str, *, scope: str,
                action_result: dict | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._queue.append((user, assistant, scope, action_result))

    def _turn_messages(self, user: str, assistant: str,
                       action_result: dict | None) -> list[dict]:
        content = assistant
        if action_result:
            act = action_result.get("action", "eylem")
            content = f"{content}\n[eylem: {act}]"
        return [{"role": "user", "content": user},
                {"role": "assistant", "content": content}]

    def flush(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            pending = self._queue
            self._queue = []
        if not pending:
            return
        mem = self._get()
        if mem is None:
            return
        grouped: dict[str, list[dict]] = {}
        for user, assistant, scope, ar in pending:
            grouped.setdefault(scope, []).extend(
                self._turn_messages(user, assistant, ar))

        def _write():
            for scope, messages in grouped.items():
                try:
                    mem.add(messages, user_id=self._owner,
                            run_id=scope, agent_id=JARVIS_AGENT)
                except Exception as exc:  # noqa: BLE001
                    log.warning("mem0 flush yazımı başarısız (scope=%s): %s", scope, exc)

        self._runner(_write)

    def list_all(self, scope=None, agent=None):
        mem = self._get()
        if mem is None:
            return []
        f = {"user_id": self._owner}
        if scope:
            f["run_id"] = scope
        if agent:
            f["agent_id"] = agent
        try:
            res = mem.get_all(filters=f)
        except Exception as exc:  # noqa: BLE001 — bakım okuması pipeline'ı bozmasın
            log.warning("mem0 list_all başarısız: %s", exc)
            return []
        return res.get("results", []) if isinstance(res, dict) else []

    def search_raw(self, query, scope=None):
        mem = self._get()
        if mem is None:
            return []
        f = {"user_id": self._owner}
        if scope:
            f["run_id"] = scope
        try:
            res = mem.search(query, filters=f, top_k=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("mem0 search_raw başarısız: %s", exc)
            return []
        return res.get("results", []) if isinstance(res, dict) else []

    def delete(self, memory_id):
        mem = self._get()
        if mem is None:
            return
        try:
            mem.delete(memory_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("mem0 delete başarısız: %s", exc)


def _load_memory_cfg() -> dict:
    from arda.config import load_config
    return load_config().get("memory", {})


def _fmt(item: dict) -> str:
    ts = (item.get("created_at") or "")[:16].replace("T", " ")
    return f"[{ts}] ({item.get('run_id','-')}/{item.get('agent_id','-')}) {item.get('id','?')}  {item.get('memory','')}"


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="arda.memory", description="Jarvis ortak hafıza bakımı")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list"); pl.add_argument("--scope"); pl.add_argument("--agent")
    ps = sub.add_parser("search"); ps.add_argument("query"); ps.add_argument("--scope")
    pr = sub.add_parser("rm"); pr.add_argument("memory_id"); pr.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    store = MemoryStore(_load_memory_cfg())
    if not store.enabled:
        print("Hafıza kapalı ya da erişilemiyor."); return 1

    if args.cmd == "list":
        for it in store.list_all(args.scope, args.agent):
            print(_fmt(it))
    elif args.cmd == "search":
        for it in store.search_raw(args.query, args.scope):
            print(_fmt(it))
    elif args.cmd == "rm":
        if args.dry_run:
            print(f"[dry-run] silinecekti: {args.memory_id}")
        else:
            store.delete(args.memory_id); print(f"silindi: {args.memory_id}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
