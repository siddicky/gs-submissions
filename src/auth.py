from __future__ import annotations

import json
import logging

import rookiepy
from playwright.async_api import Playwright, BrowserContext

from .config import AUTH_STATE_FILE, ARENA_BASE, NAVIGATION_TIMEOUT

logger = logging.getLogger("grayswan")

LOGIN_INDICATORS = ("auth", "login", "signin", "oauth", "accounts.google", "stytch")

# Cookies required for authenticated access
REQUIRED_COOKIES = ("gs_stytch_session", "gs_stytch_session_jwt")


def _extract_chrome_cookies() -> list[dict]:
    """Extract cookies for grayswan.ai from the user's Chrome browser."""
    raw_cookies = rookiepy.chrome(domains=[".grayswan.ai"])
    if not raw_cookies:
        raise RuntimeError(
            "No cookies found for grayswan.ai in Chrome. "
            "Please log in to app.grayswan.ai in Chrome first."
        )

    # Check for required auth cookies
    cookie_names = {c["name"] for c in raw_cookies}
    missing = [name for name in REQUIRED_COOKIES if name not in cookie_names]
    if missing:
        raise RuntimeError(
            f"Missing auth cookies in Chrome: {missing}. "
            "Please log in to app.grayswan.ai in Chrome first."
        )

    # Convert to Playwright cookie format
    pw_cookies = []
    for c in raw_cookies:
        pw_cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".grayswan.ai"),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", c.get("is_secure", False))),
            "httpOnly": bool(c.get("httpOnly", c.get("is_httponly", False))),
        }
        # Add expiry if available
        expires = c.get("expires", c.get("expiry", 0))
        if expires and expires > 0:
            pw_cookie["expires"] = expires

        # sameSite defaults
        pw_cookie["sameSite"] = "Lax"
        pw_cookies.append(pw_cookie)

    return pw_cookies


def _save_cookies_as_state(cookies: list[dict]) -> None:
    """Save cookies as Playwright storage state JSON."""
    state = {
        "cookies": cookies,
        "origins": [],
    }
    AUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_STATE_FILE.write_text(json.dumps(state, indent=2))


async def _verify_auth(context: BrowserContext) -> bool:
    """Navigate to a known page and check if we see authenticated content."""
    page = await context.new_page()
    try:
        await page.goto(
            f"{ARENA_BASE}/challenge/safeguards/submissions",
            timeout=NAVIGATION_TIMEOUT,
            wait_until="networkidle",
        )
        await page.wait_for_timeout(3000)

        url = page.url.lower()
        if any(ind in url for ind in LOGIN_INDICATORS):
            logger.warning(f"Auth check failed — redirected to {page.url}")
            return False

        # Definitive NOT-logged-in indicator: "Sign In" button in navbar
        sign_in_btn = page.locator("text='Sign In'")
        if await sign_in_btn.count() > 0:
            logger.warning("Auth check failed — 'Sign In' button visible (not logged in)")
            return False

        # Check for "Submissions 0" which indicates logged in but no submissions
        # vs actual submission content
        body = await page.inner_text("body")

        # If we see "No submissions found" that's OK — means we're logged in
        # but haven't submitted yet. Still valid auth.
        if "no submissions found" in body.lower():
            # Only valid if "Sign In" is NOT present (already checked above)
            logger.info("Auth valid — logged in but no submissions for this arena")
            return True

        # Check for actual submission rows
        accordion = page.locator("div[data-slot='accordion-item']")
        if await accordion.count() > 0:
            return True

        # Check for "Showing X of Y submissions" text
        if "showing" in body.lower() and "of" in body.lower() and "submissions" in body.lower():
            return True

        logger.warning("Auth check failed — no authenticated content visible")
        return False
    except Exception as e:
        logger.warning(f"Auth verification error: {e}")
        return False
    finally:
        await page.close()


async def ensure_authenticated(
    playwright: Playwright,
    force_reauth: bool = False,
    headed: bool = False,
) -> BrowserContext:
    """Ensure we have a valid authenticated browser context.

    Strategy: Extract cookies from the user's Chrome browser and inject
    them into a Playwright context. This avoids the Google OAuth
    "insecure browser" issue entirely.
    """
    # Try existing saved state first
    if not force_reauth and AUTH_STATE_FILE.exists():
        logger.info("Loading saved auth state...")
        browser = await playwright.chromium.launch(headless=not headed)
        context = await browser.new_context(storage_state=str(AUTH_STATE_FILE))

        if await _verify_auth(context):
            logger.info("Saved auth state is valid.")
            return context

        logger.warning("Saved auth state is expired. Extracting fresh cookies from Chrome...")
        await browser.close()

    # Extract cookies from Chrome
    logger.info("Extracting cookies from Chrome browser...")
    try:
        cookies = _extract_chrome_cookies()
    except Exception as e:
        logger.error(str(e))
        raise

    auth_cookies = [c for c in cookies if c["name"] in REQUIRED_COOKIES]
    logger.info(
        f"Found {len(cookies)} cookies ({len(auth_cookies)} auth cookies)"
    )

    # Save as storage state
    _save_cookies_as_state(cookies)
    logger.info(f"Auth state saved to {AUTH_STATE_FILE}")

    # Create context with the cookies
    browser = await playwright.chromium.launch(headless=not headed)
    context = await browser.new_context(storage_state=str(AUTH_STATE_FILE))

    # Verify
    if await _verify_auth(context):
        logger.info("Authentication successful!")
        return context

    await browser.close()
    raise RuntimeError(
        "Authentication failed even with Chrome cookies. "
        "Please make sure you're logged in to app.grayswan.ai in Chrome, "
        "then try again."
    )


async def reauthenticate(
    playwright: Playwright, old_context: BrowserContext
) -> BrowserContext:
    """Re-authenticate mid-scrape when session expires."""
    logger.warning("Session expired. Extracting fresh cookies from Chrome...")
    try:
        await old_context.browser.close()
    except Exception:
        pass

    return await ensure_authenticated(playwright, force_reauth=True)
