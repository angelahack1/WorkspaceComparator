#!/usr/bin/env python
# Visible Playwright hard-stone regression test for Workspace Comparator.
#
# This test creates a 210-file dataset, opens a real Chromium window, and
# proves that matched, unmatched, unsupported, excluded, dot-directory,
# extensionless, and "." / ".." alias rows are all visible in the table.
#
# Run:
#   python HardStoneVisiblePlaywrightTest.py
#
# Optional:
#   python HardStoneVisiblePlaywrightTest.py --hold-seconds 60
import argparse
import base64
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "test_screenshots" / "hard_stone_visible"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8"
    "z8BQDwAFgwJ/lz6L2wAAAABJRU5ErkJggg=="
)


def log(title: str) -> None:
    print()
    print("=" * 76)
    print("  " + title)
    print("=" * 76)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.25)
    return False


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def create_dataset(root: Path) -> dict:
    left = root / "left_project"
    right = root / "right_project"
    left.mkdir(parents=True)
    right.mkdir(parents=True)

    counts = {
        "matched": 0,
        "unmatched": 0,
        "ignored_per_side": 0,
        "disk_files": 0,
    }

    # 20 exact text matches.
    for i in range(20):
        rel = Path("src") / f"module_{i:03d}" / f"exact_{i:03d}.py"
        body = f"def value_{i}():\n    return {i}\n"
        write_text(left / rel, body)
        write_text(right / rel, body)
        counts["matched"] += 1
        counts["disk_files"] += 2

    # 5 exact binary matches.
    for i in range(5):
        rel = Path("assets") / f"icon_{i:03d}.png"
        data = PNG_1X1 + bytes([i])
        write_bytes(left / rel, data)
        write_bytes(right / rel, data)
        counts["matched"] += 1
        counts["disk_files"] += 2

    # 10 left-only and 10 right-only comparable files.  Use different
    # extensions so Phase 3b cannot content-match the artificial pairs.
    for i in range(10):
        write_text(left / "left_only" / f"left_only_{i:03d}.py", f"LEFT_ONLY = {i}\n")
        write_text(right / "right_only" / f"right_only_{i:03d}.rs", f"pub const RIGHT_ONLY_{i}: i32 = {i};\n")
        counts["unmatched"] += 2
        counts["disk_files"] += 2

    # 30 unsupported files per side.
    unsupported_exts = [".md", ".json", ".html", ".css", ".svg"]
    for i in range(30):
        ext = unsupported_exts[i % len(unsupported_exts)]
        rel = Path("docs") / f"ignored_unsupported_{i:03d}{ext}"
        write_text(left / rel, f"left unsupported {i}\n")
        write_text(right / rel, f"right unsupported {i}\n")
        counts["ignored_per_side"] += 1
        counts["disk_files"] += 2

    # 15 supported Java files ignored by file exclusion.
    for i in range(15):
        rel = Path("blocked") / f"visible_but_blocked_{i:03d}.blocked.java"
        body = f"public class Blocked{i:03d} {{}}\n"
        write_text(left / rel, body)
        write_text(right / rel, body)
        counts["ignored_per_side"] += 1
        counts["disk_files"] += 2

    # 10 supported Python files ignored by directory exclusion.
    for i in range(10):
        rel = Path("excluded_dir") / f"dir_excluded_{i:03d}.py"
        write_text(left / rel, f"print('left excluded dir {i}')\n")
        write_text(right / rel, f"print('right excluded dir {i}')\n")
        counts["ignored_per_side"] += 1
        counts["disk_files"] += 2

    # 8 files inside a dot-directory. They must still be visible.
    for i in range(8):
        rel = Path(".ghost") / f"hidden_visible_{i:03d}.txt"
        write_text(left / rel, f"left hidden {i}\n")
        write_text(right / rel, f"right hidden {i}\n")
        counts["ignored_per_side"] += 1
        counts["disk_files"] += 2

    # 7 extensionless files per side.
    for i in range(7):
        rel = Path("extensionless") / f"Dockerfile_{i:03d}"
        write_text(left / rel, f"FROM scratch\n# left {i}\n")
        write_text(right / rel, f"FROM scratch\n# right {i}\n")
        counts["ignored_per_side"] += 1
        counts["disk_files"] += 2

    # Scanner adds "." and ".." aliases per side as ignored rows.
    counts["ignored_per_side"] += 2
    counts["expected_ignored_total"] = counts["ignored_per_side"] * 2
    counts["expected_total_left"] = 20 + 5 + 10 + (counts["ignored_per_side"])
    counts["expected_total_right"] = 20 + 5 + 10 + (counts["ignored_per_side"])
    counts["left"] = str(left)
    counts["right"] = str(right)
    return counts


def slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_").lower()
    return text[:60] or "check"


class VisualAsserter:
    def __init__(self, page):
        self.page = page
        self.passed = 0
        self.failed = 0
        self.index = 0

    def shot(self, name: str) -> None:
        self.index += 1
        path = SHOTS / f"{self.index:02d}_{slug(name)}.png"
        self.page.screenshot(path=str(path), full_page=False)
        print(f"  screenshot: {path}")

    def check(self, name: str, condition: bool, scroll_locator=None) -> None:
        if scroll_locator is not None:
            try:
                scroll_locator.scroll_into_view_if_needed(timeout=5000)
                time.sleep(0.25)
            except Exception:
                pass
        self.shot(name)
        if condition:
            self.passed += 1
            print(f"  PASS {self.passed:02d}: {name}")
        else:
            self.failed += 1
            print(f"  FAIL: {name}")

    def require(self, name: str, condition: bool, scroll_locator=None) -> None:
        self.check(name, condition, scroll_locator)
        if not condition:
            raise AssertionError(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold-seconds", type=int, default=20)
    args = parser.parse_args()

    SHOTS.mkdir(parents=True, exist_ok=True)
    for old in SHOTS.glob("*.png"):
        old.unlink()

    dataset_root = Path(tempfile.mkdtemp(prefix="wc_hard_stone_"))
    counts = create_dataset(dataset_root)
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    log("DATASET")
    print(f"  left : {counts['left']}")
    print(f"  right: {counts['right']}")
    print(f"  disk files created: {counts['disk_files']}")
    print(f"  expected matched rows: {counts['matched']}")
    print(f"  expected unmatched rows: {counts['unmatched']}")
    print(f"  expected ignored rows: {counts['expected_ignored_total']}")
    if counts["disk_files"] < 100:
        raise AssertionError("dataset must contain at least 100 real files")

    log("START DJANGO")
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", str(port), "--noreload"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        if not wait_port(port):
            raise RuntimeError("Django server did not start")

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        log("VISIBLE PLAYWRIGHT RUN")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, slow_mo=180)
            context = browser.new_context(viewport={"width": 1450, "height": 950})
            page = context.new_page()
            v = VisualAsserter(page)

            page.goto(base + "/", wait_until="networkidle")
            v.require("01 header shows Workspace Comparator v1.5.0",
                      "v1.5.0" in page.locator(".app-header h1").inner_text())
            v.require("02 compare button starts disabled",
                      page.locator("#btnCompare").is_disabled())

            page.locator("#btnSettings").click()
            v.require("03 settings modal opens visibly",
                      page.locator("#settingsModal").evaluate("el => el.style.display") == "block")
            page.locator("#numMaxLlm").fill("0")
            page.locator("#numMaxLlm").press("Tab")
            v.require("04 max LLM candidates visible at zero",
                      page.locator("#numMaxLlm").input_value() == "0")
            page.locator("[data-action='settings-accept']").click()
            v.require("05 settings modal closes after accept",
                      page.locator("#settingsModal").evaluate("el => el.style.display") == "none")

            page.locator("#btnExclusions").click()
            v.require("06 exclusions modal opens visibly",
                      page.locator("#exclusionsModal").evaluate("el => el.style.display") == "block")
            page.locator("#exclFileInput").fill("*.blocked.java")
            page.locator("[data-action='excl-add-files']").click()
            v.require("07 excluded file pattern is visible",
                      "*.blocked.java" in page.locator("#exclFileList").inner_text())
            page.locator("#exclDirInput").fill("excluded_dir,.ghost")
            page.locator("[data-action='excl-add-dirs']").click()
            dir_text = page.locator("#exclDirList").inner_text()
            v.require("08 excluded directories are visible",
                      "excluded_dir" in dir_text and ".ghost" in dir_text)
            page.locator("[data-action='exclusions-accept']").click()
            v.require("09 exclusions modal closes after accept",
                      page.locator("#exclusionsModal").evaluate("el => el.style.display") == "none")

            page.locator("#leftDir").fill(counts["left"])
            page.locator("#rightDir").fill(counts["right"])
            v.require("10 both dataset paths visible in inputs",
                      counts["left"] in page.locator("#leftDir").input_value()
                      and counts["right"] in page.locator("#rightDir").input_value())
            v.require("11 compare button enables with both paths",
                      not page.locator("#btnCompare").is_disabled())

            page.route("**/api/compare/", lambda route: (time.sleep(0.8), route.continue_()))
            page.locator("#btnCompare").click()
            v.require("12 loading overlay appears visibly",
                      page.locator("#loadingOverlay").is_visible())
            page.locator("#resultsSection").wait_for(state="visible", timeout=60000)
            page.locator("#loadingOverlay").wait_for(state="hidden", timeout=60000)
            v.require("13 results section is visible",
                      page.locator("#resultsSection").is_visible())
            v.require("14 stats bar is visible",
                      page.locator("#statsBar").is_visible())

            matched_rows = page.locator("tr.row-matched").count()
            unmatched_rows = page.locator("tr.row-unmatched").count()
            ignored_rows = page.locator("tr.row-ignored").count()
            v.require("15 matched row count is exact",
                      matched_rows == counts["matched"])
            v.require("16 unmatched row count is exact",
                      unmatched_rows == counts["unmatched"])
            v.require("17 ignored row count is exact",
                      ignored_rows == counts["expected_ignored_total"])

            labels = page.locator(".sep-label").all_inner_texts()
            v.require("18 corresponding section label is visible",
                      any("CORRESPONDING FILES" in label for label in labels),
                      page.locator(".sep-label").first)
            v.require("19 unmatched section label is visible",
                      any("UNMATCHED FILES" in label for label in labels))
            v.require("20 ignored section label is visible",
                      any("IGNORED FILES" in label for label in labels))

            v.require("21 green matched rows are visible",
                      page.locator("tr.row-matched").first.is_visible(),
                      page.locator("tr.row-matched").first)
            v.require("22 red unmatched rows are visible",
                      page.locator("tr.row-unmatched").first.is_visible(),
                      page.locator("tr.row-unmatched").first)
            v.require("23 dark gray ignored rows are visible",
                      page.locator("tr.row-ignored").first.is_visible(),
                      page.locator("tr.row-ignored").first)

            body_text = page.locator("#comparisonBody").inner_text()
            v.require("24 unsupported extension reason is visible",
                      "Unsupported extension: .md" in body_text
                      or "Unsupported extension: .json" in body_text)
            v.require("25 excluded file pattern reason is visible",
                      "Excluded file pattern" in body_text)
            v.require("26 excluded directory pattern reason is visible",
                      "Excluded directory pattern" in body_text)
            v.require("27 dot alias reason is visible",
                      "Directory alias (not a file)" in body_text)
            v.require("28 dot directory file is visible",
                      ".ghost" in body_text)
            v.require("29 extensionless file is visible",
                      "Dockerfile_000" in body_text)

            stat_text = page.locator("#statsBar").inner_text()
            v.require("30 ignored stat badge is visible",
                      "Ignored" in stat_text
                      and str(counts["expected_ignored_total"]) in stat_text)

            page.locator("#matchHdr").dblclick()
            v.require("31 match header sorting indicator appears",
                      page.locator("#matchHdr").inner_text().strip().startswith("Match"),
                      page.locator("#matchHdr"))

            matched_first = page.locator("tr.row-matched").first
            with context.expect_page(timeout=5000) as popup_info:
                matched_first.dblclick()
            diff_page = popup_info.value
            diff_page.wait_for_load_state("networkidle")
            v.page = diff_page
            v.require("32 double-click matched row opens file compare",
                      "File Compare" in diff_page.title()
                      and diff_page.locator("#panels").is_visible())
            diff_page.close()
            v.page = page

            ignored_first = page.locator("tr.row-ignored").first
            ignored_first.scroll_into_view_if_needed()
            before_pages = len(context.pages)
            try:
                with context.expect_page(timeout=1200):
                    ignored_first.dblclick()
                opened = True
            except PlaywrightTimeoutError:
                opened = False
            after_pages = len(context.pages)
            v.require("33 double-click ignored row does not open compare",
                      not opened and after_pages == before_pages,
                      ignored_first)

            log("VISIBLE TEST SUMMARY")
            print(f"  passed: {v.passed}")
            print(f"  failed: {v.failed}")
            print(f"  screenshots: {SHOTS}")
            print(f"  holding visible browser for {args.hold_seconds} seconds")
            time.sleep(max(0, args.hold_seconds))
            browser.close()
            return 1 if v.failed else 0

    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(dataset_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
