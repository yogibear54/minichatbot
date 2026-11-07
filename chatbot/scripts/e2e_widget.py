"""E2E: drive the chatbot widget on the real site (launcher -> chat -> Show me -> highlight)."""
import sys
from playwright.sync_api import sync_playwright

BOT = "http://localhost:8010"
SITE = "http://localhost:8081"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "What services do you offer?"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(SITE + "/", wait_until="networkidle")
    page.wait_for_timeout(1500)

    # 1) launcher injected?
    assert page.locator(".cb-launcher").count() == 1, "launcher missing"
    page.click(".cb-launcher")
    page.wait_for_timeout(800)
    assert "open" in (page.locator(".cb-frame").get_attribute("class") or ""), "iframe did not open"

    # 2) type + send a question inside the (cross-origin) iframe
    frame = page.frame_locator(".cb-frame")
    frame.locator("#q").fill(QUESTION)
    frame.locator("#composer button").click()

    # 3) wait for the answer + citation chips (guard -> stream -> citations)
    frame.locator(".cb-source a").first.wait_for(timeout=45000)
    chips = frame.locator(".cb-source a")
    print("citation chips:")
    for i in range(chips.count()):
        c = chips.nth(i)
        print(f"  [{c.text_content().strip()[:60]}] -> {c.get_attribute('href')}")

    answer_len = frame.locator(".cb-msg.bot").first.text_content()
    print("answer chars:", len(answer_len or ""))

    # 4) Show me -> GUIDE -> parent scrolls/highlights (same page) or navigates (other page)
    showme = frame.locator(".cb-showme")
    if showme.count():
        showme.first.click()
        page.wait_for_timeout(2500)
        print("url after Show me:", page.url.split("#")[0])
        marks = page.evaluate("document.querySelectorAll('.cb-guide-mark').length")
        hl = page.evaluate("window.CSS && CSS.highlights ? Array.from(CSS.highlights.keys()) : []")
        print("mark fallbacks:", marks, "| highlight registries:", hl)
        print("pending cross-page guide:", page.evaluate(
            "sessionStorage.getItem('cb-pending-guide') ? 'yes (cross-page nav)' : 'no (same-page)'"))
    else:
        print("no Show me button (rescued citation or bridge off)")

    page.screenshot(path="/tmp/lotus-e2e.png", full_page=False)
    print("screenshot: /tmp/lotus-e2e.png")
    b.close()
