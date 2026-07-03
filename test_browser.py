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

            # ---- TEST 5: Navigate to D:\Proyectos\Workspaces ----
            print("\n[TEST 5] Navigate D: -> Proyectos -> Workspaces...")
            try:
                page.locator("#modalList li", has_text="D:").click()
                time.sleep(1)
                shot(page, "04_d_drive")

                page.locator("#modalList li", has_text="Proyectos").click()
                time.sleep(1)
                shot(page, "05_proyectos")

                page.locator("#modalList li").filter(has_text="Workspaces").first.click()
                time.sleep(1)
                shot(page, "06_workspaces")

                mae = page.locator("#modalList li").filter(has_text="WorkspaceMAE").first
                mae2check = page.locator("#modalList li", has_text="WorkspaceMAE")
                if mae2check.count() > 0:
                    print("  PASS - WorkspaceMAE found")
                    passed += 1

                    # Click WorkspaceMAE then Select
                    mae.click()
                    time.sleep(0.5)
                    shot(page, "07_inside_mae")

                    page.locator('[data-action="browse-select"]').click()
                    time.sleep(0.5)

                    val = page.locator("#leftDir").input_value()
                    if "WorkspaceMAE" in val:
                        print(f"  PASS - Left input: {val}")
                        passed += 1
                    else:
                        print(f"  FAIL - Left input: {val}")
                        failed += 1
                else:
                    print("  FAIL - WorkspaceMAE not found")
                    failed += 1
            except Exception as ex:
                print(f"  FAIL - Navigation error: {ex}")
                failed += 1
                shot(page, "05_nav_error")

            # ---- TEST 6: Right side browse ----
            print("\n[TEST 6] Browse right side for WorkspaceMAEMaven...")
            try:
                page.locator("#btnBrowseRight").click()
                time.sleep(1)
                page.locator("#modalList li", has_text="D:").click()
                time.sleep(0.5)
                page.locator("#modalList li", has_text="Proyectos").click()
                time.sleep(0.5)
                page.locator("#modalList li").filter(has_text="Workspaces").first.click()
                time.sleep(0.5)
                page.locator("#modalList li").filter(has_text="WorkspaceMAEMaven").first.click()
                time.sleep(0.5)
                page.locator('[data-action="browse-select"]').click()
                time.sleep(0.5)
                val = page.locator("#rightDir").input_value()
                if "WorkspaceMAEMaven" in val:
                    print(f"  PASS - Right input: {val}")
                    passed += 1
                else:
                    print(f"  FAIL - Right input: {val}")
                    failed += 1
            except Exception as ex:
                print(f"  FAIL - {ex}")
                failed += 1

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
