from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings
from app.jd import fetcher
from app.jd.fetcher import FetchError, ManualPasteRequired, fetch_job_page, validate_url

HTML = (
    "<html><body>" + ("<p>Platform engineering role with Kubernetes.</p>" * 10) + "</body></html>"
)
PUBLIC = "93.184.216.34"


def _settings() -> Settings:
    return Settings(llm_provider="none", ollama_timeout_seconds=1)


def test_validate_url_rejects_unsafe_shapes() -> None:
    for url in (
        "ftp://example.com/job",
        "http://example.com:8080/job",
        "http://user:pass@example.com/job",
        "http://localhost/job",
        "http://127.0.0.1/job",
        "http://[::1]/job",
        "http://example.com:99999/job",
        "not a url",
    ):
        with pytest.raises(FetchError):
            validate_url(url)


async def test_private_ip_resolution_is_blocked() -> None:
    with pytest.raises(FetchError, match="Private"):
        await fetcher._ensure_public_host("127.0.0.1")
    with pytest.raises(FetchError, match="Private"):
        await fetcher._ensure_public_host("::1")


async def test_auth_wall_requires_manual_paste() -> None:
    with pytest.raises(ManualPasteRequired):
        await fetch_job_page("https://www.linkedin.com/jobs/view/123", _settings())


async def test_follows_only_validated_public_redirects() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"https://{PUBLIC}/start").mock(
            return_value=httpx.Response(
                302,
                headers={"location": f"https://{PUBLIC}/job"},
            )
        )
        mock.get(f"https://{PUBLIC}/job").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=HTML,
            )
        )
        result = await fetch_job_page(f"https://{PUBLIC}/start", _settings())
    assert result.url == f"https://{PUBLIC}/start"
    assert result.final_url == f"https://{PUBLIC}/job"
    assert "redirected" in result.warnings[0]


async def test_redirect_to_private_address_is_blocked() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"https://{PUBLIC}/start").mock(
            return_value=httpx.Response(
                302,
                headers={"location": "https://127.0.0.1/job"},
            )
        )
        with pytest.raises(FetchError, match="Private"):
            await fetch_job_page(f"https://{PUBLIC}/start", _settings())


async def test_https_to_http_redirect_is_blocked() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"https://{PUBLIC}/start").mock(
            return_value=httpx.Response(
                302,
                headers={"location": f"http://{PUBLIC}/job"},
            )
        )
        with pytest.raises(FetchError, match="HTTPS to HTTP"):
            await fetch_job_page(f"https://{PUBLIC}/start", _settings())


async def test_response_size_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "MAX_RESPONSE_BYTES", 100)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"https://{PUBLIC}/job").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=HTML,
            )
        )
        with pytest.raises(FetchError, match="too large"):
            await fetch_job_page(f"https://{PUBLIC}/job", _settings())


async def test_redirect_limit_is_enforced() -> None:
    with respx.mock(assert_all_called=False) as mock:
        for index in range(fetcher.MAX_REDIRECTS + 1):
            mock.get(f"https://{PUBLIC}/{index}").mock(
                return_value=httpx.Response(
                    302,
                    headers={"location": f"https://{PUBLIC}/{index + 1}"},
                )
            )
        with pytest.raises(FetchError, match="too many times"):
            await fetch_job_page(f"https://{PUBLIC}/0", _settings())
