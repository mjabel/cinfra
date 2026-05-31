import asyncio

from cinfra.core.logging import get_logger, setup_logging
from cinfra.crawler.crawl import Crawler
from cinfra.crawler.setup import Obscura

LOGGER = get_logger(__name__)

PORT = 9222
# Worker pool the server provides; the crawler decides how many to use.
WORKERS = 8


async def run(port: int = PORT) -> None:
    crawler = Crawler(
        f"ws://127.0.0.1:{port}/devtools/browser", available_workers=WORKERS
    )
    try:
        visited = await crawler.crawl(
            "https://poliris.io",
            max_pages=50,
            concurrency=4,
        )
        LOGGER.info("Visited %d pages", len(visited))
    finally:
        await crawler.close()


def main() -> None:
    setup_logging()
    # Start (or reuse) the Obscura CDP server before crawling.
    Obscura().serve(port=PORT, workers=WORKERS)
    asyncio.run(run(PORT))


if __name__ == "__main__":
    main()
