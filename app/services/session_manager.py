"""
Browser session management using Playwright persistent contexts.

Flow for Mercari / Poshmark:
  1. User clicks "Login" → open_login_browser() launches a visible Chromium window.
  2. User logs in manually (MFA, captcha, etc. all work naturally).
  3. When the post-login URL is detected, the session (cookies + localStorage) is
     saved to ~/.baum-reseller/{platform}_session.json.
  4. Future syncs call headless_page() which reuses that saved state — no login needed.
  5. If the session expires the scraper detects the /login redirect, deletes the stale
     file, and raises RuntimeError asking the user to log in again.
"""

from contextlib import contextmanager
import threading
from pathlib import Path

DATA_DIR = Path.home() / ".baum-reseller"
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


def session_path(platform: str) -> Path:
    return DATA_DIR / f"{platform}_session.json"


def has_session(platform: str) -> bool:
    return session_path(platform).exists()


def clear_session(platform: str):
    p = session_path(platform)
    if p.exists():
        p.unlink()


def open_login_browser(platform: str, start_url: str,
                       success_glob: str, done_cb=None):
    """
    Launch a visible Chromium browser at start_url.
    When the URL matches success_glob the session is saved and
    done_cb(True, None) is called. Times out after 3 minutes.
    """
    def _run():
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=False, args=_LAUNCH_ARGS)
                ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.goto(start_url)
                try:
                    page.wait_for_url(success_glob, timeout=180_000)
                except PWTimeout:
                    browser.close()
                    if done_cb:
                        done_cb(False, "Login timed out (3 min) — please try again.")
                    return
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                ctx.storage_state(path=str(session_path(platform)))
                browser.close()
            if done_cb:
                done_cb(True, None)
        except Exception as exc:
            if done_cb:
                done_cb(False, str(exc))

    threading.Thread(target=_run, daemon=True).start()


@contextmanager
def headless_page(platform: str):
    """
    Context manager that yields a Playwright page pre-loaded with the saved
    session cookies (runs headless).  Raises RuntimeError if no session exists.

    Usage:
        with headless_page("mercari") as page:
            page.goto("https://www.mercari.com/mypage/listings/")
    """
    sp = session_path(platform)
    if not sp.exists():
        raise RuntimeError(
            f"No saved {platform} session — click 'Login' in Settings first."
        )
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = browser.new_context(
            storage_state=str(sp),
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            yield page
        finally:
            browser.close()
