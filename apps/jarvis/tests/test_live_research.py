"""M2.3B safe live acquisition tests; all network tests use local fixtures/mocks."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import arda.live_research as live
from arda.live_research import (
    ForbiddenDestinationError,
    LiveResearchError,
    LiveSourceAcquirer,
    NonSuccessResponseError,
    ProviderSearchAdapter,
    RedirectPolicyError,
    ResponseTooLargeError,
    SafeHTTPFetcher,
    SearXNGSearchAdapter,
    TrafilaturaExtractor,
    UnsupportedContentTypeError,
    SourceNetworkError,
    redact_url,
)
from arda.research import Source
from arda.research_worker import FixtureSourceAdapter, ResearchTask, build_research


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path.startswith("/ok"):
            body = b"<html><title>Fixture</title><script>ignore()</script><p>Durable evidence.</p></html>"
            content_type = "text/html; charset=utf-8"
            status = 200
        elif self.path.startswith("/same"):
            body = b"same content"
            content_type = "text/plain"
            status = 200
        elif self.path.startswith("/large"):
            body = b"x" * 2048
            content_type = "text/plain"
            status = 200
        elif self.path.startswith("/json"):
            body = b'{"answer":"fixture"}'
            content_type = "application/json"
            status = 200
        elif self.path.startswith("/search"):
            body = json.dumps({"results": [{"title": "Fixture result", "url": "/ok", "content": "snippet", "engines": ["fixture"]}]}).encode()
            content_type = "application/json"
            status = 200
        elif self.path.startswith("/binary"):
            body = b"binary"
            content_type = "application/octet-stream"
            status = 200
        elif self.path.startswith("/missing"):
            body = b"missing"
            content_type = "text/plain"
            status = 404
        elif self.path.startswith("/loop"):
            body = b""
            content_type = "text/plain"
            status = 302
            self.send_response(status)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        else:
            body = b""
            content_type = "text/plain"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.fixture
def fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def local_validation(monkeypatch):
    monkeypatch.setattr(live, "_validate_destination", lambda url, allow_http: ("127.0.0.1", 80))


def test_safe_fetch_normalizes_html_and_preserves_hash(fixture_server, local_validation):
    fetched = SafeHTTPFetcher(allow_http=True).fetch(f"{fixture_server}/ok")
    assert fetched.status == 200
    assert fetched.content_type == "text/html"
    assert fetched.content == "Fixture Durable evidence."
    assert fetched.content_sha256 == hashlib.sha256(
        b"<html><title>Fixture</title><script>ignore()</script><p>Durable evidence.</p></html>"
    ).hexdigest()
    assert fetched.final_url.startswith(fixture_server)


def test_searxng_adapter_calls_real_json_http_endpoint(fixture_server):
    candidates = SearXNGSearchAdapter(fixture_server).search("durable", max_results=1)
    assert candidates[0].provider == "searxng"
    assert candidates[0].title == "Fixture result"
    assert candidates[0].snippet == "snippet"


def test_optional_trafilatura_parser_is_after_fetch_policy(monkeypatch):
    class FakeTrafilatura:
        @staticmethod
        def extract(text, **_kwargs):
            assert "<script>" in text
            return "extracted article"

    monkeypatch.setitem(__import__("sys").modules, "trafilatura", FakeTrafilatura)
    extractor = TrafilaturaExtractor()
    assert extractor.extract(b"<script>x</script><p>article</p>", url="https://example.com", content_type="text/html") == "extracted article"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1", "fc00::1"])
def test_destination_policy_rejects_private_loopback_and_link_local(monkeypatch, address):
    def fake_getaddrinfo(*_args, **_kwargs):
        family = 10 if ":" in address else 2
        return [(family, 1, 6, "", (address, 443, 0, 0) if family == 10 else (address, 443))]

    monkeypatch.setattr(live.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ForbiddenDestinationError):
        live._validate_destination("https://fixture.invalid", allow_http=False)


def test_unsupported_scheme_and_http_policy_are_rejected():
    with pytest.raises(ForbiddenDestinationError):
        live._validate_destination("file:///etc/passwd", allow_http=True)
    with pytest.raises(ForbiddenDestinationError):
        live._validate_destination("http://example.com", allow_http=False)


def test_fetch_limits_content_type_status_and_redirects(fixture_server, local_validation):
    with pytest.raises(UnsupportedContentTypeError):
        SafeHTTPFetcher(allow_http=True).fetch(f"{fixture_server}/binary")
    with pytest.raises(NonSuccessResponseError):
        SafeHTTPFetcher(allow_http=True).fetch(f"{fixture_server}/missing")
    with pytest.raises(ResponseTooLargeError):
        SafeHTTPFetcher(allow_http=True, max_response_bytes=32).fetch(f"{fixture_server}/large")
    with pytest.raises(RedirectPolicyError):
        SafeHTTPFetcher(allow_http=True, max_redirects=1).fetch(f"{fixture_server}/loop")


def test_redirect_destination_is_validated_again(monkeypatch, fixture_server):
    calls = []

    def validate(url, *, allow_http):
        calls.append(url)
        if "blocked.invalid" in url:
            raise ForbiddenDestinationError("redirect destination forbidden")
        return ("127.0.0.1", 80)

    monkeypatch.setattr(live, "_validate_destination", validate)
    # The URL is intercepted at the fetch boundary; no real DNS or internet is used.
    class RedirectResponse:
        def getcode(self): return 302
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    class RedirectOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/start"):
                raise live.urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "http://blocked.invalid/secret"}, None)
            return RedirectResponse()

    fetcher = SafeHTTPFetcher(allow_http=True)
    fetcher._opener = RedirectOpener()
    with pytest.raises(ForbiddenDestinationError):
        fetcher.fetch(f"{fixture_server}/start")
    assert len(calls) == 2


def test_search_to_fetch_dedupes_urls_and_content_and_preserves_provenance(fixture_server, local_validation):
    class Provider:
        name = "fixture-search"

        def search(self, query, *, max_results):
            return [
                Source("one", f"{fixture_server}/ok#fragment", "snippet", self.name),
                Source("duplicate-url", f"{fixture_server}/ok", "snippet", self.name),
                Source("same-content", f"{fixture_server}/same", "snippet", self.name),
            ][:max_results]

    result = LiveSourceAcquirer(SafeHTTPFetcher(allow_http=True)).acquire(
        "durable evidence", ProviderSearchAdapter(Provider()), max_sources=5
    )
    assert len(result.sources) == 2
    assert result.failures == ()
    assert result.sources[0].provenance["search"]["provider"] == "fixture-search"
    assert result.sources[0].provenance["http_status"] == 200


def test_live_source_acquirer_stops_when_lease_cancelled():
    import threading

    cancelled = threading.Event()
    cancelled.set()

    class Search:
        def search(self, *_args, **_kwargs):
            raise AssertionError("cancelled worker must not start search")

    with pytest.raises(InterruptedError, match="lease lost"):
        LiveSourceAcquirer(SafeHTTPFetcher(allow_http=True)).acquire(
            "query", Search(), cancel_event=cancelled
        )


def test_live_source_feeds_m23a_evidence_with_search_provenance(fixture_server, local_validation):
    class Provider:
        name = "fixture-search"

        def search(self, query, *, max_results):
            return [Source("one", f"{fixture_server}/ok", "snippet", self.name)]

    acquired = LiveSourceAcquirer(SafeHTTPFetcher(allow_http=True)).acquire(
        "durable evidence", ProviderSearchAdapter(Provider()), max_sources=1
    )
    source = acquired.sources[0]
    task = ResearchTask.from_objective(json.dumps({
        "research_questions": ["What is supported?"],
        "source_refs": [source.uri],
        "source_constraints": {"allowed_source_types": ["live_http"]},
        "max_sources": 1,
        "time_budget_seconds": 30,
        "required_output_format": ["research.json", "research.md"],
        "expected_claims": [{"claim_id": "durable", "text": "Durable evidence."}],
    }))
    result = build_research(task, FixtureSourceAdapter({source.uri: source}))
    evidence = result["evidence"][0]
    assert evidence["provenance"]["source_provenance"]["search"]["provider"] == "fixture-search"

def test_partial_provider_failure_is_explicit(fixture_server, local_validation):
    class Provider:
        name = "fixture-search"

        def search(self, query, *, max_results):
            return [Source("ok", f"{fixture_server}/ok", "snippet", self.name),
                    Source("bad", f"{fixture_server}/missing", "snippet", self.name)]

    result = LiveSourceAcquirer(SafeHTTPFetcher(allow_http=True)).acquire(
        "query", ProviderSearchAdapter(Provider()), max_sources=2
    )
    assert len(result.sources) == 1
    assert result.failures[0]["error_class"] == "NonSuccessResponseError"


def test_search_provider_failure_is_explicit():
    class BrokenProvider:
        name = "broken"

        def search(self, query, *, max_results):
            raise OSError("provider unavailable")

    with pytest.raises(LiveResearchError, match="search provider unavailable"):
        ProviderSearchAdapter(BrokenProvider()).search("query", max_results=1)


def test_fetch_timeout_is_normalized():
    class TimeoutOpener:
        def open(self, request, timeout):
            raise TimeoutError("fixture timeout")

    fetcher = SafeHTTPFetcher(allow_http=True)
    fetcher._opener = TimeoutOpener()
    with pytest.raises(SourceNetworkError):
        fetcher.fetch("http://fixture.test/timeout")


def test_secret_bearing_url_is_redacted_from_provenance():
    redacted = redact_url("https://example.com/path?token=secret-value&x=1")
    assert "secret-value" not in redacted
    assert "token=%3Credacted%3E" in redacted
