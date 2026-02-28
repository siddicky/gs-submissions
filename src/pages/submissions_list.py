from __future__ import annotations

import json
import logging
import math
import re
from urllib.parse import urlparse, parse_qs

from playwright.async_api import Page

from ..config import submissions_list_url, PAGE_SIZE, PAGE_LOAD_TIMEOUT
from ..models import SubmissionListItem, SubmissionStatus
from ..utils import polite_delay

logger = logging.getLogger("grayswan")


def _parse_detail_url(href: str, arena: str) -> tuple[str, str] | None:
    """Extract chat_id and submission_id from a detail page URL.

    URL pattern: /arena/challenge/{arena}/chat/{chatId}?submissionId={submissionId}
    """
    try:
        parsed = urlparse(href)
        # Chat ID is the last segment of the path
        path_parts = parsed.path.rstrip("/").split("/")
        chat_id = path_parts[-1] if path_parts else None

        # Submission ID from query params
        qs = parse_qs(parsed.query)
        submission_id = qs.get("submissionId", [None])[0]

        if chat_id and submission_id:
            return chat_id, submission_id
    except Exception:
        pass
    return None


def _parse_status_from_icon(icon_html: str) -> SubmissionStatus:
    """Determine success/failure from the icon element."""
    lower = icon_html.lower()
    if "green" in lower or "check" in lower or "success" in lower:
        return SubmissionStatus.SUCCESS
    if "red" in lower or "cross" in lower or "fail" in lower or "close" in lower:
        return SubmissionStatus.FAILURE
    return SubmissionStatus.UNKNOWN


async def scrape_submissions_list(
    page: Page,
    arena: str,
    delay_min: float = 1.5,
    delay_max: float = 3.0,
) -> list[SubmissionListItem]:
    """Scrape all submissions from the paginated list page for an arena."""
    url = submissions_list_url(arena)
    logger.info(f"Navigating to submissions list: {url}")

    # Set up network interception to capture API responses
    captured_api: list[dict] = []

    async def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            if response.status == 200 and "json" in ct:
                body = await response.json()
                captured_api.append({"url": response.url, "body": body})
        except Exception:
            pass

    page.on("response", on_response)

    await page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
    # Wait for submission content to render
    await page.wait_for_timeout(2000)

    # Try to get total count from "Showing X-Y of Z submissions"
    total_submissions = await _extract_total_count(page)
    if total_submissions is not None:
        total_pages = math.ceil(total_submissions / PAGE_SIZE)
        logger.info(
            f"Found {total_submissions} submissions across {total_pages} pages"
        )
    else:
        total_pages = None
        logger.warning("Could not determine total submissions count. Will paginate until no more pages.")

    all_items: list[SubmissionListItem] = []
    current_page = 1

    while True:
        logger.info(f"Scraping page {current_page}" + (f"/{total_pages}" if total_pages else ""))

        # Parse rows on this page
        items = await _parse_submission_rows(page, arena)
        if not items:
            logger.info("No more submission rows found. Done with pagination.")
            break

        all_items.extend(items)
        logger.debug(f"  Page {current_page}: {len(items)} items (total so far: {len(all_items)})")

        # Check if there's a next page
        if total_pages and current_page >= total_pages:
            break

        # Try to click Next
        has_next = await _click_next_page(page)
        if not has_next:
            break

        current_page += 1
        # Minimal delay between list pages (just clicking Next, very light)
        await polite_delay(min(delay_min, 0.5), min(delay_max, 1.0))

    page.remove_listener("response", on_response)

    # Log any captured API responses for debugging
    if captured_api:
        logger.debug(f"Captured {len(captured_api)} API responses during list scraping")

    logger.info(f"Total submissions collected for {arena}: {len(all_items)}")
    return all_items


async def _extract_total_count(page: Page) -> int | None:
    """Extract total count from 'Showing X-Y of Z submissions' text."""
    try:
        # Look for text matching the pattern
        text_content = await page.text_content("body")
        if text_content:
            match = re.search(r"Showing\s+\d+-\d+\s+of\s+(\d+)\s+submissions", text_content)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    return None


async def _parse_submission_rows(
    page: Page, arena: str
) -> list[SubmissionListItem]:
    """Parse all submission rows on the current page."""
    items: list[SubmissionListItem] = []

    # Find all clickable submission rows — these are likely anchor tags or
    # divs with click handlers that link to the detail page.
    # Strategy: find all links whose href matches the chat URL pattern
    links = await page.query_selector_all(
        f'a[href*="/arena/challenge/{arena}/chat/"]'
    )

    if not links:
        # Fallback: try broader selectors
        links = await page.query_selector_all('a[href*="/chat/"][href*="submissionId"]')

    if not links:
        # Another fallback: look for any row-like elements
        logger.warning("Could not find submission links via href. Trying row-based approach.")
        return await _parse_rows_fallback(page, arena)

    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            parsed = _parse_detail_url(href, arena)
            if not parsed:
                continue
            chat_id, submission_id = parsed

            # Get the text content of the row
            text = (await link.inner_text()).strip()

            # The row text typically has model name + behavior on first line, timestamp below
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            title_line = lines[0] if lines else ""
            timestamp = lines[1] if len(lines) > 1 else ""

            # Parse model name and behavior from title
            # Pattern: "Model Name - behavior-sni..."
            model_name = ""
            behavior_snippet = ""
            if " - " in title_line:
                parts = title_line.split(" - ", 1)
                model_name = parts[0].strip()
                behavior_snippet = parts[1].strip()
            else:
                model_name = title_line

            # Determine status from icon
            # Look for SVG or icon element within or adjacent to the link
            parent = await link.evaluate_handle("el => el.closest('div, tr, li') || el.parentElement")
            parent_html = await parent.evaluate("el => el.innerHTML")
            status = _parse_status_from_icon(parent_html)

            items.append(SubmissionListItem(
                submission_id=submission_id,
                chat_id=chat_id,
                detail_url=href,
                model_name=model_name,
                behavior_snippet=behavior_snippet,
                timestamp_raw=timestamp,
                status=status,
                arena=arena,
            ))
        except Exception as e:
            logger.warning(f"Failed to parse a submission row: {e}")
            continue

    return items


async def _parse_rows_fallback(
    page: Page, arena: str
) -> list[SubmissionListItem]:
    """Fallback row parser using broader selectors."""
    items: list[SubmissionListItem] = []
    # Try to find all links on the page and filter for chat URLs
    all_links = await page.query_selector_all("a")
    for link in all_links:
        href = await link.get_attribute("href") or ""
        if "/chat/" not in href or "submissionId" not in href:
            continue
        parsed = _parse_detail_url(href, arena)
        if not parsed:
            continue
        chat_id, submission_id = parsed
        text = (await link.inner_text()).strip()
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        items.append(SubmissionListItem(
            submission_id=submission_id,
            chat_id=chat_id,
            detail_url=href,
            model_name=lines[0] if lines else "unknown",
            behavior_snippet="",
            timestamp_raw=lines[1] if len(lines) > 1 else "",
            status=SubmissionStatus.UNKNOWN,
            arena=arena,
        ))
    return items


async def _click_next_page(page: Page) -> bool:
    """Click the 'Next' pagination button. Returns True if successful."""
    try:
        # Try the most common pattern: a button or link with text "Next"
        next_btn = page.get_by_role("button", name="Next")
        if await next_btn.count() > 0 and await next_btn.is_enabled():
            await next_btn.click()
            await page.wait_for_timeout(1500)
            return True
    except Exception:
        pass

    try:
        # Fallback: look for link with text "Next"
        next_link = page.get_by_text("Next", exact=True)
        if await next_link.count() > 0:
            await next_link.click()
            await page.wait_for_timeout(1500)
            return True
    except Exception:
        pass

    try:
        # Fallback: look for "Next >" or ">" style buttons
        next_arrow = page.locator("button:has-text('Next'), a:has-text('Next'), [aria-label='Next']")
        if await next_arrow.count() > 0:
            await next_arrow.first.click()
            await page.wait_for_timeout(1500)
            return True
    except Exception:
        pass

    logger.debug("No 'Next' button found or clickable.")
    return False
