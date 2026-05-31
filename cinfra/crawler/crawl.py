import asyncio
from urllib.parse import urldefrag, urlsplit

import tldextract
from playwright.async_api import Browser, Page, Playwright, async_playwright

from cinfra.core.logging import get_logger

LOGGER = get_logger(__name__)

# Extensions we treat as non-HTML; everything else (incl. extensionless) is crawled.
NON_HTML_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "bmp", "tiff", "avif",
    "mp3", "mp4", "wav", "ogg", "webm", "avi", "mov", "mkv", "flac",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "tar", "gz",
    "rar", "7z", "csv",
    "css", "js", "mjs", "json", "xml", "rss", "woff", "woff2", "ttf", "eot",
}


def _registrable_domain(url: str) -> str:
    return tldextract.extract(url).registered_domain.lower()


class Crawler:
    def __init__(
        self,
        cdp_endpoint: str = "ws://127.0.0.1:9222/devtools/browser",
        available_workers: int = 1,
    ) -> None:
        self.cdp_endpoint = cdp_endpoint
        self.available_workers = available_workers
        self._playwright: Playwright | None = None
        self._browsers: list[Browser] = []

    async def _connect_browser(self) -> Browser:
        # One connection per worker: Obscura deadlocks on concurrent CDP
        # evaluate calls sharing a single connection, so each worker gets its
        # own browser (routed to a separate serve worker process).
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        browser = await self._playwright.chromium.connect_over_cdp(self.cdp_endpoint)
        self._browsers.append(browser)
        return browser

    async def close(self) -> None:
        for browser in self._browsers:
            await browser.close()
        self._browsers = []
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        LOGGER.info("Browser instances closed")

    def _in_scope(self, url: str, allowed_domain: str) -> bool:
        parts = urlsplit(url)

        if parts.scheme not in ("http", "https"):
            return False

        if _registrable_domain(parts.netloc) != allowed_domain:
            return False

        last_segment = parts.path.rsplit("/", 1)[-1]
        if "." in last_segment:
            ext = last_segment.rsplit(".", 1)[-1].lower()
            if ext in NON_HTML_EXTENSIONS:
                return False

        return True

    async def _extract_links(self, page: Page, allowed_domain: str) -> list[str]:
        # Obscura exposes anchors as generic Element nodes, so el.href isn't
        # resolved and document.baseURI is null; resolve against location.href.
        hrefs = await page.locator("a").evaluate_all(
            """elements => elements.map(el => {
                const raw = el.getAttribute('href');
                if (!raw) return null;
                try { return new URL(raw, location.href).href; }
                catch { return null; }
            }).filter(Boolean)"""
        )

        return sorted(
            {
                urldefrag(href).url
                for href in hrefs
                if self._in_scope(href, allowed_domain)
            }
        )

    async def crawl(
        self,
        start_url: str,
        max_pages: int = 100,
        concurrency: int = 4,
        delay: float = 0.5,
    ) -> list[str]:
        """Crawl in-scope pages from start_url using `concurrency` browsers.

        `concurrency` is how many of the server's workers to use; it's capped at
        ``available_workers`` since each worker needs its own browser connection.
        Stays within the start URL's registrable domain, follows only HTML
        pages, and visits each URL once. Returns the pages visited.
        """
        workers = max(1, min(concurrency, self.available_workers))
        if workers < concurrency:
            LOGGER.warning(
                "Requested concurrency %d exceeds %d available workers; using %d",
                concurrency,
                self.available_workers,
                workers,
            )

        allowed_domain = _registrable_domain(start_url)
        start = urldefrag(start_url).url

        frontier: asyncio.Queue[str] = asyncio.Queue()
        frontier.put_nowait(start)
        seen: set[str] = {start}
        visited: list[str] = []
        # Reserved before any await so workers can't race past max_pages.
        started = 0

        LOGGER.info(
            "Starting crawl at %s (scope: %s, max_pages: %d, workers: %d)",
            start,
            allowed_domain,
            max_pages,
            workers,
        )

        idx_width = len(str(max_pages))

        async def worker(page: Page) -> None:
            nonlocal started
            while True:
                url = await frontier.get()
                try:
                    if started >= max_pages:
                        continue
                    started += 1

                    try:
                        await page.goto(url, wait_until="domcontentloaded")
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning("Failed to load %s: %s", url, exc)
                        started -= 1
                        continue

                    visited.append(url)
                    index = len(visited)  # captured before awaiting extraction
                    links = await self._extract_links(page, allowed_domain)

                    new_links = [link for link in links if link not in seen]
                    seen.update(new_links)
                    for link in new_links:
                        frontier.put_nowait(link)

                    LOGGER.info(
                        "[%*d/%d] %s -> %d links (%d new, %d queued)",
                        idx_width,
                        index,
                        max_pages,
                        url,
                        len(links),
                        len(new_links),
                        frontier.qsize(),
                    )

                    if delay:
                        await asyncio.sleep(delay)
                finally:
                    frontier.task_done()

        pages: list[Page] = []
        for _ in range(workers):
            browser = await self._connect_browser()
            pages.append(await browser.new_page())

        workers = [asyncio.create_task(worker(page)) for page in pages]
        try:
            await frontier.join()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        LOGGER.info("Crawl finished: visited %d pages", len(visited))
        return visited
