"""
Platform browser utilities.

Login  — uses Playwright's own launch() with the actual Chrome or Edge binary
(channel="chrome" / "msedge"). Playwright creates an isolated process so there
are no Chrome singleton issues and no "Restore pages" dialogs from previous
sessions. Automation detection flags are stripped so Google SSO and 2FA work.

Sync   — headless Playwright context loaded from a saved storage_state JSON.
"""
import os
import threading

# Script injected into every login page to hide automation fingerprints
_ANTI_DETECT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
    window.chrome = { runtime: {} };
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--start-maximized",
    "--disable-features=ChromeWhatsNewUI,TranslateUI",
    "--disable-session-crashed-bubble",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Headed browser for login ──────────────────────────────────────────────

def _launch_headed(playwright):
    """
    Launch a headed browser for interactive login.
    Tries Chrome → Edge → Playwright's Chromium.
    All automation-related flags are stripped.
    """
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = dict(
                headless=False,
                args=_LAUNCH_ARGS,
                ignore_default_args=["--enable-automation"],
            )
            if channel:
                kwargs["channel"] = channel
            return playwright.chromium.launch(**kwargs)
        except Exception:
            continue
    raise RuntimeError(
        "No browser found (Chrome, Edge, or Playwright Chromium).\n"
        "Install Chrome or run:  py -m playwright install chromium"
    )


def launch_login_window(
    login_url: str,
    profile_dir: str,   # kept for API compatibility; not used in this approach
    is_logged_in,       # callable(url: str) -> bool
    state_file: str,
    done_cb=None,
):
    """
    Open a headed browser, navigate to login_url, and wait for the user to
    complete authentication (including 2FA). Saves cookies to state_file
    and closes the browser.

    Uses Playwright's own process management — no Chrome singleton issues.
    """
    def _worker():
        ok, err = False, None
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            with sync_playwright() as p:
                browser = _launch_headed(p)

                ctx = browser.new_context(user_agent=_USER_AGENT)
                page = ctx.new_page()
                page.add_init_script(_ANTI_DETECT)

                page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
                page.bring_to_front()

                try:
                    page.wait_for_url(is_logged_in, timeout=300_000)
                except PWTimeout:
                    raise RuntimeError(
                        "Login timed out (5 minutes). If two-factor authentication "
                        "was required, complete it in the browser window and wait "
                        "for the home page to finish loading."
                    )

                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                ctx.storage_state(path=state_file)
                browser.close()
                ok = True

        except Exception as e:
            raw = str(e)
            if any(w in raw.lower() for w in ("closed", "target", "detached")):
                err = "Login cancelled — the browser window was closed."
            else:
                err = raw
        finally:
            if done_cb:
                from app.utils.qt_thread import post_to_main
                post_to_main(lambda: done_cb(ok, err))

    threading.Thread(target=_worker, daemon=True).start()


# ── Headless context for sync ─────────────────────────────────────────────

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
        user_agent=_USER_AGENT,
    )
    return browser, ctx
