"""Optional browser end-to-end check (not collected by pytest).

    # terminal 1
    uvicorn app.asgi:app --port 8765
    # terminal 2
    python tests/e2e_browser.py http://127.0.0.1:8765 [screenshot_dir]

Needs:  pip install playwright && playwright install chromium
Drives the real UI through the whole learning loop and fails on any console error
(which also catches Content-Security-Policy violations).
"""
import re
import sys
from fractions import Fraction
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None
errors: list[str] = []


def shot(page, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def fr(s):
    n, d = s.split("/")
    return Fraction(int(n), int(d))


def fmt(v):
    return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"


def run(page):
    page.goto(BASE)
    page.wait_for_selector("text=Fractions, understood.")
    shot(page, "1-home")

    # ---- adding: wrong answer by adding tops and bottoms -> targeted M01 -> fresh question
    page.click("button.skill:has-text('Adding fractions')")
    page.wait_for_selector(".prompt")
    prompt = page.inner_text(".prompt")
    a, b, c, d = map(int, re.findall(r"\d+", prompt)[:4])
    shot(page, "2-question")
    page.fill("#answer", f"{a + c}/{b + d}")
    page.click("text=Check my answer")
    page.wait_for_selector("text=Let’s look at your thinking")
    shot(page, "3-followup")
    page.click("label.opt:has-text('I added the two bottom numbers together')")
    page.click("button:has-text('Continue')")
    page.wait_for_selector("text=Possible pattern: Adding the bottom numbers directly")
    assert page.locator(".barrow").count() >= 5, "worked-example bars missing"
    page.click("summary:has-text('Why am I seeing this?')")
    assert page.locator("details li").count() >= 2
    assert "Experimental model" in page.inner_text("details"), "ML opinion not shown"
    shot(page, "4-repair")
    page.click("text=Try a fresh question")
    page.wait_for_selector("text=Fresh question")
    fresh = page.inner_text(".prompt")
    a, b, c, d = map(int, re.findall(r"\d+", fresh)[:4])
    shot(page, "5-fresh")
    page.fill("#answer", fmt(Fraction(a, b) + Fraction(c, d)))
    page.click("text=Check my answer")
    page.wait_for_selector("text=Nice work!")
    shot(page, "6-mastered")

    # ---- unreadable input is explained, not recorded
    page.click("text=Keep practising")
    page.wait_for_selector(".prompt")
    page.fill("#answer", "banana") if page.locator("#answer").count() else None
    if page.locator("#answer").count():
        page.click("text=Check my answer")
        page.wait_for_selector(".error:has-text('Please type a fraction')")

    # ---- comparing: radio choices
    page.click("text=Back to topics")
    page.click("button.skill:has-text('Comparing fractions')")
    page.wait_for_selector(".opts.big")
    page.locator(".opts.big .opt").first.click()
    page.click("text=Check my answer")
    page.wait_for_selector(".card")
    shot(page, "7-compare-result")

    # ---- progress
    page.click("#nav-progress")
    page.wait_for_selector("h1:has-text('My progress')")
    assert page.locator(".skillrow").count() == 5
    assert "Adding the bottom numbers directly" in page.inner_text("main")
    assert "null" not in page.inner_text("main").lower().split(), "stray 'null' rendered"
    shot(page, "8-progress")

    # ---- mobile layout
    page.set_viewport_size({"width": 375, "height": 740})
    page.click("#nav-home")
    page.wait_for_selector("text=Fractions, understood.")
    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    assert not overflow, "horizontal scroll on mobile"
    shot(page, "9-mobile-home")

    # ---- delete my data
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("#delete-data")
    page.wait_for_selector("text=Your data has been deleted.")
    shot(page, "10-deleted")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 900, "height": 900})
    # The deliberate unreadable-answer step makes the server return an expected 422.
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" and "status of 422" not in m.text else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        run(page)
    except Exception:
        shot(page, "FAILED")
        raise
    finally:
        browser.close()

if errors:
    print("CONSOLE ERRORS:", errors)
    sys.exit(1)
print("E2E OK: full loop, validation, compare, progress, mobile, delete")
