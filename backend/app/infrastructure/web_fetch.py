from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class WebFetchError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class FetchedWebPage:
    requested_url: str
    final_url: str
    content: bytes
    content_type: str
    fetched_at: datetime


class WebPageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedWebPage: ...


class _InspectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def validate_public_web_target(url: str) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise WebFetchError("web_url_invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WebFetchError("web_url_invalid")
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        }
    except OSError as exc:
        raise WebFetchError("web_target_unresolved", retryable=True) from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise WebFetchError("web_target_not_public")


class SafeWebFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 60,
        max_bytes: int = 20 * 1024 * 1024,
        max_redirects: int = 5,
        target_validator=validate_public_web_target,
    ) -> None:
        if timeout_seconds <= 0 or max_bytes <= 0 or max_redirects < 0:
            raise ValueError("invalid web fetch limits")
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._target_validator = target_validator
        self._opener = build_opener(_InspectRedirects())

    def fetch(self, url: str) -> FetchedWebPage:
        requested_url = url.strip()
        current_url = requested_url
        for redirect_count in range(self._max_redirects + 1):
            self._target_validator(current_url)
            request = Request(
                current_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Knowledge-Web-Ingestion/1.0",
                },
                method="GET",
            )
            try:
                response = self._opener.open(request, timeout=self._timeout_seconds)
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        raise WebFetchError("web_redirect_invalid") from exc
                    if redirect_count >= self._max_redirects:
                        raise WebFetchError("web_redirect_limit") from exc
                    current_url = urljoin(current_url, location)
                    continue
                raise WebFetchError(
                    "web_fetch_rejected", retryable=exc.code == 429 or exc.code >= 500
                ) from exc
            except (TimeoutError, URLError, OSError) as exc:
                raise WebFetchError("web_fetch_unavailable", retryable=True) from exc

            with response:
                content_type = response.headers.get_content_type().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise WebFetchError("web_content_type_unsupported")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) > self._max_bytes:
                            raise WebFetchError("web_content_too_large")
                    except ValueError as exc:
                        raise WebFetchError("web_response_invalid") from exc
                content = response.read(self._max_bytes + 1)
                if len(content) > self._max_bytes:
                    raise WebFetchError("web_content_too_large")
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    normalized_content = content.decode(charset).encode("utf-8")
                except LookupError as exc:
                    raise WebFetchError("web_response_invalid") from exc
                except UnicodeDecodeError:
                    normalized_content = content.decode(charset, errors="replace").encode(
                        "utf-8"
                    )
                return FetchedWebPage(
                    requested_url=requested_url,
                    final_url=response.geturl(),
                    content=normalized_content,
                    content_type=content_type,
                    fetched_at=datetime.now(UTC).replace(microsecond=0),
                )
        raise WebFetchError("web_redirect_limit")