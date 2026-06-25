"""test_site.py — front-end smoke test.

Serves site/ and loads it in a headless browser to confirm the page actually
renders against the current urgent.json: the table populates, the summary stats
show, the detail modal and the add-filter popover open, and there are NO console
or page JS errors. Catches the front end breaking when the data shape changes or
the code regresses. Needs: pip install playwright && playwright install chromium.

    python scripts/test_site.py
"""
from __future__ import annotations

import functools
import http.server
import pathlib
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
PORT = 8137


def main() -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    errors: list[str] = []
    rows = stats = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector("#mainTable tbody tr", timeout=20000)
            rows = page.eval_on_selector_all("#mainTable tbody tr", "els => els.length")
            stats = page.eval_on_selector_all(".stat .n", "els => els.length")
            # detail modal opens on row click, then closes via its button (more
            # deterministic than Escape, which can miss in headless CI)
            page.click("#mainTable tbody tr:first-child td:first-child")
            page.wait_for_selector("#detailModal.show", timeout=10000)
            page.click("#detailModal .btn-close")
            page.wait_for_selector("#detailModal", state="hidden", timeout=15000)
            page.wait_for_selector(".modal-backdrop", state="detached", timeout=10000)
            # add-filter popover opens
            page.click("#addFilterBtn")
            page.wait_for_selector(".filter-popover", timeout=10000)
            browser.close()
    finally:
        httpd.shutdown()

    problems = []
    if rows < 1:
        problems.append("no table rows rendered")
    if stats < 2:
        problems.append(f"summary stats missing (found {stats})")
    if errors:
        problems.append("JS errors: " + " | ".join(errors[:5]))
    if problems:
        print("FRONT-END SMOKE TEST FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"front-end OK: {rows} rows, {stats} stat cards, modal + add-filter open, no JS errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
