from __future__ import annotations

from pathlib import Path


class RenderError(Exception):
    pass


async def render_pdf(html: str, output_path: Path) -> Path:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RenderError("Playwright is not installed.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                await page.emulate_media(media="print")
                header_template = "<div></div>"
                footer_template = (
                    "<div style='font-family:Arial,sans-serif;font-size:8pt;color:#6b7280;"
                    "text-align:center;width:100%;'>Page "
                    "<span class='pageNumber'></span> of "
                    "<span class='totalPages'></span></div>"
                )
                await page.pdf(
                    path=str(output_path),
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    display_header_footer=True,
                    header_template=header_template,
                    footer_template=footer_template,
                )
            finally:
                await browser.close()
    except Exception as exc:
        raise RenderError(
            "PDF rendering failed. Install the Chromium browser with `playwright install chromium`."
        ) from exc
    return output_path
