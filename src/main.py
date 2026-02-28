from __future__ import annotations

import argparse
import asyncio

from .config import DEFAULT_ARENAS, REQUEST_DELAY_MIN, REQUEST_DELAY_MAX
from .utils import setup_logging


def main():
    parser = argparse.ArgumentParser(
        description="Scrape submissions from Gray Swan Arena"
    )
    parser.add_argument(
        "--arenas",
        nargs="+",
        default=DEFAULT_ARENAS,
        help=f"Arena slugs to scrape (default: {' '.join(DEFAULT_ARENAS)})",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Force re-authentication even if saved state exists",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed (visible) mode for debugging",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Run discovery mode only — save HTML/screenshots/API data to data/debug/",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=REQUEST_DELAY_MIN,
        help=f"Min delay between requests in seconds (default: {REQUEST_DELAY_MIN})",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=REQUEST_DELAY_MAX,
        help=f"Max delay between requests in seconds (default: {REQUEST_DELAY_MAX})",
    )
    parser.add_argument(
        "--max-submissions",
        type=int,
        default=None,
        help="Max submissions to scrape per arena (for testing)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of parallel browser tabs for detail scraping (default: 5)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.discover:
        from .scraper import run_discovery
        from playwright.async_api import async_playwright

        async def _discover():
            async with async_playwright() as pw:
                for arena in args.arenas:
                    await run_discovery(pw, arena, headed=args.headed)

        asyncio.run(_discover())
    else:
        from .scraper import scrape_all

        # Set concurrency
        from .scraper import CONCURRENCY
        import src.scraper
        src.scraper.CONCURRENCY = args.concurrency

        asyncio.run(
            scrape_all(
                arenas=args.arenas,
                force_reauth=args.reauth,
                headed=args.headed,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
                max_submissions=args.max_submissions,
            )
        )


if __name__ == "__main__":
    main()
