"""M2.3B bounded live-source acquisition; no browsing loop or LLM synthesis."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from arda.research import SearchProvider, Source
from arda.research_worker import FixtureSource


class LiveResearchError(RuntimeError):
    """Base error for bounded search/fetch acquisition."""


class ForbiddenDestinationError(LiveResearchError):
    """The URL or a redirect resolves to a forbidden network destination."""


class RedirectPolicyError(LiveResearchError):
    """Redirect is missing, excessive, or cannot be validated safely."""


class ResponseTooLargeError(LiveResearchError):
    """The source exceeds the configured response limit."""


class UnsupportedContentTypeError(LiveResearchError):
    """The source media type is outside the intentionally small M2.3B set."""


class NonSuccessResponseError(LiveResearchError):
    """The source returned a non-2xx response."""


class SourceNetworkError(LiveResearchError):
    """DNS, connection, TLS, timeout, or malformed network response."""


@dataclass(frozen=True)
class SearchCandidate:
    provider: str
    query: str
    rank: int
    title: str
    url: str
    snippet: str
    retrieved_at: str
    provider_metadata: dict[str, Any]


@dataclass(frozen=True)
class FetchedSource:
    requested_url: str
    final_url: str
    status: int
    content_type: str
    retrieved_at: str
    content: str
    content_sha256: str


@dataclass(frozen=True)
class AcquisitionResult:
    sources: tuple[FixtureSource, ...]
    failures: tuple[dict[str, str], ...]
    candidates_seen: int


class SearchAdapter(Protocol):
    name: str

    def search(self, query: str, *, max_results: int) -> list[SearchCandidate]:
        ...


class ProviderSearchAdapter:
    """Adapt the existing provider abstraction without making it authoritative evidence."""

    def __init__(self, provider: SearchProvider):
        self.provider = provider
        self.name = provider.name

    def search(self, query: str, *, max_results: int) -> list[SearchCandidate]:
        retrieved_at = _now()
        try:
            sources = self.provider.search(query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 - provider boundary normalizes failures
            raise LiveResearchError(f"search provider unavailable: {self.name}") from exc
        candidates: list[SearchCandidate] = []
        for rank, source in enumerate(sources, start=1):
            candidates.append(SearchCandidate(
                provider=self.name,
                query=query,
                rank=rank,
                title=source.title,
                url=source.url,
                snippet=source.snippet,
                retrieved_at=source.published_date or retrieved_at,
                provider_metadata={"published_date": source.published_date},
            ))
        return candidates


class SearXNGSearchAdapter:
    """HTTP adapter for a configured SearXNG sidecar; snippets remain candidates only."""

    name = "searxng"

    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0,
                 max_response_bytes: int = 128 * 1024):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("SearXNG base_url must be an HTTP(S) URL without credentials")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("invalid SearXNG limits")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def search(self, query: str, *, max_results: int) -> list[SearchCandidate]:
        if not query.strip() or max_results < 1:
            raise ValueError("query and max_results are required")
        endpoint = f"{self.base_url}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
        request = urllib.request.Request(
            endpoint,
            headers={"Accept": "application/json", "User-Agent": "ArdaResearch/2.3B"},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if response.getcode() < 200 or response.getcode() >= 300:
                    raise LiveResearchError("SearXNG returned a non-success response")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > self.max_response_bytes:
                    raise ResponseTooLargeError("SearXNG response exceeds limit")
                body = response.read(self.max_response_bytes + 1)
        except LiveResearchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LiveResearchError("SearXNG request failed") from exc
        if len(body) > self.max_response_bytes:
            raise ResponseTooLargeError("SearXNG response exceeds limit")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LiveResearchError("SearXNG returned malformed JSON") from exc
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise LiveResearchError("SearXNG response has no results list")
        retrieved_at = _now()
        candidates: list[SearchCandidate] = []
        for rank, item in enumerate(raw_results[:max_results], start=1):
            if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].strip():
                continue
            candidates.append(SearchCandidate(
                provider=self.name,
                query=query,
                rank=rank,
                title=str(item.get("title") or item["url"]),
                url=item["url"].strip(),
                snippet=str(item.get("content") or item.get("snippet") or ""),
                retrieved_at=retrieved_at,
                provider_metadata={"engines": item.get("engines", [])},
            ))
        return candidates


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_url(url: str) -> str:
    """Keep URL provenance while removing common credential-bearing query values."""
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = []
    sensitive = {"token", "key", "secret", "signature", "auth", "api_key", "access_token"}
    for key, value in pairs:
        redacted.append((key, "<redacted>" if key.casefold() in sensitive else value))
    query = urllib.parse.urlencode(redacted)
    host = parsed.hostname or ""
    if parsed.username or parsed.password:
        host = "<redacted>"
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


def _forbidden_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any((address.is_loopback, address.is_private, address.is_link_local,
                address.is_reserved, address.is_unspecified, address.is_multicast))


def _validate_destination(url: str, *, allow_http: bool) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"https", "http"} or (parsed.scheme == "http" and not allow_http):
        raise ForbiddenDestinationError("unsupported URL scheme")
    if parsed.username or parsed.password or not parsed.hostname:
        raise ForbiddenDestinationError("credential-bearing or invalid URL")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ForbiddenDestinationError("invalid URL port") from exc
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceNetworkError("DNS resolution failed") from exc
    if not addresses:
        raise SourceNetworkError("DNS returned no addresses")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except (IndexError, ValueError) as exc:
            raise SourceNetworkError("invalid DNS address") from exc
        if _forbidden_ip(ip):
            raise ForbiddenDestinationError("destination resolves to a forbidden address")
    return parsed.hostname, port


def _content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().casefold()


def _normalize_content(content: bytes, content_type: str) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceNetworkError("source is not valid UTF-8") from exc
    if content_type == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class TrafilaturaExtractor:
    """Optional HTML parser; it receives bytes only after SafeHTTPFetcher policy checks."""

    def extract(self, content: bytes, *, url: str, content_type: str) -> str:
        if content_type != "text/html":
            return _normalize_content(content, content_type)
        try:
            import trafilatura
        except ImportError as exc:
            raise LiveResearchError("trafilatura optional dependency is not installed") from exc
        text = trafilatura.extract(
            content.decode("utf-8", errors="replace"),
            url=url,
            include_comments=False,
            include_tables=True,
            output_format="txt",
        )
        if not text:
            raise LiveResearchError("trafilatura extracted no article text")
        return re.sub(r"\s+", " ", text).strip()


class SafeHTTPFetcher:
    """Fetch only bounded text sources after validating every destination."""

    def __init__(self, *, allow_http: bool = False, timeout_seconds: float = 5.0,
                 max_redirects: int = 3, max_response_bytes: int = 128 * 1024,
                 extractor: Any | None = None):
        if timeout_seconds <= 0 or max_redirects < 0 or max_response_bytes <= 0:
            raise ValueError("invalid fetch limits")
        self.allow_http = allow_http
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.extractor = extractor
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.ProxyHandler({})
        )

    def fetch(self, url: str) -> FetchedSource:
        current = url
        requested = redact_url(url)
        for redirect_count in range(self.max_redirects + 1):
            _validate_destination(current, allow_http=self.allow_http)
            request = urllib.request.Request(
                current,
                headers={"Accept": "text/html, text/plain, application/json", "User-Agent": "ArdaResearch/2.3B"},
                method="GET",
            )
            try:
                response = self._opener.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        raise RedirectPolicyError("redirect missing location") from exc
                    if redirect_count >= self.max_redirects:
                        raise RedirectPolicyError("maximum redirects exceeded") from exc
                    current = urllib.parse.urljoin(current, location)
                    continue
                raise NonSuccessResponseError(f"source returned HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise SourceNetworkError("source fetch failed") from exc
            with response:
                status = response.getcode()
                if status < 200 or status >= 300:
                    raise NonSuccessResponseError(f"source returned HTTP {status}")
                content_type = _content_type(response.headers.get("Content-Type"))
                if content_type not in {"text/html", "text/plain", "application/json"}:
                    raise UnsupportedContentTypeError("unsupported source content type")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > self.max_response_bytes:
                    raise ResponseTooLargeError("source response exceeds limit")
                content = response.read(self.max_response_bytes + 1)
                if len(content) > self.max_response_bytes:
                    raise ResponseTooLargeError("source response exceeds limit")
            text = (self.extractor.extract(content, url=current, content_type=content_type)
                    if self.extractor is not None else _normalize_content(content, content_type))
            return FetchedSource(
                requested_url=requested,
                final_url=redact_url(current),
                status=status,
                content_type=content_type,
                retrieved_at=_now(),
                content=text,
                content_sha256=hashlib.sha256(content).hexdigest(),
            )
        raise RedirectPolicyError("maximum redirects exceeded")


class LiveSourceAcquirer:
    """Turn search candidates into normalized M2.3A-compatible sources."""

    def __init__(self, fetcher: SafeHTTPFetcher):
        self.fetcher = fetcher

    def acquire(self, query: str, search: SearchAdapter, *, max_sources: int = 5,
                cancel_event=None) -> AcquisitionResult:
        if max_sources < 1:
            raise ValueError("max_sources must be positive")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("worker lease lost before search")
        candidates = search.search(query, max_results=max_sources)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("worker lease lost after search")
        seen_urls: set[str] = set()
        seen_content: set[str] = set()
        sources: list[FixtureSource] = []
        failures: list[dict[str, str]] = []
        for candidate in candidates:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("worker lease lost during source acquisition")
            normalized_url = redact_url(candidate.url).rstrip("/").casefold()
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            if len(sources) >= max_sources:
                break
            try:
                fetched = self.fetcher.fetch(candidate.url)
            except LiveResearchError as exc:
                failures.append({"url": redact_url(candidate.url), "error_class": exc.__class__.__name__})
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("worker lease lost after source fetch")
            if fetched.content_sha256 in seen_content:
                continue
            seen_content.add(fetched.content_sha256)
            source_id = "source_" + hashlib.sha256(
                f"{fetched.final_url}:{fetched.content_sha256}".encode("utf-8")
            ).hexdigest()[:16]
            sources.append(FixtureSource(
                source_id=source_id,
                source_type="live_http",
                uri=fetched.final_url,
                retrieved_at=fetched.retrieved_at,
                title=candidate.title,
                content=fetched.content,
                provenance={
                    "search": {"provider": candidate.provider, "query": candidate.query, "rank": candidate.rank},
                    "requested_url": fetched.requested_url,
                    "final_url": fetched.final_url,
                    "http_status": fetched.status,
                    "content_type": fetched.content_type,
                    "content_sha256": fetched.content_sha256,
                },
            ))
        return AcquisitionResult(tuple(sources), tuple(failures), len(candidates))
