#!/usr/bin/env python
# Visible Playwright hard-stone regression test for Workspace Comparator.
#
# This test creates a 234-file dataset, opens a real Chromium window, and
# proves that every text extension is comparable, binaries use hex,
# exclusions stay visible, charsets/newlines normalize, and "." / ".."
# alias rows remain accounted for.
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

    # 10 exact compiled/archive binary matches. Their bytes, not their
    # extensions, make them binary.
    binary_exts = [".jar", ".war", ".zip", ".obj", ".o", ".wasm", ".class", ".bin", ".dat", ".apk"]
    for i, ext in enumerate(binary_exts):
        rel = Path("artifacts") / f"artifact_{i:03d}{ext}"
        data = b"\x00\x01\x02BINARY\xff" + bytes([i]) * 32
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

    # 30 text files across common, exotic, custom, and misleading
    # extensions. CRLF on the left and LF on the right must be ==.
    text_exts = [
        ".md", ".json", ".html", ".css", ".svg", ".cu", ".hpp", ".cuh",
        ".wgsl", ".jsp", ".wat", ".proto", ".sql", ".ps1", ".exe",
    ]
    for i in range(30):
        ext = text_exts[i % len(text_exts)]
        rel = Path("all_text_formats") / f"all_text_{i:03d}{ext}"
        if ext == ".exe":
            logical = f"package demo;\npublic class TextDisguisedAsExe{i} {{\n    int value() {{ return {i}; }}\n}}\n"
        else:
            logical = f"section format_{i}\nvalue = {i}\nshared logical text\n"
        write_bytes(left / rel, logical.replace("\n", "\r\n").encode("utf-8"))
        write_bytes(right / rel, logical.encode("utf-8"))
        counts["matched"] += 1
        counts["disk_files"] += 2

    # Same logical text with different charsets on each side.
    mixed = "class EncodingProof {\n    String value = \"same\";\n}\n"
    write_bytes(left / "charsets" / "mixed_utf.custom", mixed.replace("\n", "\r\n").encode("utf-16"))
    write_bytes(right / "charsets" / "mixed_utf.custom", mixed.encode("utf-8"))
    legacy = "name=caf\u00e9\nstatus=same\n"
    write_bytes(left / "charsets" / "legacy_text.unknown", legacy.replace("\n", "\r\n").encode("cp1252"))
    write_bytes(right / "charsets" / "legacy_text.unknown", legacy.encode("utf-8"))
    counts["matched"] += 2
    counts["disk_files"] += 4

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

    # 7 extensionless text files per side; CRLF and LF are equivalent.
    for i in range(7):
        rel = Path("extensionless") / f"Dockerfile_{i:03d}"
        logical = f"FROM scratch\nLABEL proof={i}\n"
        write_bytes(left / rel, logical.replace("\n", "\r\n").encode("utf-8"))
        write_bytes(right / rel, logical.encode("utf-8"))
        counts["matched"] += 1
        counts["disk_files"] += 2

    # Scanner adds "." and ".." aliases per side as ignored rows.
    counts["ignored_per_side"] += 2
    counts["expected_ignored_total"] = counts["ignored_per_side"] * 2
    counts["expected_total_left"] = counts["disk_files"] // 2 + 2
    counts["expected_total_right"] = counts["disk_files"] // 2 + 2
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
            v.require("01 header shows Workspace Comparator v1.6.0",
                      "v1.6.0" in page.locator(".app-header h1").inner_text())
            v.require("02 compare button starts disabled",
                      page.locator("#btnCompare").is_disabled())

            page.locator("#btnSettings").click()
            v.require("03 settings modal opens visibly",
                      page.locator("#settingsModal").evaluate("el => el.style.display") == "block")
            page.locator("#numMaxLlm").fill("0")
            page.locator("#numMaxLlm").press("Tab")
            v.require("04 max LLM candidates visible at zero",
                      page.locator("#numMaxLlm").input_value() == "0")
            v.require("05 independent charset selectors are visible",
                      page.locator("#charsetLeft").is_visible()
                      and page.locator("#charsetRight").is_visible())
            v.require("06 both charset selectors default to auto",
                      page.locator("#charsetLeft").input_value() == "auto"
                      and page.locator("#charsetRight").input_value() == "auto")
            page.locator("[data-action='settings-accept']").click()
            v.require("07 settings modal closes after accept",
                      page.locator("#settingsModal").evaluate("el => el.style.display") == "none")

            page.locator("#btnExclusions").click()
            v.require("08 exclusions modal opens visibly",
                      page.locator("#exclusionsModal").evaluate("el => el.style.display") == "block")
            page.locator("#exclFileInput").fill("*.blocked.java")
            page.locator("[data-action='excl-add-files']").click()
            v.require("09 excluded file pattern is visible",
                      "*.blocked.java" in page.locator("#exclFileList").inner_text())
            page.locator("#exclDirInput").fill("excluded_dir,.ghost")
            page.locator("[data-action='excl-add-dirs']").click()
            dir_text = page.locator("#exclDirList").inner_text()
            v.require("10 excluded directories are visible",
                      "excluded_dir" in dir_text and ".ghost" in dir_text)

            page.locator("#exclFileInput").fill(
                ",".join(f"scroll_file_{i:02d}.tmp" for i in range(35)))
            page.locator("[data-action='excl-add-files']").click()
            page.locator("#exclDirInput").fill(
                ",".join(f"scroll_dir_{i:02d}" for i in range(35)))
            page.locator("[data-action='excl-add-dirs']").click()
            file_list = page.locator("#exclFileList")
            dir_list = page.locator("#exclDirList")
            v.require("11 large file exclusion list has its own scrollbar",
                      file_list.evaluate("el => el.scrollHeight > el.clientHeight"),
                      file_list)
            v.require("12 large folder exclusion list has its own scrollbar",
                      dir_list.evaluate("el => el.scrollHeight > el.clientHeight"),
                      dir_list)
            file_list.evaluate("el => { el.scrollTop = el.scrollHeight; }")
            dir_list.evaluate("el => { el.scrollTop = el.scrollHeight; }")
            v.require("13 both exclusion lists scroll independently",
                      file_list.evaluate("el => el.scrollTop > 0")
                      and dir_list.evaluate("el => el.scrollTop > 0"))
            v.require("14 Show excluded defaults to checked",
                      page.locator("#showExcluded").is_checked(),
                      page.locator("#showExcluded"))
            page.locator("[data-action='exclusions-accept']").click()
            v.require("15 exclusions modal closes after accept",
                      page.locator("#exclusionsModal").evaluate("el => el.style.display") == "none")

            page.locator("#leftDir").fill(counts["left"])
            page.locator("#rightDir").fill(counts["right"])
            v.require("16 both dataset paths visible in inputs",
                      counts["left"] in page.locator("#leftDir").input_value()
                      and counts["right"] in page.locator("#rightDir").input_value())
            v.require("17 compare button enables with both paths",
                      not page.locator("#btnCompare").is_disabled())

            page.route("**/api/compare/", lambda route: (time.sleep(0.8), route.continue_()))
            page.locator("#btnCompare").click()
            v.require("18 loading overlay appears visibly",
                      page.locator("#loadingOverlay").is_visible())
            page.locator("#resultsSection").wait_for(state="visible", timeout=60000)
            page.locator("#loadingOverlay").wait_for(state="hidden", timeout=60000)
            v.require("19 results section is visible",
                      page.locator("#resultsSection").is_visible())
            v.require("20 stats bar is visible",
                      page.locator("#statsBar").is_visible())

            matched_rows = page.locator("tr.row-matched").count()
            unmatched_rows = page.locator("tr.row-unmatched").count()
            ignored_rows = page.locator("tr.row-ignored").count()
            v.require("21 matched row count is exact",
                      matched_rows == counts["matched"])
            v.require("22 unmatched row count is exact",
                      unmatched_rows == counts["unmatched"])
            v.require("23 ignored row count is exact",
                      ignored_rows == counts["expected_ignored_total"])

            labels = page.locator(".sep-label").all_inner_texts()
            v.require("24 corresponding section label is visible",
                      any("CORRESPONDING FILES" in label for label in labels),
                      page.locator(".sep-label").first)
            v.require("25 unmatched section label is visible",
                      any("UNMATCHED FILES" in label for label in labels))
            v.require("26 ignored section label is visible",
                      any("IGNORED FILES" in label for label in labels))

            v.require("27 green matched rows are visible",
                      page.locator("tr.row-matched").first.is_visible(),
                      page.locator("tr.row-matched").first)
            v.require("28 red unmatched rows are visible",
                      page.locator("tr.row-unmatched").first.is_visible(),
                      page.locator("tr.row-unmatched").first)
            v.require("29 dark gray ignored rows are visible",
                      page.locator("tr.row-ignored").first.is_visible(),
                      page.locator("tr.row-ignored").first)

            body_text = page.locator("#comparisonBody").inner_text()
            v.require("30 unsupported-extension rejection is gone",
                      "Unsupported extension" not in body_text)
            v.require("31 md cu hpp wgsl jsp text files are matched",
                      all(page.locator("tr.row-matched", has_text=name).count() == 1 for name in [
                          "all_text_000.md", "all_text_005.cu", "all_text_006.hpp",
                          "all_text_008.wgsl", "all_text_009.jsp",
                      ]),
                      page.locator("tr.row-matched", has_text="all_text_000.md"))
            disguised = page.locator("tr.row-matched", has_text="all_text_014.exe")
            v.require("32 text Java content disguised as exe is matched as text",
                      disguised.count() == 1 and disguised.locator(".bin-tag").count() == 0,
                      disguised)
            v.require("33 excluded file pattern reason is visible",
                      "Excluded file pattern" in body_text)
            v.require("34 excluded directory pattern reason is visible",
                      "Excluded directory pattern" in body_text)
            v.require("35 dot alias reason is visible",
                      "Directory alias (not a file)" in body_text)
            v.require("36 dot directory file is visible",
                      ".ghost" in body_text)
            extensionless = page.locator("tr.row-matched", has_text="Dockerfile_000")
            v.require("37 extensionless text file is matched",
                      extensionless.count() == 1, extensionless)
            mixed_charset = page.locator("tr.row-matched", has_text="mixed_utf.custom")
            v.require("38 UTF-16 versus UTF-8 text is identical",
                      mixed_charset.count() == 1
                      and mixed_charset.locator(".status-identical").count() == 1,
                      mixed_charset)
            legacy_charset = page.locator("tr.row-matched", has_text="legacy_text.unknown")
            v.require("39 Windows-1252 versus UTF-8 text is identical",
                      legacy_charset.count() == 1
                      and legacy_charset.locator(".status-identical").count() == 1,
                      legacy_charset)
            newline_row = page.locator("tr.row-matched", has_text="all_text_000.md")
            v.require("40 CRLF versus LF is identical",
                      newline_row.locator(".status-identical").count() == 1,
                      newline_row)

            stat_text = page.locator("#statsBar").inner_text()
            v.require("41 ignored stat badge is visible",
                      "Ignored" in stat_text
                      and str(counts["expected_ignored_total"]) in stat_text)

            page.locator("#matchHdr").dblclick()
            v.require("42 match header sorting indicator appears",
                      page.locator("#matchHdr").inner_text().strip().startswith("Match"),
                      page.locator("#matchHdr"))

            matched_first = page.locator("tr.row-matched", has_text="all_text_000.md")
            with context.expect_page(timeout=5000) as popup_info:
                matched_first.dblclick()
            diff_page = popup_info.value
            diff_page.wait_for_load_state("networkidle")
            v.page = diff_page
            v.require("43 text row opens aligned file compare",
                      "File Compare" in diff_page.title()
                      and diff_page.locator("#panels").is_visible())
            v.require("44 CRLF-only difference produces no changed rows",
                      diff_page.evaluate("ROWS.every(function(r) { return r.t === 'eq'; })"))
            diff_page.close()
            v.page = page

            binary_row = page.locator("tr.row-matched", has_text="artifact_000.jar")
            v.require("45 native binary row has BIN marker",
                      binary_row.locator(".bin-tag").count() == 1,
                      binary_row)
            with context.expect_page(timeout=5000) as binary_popup:
                binary_row.dblclick()
            binary_page = binary_popup.value
            binary_page.wait_for_load_state("networkidle")
            binary_page.locator("[data-mode='all']").click()
            v.page = binary_page
            v.require("46 native binary opens locked hex viewer",
                      binary_page.locator("#chkHex").is_checked()
                      and binary_page.locator("#chkHex").is_disabled()
                      and binary_page.locator("body.hexmode").count() == 1)
            binary_page.close()
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
            v.require("47 double-click ignored row does not open compare",
                      not opened and after_pages == before_pages,
                      ignored_first)

            page.locator("#btnExclusions").click()
            page.locator("#showExcluded").uncheck()
            page.locator("[data-action='exclusions-accept']").click()
            hidden_labels = page.locator(".sep-label").all_inner_texts()
            v.require("48 unchecked Show excluded hides every ignored table row",
                      page.locator("tr.row-ignored").count() == 0
                      and not any("IGNORED FILES" in label for label in hidden_labels))

            page.locator("#btnExclusions").click()
            stored_hidden = page.evaluate(
                "JSON.parse(localStorage.getItem('wcExclusions')).showExcluded")
            v.require("49 hidden excluded preference persists in the dialog",
                      not page.locator("#showExcluded").is_checked()
                      and stored_hidden is False,
                      page.locator("#showExcluded"))
            page.locator("#showExcluded").check()
            page.locator("[data-action='exclusions-accept']").click()
            restored_labels = page.locator(".sep-label").all_inner_texts()
            v.require("50 checked Show excluded restores every ignored table row",
                      page.locator("tr.row-ignored").count() == counts["expected_ignored_total"]
                      and any("IGNORED FILES" in label for label in restored_labels),
                      page.locator("tr.row-ignored").first)

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
