import asyncio

import pytest

from cinfra.crawler.crawl import Crawler, _registrable_domain


class FakeLocator:
    def __init__(self, hrefs: list[str]) -> None:
        self._hrefs = hrefs

    async def evaluate_all(self, _script: str) -> list[str]:
        return list(self._hrefs)


class FakePage:
    """Stand-in for a Playwright page driven by a {url: [hrefs]} graph."""

    def __init__(
        self, graph: dict[str, list[str]], visited_log: list[str], fail: set[str]
    ) -> None:
        self._graph = graph
        self._visited_log = visited_log
        self._fail = fail
        self._current: str | None = None

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self._visited_log.append(url)
        if url in self._fail:
            raise RuntimeError(f"goto failed: {url}")
        self._current = url

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator(self._graph.get(self._current, []))

    async def close(self) -> None:
        pass


class FakeBrowser:
    def __init__(
        self, graph: dict[str, list[str]], visited_log: list[str], fail: set[str]
    ) -> None:
        self._graph = graph
        self._visited_log = visited_log
        self._fail = fail

    async def new_page(self) -> FakePage:
        return FakePage(self._graph, self._visited_log, self._fail)

    async def close(self) -> None:
        pass


def make_crawler(
    graph: dict[str, list[str]],
    *,
    available_workers: int = 4,
    fail: set[str] | None = None,
) -> tuple[Crawler, dict[str, int]]:
    """Crawler whose _connect_browser yields FakeBrowsers over `graph`."""
    crawler = Crawler(available_workers=available_workers)
    counters = {"connects": 0}
    fail = fail or set()

    async def fake_connect() -> FakeBrowser:
        counters["connects"] += 1
        browser = FakeBrowser(graph, [], fail)
        crawler._browsers.append(browser)  # type: ignore[arg-type]
        return browser

    crawler._connect_browser = fake_connect  # type: ignore[method-assign]
    return crawler, counters


# --- _registrable_domain ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/page", "example.com"),
        ("https://www.example.com", "example.com"),
        ("https://blog.example.com/x", "example.com"),
        ("example.com", "example.com"),
        ("https://a.b.example.co.uk/p", "example.co.uk"),
    ],
)
def test_registrable_domain(value: str, expected: str) -> None:
    assert _registrable_domain(value) == expected


# --- _in_scope -------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/page", True),
        ("http://example.com/", True),
        ("https://www.example.com/page", True),
        ("https://blog.example.com/x", True),
        ("https://example.com/order.php", True),
        ("https://example.com/path/", True),
        ("https://other.org/", False),
        ("https://notexample.com/", False),
        ("mailto:hi@example.com", False),
        ("javascript:void(0)", False),
        ("ftp://example.com/file", False),
        ("https://example.com/logo.png", False),
        ("https://example.com/doc.pdf", False),
        ("https://example.com/app.js", False),
    ],
)
def test_in_scope(url: str, expected: bool) -> None:
    crawler = Crawler()
    assert crawler._in_scope(url, "example.com") is expected


# --- _extract_links --------------------------------------------------------


def test_extract_links_filters_dedupes_and_strips_fragments() -> None:
    crawler = Crawler()
    page = FakePage(
        {
            "x": [
                "https://example.com/a",
                "https://example.com/a#section",  # fragment of a duplicate
                "https://example.com/a",  # exact duplicate
                "https://example.com/b",
                "https://other.org/x",  # out of scope
                "https://example.com/pic.png",  # asset
            ]
        },
        [],
        set(),
    )
    page._current = "x"

    links = asyncio.run(crawler._extract_links(page, "example.com"))  # type: ignore[arg-type]
    assert links == ["https://example.com/a", "https://example.com/b"]


# --- crawl -----------------------------------------------------------------

SITE = {
    "https://example.com/": [
        "https://example.com/a",
        "https://example.com/b",
        "https://other.org/x",  # external, never visited
        "https://example.com/logo.png",  # asset, never visited
        "mailto:hi@example.com",  # non-http, never visited
    ],
    "https://example.com/a": [
        "https://example.com/",  # back-link, already seen
        "https://example.com/c",
    ],
    "https://example.com/b": ["https://example.com/a"],
    "https://example.com/c": [],
}


def test_crawl_visits_all_in_scope_pages_once() -> None:
    crawler, _ = make_crawler(SITE)
    visited = asyncio.run(
        crawler.crawl("https://example.com/", concurrency=1, delay=0)
    )

    assert set(visited) == {
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }
    assert len(visited) == len(set(visited))  # no duplicates


def test_crawl_stays_in_scope() -> None:
    crawler, _ = make_crawler(SITE)
    visited = asyncio.run(
        crawler.crawl("https://example.com/", concurrency=1, delay=0)
    )
    assert all(v.startswith("https://example.com/") for v in visited)


def test_crawl_respects_max_pages() -> None:
    crawler, _ = make_crawler(SITE)
    visited = asyncio.run(
        crawler.crawl("https://example.com/", max_pages=2, concurrency=1, delay=0)
    )
    assert len(visited) == 2


def test_crawl_normalizes_start_url_fragment() -> None:
    crawler, _ = make_crawler(SITE)
    visited = asyncio.run(
        crawler.crawl("https://example.com/#top", concurrency=1, delay=0)
    )
    assert "https://example.com/" in visited
    assert "https://example.com/#top" not in visited


def test_crawl_clamps_concurrency_to_available_workers() -> None:
    crawler, counters = make_crawler({"https://example.com/": []}, available_workers=2)
    asyncio.run(crawler.crawl("https://example.com/", concurrency=5, delay=0))
    # One browser connection per worker, capped at available_workers.
    assert counters["connects"] == 2


def test_crawl_skips_failed_navigations() -> None:
    graph = {
        "https://example.com/": [
            "https://example.com/good",
            "https://example.com/bad",
        ],
        "https://example.com/good": [],
        "https://example.com/bad": [],
    }
    crawler, _ = make_crawler(
        graph, available_workers=1, fail={"https://example.com/bad"}
    )
    visited = asyncio.run(
        crawler.crawl("https://example.com/", concurrency=1, delay=0)
    )
    assert "https://example.com/bad" not in visited
    assert set(visited) == {
        "https://example.com/",
        "https://example.com/good",
    }
