"""
smoke_test_browser.py — confirm Playwright can drive your system Chrome and
fill the mock portal, independent of the Flask app.

Run the Flask app first (so /portal is served):
    .venv/bin/python app.py
Then in another terminal:
    .venv/bin/python smoke_test_browser.py

Expected: a Chrome window opens, types into the SkyClaim form field-by-field,
pauses ~2s at the submit button, clicks submit, prints the SKY-XXXXXX reference.
"""

import sys
import time
from playwright.sync_api import sync_playwright

PORTAL_URL = "http://localhost:5000/portal"

SAMPLE = {
    "#passenger_name": ("type", "Jane Doe"),
    "#flight_number":  ("type", "NW482"),
    "#route":          ("type", "LHR → ATH"),
    "#flight_date":    ("fill", "2026-03-14"),
    "#delay_reason":   ("select", "technical"),
    "#expense_total":  ("fill", "182.50"),
    "#expense_description": ("type", "Hotel, dinner, breakfast, airport taxi"),
    "#claim_narrative": ("type",
        "Flight NW482 was cancelled the night before departure due to a technical "
        "fault; rebooked ~16h later. Claiming EU261 compensation plus overnight expenses."),
}


def main():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=False, slow_mo=350)
        except Exception as e:
            print("FAILED to launch system Chrome:", e)
            print("Is Google Chrome installed? Try: playwright install chromium")
            sys.exit(1)

        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded")
        except Exception as e:
            print(f"FAILED to open {PORTAL_URL}: {e}")
            print("Is the Flask app running?  .venv/bin/python app.py")
            browser.close()
            sys.exit(1)

        page.bring_to_front()

        for selector, (kind, value) in SAMPLE.items():
            loc = page.locator(selector)
            loc.scroll_into_view_if_needed()
            if kind == "select":
                page.select_option(selector, value)
            elif kind == "fill":
                loc.click(); loc.fill(value)
            else:
                loc.click(); page.type(selector, value, delay=45)

        page.locator("#submit-claim").scroll_into_view_if_needed()
        print("Form filled — pausing at submit for 2s (this is the approval gate)…")
        time.sleep(2)

        page.locator("#submit-claim").click()
        page.wait_for_selector("#reference-number", timeout=15000)
        ref = page.inner_text("#reference-number").strip()
        print("SUCCESS — portal returned reference:", ref)
        time.sleep(2)
        browser.close()


if __name__ == "__main__":
    main()
