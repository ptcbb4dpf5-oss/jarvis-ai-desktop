"""
modules/browser.py
==================
Browser automation via Playwright (sync API).

Runs a persistent Chromium instance so Jarvis can open pages, navigate, search,
click and type across multiple commands within a session. Playwright's sync API
must run on the thread that created it, so the Agent invokes this from a worker
thread (never the Qt GUI thread).

If Playwright (or its browser binaries) aren't installed, every method returns a
friendly message instead of raising.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, Optional

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    _PLAYWRIGHT = True
except Exception:  # pragma: no cover
    sync_playwright = None  # type: ignore
    _PLAYWRIGHT = False


class Browser:
    def __init__(self, headless: bool = False, search_engine: str = "https://www.google.com/search?q=") -> None:
        self.headless = headless
        self.search_engine = search_engine
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def available(self) -> bool:
        return _PLAYWRIGHT

    # ------------------------------------------------------------------ #
    def _ensure(self) -> Optional[str]:
        """Lazily start Playwright + a browser + a page. Returns error str or None."""
        if not _PLAYWRIGHT:
            return ("Browser automation unavailable (playwright not installed). "
                    "Run: pip install playwright && playwright install chromium")
        try:
            if self._pw is None:
                self._pw = sync_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                self._browser = self._pw.chromium.launch(headless=self.headless)
                self._context = self._browser.new_context(
                    viewport={"width": 1366, "height": 850},
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/122.0 Safari/537.36"),
                )
                self._page = self._context.new_page()
            if self._page is None:
                self._page = self._context.new_page()
            return None
        except Exception as exc:
            msg = str(exc)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                return ("Chromium isn't installed for Playwright. "
                        "Run: playwright install chromium")
            return f"Couldn't start the browser: {exc}"

    # ------------------------------------------------------------------ #
    def execute(self, action: str, args: Dict[str, Any]) -> str:
        action = (action or "open").lower()
        if action == "close":
            return self.close()

        err = self._ensure()
        if err:
            return err

        try:
            if action == "open":
                url = args.get("url")
                if url:
                    return self.navigate(url)
                return self.navigate("https://www.google.com")
            if action == "navigate":
                return self.navigate(args.get("url", ""))
            if action == "search":
                return self.search(args.get("query", ""))
            if action == "click":
                return self.click(args.get("selector", ""))
            if action == "type":
                return self.type(args.get("selector", ""), args.get("text", ""))
            if action == "read":
                return self.read_text(args.get("selector"))
            if action == "screenshot":
                return self.screenshot(args.get("path", "browser_shot.png"))
            return f"Unknown browser action: {action}"
        except Exception as exc:
            return f"Browser action '{action}' failed: {exc}"

    # ------------------------------------------------------------------ #
    def navigate(self, url: str) -> str:
        if not url:
            return "No URL given."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Opened {self._page.title() or url}."

    def search(self, query: str) -> str:
        if not query:
            return "What should I search for?"
        url = self.search_engine + urllib.parse.quote(query)
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f'Searched the web for "{query}".'

    def click(self, selector: str) -> str:
        if not selector:
            return "No element selector given."
        self._page.click(selector, timeout=10000)
        return f"Clicked {selector}."

    def type(self, selector: str, text: str) -> str:
        if not selector:
            return "No input selector given."
        self._page.fill(selector, text, timeout=10000)
        return f'Typed into {selector}.'

    def read_text(self, selector: Optional[str] = None) -> str:
        if selector:
            el = self._page.query_selector(selector)
            return el.inner_text() if el else "Element not found."
        # Whole page text, trimmed.
        text = self._page.inner_text("body")
        return text[:2000]

    def screenshot(self, path: str = "browser_shot.png") -> str:
        self._page.screenshot(path=path, full_page=False)
        return f"Saved browser screenshot to {path}."

    def current_url(self) -> str:
        if self._page:
            return self._page.url
        return "No page open."

    # ------------------------------------------------------------------ #
    def close(self) -> str:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._pw = self._browser = self._context = self._page = None
        return "Closed the browser."
