from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from playwright.async_api import Page

from ..config import PAGE_LOAD_TIMEOUT
from ..models import (
    Message,
    MessageRole,
    ReasoningBlock,
    Submission,
    SubmissionListItem,
    SubmissionStatus,
    ToolCall,
)

logger = logging.getLogger("grayswan")


async def scrape_submission_detail(
    page: Page, item: SubmissionListItem
) -> Submission:
    """Navigate to a submission detail page and extract all data."""
    t0 = time.monotonic()
    logger.info(f"Scraping detail: {item.submission_id} ({item.model_name})")

    url = item.detail_url
    if not url.startswith("http"):
        url = f"https://app.grayswan.ai{url}"

    await page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
    t1 = time.monotonic()
    logger.debug(f"  page.goto: {t1-t0:.1f}s")

    # Wait for messages container to appear (this is the critical content)
    try:
        await page.locator("#messagesContainer").wait_for(state="attached", timeout=5000)
    except Exception:
        await page.wait_for_timeout(500)
    t2 = time.monotonic()
    logger.debug(f"  wait for container: {t2-t1:.1f}s")

    # Run breadcrumb + status extraction in parallel (both are fast DOM reads)
    wave, behavior_name, model_name = await _extract_breadcrumb(page)
    if not model_name:
        model_name = item.model_name
    if not wave:
        wave = ""

    status = await _extract_status(page)
    if status == SubmissionStatus.UNKNOWN:
        status = item.status
    t3 = time.monotonic()
    logger.debug(f"  breadcrumb+status: {t3-t2:.1f}s")

    # Extract behavior criteria from the sheet/drawer
    behavior_criteria = await _extract_behavior_criteria(page)
    t4 = time.monotonic()
    logger.debug(f"  behavior criteria: {t4-t3:.1f}s")

    # Expand only message-specific collapsed sections before extracting conversation
    await _expand_message_collapses(page)
    t5 = time.monotonic()
    logger.debug(f"  expand collapses: {t5-t4:.1f}s")

    # Extract conversation messages
    conversation = await _extract_conversation(page)
    t6 = time.monotonic()
    logger.debug(f"  conversation: {t6-t5:.1f}s")

    # Get timestamp from the page if available
    timestamp = await _extract_timestamp(page) or item.timestamp_raw

    logger.info(f"  Total detail scrape: {time.monotonic()-t0:.1f}s ({len(conversation)} messages)")

    return Submission(
        submission_id=item.submission_id,
        chat_id=item.chat_id,
        arena=item.arena,
        model_name=model_name,
        behavior_name=behavior_name or item.behavior_snippet,
        behavior_criteria=behavior_criteria,
        wave=wave,
        timestamp_raw=timestamp,
        status=status,
        conversation=conversation,
        detail_url=item.detail_url,
        scraped_at=datetime.utcnow(),
    )


async def _extract_breadcrumb(page: Page) -> tuple[str, str, str]:
    """Extract wave, behavior name, and model name from the breadcrumb button.

    Uses data-testid="challengeConfigPanelButton" which contains 3 spans:
    span[0] = Wave, span[1] = Behavior, span[2] = Model
    """
    wave = ""
    behavior = ""
    model = ""

    try:
        btn = page.locator('[data-testid="challengeConfigPanelButton"]')
        if await btn.count() > 0:
            spans = btn.locator("span")
            count = await spans.count()
            if count >= 1:
                wave = (await spans.nth(0).text_content() or "").strip()
            if count >= 2:
                behavior = (await spans.nth(1).text_content() or "").strip()
            if count >= 3:
                model = (await spans.nth(2).text_content() or "").strip()
    except Exception as e:
        logger.warning(f"Breadcrumb extraction failed: {e}")

    return wave, behavior, model


async def _extract_behavior_criteria(page: Page) -> str:
    """Click 'Behavior Criteria' button and extract from the sheet/drawer."""
    try:
        # Click the Behavior Criteria button
        btn = page.get_by_text("Behavior Criteria")
        if await btn.count() == 0:
            logger.debug("No 'Behavior Criteria' button found")
            return ""

        await btn.first.click()

        # Wait for the sheet/drawer to appear (reduced timeout)
        sheet = page.locator('[data-slot="sheet-content"][role="dialog"]')
        try:
            await sheet.wait_for(state="visible", timeout=1500)
        except Exception:
            logger.debug("Behavior criteria sheet did not appear")
            await page.keyboard.press("Escape")
            return ""

        # Extract title, description, and criteria text concurrently
        title = ""
        title_el = sheet.locator('[data-slot="sheet-title"]')
        if await title_el.count() > 0:
            title = (await title_el.text_content() or "").strip()

        desc = ""
        desc_el = sheet.locator('[data-slot="sheet-description"]')
        if await desc_el.count() > 0:
            desc = (await desc_el.text_content() or "").strip()

        # Extract criteria content — try specific panel first, then fallback
        criteria_text = ""
        criterion_el = sheet.locator("[id^='criterion-panel'] .prose")
        if await criterion_el.count() > 0:
            criteria_text = (await criterion_el.first.text_content() or "").strip()
        else:
            prose_el = sheet.locator(".prose")
            if await prose_el.count() > 0:
                criteria_text = (await prose_el.first.text_content() or "").strip()
            else:
                scroll_el = sheet.locator('[data-slot="scroll-area"]')
                if await scroll_el.count() > 0:
                    criteria_text = (await scroll_el.text_content() or "").strip()

        # Combine parts
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if desc:
            parts.append(f"Description: {desc}")
        if criteria_text:
            parts.append(criteria_text)
        full_text = "\n\n".join(parts) if parts else ""

        # Close the sheet immediately
        await page.keyboard.press("Escape")
        return full_text

    except Exception as e:
        logger.warning(f"Failed to extract behavior criteria: {e}")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return ""


async def _extract_status(page: Page) -> SubmissionStatus:
    """Extract the submission status from the detail page."""
    try:
        if await page.get_by_text("Break Successful!").count() > 0:
            return SubmissionStatus.SUCCESS
        if await page.get_by_text("Submission Successful").count() > 0:
            return SubmissionStatus.SUCCESS
        if await page.get_by_text("Break Failed").count() > 0:
            return SubmissionStatus.FAILURE
        if await page.get_by_text("Submission Failed").count() > 0:
            return SubmissionStatus.FAILURE
    except Exception:
        pass
    return SubmissionStatus.UNKNOWN


async def _expand_message_collapses(page: Page) -> None:
    """Expand collapsed content sections ONLY within the messages area.

    Expands:
    - <details> elements (reasoning/thinking blocks)
    - "Show context" buttons (truncated tool message content)
    - aria-expanded='false' buttons
    """
    container = page.locator("#messagesContainer")
    if await container.count() == 0:
        return

    # 1. Expand closed <details> elements (reasoning/thinking toggles)
    # 2. Click "Show context" buttons (expand truncated tool message content)
    # 3. Expand aria-expanded buttons
    selectors = [
        "details:not([open]) summary",
        "button:has(span:text('Show context'))",
        "button[aria-expanded='false']",
    ]
    for sel in selectors:
        try:
            elements = container.locator(sel)
            count = await elements.count()
            if count > 50:
                logger.debug(f"Skipping expand: {count} elements for '{sel}' (too many)")
                continue
            for i in range(count):
                try:
                    el = elements.nth(i)
                    if await el.is_visible():
                        await el.click()
                        await page.wait_for_timeout(100)
                except Exception:
                    continue
        except Exception:
            continue


async def _extract_conversation(page: Page) -> list[Message]:
    """Extract all messages from the chat using data-testid selectors.

    DOM structure (from inspection):
    - User messages: [data-testid="userMessageHoverArea"] containing
      [data-testid="userMessage"] for regular messages, or
      [data-testid="toolMessage"] for tool call results
    - Assistant messages: [data-testid="assistantMessage"] — the response text.
      Reasoning blocks are in a sibling <details> element (NOT inside
      assistantMessage), both sharing a common parent div.
    """
    messages: list[Message] = []

    container = page.locator("#messagesContainer")
    if await container.count() == 0:
        logger.warning("No #messagesContainer found — trying fallback")
        return await _extract_conversation_fallback(page)

    # Select all message-level elements in DOM order, including toolMessage
    all_msg_elements = container.locator(
        '[data-testid="userMessageHoverArea"], '
        '[data-testid="assistantMessage"], '
        '[data-testid="toolMessage"]'
    )
    count = await all_msg_elements.count()
    logger.debug(f"Found {count} message elements in container")

    if count == 0:
        return await _extract_messages_separately(page)

    for i in range(count):
        el = all_msg_elements.nth(i)
        try:
            testid = await el.get_attribute("data-testid")

            if testid == "userMessageHoverArea":
                # Check if this contains a toolMessage (tool call result)
                tool_msg = el.locator('[data-testid="toolMessage"]')
                if await tool_msg.count() > 0:
                    # This is a tool call result, not a user message — skip here,
                    # it will be picked up by the toolMessage selector separately
                    continue

                # Regular user message
                content_el = el.locator('[data-testid="userMessage"]')
                if await content_el.count() > 0:
                    content = (await content_el.text_content() or "").strip()
                else:
                    content = (await el.text_content() or "").strip()

                if content:
                    messages.append(Message(
                        role=MessageRole.USER,
                        content=content,
                        order=len(messages),
                    ))

            elif testid == "toolMessage":
                # Tool call result — extract the full content
                content = (await el.text_content() or "").strip()
                tool_name = await _extract_tool_name_from_tool_message(el)

                if content:
                    messages.append(Message(
                        role=MessageRole.TOOL,
                        content=content,
                        tool_calls=[ToolCall(
                            tool_name=tool_name,
                            arguments="",
                            result=content,
                        )] if tool_name else [],
                        order=len(messages),
                    ))

            elif testid == "assistantMessage":
                content = (await el.text_content() or "").strip()

                # Extract reasoning from sibling <details> in the parent container.
                # The reasoning block is a sibling of assistantMessage, both inside
                # a common parent div (class="text-ellipses...").
                reasoning = await _extract_reasoning_from_parent(el)

                # Extract any tool call invocations from the assistant message
                tool_calls = await _extract_tool_calls(el)

                if content:
                    messages.append(Message(
                        role=MessageRole.ASSISTANT,
                        content=content,
                        tool_calls=tool_calls,
                        reasoning=reasoning,
                        order=len(messages),
                    ))

        except Exception as e:
            logger.warning(f"Failed to parse message {i}: {e}")
            continue

    return messages


async def _extract_messages_separately(page: Page) -> list[Message]:
    """Extract user and assistant messages separately and interleave them."""
    messages: list[Message] = []

    user_msgs = page.locator('[data-testid="userMessageHoverArea"]')
    asst_msgs = page.locator('[data-testid="assistantMessage"]')
    tool_msgs = page.locator('[data-testid="toolMessage"]')

    user_count = await user_msgs.count()
    asst_count = await asst_msgs.count()
    tool_count = await tool_msgs.count()

    logger.debug(
        f"Separate extraction: {user_count} user, {asst_count} assistant, "
        f"{tool_count} tool messages"
    )

    # Interleave: typically user first, then assistant, alternating
    max_count = max(user_count, asst_count)
    for i in range(max_count):
        if i < user_count:
            try:
                hover_el = user_msgs.nth(i)
                # Skip if this contains a toolMessage (handled separately)
                has_tool = await hover_el.locator('[data-testid="toolMessage"]').count() > 0
                if not has_tool:
                    content_el = hover_el.locator('[data-testid="userMessage"]')
                    if await content_el.count() > 0:
                        content = (await content_el.text_content() or "").strip()
                    else:
                        content = (await hover_el.text_content() or "").strip()
                    if content:
                        messages.append(Message(
                            role=MessageRole.USER,
                            content=content,
                            order=len(messages),
                        ))
            except Exception as e:
                logger.warning(f"Failed to extract user message {i}: {e}")

        if i < asst_count:
            try:
                asst_el = asst_msgs.nth(i)
                content = (await asst_el.text_content() or "").strip()
                tool_calls = await _extract_tool_calls(asst_el)
                reasoning = await _extract_reasoning_from_parent(asst_el)
                if content:
                    messages.append(Message(
                        role=MessageRole.ASSISTANT,
                        content=content,
                        tool_calls=tool_calls,
                        reasoning=reasoning,
                        order=len(messages),
                    ))
            except Exception as e:
                logger.warning(f"Failed to extract assistant message {i}: {e}")

    # Also extract any tool messages
    for i in range(tool_count):
        try:
            content = (await tool_msgs.nth(i).text_content() or "").strip()
            tool_name = await _extract_tool_name_from_tool_message(tool_msgs.nth(i))
            if content:
                messages.append(Message(
                    role=MessageRole.TOOL,
                    content=content,
                    tool_calls=[ToolCall(
                        tool_name=tool_name,
                        arguments="",
                        result=content,
                    )] if tool_name else [],
                    order=len(messages),
                ))
        except Exception as e:
            logger.warning(f"Failed to extract tool message {i}: {e}")

    return messages


async def _extract_conversation_fallback(page: Page) -> list[Message]:
    """Last-resort fallback: grab the main content area text."""
    messages: list[Message] = []
    try:
        # Try to get user messages by testid even without container
        user_msgs = page.locator('[data-testid="userMessage"]')
        if await user_msgs.count() > 0:
            for i in range(await user_msgs.count()):
                text = (await user_msgs.nth(i).text_content() or "").strip()
                if text:
                    messages.append(Message(
                        role=MessageRole.USER,
                        content=text,
                        order=len(messages),
                    ))

        asst_msgs = page.locator('[data-testid="assistantMessage"]')
        if await asst_msgs.count() > 0:
            for i in range(await asst_msgs.count()):
                text = (await asst_msgs.nth(i).text_content() or "").strip()
                if text:
                    messages.append(Message(
                        role=MessageRole.ASSISTANT,
                        content=text,
                        order=len(messages),
                    ))

    except Exception as e:
        logger.warning(f"Conversation fallback extraction failed: {e}")

    return messages


async def _extract_tool_name_from_tool_message(el) -> str:
    """Extract tool/function name from a toolMessage or its parent context.

    The function name (e.g. get_recent_texts()) appears in a <code> element
    inside a sibling <span> above the toolMessage div in the DOM, within
    the wrapping userMessageHoverArea.
    """
    try:
        # Navigate up to the wrapping userMessageHoverArea
        hover_area = el.locator('xpath=ancestor::div[@data-testid="userMessageHoverArea"]')
        if await hover_area.count() > 0:
            code_els = hover_area.locator("code")
            count = await code_els.count()
            for i in range(count):
                text = (await code_els.nth(i).text_content() or "").strip()
                if re.match(r"^[a-zA-Z_]\w*\(.*\)$", text):
                    return text.rstrip("()")

        # Fallback: try walking up parent levels
        for levels in range(1, 6):
            parent = el.locator("xpath=" + "/".join([".."] * levels))
            if await parent.count() > 0:
                code_els = parent.locator("code")
                count = await code_els.count()
                for i in range(count):
                    text = (await code_els.nth(i).text_content() or "").strip()
                    if re.match(r"^[a-zA-Z_]\w*\(.*\)$", text):
                        return text.rstrip("()")
    except Exception:
        pass
    return "unknown"


async def _extract_tool_calls(el) -> list[ToolCall]:
    """Extract tool call invocations from within an assistant message element.

    These appear when the assistant decides to call a tool — the tool name
    and arguments may be displayed inline.
    """
    tool_calls: list[ToolCall] = []
    try:
        tc_selectors = [
            "[data-testid*='toolCall']",
            "[data-testid*='tool-call']",
            "details:has(summary:has-text('Tool'))",
        ]
        for sel in tc_selectors:
            elements = el.locator(sel)
            count = await elements.count()
            for i in range(count):
                tc_el = elements.nth(i)
                tc_text = (await tc_el.text_content() or "").strip()
                if tc_text:
                    tool_name = "unknown"
                    name_match = re.search(r"(?:Tool|Function):\s*(\w+)", tc_text)
                    if name_match:
                        tool_name = name_match.group(1)
                    tool_calls.append(ToolCall(
                        tool_name=tool_name,
                        arguments=tc_text,
                    ))
    except Exception:
        pass
    return tool_calls


async def _extract_reasoning_from_parent(assistant_el) -> list[ReasoningBlock]:
    """Extract reasoning/thinking blocks from sibling elements of assistantMessage.

    In the Gray Swan UI, reasoning is a <details> element with
    <summary>Reasoning</summary> that sits as a SIBLING of the
    [data-testid="assistantMessage"] div, both sharing a common parent.

    DOM structure:
      <div class="text-ellipses...">
        <header>Model Name | timestamp</header>
        <div class="not-prose...">          ← reasoning wrapper
          <details open="">
            <summary>Reasoning <svg.../></summary>
            <div class="border-border...">  ← reasoning content
              <p>reasoning text...</p>
            </div>
          </details>
        </div>
        <div data-testid="assistantMessage">  ← the response
          <p>response text...</p>
        </div>
      </div>
    """
    blocks: list[ReasoningBlock] = []
    try:
        # Navigate to the parent container that holds both reasoning and assistantMessage
        parent = assistant_el.locator("xpath=..")
        if await parent.count() == 0:
            return blocks

        # Look for <details> elements containing "Reasoning" or "Thinking" in summary
        for keyword in ["Reasoning", "Thinking"]:
            details = parent.locator(f"details:has(summary:has-text('{keyword}'))")
            count = await details.count()
            for i in range(count):
                detail_el = details.nth(i)
                # Extract the content div (skip the summary text)
                content_div = detail_el.locator("div")
                if await content_div.count() > 0:
                    # Get the prose content div (the actual reasoning text)
                    prose = content_div.locator(".prose")
                    if await prose.count() > 0:
                        text = (await prose.first.text_content() or "").strip()
                    else:
                        text = (await content_div.first.text_content() or "").strip()
                else:
                    text = (await detail_el.text_content() or "").strip()

                # Remove the summary label from the beginning if present
                for label in ["Reasoning", "Thinking"]:
                    if text.startswith(label):
                        text = text[len(label):].strip()

                if text:
                    blocks.append(ReasoningBlock(content=text))
    except Exception as e:
        logger.debug(f"Reasoning extraction failed: {e}")
    return blocks


async def _extract_timestamp(page: Page) -> str | None:
    """Try to extract timestamp from the detail page."""
    try:
        # Look for timestamp near user messages (group-hover elements)
        time_el = page.locator("time, [datetime]")
        if await time_el.count() > 0:
            return (await time_el.first.text_content() or "").strip() or None

        # Look for small text near messages that looks like a time
        small_els = page.locator("#messagesContainer small")
        if await small_els.count() > 0:
            text = (await small_els.first.text_content() or "").strip()
            if text:
                return text
    except Exception:
        pass
    return None
