#!/usr/bin/env python
"""Capture high-quality screenshots of the Flow demo UI for GitHub repo assets.

Axis Love theme only. 1440×900 viewport at 3× DPR for crisp rendering.

Screenshots
-----------
1. board.png      — Board with "Automation rules engine" (doing) hovered,
                      showing 3 dependency lines:
                      → child in todo (Discord & Telegram notifications)
                      → parent in review (API key roles)
                      → parent in done (Agent-first REST + MCP API)
2. ideas.png      — Ideas overlay with "Add new idea" card expanded, cursor in title.
3. settings.png   — Settings overlay scrolled to API Keys section with demo data.
"""
from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ASSETS.mkdir(exist_ok=True)

BASE = "http://localhost:8100"
VIEWPORT = {"width": 1440, "height": 900}
SCALE = 3  # high-DPI for crisp fonts and icons

# Task ID for "Automation rules engine" (the hover target)
RULES_TASK_ID = "flow_000010"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=SCALE,
        )

        # ---- 1. Board with dependency lines (hovered card) ----
        page = context.new_page()
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_timeout(1500)

        # Hover the "Automation rules engine" card
        card = page.locator(f".task-card[data-id='{RULES_TASK_ID}']")
        if card.count() > 0:
            card.first.hover()
            page.wait_for_timeout(1200)
        else:
            print(f"⚠ Card {RULES_TASK_ID} not found")
            sys.exit(1)

        page.screenshot(path=str(ASSETS / "board.png"), full_page=False)
        print("✓ board.png")

        # ---- 2. Ideas overlay with new idea expanded ----
        page.evaluate("openIdeas(true)")
        page.wait_for_selector("#ideasOverlay.active", timeout=5000)
        page.wait_for_timeout(2500)

        # Focus the title input inside the ideas iframe
        ideas_frame = page.frame_locator("#ideasFrame")
        title_input = ideas_frame.locator(".idea-card.is-editing .editor-title input")
        if title_input.count() > 0:
            title_input.first.click()
            page.wait_for_timeout(300)

        page.screenshot(path=str(ASSETS / "ideas.png"), full_page=False)
        print("✓ ideas.png")

        # ---- 3. Settings overlay scrolled to API Keys ----
        page.evaluate("closeIdeas()")
        page.wait_for_timeout(800)

        page.evaluate("openSettings()")
        page.wait_for_selector("#settingsOverlay.active", timeout=5000)
        page.wait_for_timeout(2500)

        # Scroll the settings iframe to the API Keys section
        settings_frame = page.frame_locator("#settingsFrame")
        api_keys_heading = settings_frame.locator("#api-keys")
        if api_keys_heading.count() > 0:
            api_keys_heading.scroll_into_view_if_needed()
            page.wait_for_timeout(800)

        page.screenshot(path=str(ASSETS / "settings.png"), full_page=False)
        print("✓ settings.png")

        browser.close()

    print(f"\nAll screenshots saved to {ASSETS}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())