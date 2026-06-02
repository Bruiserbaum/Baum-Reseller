"""
Platform browser utilities.

Login  — launches Chrome/Edge via subprocess with ZERO Playwright automation
flags. The user sees a completely normal browser. Google SSO, 2FA, and all
platform auth flows work exactly as they do in their everyday browser.

Sync   — uses a saved storage_state JSON (cookies) with a headless Playwright
context. No automation detection needed for headless data scraping.
"""
import os
import socket
import subprocess
import threading
import time
import urllib.request


# ── Browser discovery ─────────────────────────────────────────────────────

def find_browser_exe() -> str | None:
    """Return the first Chrome or Edge executable found on this machine."""
    candidates = [
        # Chrome — user install
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
        # Chrome — machine install
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Edge (always present on Windows 10/11)
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


# ── Helpers ───────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_cdp(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/json", timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


# ── Headed login (subprocess + CDP) ──────────────────────────────────────

def launch_login_window(
    login_url: str,
    profile_dir: str,
    is_logged_in,       # callable(url: str) -> bool
    state_file: str,
    done_cb=None,
):
    """
    Launch Chrome/Edge as a real OS process (no Playwright injected flags),
    connect via CDP to detect login completion, then save cookies to
    state_file and close the browser.

    The user interacts with a completely normal browser — Google SSO, 2FA,
    SMS codes, authenticator apps — all work exactly as usual.
    """
    def _worker():
        ok, err = False, None
        proc = None
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            exe = find_browser_exe()
            if not exe:
                raise RuntimeError(
                    "Google Chrome or Microsoft Edge is required for platform login.\n"
                    "Please install Chrome: https://www.google.com/chrome/"
                )

            port = _free_port()
            os.makedirs(profile_dir, exist_ok=True)

            # Launch as a completely normal browser process — no Playwright flags
            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.Popen(
                [
                    exe,
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-infobars",
                    "--start-maximized",
                    login_url,          # Opens as the first tab
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )

            if not _wait_for_cdp(port):
                raise RuntimeError(
                    "Browser did not start in time. "
                    "Make sure Chrome or Edge is installed and not blocked by antivirus."
                )

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")

                # Get the existing context (profile-based browser has one by default)
                ctx = browser.contexts[0] if browser.contexts else None
                if ctx is None:
                    raise RuntimeError("Could not connect to browser context.")

                # Find the login page tab (it was opened via command-line arg)
                page = None
                for _ in range(10):   # wait up to 5 s for the tab to appear
                    pages = ctx.pages
                    if pages:
                        page = pages[0]
                        break
                    time.sleep(0.5)

                if page is None:
                    page = ctx.new_page()
                    page.goto(login_url, wait_until="domcontentloaded", timeout=20_000)

                page.bring_to_front()

                # Wait for user to finish login (including 2FA)
                try:
                    page.wait_for_url(is_logged_in, timeout=300_000)
                except PWTimeout:
                    raise RuntimeError(
                        "Login timed out (5 minutes). "
                        "If two-factor authentication was required, complete it in the "
                        "browser window and wait for the home page to finish loading."
                    )

                # Save cookies so future headless syncs can reuse the session
                ctx.storage_state(path=state_file)
                ok = True

        except Exception as e:
            err = str(e)
        finally:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            if done_cb:
                from app.utils.qt_thread import post_to_main
                post_to_main(lambda: done_cb(ok, err))

    threading.Thread(target=_worker, daemon=True).start()


# ── Headless sync context ─────────────────────────────────────────────────

def headless_context(playwright, state_file: str):
    """
    Return (browser, context) for headless scraping using saved session cookies.
    Caller must call browser.close() when done.
    """
    browser = playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        storage_state=state_file,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    return browser, ctx
