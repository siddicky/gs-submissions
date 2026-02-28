from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import TypeVar, Callable, Any

from rich.console import Console
from rich.logging import RichHandler

from .config import (
    DATA_DIR,
    MAX_RETRIES,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    RETRY_BACKOFF_BASE,
)

console = Console()

F = TypeVar("F", bound=Callable[..., Any])


def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    log_file = DATA_DIR / "scrape.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("grayswan")
    logger.setLevel(level)

    if not logger.handlers:
        # Rich console handler
        rich_handler = RichHandler(console=console, show_path=False, markup=True)
        rich_handler.setLevel(level)
        logger.addHandler(rich_handler)

        # File handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


async def polite_delay(
    min_delay: float = REQUEST_DELAY_MIN,
    max_delay: float = REQUEST_DELAY_MAX,
) -> None:
    delay = random.uniform(min_delay, max_delay)
    await asyncio.sleep(delay)


def retry(
    max_attempts: int = MAX_RETRIES,
    backoff_base: float = RETRY_BACKOFF_BASE,
    exceptions: tuple = (Exception,),
):
    """Async retry decorator with exponential backoff."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            logger = logging.getLogger("grayswan")
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        wait = backoff_base * attempt
                        logger.warning(
                            f"Attempt {attempt}/{max_attempts} failed: {e}. "
                            f"Retrying in {wait}s..."
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {e}"
                        )
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
