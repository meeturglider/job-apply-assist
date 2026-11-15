from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.config import Settings


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
    "www.linkedin.com",
    "indeed.com",
    "www.indeed.com",
    "glassdoor.com",
    "www.glassdoor.com",
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
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise FetchError("Only http and https URLs are supported.")
    if not parsed.hostname:
        raise FetchError("The URL does not contain a valid hostname.")
    return url.strip()


async def _ensure_public_host(host: str) -> None:
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve host: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("Private, loopback, and local network URLs are not allowed.")


async def fetch_job_page(url: str, settings: Settings) -> FetchResult:
    url = validate_url(url)
    host = (urlparse(url).hostname or "").casefold()
    hostname = host.removeprefix("www.")
    if hostname in {value.removeprefix("www.") for value in AUTH_WALL_HOSTS}:
        raise ManualPasteRequired(
            f"{hostname} usually requires a signed-in browser session. "
            "Paste the job description text below instead.",
            url,
        )
    await _ensure_public_host(host)

    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    warnings: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=25.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"Could not fetch the job page: {exc}") from exc

    if response.status_code in {401, 403, 429, 999}:
        raise ManualPasteRequired(
            "The job site blocked automated access. Paste the job description text instead.",
            url,
        )
    if response.status_code >= 400:
        raise FetchError(f"The job page returned HTTP {response.status_code}.")

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.casefold() and "text" not in content_type.casefold():
        raise FetchError(f"Unsupported content type: {content_type or 'unknown'}")

    html = response.text
    if len(html.encode("utf-8")) > 5_000_000:
        raise FetchError("The job page is too large to process safely.")
    if len(html.strip()) < 200:
        raise ManualPasteRequired(
            "The job page returned almost no content. Paste the job description text instead.",
            url,
        )
    lowered = html.casefold()
    if any(marker in lowered for marker in LOGIN_MARKERS) and len(html) < 30_000:
        raise ManualPasteRequired(
            "The job page appears to require a logged-in session. "
            "Paste the job description text instead.",
            url,
        )
    if str(response.url) != url:
        warnings.append(f"URL redirected to {response.url}")

    return FetchResult(
        url=url,
        final_url=str(response.url),
        html=html,
        content_type=content_type,
        warnings=warnings,
    )
