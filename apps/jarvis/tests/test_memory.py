import logging
from arda.memory import MemoryStore, DEFAULT_COLLECTION, EMBED_DIMS


class FakeMem:
    """mem0 Memory yerine geçen sahte: add/search/get_all/delete kaydeder."""
    def __init__(self):
        self.added = []
        self.searched = []
        self.deleted = []
        self._all = {"results": []}
        self.raise_on = set()          # {"get_all","search","add"} → o çağrı patlar

    def get_all(self, **kw):
        if "get_all" in self.raise_on:
            raise RuntimeError("qdrant down")
        return self._all

    def search(self, query, **kw):
        if "search" in self.raise_on:
            raise RuntimeError("search boom")
        self.searched.append((query, kw))
        return {"results": []}

    def add(self, messages, **kw):
        if "add" in self.raise_on:
            raise RuntimeError("add boom")
        self.added.append((messages, kw))
        return {"results": [{"event": "ADD", "memory": "x"}]}

    def delete(self, memory_id):
        self.deleted.append(memory_id)


def _sync_runner(fn):        # arka plan yerine hemen çalıştır (deterministik test)
    fn()


def make_store(cfg=None, fake=None):
    fake = fake or FakeMem()
    store = MemoryStore(
        cfg if cfg is not None else {"enabled": True},
        memory_factory=lambda: fake,
        runner=_sync_runner,
    )
    return store, fake


def test_disabled_when_config_flag_false():
    store, fake = make_store({"enabled": False})
    assert store.enabled is False
    assert store.recall("selam") == ()
    store.enqueue("u", "a", scope="personal")
    store.flush()
    assert fake.added == []          # hiçbir yazma denenmez


def test_health_true_when_get_all_succeeds():
    store, fake = make_store({"enabled": True})
    assert store.health() is True


def test_health_false_and_disables_when_backend_unreachable(caplog):
    fake = FakeMem()
    fake.raise_on.add("get_all")
    store, _ = make_store({"enabled": True}, fake=fake)
    with caplog.at_level(logging.WARNING):
        assert store.health() is False
    # health başarısız → sonraki çağrılar no-op
    assert store.recall("x") == ()


def test_recall_merges_scopes_sorted_by_score_and_capped():
    fake = FakeMem()

    def search(query, **kw):
        run_id = kw.get("filters", {}).get("run_id")
        if run_id == "personal":
            return {"results": [{"memory": "ben example-user", "score": 0.9}]}
        if run_id == "jarvis":
            return {"results": [
                {"memory": "proje X aktif", "score": 0.5},
                {"memory": "orta", "score": 0.7},
            ]}
        return {"results": []}

    fake.search = search
    store = MemoryStore(
        {"enabled": True, "recall_scopes": ["personal", "jarvis"], "recall_top_k": 2},
        memory_factory=lambda: fake, runner=_sync_runner,
    )
    out = store.recall("kim")
    assert out == ("ben example-user", "orta")     # skor desc, top_k=2


def test_recall_hits_include_provenance_for_baseline_and_active_project():
    fake = FakeMem()

    def search(query, **kw):
        scope = kw["filters"]["run_id"]
        return {"results": [{
            "id": f"{scope}-1", "memory": f"{scope} kararı", "run_id": scope,
            "agent_id": "claude", "score": 0.8,
        }]}

    fake.search = search
    store, _ = make_store({
        "enabled": True,
        "project_scopes": ["example-project"],
        "recall_top_k": 10,
    }, fake=fake)

    hits = store.recall_hits("karar", active_project="example-project")

    assert {hit.scope for hit in hits} == {"personal", "jarvis", "example-project"}
    project_hit = next(hit for hit in hits if hit.scope == "example-project")
    assert project_hit.memory_id == "example-project-1"
    assert project_hit.agent == "claude"
    assert "scope=example-project" in project_hit.prompt_text()


def test_unknown_active_project_fails_closed_to_baseline_scopes():
    fake = FakeMem()
    fake.search = lambda query, **kw: {"results": [{
        "memory": kw["filters"]["run_id"], "run_id": kw["filters"]["run_id"], "score": 1.0,
    }]}
    store, _ = make_store({
        "enabled": True,
        "project_scopes": ["example-project"],
    }, fake=fake)

    hits = store.recall_hits("karar", active_project="secret-project")

    assert {hit.scope for hit in hits} == {"personal", "jarvis"}


def test_shared_scope_requires_explicit_allowlist_and_flag():
    fake = FakeMem()
    fake.search = lambda query, **kw: {"results": [{
        "memory": kw["filters"]["run_id"], "run_id": kw["filters"]["run_id"], "score": 1.0,
    }]}
    store, _ = make_store({
        "enabled": True,
        "project_scopes": ["example-project", "shared"],
        "shared_scope": "shared",
    }, fake=fake)

    without_flag = store.recall_hits("karar", active_project="example-project")
    with_flag = store.recall_hits("karar", active_project="example-project", include_shared=True)

    assert "shared" not in {hit.scope for hit in without_flag}
    assert "shared" in {hit.scope for hit in with_flag}


def test_recall_returns_empty_on_search_error():
    fake = FakeMem()
    fake.raise_on.add("search")
    store, _ = make_store({"enabled": True, "recall_scopes": ["personal"]}, fake=fake)
    assert store.recall("x") == ()


def test_recall_empty_when_disabled():
    store, _ = make_store({"enabled": False})
    assert store.recall("x") == ()


def test_enqueue_then_flush_writes_one_add_per_scope_with_messages():
    store, fake = make_store(
        {"enabled": True}, fake=FakeMem())
    store.enqueue("Adım Example User", "Tamam Example User", scope="personal")
    store.enqueue("Proje X'i başlat", "Başlattım", scope="jarvis",
                  action_result={"action": "run", "success": True})
    store.enqueue("Kız kardeşim Sample User", "Not aldım", scope="personal")
    assert fake.added == []          # flush'a kadar yazma YOK
    store.flush()

    by_scope = {kw["run_id"]: (msgs, kw) for msgs, kw in fake.added}
    assert set(by_scope) == {"personal", "jarvis"}
    for _msgs, kw in fake.added:
        assert kw["user_id"] == "default"
        assert kw["agent_id"] == "jarvis"
    p_msgs, _ = by_scope["personal"]
    # 2 tur × (user+assistant) = 4 mesaj, doğru sırada
    assert [m["role"] for m in p_msgs] == ["user", "assistant", "user", "assistant"]
    assert p_msgs[0] == {"role": "user", "content": "Adım Example User"}
    j_msgs, _ = by_scope["jarvis"]
    assert "[eylem:" in j_msgs[1]["content"]        # action_result iliştirildi


def test_flush_empty_queue_is_noop():
    store, fake = make_store({"enabled": True})
    store.flush()
    assert fake.added == []


def test_flush_clears_queue_even_if_add_raises(caplog):
    fake = FakeMem()
    fake.raise_on.add("add")
    store, _ = make_store({"enabled": True}, fake=fake)
    store.enqueue("u", "a", scope="personal")
    with caplog.at_level(logging.WARNING):
        store.flush()          # patlar ama pipeline'a sızmaz
    store.flush()              # kuyruk temiz → ikinci flush no-op
    assert fake.added == []    # add hep patladı


def test_enqueue_noop_when_disabled():
    store, fake = make_store({"enabled": False})
    store.enqueue("u", "a", scope="personal")
    store.flush()
    assert fake.added == []


def test_recall_returns_within_timeout_when_a_scope_hangs():
    import time as _time
    fake = FakeMem()

    def search(query, **kw):
        run_id = kw.get("filters", {}).get("run_id")
        if run_id == "slow":
            _time.sleep(2.0)
            return {"results": [{"memory": "geç", "score": 1.0}]}
        return {"results": [{"memory": "hızlı", "score": 0.5}]}

    fake.search = search
    store = MemoryStore(
        {"enabled": True, "recall_scopes": ["fast", "slow"],
         "recall_top_k": 5, "recall_timeout_sec": 0.2},
        memory_factory=lambda: fake, runner=_sync_runner,
    )
    t0 = _time.monotonic()
    out = store.recall("x")
    elapsed = _time.monotonic() - t0
    assert elapsed < 1.0            # takılan scope recall'ı bloklamadı
    assert "hızlı" in out           # zamanında dönen scope sonucu geldi
    assert "geç" not in out         # geç dönen scope terk edildi


def test_list_all_and_delete_via_store():
    fake = FakeMem()
    fake._all = {"results": [
        {"id": "m1", "memory": "ben example-user", "run_id": "personal", "agent_id": "jarvis"},
    ]}
    store, _ = make_store({"enabled": True}, fake=fake)
    rows = store.list_all(scope="personal")
    assert rows[0]["id"] == "m1"
    store.delete("m1")
    assert fake.deleted == ["m1"]


def test_cli_rm_dry_run_does_not_delete(monkeypatch, capsys):
    from arda import memory as m
    fake = FakeMem()
    monkeypatch.setattr(m.MemoryStore, "_default_factory", lambda self: fake)
    # config'i enabled kur (CLI load_config'i çağırır → monkeypatch veya env)
    monkeypatch.setattr(m, "_load_memory_cfg", lambda: {"enabled": True})
    rc = m.main(["rm", "m1", "--dry-run"])
    assert rc == 0
    assert fake.deleted == []           # dry-run → silme yok


def test_maintenance_methods_safe_when_disabled():
    store, fake = make_store({"enabled": False})
    assert store.list_all() == []
    assert store.search_raw("x") == []
    store.delete("m1")            # disabled → no-op, no crash
    assert fake.deleted == []


def test_list_all_returns_empty_on_backend_error():
    fake = FakeMem()
    fake.raise_on.add("get_all")
    store, _ = make_store({"enabled": True}, fake=fake)
    assert store.list_all() == []       # canlı çağrı hatası yutuldu


def test_cli_list_returns_1_when_disabled(monkeypatch):
    from arda import memory as m
    monkeypatch.setattr(m, "_load_memory_cfg", lambda: {"enabled": False})
    assert m.main(["list"]) == 1
