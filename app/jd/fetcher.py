from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5_000_000
ALLOWED_PORTS = {80, 443}


class FetchError(Exception):
    pass


class ManualPasteRequired(FetchError):
    def __init__(self, message: str, url: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.url = url


@dataclass
class FetchResult:
    url: str
    final_url: str
    html: str
    content_type: str = ""
    warnings: list[str] = field(default_factory=list)


AUTH_WALL_HOSTS = {
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
}
LOGIN_MARKERS = (
    "sign in to view",
    "sign in to continue",
    "authwall",
    "join linkedin",
    "login required",
    "please log in",
)


def validate_url(url: str) -> str:
    value = url.strip()
    if not value or len(value) > 2048:
        raise FetchError("The URL is empty or too long.")
    if any(character.isspace() for character in value):
        raise FetchError("The URL contains whitespace.")
    parsed = urlparse(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise FetchError("Only http and https URLs are supported.")
    if parsed.username or parsed.password:
        raise FetchError("URLs with embedded credentials are not supported.")
    if not parsed.hostname:
        raise FetchError("The URL does not contain a valid hostname.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise FetchError("The URL contains an invalid port.") from exc
    if port is not None and port not in ALLOWED_PORTS:
        raise FetchError("Only standard HTTP and HTTPS ports are supported.")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise FetchError("Private, loopback, and local network URLs are not allowed.")
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise FetchError("Private, loopback, and local network URLs are not allowed.")
    return value


def _host_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").rstrip(".").casefold()


def _check_auth_wall(url: str) -> None:
    host = _host_from_url(url).removeprefix("www.")
    if host in AUTH_WALL_HOSTS:
        raise ManualPasteRequired(
            f"{host} usually requires a signed-in browser session. "
            "Paste the job description text below instead.",
            url,
        )


async def _ensure_public_host(host: str) -> None:
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve host: {host}") from exc
    if not addresses:
        raise FetchError(f"Could not resolve host: {host}")
    for address in addresses:
        raw = str(address[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise FetchError("The URL resolved to an invalid network address.") from exc
        if not ip.is_global:
            raise FetchError("Private, loopback, and local network URLs are not allowed.")


async def _validate_target(url: str) -> str:
    value = validate_url(url)
    await _ensure_public_host(_host_from_url(value))
    return value


async def fetch_job_page(url: str, settings: Settings) -> FetchResult:
    original_url = validate_url(url)
    current_url = original_url
    warnings: list[str] = []
    redirects = 0
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with httpx.AsyncClient(
            timeout=25.0,
            follow_redirects=False,
            headers=headers,
            trust_env=False,
        ) as client:
            while True:
                _check_auth_wall(current_url)
                await _ensure_public_host(_host_from_url(current_url))
                async with client.stream("GET", current_url) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FetchError("The job page returned a redirect without a location.")
                        if redirects >= MAX_REDIRECTS:
                            raise FetchError("The job page redirected too many times.")
                        next_url = validate_url(urljoin(str(response.url), location))
                        if (
                            urlparse(current_url).scheme.casefold() == "https"
                            and urlparse(next_url).scheme.casefold() != "https"
                        ):
                            raise FetchError("Refusing to follow a redirect from HTTPS to HTTP.")
                        current_url = next_url
                        redirects += 1
                        continue

                    if response.status_code in {401, 403, 429, 999}:
                        raise ManualPasteRequired(
                            "The job site blocked automated access. "
                            "Paste the job description text instead.",
                            current_url,
                        )
                    if response.status_code >= 400:
                        raise FetchError(f"The job page returned HTTP {response.status_code}.")

                    content_type = response.headers.get("content-type", "")
                    if (
                        "html" not in content_type.casefold()
                        and "text" not in content_type.casefold()
                    ):
                        raise FetchError(f"Unsupported content type: {content_type or 'unknown'}")

                    content_length = response.headers.get("content-length", "")
                    if content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
                        raise FetchError("The job page is too large to process safely.")

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise FetchError("The job page is too large to process safely.")

                    final_url = str(response.url)
                    html = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    break
    except httpx.HTTPError as exc:
        raise FetchError(f"Could not fetch the job page: {exc}") from exc

    if len(html.strip()) < 200:
        raise ManualPasteRequired(
            "The job page returned almost no content. Paste the job description text instead.",
            current_url,
        )
    lowered = html.casefold()
    if any(marker in lowered for marker in LOGIN_MARKERS) and len(html) < 30_000:
        raise ManualPasteRequired(
            "The job page appears to require a logged-in session. "
            "Paste the job description text instead.",
            current_url,
        )
    if final_url != original_url:
        warnings.append(f"URL redirected to {final_url}")

    return FetchResult(
        url=original_url,
        final_url=final_url,
        html=html,
        content_type=content_type,
        warnings=warnings,
    )
