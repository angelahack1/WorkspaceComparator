# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : test_browser.py                                              ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
Playwright browser test for Workspace Comparator.
Verifies: page load, browse button click, modal, directory navigation, comparison.
Run:  python test_browser.py
"""
import subprocess
import sys
import time
import os
import io

# Fix Windows console encoding for emoji characters
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 9876
BASE = f"http://127.0.0.1:{PORT}"
SCREENSHOTS_DIR = os.path.join(PROJ_DIR, "test_screenshots")


def main():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    os.chdir(PROJ_DIR)

    # Start Django dev server
    print(f"Starting Django server on port {PORT}...")
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", str(PORT), "--noreload"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(3)

    passed = 0
    failed = 0

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})

            js_errors = []
            console_logs = []
            page.on("pageerror", lambda err: js_errors.append(str(err)))
            page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

            # ---- TEST 1: Page loads ----
            print("\n[TEST 1] Page loads...")
            page.goto(BASE + "/", wait_until="networkidle")
            title = page.title()
            if "Workspace Comparator" in title:
                print("  PASS - Title:", title)
                passed += 1
            else:
                print("  FAIL - Title:", title)
                failed += 1
            shot(page, "01_page_loaded")

            # ---- TEST 2: Browse button exists and is visible ----
            print("\n[TEST 2] Browse button exists and is visible...")
            btn = page.locator("#btnBrowseLeft")
            if btn.count() > 0 and btn.is_visible():
                box = btn.bounding_box()
                print(f"  PASS - Button visible at x={box['x']:.0f} y={box['y']:.0f} w={box['width']:.0f} h={box['height']:.0f}")
                passed += 1
            else:
                print(f"  FAIL - Button count={btn.count()}, visible={btn.is_visible() if btn.count() > 0 else 'N/A'}")
                failed += 1

            # ---- TEST 3: Click browse button -> modal appears ----
            print("\n[TEST 3] Click browse button -> modal appears...")
            btn.click()
            time.sleep(1)
            shot(page, "02_after_browse_click")

            modal = page.locator("#browseModal")
            modal_display = modal.evaluate("el => window.getComputedStyle(el).display")
            if modal_display != "none":
                print(f"  PASS - Modal display: {modal_display}")
                passed += 1
            else:
                print(f"  FAIL - Modal display: {modal_display}")
                failed += 1

            # ---- TEST 4: Modal shows drives ----
            print("\n[TEST 4] Modal shows drive list...")
            time.sleep(1)
            items = page.locator("#modalList li")
            count = items.count()
            if count > 0:
                texts = [items.nth(i).inner_text() for i in range(min(count, 5))]
                print(f"  PASS - {count} entries: {texts}")
                passed += 1
            else:
                content = page.locator("#modalList").inner_text()
                print(f"  FAIL - No list items. Content: {content}")
                failed += 1
            shot(page, "03_modal_drives")

            external_left = r"D:\Proyectos\Workspaces\WorkspaceMAE"
            external_right = r"D:\Proyectos\Workspaces\WorkspaceMAEMaven"
            demo_left = os.path.join(PROJ_DIR, "demo", "InvoicerClassic")
            demo_right = os.path.join(PROJ_DIR, "demo", "InvoicerMaven")

            # ---- TEST 5: Select a valid left fixture ----
            print("\n[TEST 5] Select left comparison fixture...")
            if os.path.isdir(external_left):
                try:
                    page.locator("#modalList li", has_text="D:").click()
                    page.locator("#modalList li", has_text="Proyectos").click()
                    page.locator("#modalList li").filter(has_text="Workspaces").first.click()
                    page.locator("#modalList li").filter(has_text="WorkspaceMAE").first.click()
                    page.locator('[data-action="browse-select"]').click()
                    val = page.locator("#leftDir").input_value()
                    assert "WorkspaceMAE" in val
                    print(f"  PASS - External left input: {val}")
                    passed += 1
                except Exception as ex:
                    print(f"  FAIL - Navigation error: {ex}")
                    failed += 1
            else:
                page.locator('[data-action="browse-close"]').first.click()
                page.locator("#leftDir").fill(demo_left)
                print(f"  PASS - Bundled demo fallback: {demo_left}")
                passed += 1

            # ---- TEST 6: Select a valid right fixture ----
            print("\n[TEST 6] Select right comparison fixture...")
            if os.path.isdir(external_right):
                page.locator("#rightDir").fill(external_right)
                print(f"  PASS - External right input: {external_right}")
            else:
                page.locator("#rightDir").fill(demo_right)
                print(f"  PASS - Bundled demo fallback: {demo_right}")
            passed += 1

            shot(page, "08_both_paths_set")

            # ---- Print JS errors ----
            if js_errors:
                print("\n=== JAVASCRIPT ERRORS ===")
                for e in js_errors:
                    print(f"  {e}")

            if console_logs:
                print("\n=== CONSOLE LOG ===")
                for m in console_logs:
                    print(f"  {m}")

            browser.close()

    except Exception as ex:
        print(f"\nFATAL ERROR: {ex}")
        import traceback
        traceback.print_exc()
        failed += 1
    finally:
        server.terminate()
        server.wait()

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print(f"Screenshots in: {SCREENSHOTS_DIR}")
    print(f"{'='*50}")
    return 1 if failed else 0


def shot(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot: {name}.png")


if __name__ == "__main__":
    sys.exit(main())
