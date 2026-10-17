"""Small, bounded HTTP client for the existing Camofox browser service."""
from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from arda.browser_sessions import host_in_scope


class BrowserError(RuntimeError):
    """A bounded browser-service or validation failure."""


def _scoped_destination(url: str, allowed_domains: tuple[str, ...] | list[str], *, resolver=socket.getaddrinfo) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.hostname or not host_in_scope(parsed.hostname, allowed_domains):
        raise BrowserError("Hedef, bu proje için yetkilendirilmiş browser domain kapsamı dışında")
    return _public_destination(url, resolver=resolver)


def _public_destination(url: str, *, resolver=socket.getaddrinfo) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserError("Yalnızca http/https ve geçerli bir URL kullanılabilir")
    if parsed.username or parsed.password:
        raise BrowserError("URL içinde kullanıcı adı/parola kullanılamaz")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise BrowserError("Yerel/internal hedefe erişim engellendi")
    try:
        addresses = {item[4][0] for item in resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise BrowserError(f"Hedef çözümlenemedi: {host}") from exc
    if not addresses:
        raise BrowserError(f"Hedef adres bulunamadı: {host}")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise BrowserError("Hedef adresi geçersiz") from exc
        if any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_reserved, ip.is_multicast, ip.is_unspecified)):
            raise BrowserError("Private/internal hedefe erişim engellendi")
    return url


@dataclass
class CamofoxClient:
    base_url: str = "http://localhost:9377"
    api_key: str = ""
    timeout: float = 15.0
    request_fn: object | None = None
    egress_proxy_host: str = ""
    egress_proxy_port: int = 3129
    require_egress_proxy: bool = False
    required_camofox_version: str = "2.4.8"

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Camofox base URL geçersiz")
        if self.timeout <= 0 or self.timeout > 60:
            raise ValueError("Camofox timeout 0 ile 60 saniye arasında olmalı")
        if not 1 <= self.egress_proxy_port <= 65535:
            raise ValueError("Browser egress proxy port geçersiz")
        self.egress_proxy_host = self.egress_proxy_host.strip()
        self.required_camofox_version = self.required_camofox_version.strip()
        if self.require_egress_proxy and not self.egress_proxy_host:
            # Fail at construction so a required boundary cannot degrade to direct egress.
            raise ValueError("Browser grant egress proxy gerekli ama host tanımlı değil")
        if self.require_egress_proxy and not self.required_camofox_version:
            raise ValueError("Browser grant egress için sabit Camofox sürümü gerekli")

    def _request(self, method: str, path: str, *, query: dict | None = None, body: dict | None = None,
                 allow_http_errors: tuple[int, ...] = ()):
        url = self.base_url.rstrip("/") + path
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        opener = self.request_fn or urlopen
        try:
            response = opener(request, timeout=self.timeout)
            raw = response.read()
            return getattr(response, "status", 200), raw, response.headers.get_content_type()
        except HTTPError as exc:
            if exc.code in allow_http_errors:
                content_type = exc.headers.get_content_type() if exc.headers else ""
                exc.read(512)
                return exc.code, b"", content_type
            detail = exc.read(512).decode("utf-8", "replace")
            raise BrowserError(f"Camofox HTTP {exc.code}: {detail[:240]}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BrowserError(f"Camofox servisine erişilemedi: {exc}") from exc

    def _json(self, method: str, path: str, **kwargs) -> dict:
        status, raw, _content_type = self._request(method, path, **kwargs)
        if status < 200 or status >= 300:
            raise BrowserError(f"Camofox beklenmeyen HTTP durumu: {status}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserError("Camofox geçersiz JSON döndürdü") from exc
        if not isinstance(value, dict):
            raise BrowserError("Camofox JSON nesnesi döndürmedi")
        return value

    def health(self) -> dict:
        return self._json("GET", "/health")

    def _assert_egress_proxy_api(self) -> None:
        health = self.health()
        if health.get("version") != self.required_camofox_version:
            raise BrowserError(
                "Camofox egress proxy API sürümü doğrulanamadı; "
                f"beklenen {self.required_camofox_version}, alınan {health.get('version') or 'bilinmiyor'}"
            )

    def create_tab(self, *, user_id: str, session_key: str, url: str | None = None,
                   allowed_domains: tuple[str, ...] | list[str] = (), grant_token: str = "") -> dict:
        body = {"userId": user_id, "sessionKey": session_key}
        if self.require_egress_proxy and not self.egress_proxy_host:
            raise BrowserError("Grant domain ACL için egress proxy yapılandırılmamış")
        if self.egress_proxy_host:
            if not user_id or not grant_token:
                raise BrowserError("Grant egress proxy kimliği eksik")
            if self.require_egress_proxy:
                self._assert_egress_proxy_api()
            body["proxy"] = {
                "host": self.egress_proxy_host,
                "port": self.egress_proxy_port,
                "username": user_id,
                "password": grant_token,
            }
        if url:
            body["url"] = _scoped_destination(url, allowed_domains)
        value = self._json("POST", "/tabs", body=body)
        if not isinstance(value.get("tabId"), str) or not value["tabId"]:
            raise BrowserError("Camofox tabId döndürmedi")
        observed_url = value.get("url")
        if observed_url and observed_url != "about:blank":
            _scoped_destination(observed_url, allowed_domains)
        return value

    def navigate(self, *, user_id: str, session_key: str, tab_id: str, url: str,
                 allowed_domains: tuple[str, ...] | list[str] = ()) -> dict:
        value = self._json(
            "POST", f"/tabs/{quote(tab_id, safe='')}/navigate",
            body={"userId": user_id, "sessionKey": session_key,
                  "url": _scoped_destination(url, allowed_domains)},
        )
        if value.get("ok") is False:
            raise BrowserError(str(value.get("error") or "Camofox navigation başarısız"))
        observed_url = value.get("url")
        if observed_url:
            _scoped_destination(observed_url, allowed_domains)
        return value

    def snapshot(self, *, user_id: str, tab_id: str,
                 allowed_domains: tuple[str, ...] | list[str] = ()) -> dict:
        value = self._json(
            "GET", f"/tabs/{quote(tab_id, safe='')}/snapshot",
            query={"userId": user_id, "format": "text"},
        )
        if not isinstance(value.get("snapshot"), str) or not isinstance(value.get("url"), str):
            raise BrowserError("Camofox snapshot şeması geçersiz")
        _scoped_destination(value["url"], allowed_domains)
        return value

    def screenshot(self, *, user_id: str, tab_id: str) -> bytes:
        status, raw, content_type = self._request(
            "GET", f"/tabs/{quote(tab_id, safe='')}/screenshot", query={"userId": user_id}
        )
        if status < 200 or status >= 300 or content_type != "image/png":
            raise BrowserError("Camofox geçerli PNG screenshot döndürmedi")
        if not raw:
            raise BrowserError("Camofox boş screenshot döndürdü")
        return raw

    def close_tab(self, *, user_id: str, tab_id: str) -> None:
        status, _raw, _content_type = self._request(
            "DELETE", f"/tabs/{quote(tab_id, safe='')}", query={"userId": user_id},
            allow_http_errors=(404,),
        )
        if status != 404 and not 200 <= status < 300:
            raise BrowserError(f"Camofox tab close failed with HTTP {status}")

    def clear_session_storage(self, *, user_id: str) -> None:
        """Close one grant-owned live session and clear its persisted browser state."""
        status, _raw, _content_type = self._request(
            "DELETE", f"/sessions/{quote(user_id, safe='')}/storage_state"
        )
        if not 200 <= status < 300:
            raise BrowserError(f"Camofox session reset failed with HTTP {status}")


def validate_public_url(url: str) -> str:
    return _public_destination(url)
