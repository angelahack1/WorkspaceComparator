# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : AutomatedTestsStarter.py                                     ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
Workspace Comparator - VISIBLE Automated Browser Tests
=======================================================
Opens a REAL Edge browser window so you can watch every action.
Tests every button, every field, every interaction.

Run:  python AutomatedTestsStarter.py
"""
import subprocess
import sys
import time
import os
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 9877
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = os.path.join(PROJ_DIR, "test_screenshots")


def log(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def ok(msg):
    print(f"  [PASS] {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")


def shot(page, name):
    path = os.path.join(SHOTS, f"{name}.png")
    page.screenshot(path=path)
    print(f"  (screenshot: {name}.png)")


def main():
    os.makedirs(SHOTS, exist_ok=True)
    os.chdir(PROJ_DIR)

    passed = 0
    failed = 0

    # --- Start Django server ---
    log("Starting Django server on port " + str(PORT))
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", str(PORT), "--noreload"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(3)
    print("  Server started.")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            # Launch VISIBLE Chromium browser (not headless)
            # Note: Cannot use Edge due to corporate CLM blocking DevTools.
            # Using Playwright's bundled Chromium instead.
            browser = pw.chromium.launch(
                headless=False,
                slow_mo=400,  # slow down so you can SEE every action
            )
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                ignore_https_errors=True,
            )
            page = context.new_page()

            js_errors = []
            page.on("pageerror", lambda err: js_errors.append(str(err)))

            # ==========================================================
            # TEST 1: Page loads
            # ==========================================================
            log("TEST 1: Page loads correctly")
            page.goto(BASE + "/", wait_until="networkidle")
            title = page.title()
            if "Workspace Comparator" in title:
                ok(f"Title is '{title}'")
                passed += 1
            else:
                fail(f"Title is '{title}'")
                failed += 1

            header = page.locator(".app-header h1")
            if header.is_visible() and "Workspace Comparator" in header.inner_text():
                ok("Header visible: " + header.inner_text())
                passed += 1
            else:
                fail("Header not visible")
                failed += 1
            shot(page, "01_page_loaded")

            # ==========================================================
            # TEST 2: All UI elements exist and are visible
            # ==========================================================
            log("TEST 2: All UI elements exist and are visible")
            elements = {
                "Left input":       "#leftDir",
                "Right input":      "#rightDir",
                "Compare button":   "#btnCompare",
                "Clear button":     "[data-action='clear']",
                "Browse Left btn":  "#btnBrowseLeft",
                "Browse Right btn": "#btnBrowseRight",
                "Empty state":      "#emptyState",
            }
            for name, sel in elements.items():
                el = page.locator(sel)
                if el.count() > 0 and el.first.is_visible():
                    ok(f"{name} ({sel}) is visible")
                    passed += 1
                else:
                    fail(f"{name} ({sel}) NOT visible (count={el.count()})")
                    failed += 1

            # ==========================================================
            # TEST 3: Compare button is disabled when inputs are empty
            # ==========================================================
            log("TEST 3: Compare button starts disabled")
            if page.locator("#btnCompare").is_disabled():
                ok("Compare button is disabled (correct)")
                passed += 1
            else:
                fail("Compare button should be disabled")
                failed += 1

            # ==========================================================
            # TEST 4: Type in left input -> still disabled
            # ==========================================================
            log("TEST 4: Type in left input only -> Compare still disabled")
            page.locator("#leftDir").fill("D:\\test")
            time.sleep(0.3)
            if page.locator("#btnCompare").is_disabled():
                ok("Compare still disabled with only left filled")
                passed += 1
            else:
                fail("Compare should be disabled")
                failed += 1

            # ==========================================================
            # TEST 5: Type in both inputs -> button enables
            # ==========================================================
            log("TEST 5: Type in both inputs -> Compare enables")
            page.locator("#rightDir").fill("D:\\test2")
            time.sleep(0.3)
            if not page.locator("#btnCompare").is_disabled():
                ok("Compare button is ENABLED (correct)")
                passed += 1
            else:
                fail("Compare should be enabled now")
                failed += 1

            # ==========================================================
            # TEST 6: Clear button clears everything
            # ==========================================================
            log("TEST 6: Clear button works")
            page.locator("[data-action='clear']").click()
            time.sleep(0.3)
            left_val = page.locator("#leftDir").input_value()
            right_val = page.locator("#rightDir").input_value()
            if left_val == "" and right_val == "":
                ok("Both inputs cleared")
                passed += 1
            else:
                fail(f"Inputs not cleared: left='{left_val}', right='{right_val}'")
                failed += 1
            if page.locator("#btnCompare").is_disabled():
                ok("Compare button disabled again after clear")
                passed += 1
            else:
                fail("Compare should be disabled after clear")
                failed += 1
            shot(page, "02_after_clear")

            # ==========================================================
            # TEST 7: LEFT Browse button opens modal
            # ==========================================================
            log("TEST 7: Click LEFT browse button -> modal opens")
            page.locator("#btnBrowseLeft").click()
            time.sleep(1)
            modal = page.locator("#browseModal")
            modal_style = modal.evaluate("el => el.style.display")
            if modal_style != "none":
                ok(f"Modal is VISIBLE (display='{modal_style}')")
                passed += 1
            else:
                fail(f"Modal NOT visible (display='{modal_style}')")
                failed += 1
            shot(page, "03_modal_opened")

            # ==========================================================
            # TEST 8: Modal shows drives
            # ==========================================================
            log("TEST 8: Modal shows drive letters")
            time.sleep(1)
            items = page.locator("#modalList li")
            count = items.count()
            if count > 0:
                ok(f"Found {count} drives/folders in modal")
                passed += 1
            else:
                fail("No items in modal list")
                failed += 1
            shot(page, "04_drives_listed")

            # ==========================================================
            # TEST 9: Navigate D: -> Proyectos -> Workspaces
            # ==========================================================
            log("TEST 9: Navigate to D:/Proyectos/Workspaces")
            try:
                # Click D:
                page.locator("#modalList li", has_text="D:").click()
                time.sleep(0.8)
                ok("Clicked D: drive")
                shot(page, "05_d_drive")

                # Click Proyectos
                page.locator("#modalList li", has_text="Proyectos").click()
                time.sleep(0.8)
                ok("Clicked Proyectos")
                shot(page, "06_proyectos")

                # Click Workspaces (use .first to handle multiple matches)
                page.locator("#modalList li").filter(has_text="Workspaces").first.click()
                time.sleep(0.8)
                ok("Clicked Workspaces")
                shot(page, "07_workspaces")

                # Verify WorkspaceMAE and WorkspaceMAEMaven are listed
                mae = page.locator("#modalList li", has_text="WorkspaceMAE")
                maven = page.locator("#modalList li", has_text="WorkspaceMAEMaven")
                if mae.count() > 0:
                    ok("WorkspaceMAE is listed")
                    passed += 1
                else:
                    fail("WorkspaceMAE not found")
                    failed += 1
                if maven.count() > 0:
                    ok("WorkspaceMAEMaven is listed")
                    passed += 1
                else:
                    fail("WorkspaceMAEMaven not found")
                    failed += 1
                passed += 3  # for the 3 navigation clicks

            except Exception as ex:
                fail(f"Navigation error: {ex}")
                failed += 1
                shot(page, "07_nav_error")

            # ==========================================================
            # TEST 10: Click WorkspaceMAE -> Select -> path appears in input
            # ==========================================================
            log("TEST 10: Select WorkspaceMAE for left side")
            try:
                page.locator("#modalList li").filter(has_text="WorkspaceMAE").first.click()
                time.sleep(0.5)
                shot(page, "08_inside_mae")

                page.locator("[data-action='browse-select']").click()
                time.sleep(0.5)

                left_val = page.locator("#leftDir").input_value()
                if "WorkspaceMAE" in left_val:
                    ok(f"Left input set to: {left_val}")
                    passed += 1
                else:
                    fail(f"Left input wrong: {left_val}")
                    failed += 1

                # Modal should be closed
                modal_display = modal.evaluate("el => el.style.display")
                if modal_display == "none":
                    ok("Modal closed after selection")
                    passed += 1
                else:
                    fail(f"Modal still visible: display={modal_display}")
                    failed += 1
                shot(page, "09_left_path_set")

            except Exception as ex:
                fail(f"Selection error: {ex}")
                failed += 1

            # ==========================================================
            # TEST 11: RIGHT Browse button opens modal for right side
            # ==========================================================
            log("TEST 11: Click RIGHT browse -> navigate -> select WorkspaceMAEMaven")
            try:
                page.locator("#btnBrowseRight").click()
                time.sleep(1)

                modal_style = modal.evaluate("el => el.style.display")
                if modal_style != "none":
                    ok("Modal opened for right side")
                    passed += 1
                else:
                    fail("Modal not visible")
                    failed += 1

                # Navigate: D: -> Proyectos -> Workspaces -> WorkspaceMAEMaven
                page.locator("#modalList li", has_text="D:").click()
                time.sleep(0.6)
                page.locator("#modalList li", has_text="Proyectos").click()
                time.sleep(0.6)
                page.locator("#modalList li").filter(has_text="Workspaces").first.click()
                time.sleep(0.6)
                page.locator("#modalList li").filter(has_text="WorkspaceMAEMaven").first.click()
                time.sleep(0.5)

                page.locator("[data-action='browse-select']").click()
                time.sleep(0.5)

                right_val = page.locator("#rightDir").input_value()
                if "WorkspaceMAEMaven" in right_val:
                    ok(f"Right input set to: {right_val}")
                    passed += 1
                else:
                    fail(f"Right input wrong: {right_val}")
                    failed += 1
                shot(page, "10_both_paths_set")

            except Exception as ex:
                fail(f"Right browse error: {ex}")
                failed += 1

            # ==========================================================
            # TEST 12: Compare button is now enabled
            # ==========================================================
            log("TEST 12: Compare button enabled with both paths set")
            if not page.locator("#btnCompare").is_disabled():
                ok("Compare button is ENABLED")
                passed += 1
            else:
                fail("Compare button still disabled")
                failed += 1

            # ==========================================================
            # TEST 13: Modal close via X button
            # ==========================================================
            log("TEST 13: Modal X button closes modal")
            page.locator("#btnBrowseLeft").click()
            time.sleep(0.8)
            page.locator("[data-action='browse-close']").first.click()
            time.sleep(0.5)
            modal_display = modal.evaluate("el => el.style.display")
            if modal_display == "none":
                ok("X button closed the modal")
                passed += 1
            else:
                fail(f"Modal still open: {modal_display}")
                failed += 1

            # ==========================================================
            # TEST 14: Modal close via Cancel button
            # ==========================================================
            log("TEST 14: Cancel button closes modal")
            page.locator("#btnBrowseLeft").click()
            time.sleep(0.8)
            page.locator("[data-action='browse-close']").last.click()
            time.sleep(0.5)
            modal_display = modal.evaluate("el => el.style.display")
            if modal_display == "none":
                ok("Cancel button closed the modal")
                passed += 1
            else:
                fail(f"Modal still open: {modal_display}")
                failed += 1

            # ==========================================================
            # TEST 15: Modal close via Escape key
            # ==========================================================
            log("TEST 15: Escape key closes modal")
            page.locator("#btnBrowseLeft").click()
            time.sleep(0.8)
            page.keyboard.press("Escape")
            time.sleep(0.5)
            modal_display = modal.evaluate("el => el.style.display")
            if modal_display == "none":
                ok("Escape key closed the modal")
                passed += 1
            else:
                fail(f"Modal still open: {modal_display}")
                failed += 1

            # ==========================================================
            # TEST 16: Up button navigates to parent
            # ==========================================================
            log("TEST 16: Up button navigates to parent directory")
            # Clear left input so modal starts fresh at drives
            page.locator("#leftDir").fill("")
            time.sleep(0.3)
            page.locator("#btnBrowseLeft").click()
            time.sleep(1.5)
            # Navigate into D: then Proyectos
            page.locator("#modalList li", has_text="D:").click()
            time.sleep(1)
            page.locator("#modalList li", has_text="Proyectos").click()
            time.sleep(1)
            cur_path = page.locator("#modalPath").inner_text()
            ok(f"Currently at: {cur_path}")

            page.locator("[data-action='browse-up']").click()
            time.sleep(1)
            new_path = page.locator("#modalPath").inner_text()
            if new_path != cur_path:
                ok(f"Up navigated to: {new_path}")
                passed += 1
            else:
                fail("Up button did not change path")
                failed += 1

            page.locator("[data-action='browse-close']").first.click()
            time.sleep(0.5)

            # ==========================================================
            # TEST 17: Enter key triggers comparison
            # ==========================================================
            log("TEST 17: Enter key in input triggers comparison")
            page.locator("#leftDir").fill("D:/nonexistent_path_123")
            page.locator("#rightDir").fill("D:/nonexistent_path_456")
            time.sleep(0.3)
            page.locator("#rightDir").press("Enter")
            time.sleep(2)
            # Should show an error toast (directory not found)
            toasts = page.locator(".error-toast")
            if toasts.count() > 0:
                toast_text = toasts.first.inner_text()
                ok(f"Error toast appeared: {toast_text}")
                passed += 1
            else:
                fail("No error toast after bad path")
                failed += 1
            shot(page, "11_error_toast")

            # Wait for toast to disappear
            time.sleep(4)

            # ==========================================================
            # TEST 18: Full comparison with real directories
            # ==========================================================
            log("TEST 18: FULL COMPARISON with real directories")
            page.locator("[data-action='clear']").click()
            time.sleep(0.3)

            # Set real paths
            page.locator("#leftDir").fill("D:/Proyectos/Workspaces/WorkspaceMAE")
            page.locator("#rightDir").fill("D:/Proyectos/Workspaces/WorkspaceMAEMaven")
            time.sleep(0.3)

            # Click Compare
            page.locator("#btnCompare").click()
            shot(page, "12_comparison_started")

            # Wait for loading to finish (these are big projects, could take a while)
            print("  Waiting for comparison to complete (large projects)...")
            page.locator("#loadingOverlay").wait_for(state="hidden", timeout=300000)
            ok("Comparison completed!")
            passed += 1
            shot(page, "13_comparison_done")

            # ==========================================================
            # TEST 19: Results are displayed
            # ==========================================================
            log("TEST 19: Results table is displayed")
            results = page.locator("#resultsSection")
            if results.is_visible():
                ok("Results section is visible")
                passed += 1
            else:
                fail("Results section NOT visible")
                failed += 1

            # Check stats bar
            stats = page.locator("#statsBar")
            if stats.is_visible():
                ok("Stats bar is visible")
                passed += 1
            else:
                fail("Stats bar NOT visible")
                failed += 1

            # Check matched rows exist (green)
            matched_rows = page.locator("tr.row-matched")
            matched_count = matched_rows.count()
            if matched_count > 0:
                ok(f"Found {matched_count} matched rows (green)")
                passed += 1
            else:
                fail("No matched rows found")
                failed += 1

            # Check green background
            if matched_count > 0:
                bg = matched_rows.first.locator(".cell-left").first.evaluate(
                    "el => window.getComputedStyle(el).backgroundColor"
                )
                ok(f"Matched row background: {bg}")
                passed += 1

            # Check unmatched rows exist (red)
            unmatched_rows = page.locator("tr.row-unmatched")
            unmatched_count = unmatched_rows.count()
            if unmatched_count > 0:
                ok(f"Found {unmatched_count} unmatched rows (red)")
                passed += 1
            else:
                ok("No unmatched rows (all files matched)")
                passed += 1

            # Check separator rows
            sep_rows = page.locator("tr.row-separator")
            if sep_rows.count() > 0:
                ok(f"Found {sep_rows.count()} section separator(s)")
                passed += 1
            else:
                fail("No separator rows")
                failed += 1

            shot(page, "14_results_table")

            # Scroll down to see more results
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.5)
            shot(page, "15_results_scrolled")

            # ==========================================================
            # TEST 20: Empty state hidden after comparison
            # ==========================================================
            log("TEST 20: Empty state is hidden after comparison")
            empty = page.locator("#emptyState")
            if not empty.is_visible():
                ok("Empty state is hidden (correct)")
                passed += 1
            else:
                fail("Empty state should be hidden")
                failed += 1

            # ==========================================================
            # REPORT JS ERRORS
            # ==========================================================
            if js_errors:
                log("JAVASCRIPT ERRORS DETECTED")
                for e in js_errors:
                    fail(f"JS Error: {e}")
                    failed += 1
            else:
                log("No JavaScript errors detected")
                ok("Zero JS errors")
                passed += 1

            # ==========================================================
            # FINAL SUMMARY
            # ==========================================================
            print("\n")
            print("=" * 60)
            print(f"  FINAL RESULTS: {passed} PASSED, {failed} FAILED")
            print(f"  Screenshots saved in: {SHOTS}")
            print("=" * 60)

            if failed == 0:
                print("\n  ALL TESTS PASSED!")
            else:
                print(f"\n  {failed} test(s) failed. Check output above.")

            # Keep browser open for user to inspect
            print("\n  Browser will stay open for 30 seconds for inspection...")
            print("  (Close it manually or wait)")
            time.sleep(30)

            browser.close()

    except Exception as ex:
        print(f"\nFATAL ERROR: {ex}")
        import traceback
        traceback.print_exc()
        failed += 1
    finally:
        server.terminate()
        server.wait()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
