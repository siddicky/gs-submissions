from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from playwright.async_api import Playwright, async_playwright

from .auth import ensure_authenticated, reauthenticate
from .config import DATA_DIR, PAGE_LOAD_TIMEOUT
from .models import SubmissionListItem
from .pages.submission_detail import scrape_submission_detail
from .pages.submissions_list import scrape_submissions_list
from .storage import load_scraped_ids, save_progress, save_submission, save_failed_id, load_progress
from .utils import polite_delay, setup_logging

logger = logging.getLogger("grayswan")

# Number of concurrent browser tabs for detail scraping
CONCURRENCY = 5


async def run_discovery(
    playwright: Playwright,
    arena: str,
    headed: bool = False,
) -> None:
    """Discovery mode: capture HTML, screenshots, and API responses for debugging."""
    debug_dir = DATA_DIR / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    context = await ensure_authenticated(playwright, headed=headed)
    page = await context.new_page()

    # Capture network responses
    captured: list[dict] = []

    async def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            if response.status == 200 and "json" in ct:
                body = await response.json()
                captured.append({"url": response.url, "body": body})
        except Exception:
            pass

    page.on("response", on_response)

    # --- Submissions list page ---
    from .config import submissions_list_url
    list_url = submissions_list_url(arena)
    logger.info(f"[Discovery] Navigating to list page: {list_url}")
    await page.goto(list_url, timeout=PAGE_LOAD_TIMEOUT, wait_until="networkidle")
    await page.wait_for_timeout(3000)

    # Save HTML
    html = await page.content()
    (debug_dir / f"{arena}_list.html").write_text(html)
    logger.info(f"[Discovery] Saved list page HTML to {debug_dir / f'{arena}_list.html'}")

    # Save screenshot
    await page.screenshot(
        path=str(debug_dir / f"{arena}_list.png"), full_page=True
    )
    logger.info(f"[Discovery] Saved list page screenshot")

    # --- Try scraping the list to get real submission links ---
    from .pages.submissions_list import scrape_submissions_list
    list_items = await scrape_submissions_list(page, arena, delay_min=1.0, delay_max=2.0)
    logger.info(f"[Discovery] Found {len(list_items)} submissions from list scraper")

    # Save list items for inspection
    if list_items:
        list_items_json = [item.model_dump() for item in list_items]
        (debug_dir / f"{arena}_list_items.json").write_text(
            json.dumps(list_items_json, indent=2, default=str)
        )
        logger.info(f"[Discovery] Saved {len(list_items)} list items to JSON")

    # --- First submission detail page ---
    # Use list items if available, otherwise try finding links in DOM
    detail_href = None
    if list_items:
        item = list_items[0]
        detail_href = item.detail_url
    else:
        # Fallback: find links with submissionId in URL
        links = await page.query_selector_all('a[href*="submissionId"]')
        if not links:
            links = await page.query_selector_all(f'a[href*="/chat/"]')
            # Filter out nav links (they don't have a chat ID in the path)
            filtered = []
            for link in links:
                h = await link.get_attribute("href") or ""
                # Real submission links have a chat ID after /chat/
                parts = h.rstrip("/").split("/chat/")
                if len(parts) > 1 and parts[1]:
                    filtered.append(link)
            links = filtered
        if links:
            detail_href = await links[0].get_attribute("href")

    if detail_href:
        href = detail_href
        if not href.startswith("http"):
            href = f"https://app.grayswan.ai{href}"
        logger.info(f"[Discovery] Navigating to first detail page: {href}")
        await page.goto(href, timeout=PAGE_LOAD_TIMEOUT, wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # Save HTML
        detail_html = await page.content()
        (debug_dir / f"{arena}_detail.html").write_text(detail_html)
        logger.info(f"[Discovery] Saved detail page HTML")

        # Save screenshot
        await page.screenshot(
            path=str(debug_dir / f"{arena}_detail.png"), full_page=True
        )
        logger.info(f"[Discovery] Saved detail page screenshot")

        # Try clicking Behavior Criteria button
        try:
            btn = page.locator("button:has-text('Behavior Criteria')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(2000)
                criteria_html = await page.content()
                (debug_dir / f"{arena}_criteria_modal.html").write_text(criteria_html)
                await page.screenshot(
                    path=str(debug_dir / f"{arena}_criteria_modal.png"),
                    full_page=True,
                )
                logger.info(f"[Discovery] Saved behavior criteria modal screenshot")
                await page.keyboard.press("Escape")
        except Exception as e:
            logger.warning(f"[Discovery] Could not capture criteria modal: {e}")
    else:
        logger.warning("[Discovery] No submission links found on list page")

    page.remove_listener("response", on_response)

    # Save captured API responses
    api_file = debug_dir / f"{arena}_api_responses.json"
    api_file.write_text(json.dumps(captured, indent=2, default=str))
    logger.info(f"[Discovery] Saved {len(captured)} API responses to {api_file}")

    await context.browser.close()
    logger.info("[Discovery] Done! Check data/debug/ for captured artifacts.")


async def scrape_all(
    arenas: list[str],
    force_reauth: bool = False,
    headed: bool = False,
    delay_min: float = 1.5,
    delay_max: float = 3.0,
    max_submissions: int | None = None,
) -> None:
    """Main scraping orchestration with parallel detail page scraping."""
    async with async_playwright() as pw:
        context = await ensure_authenticated(pw, force_reauth=force_reauth, headed=headed)

        for arena in arenas:
            logger.info(f"=== Scraping arena: {arena} ===")
            arena_start = time.monotonic()

            # Load existing progress
            scraped_ids = load_scraped_ids(arena)
            progress = load_progress(arena)
            logger.info(f"Already scraped: {len(scraped_ids)} submissions")

            # Scrape submissions list (uses a single page)
            list_page = await context.new_page()
            try:
                all_items = await scrape_submissions_list(
                    list_page, arena, delay_min=delay_min, delay_max=delay_max
                )
            except Exception as e:
                logger.error(f"Failed to scrape submissions list for {arena}: {e}")
                await list_page.close()
                continue
            finally:
                await list_page.close()

            # Filter out already-scraped
            new_items = [i for i in all_items if i.submission_id not in scraped_ids]
            logger.info(
                f"Found {len(all_items)} total, {len(new_items)} new to scrape"
            )

            if max_submissions is not None:
                new_items = new_items[:max_submissions]
                logger.info(f"Limited to {max_submissions} submissions")

            # Update progress with total count
            progress.total_submissions = len(all_items)

            if not new_items:
                logger.info(f"Nothing new to scrape for {arena}")
                continue

            # --- Parallel detail scraping with N concurrent tabs ---
            semaphore = asyncio.Semaphore(CONCURRENCY)
            # Lock for thread-safe progress updates
            save_lock = asyncio.Lock()
            success_count = 0
            fail_count = 0

            async def _scrape_one(idx: int, item: SubmissionListItem) -> None:
                nonlocal success_count, fail_count, context
                async with semaphore:
                    logger.info(
                        f"[{idx + 1}/{len(new_items)}] Scraping {item.submission_id} "
                        f"({item.model_name})"
                    )
                    page = await context.new_page()
                    try:
                        submission = await scrape_submission_detail(page, item)
                        async with save_lock:
                            save_submission(submission, arena)
                            scraped_ids.add(item.submission_id)
                            progress.scraped_ids = list(scraped_ids)
                            save_progress(progress)
                            success_count += 1
                        logger.info(
                            f"  [{idx+1}] Saved: {submission.model_name} / "
                            f"{submission.behavior_name} ({submission.status.value})"
                        )
                    except Exception as e:
                        async with save_lock:
                            fail_count += 1
                        logger.error(f"  [{idx+1}] Failed: {e}")
                        save_failed_id(arena, item.submission_id, str(e))
                    finally:
                        await page.close()

                    # Small delay between submissions to avoid hammering
                    await polite_delay(min(delay_min, 0.5), min(delay_max, 1.0))

            # Process in batches for better progress visibility
            batch_size = CONCURRENCY * 2
            for batch_start in range(0, len(new_items), batch_size):
                batch = new_items[batch_start:batch_start + batch_size]
                tasks = [
                    _scrape_one(batch_start + i, item)
                    for i, item in enumerate(batch)
                ]
                await asyncio.gather(*tasks)
                elapsed = time.monotonic() - arena_start
                rate = (batch_start + len(batch)) / elapsed if elapsed > 0 else 0
                logger.info(
                    f"  Batch done. {success_count + fail_count}/{len(new_items)} processed "
                    f"({rate:.1f} submissions/sec)"
                )

            elapsed = time.monotonic() - arena_start
            logger.info(
                f"=== {arena} complete in {elapsed:.0f}s: {success_count} scraped, "
                f"{fail_count} failed, {len(scraped_ids)} total ==="
            )

        try:
            await context.browser.close()
        except Exception:
            await context.close()
