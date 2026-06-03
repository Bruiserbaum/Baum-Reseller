"""
Platform browser utilities.

Login flow (new):
  1. open_in_system_browser(url) — opens the platform in the user's everyday
     browser using webbrowser.open(). No automation, no fingerprinting.
     Google SSO, 2FA, and all auth methods work exactly as normal.

  2. import_cookies_from_browser(domain, state_file) — reads the user's
     existing cookies for that domain from Chrome, Edge, or Firefox and
     saves them as a Playwright storage_state JSON. The user just needs to
     be already logged in.

Sync (headless):
  headless_context(playwright, state_file) — loads the saved cookies into
  a Playwright context for scraping without re-authenticating.
"""
import json
import os
import threading
import webbrowser

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── System-browser helpers ────────────────────────────────────────────────

def open_in_system_browser(url: str):
    """Open url in the user's default browser (Chrome/Edge/Firefox/etc.)."""
    webbrowser.open(url)


def import_cookies_from_browser(
    platform_domain: str,
    state_file: str,
    done_cb=None,
):
    """
    Read cookies for platform_domain from Chrome, Edge, or Firefox and save
    them as a Playwright storage_state JSON at state_file.

    Runs in a background thread. Calls done_cb(ok: bool, message: str).
    """
    def _worker():
        ok, msg = False, ""
        try:
            ok, msg = _do_import(platform_domain, state_file)
        except Exception as e:
            msg = str(e)
        finally:
            if done_cb:
                from app.utils.qt_thread import post_to_main
                post_to_main(lambda: done_cb(ok, msg))

    threading.Thread(target=_worker, daemon=True).start()


def _do_import(domain: str, state_file: str) -> tuple[bool, str]:
    try:
        import browser_cookie3
    except ImportError:
        return False, (
            "browser-cookie3 is not installed.\n"
            "Run:  py -m pip install browser-cookie3"
        )

    extractors = [
        ("Chrome",  browser_cookie3.chrome),
        ("Edge",    browser_cookie3.edge),
        ("Firefox", browser_cookie3.firefox),
    ]

    cookies = []
    source = None
    errors = []

    for name, extract in extractors:
        try:
            jar = extract(domain_name=domain)
            found = [_convert_cookie(c) for c in jar]
            if found:
                cookies = found
                source = name
                break
        except Exception as e:
            errors.append(f"{name}: {e}")

    if not cookies:
        detail = "; ".join(errors) if errors else "no cookies found"
        return False, (
            f"No session cookies found for {domain}.\n\n"
            f"Make sure you are logged into {domain} in Chrome, Edge, or Firefox, "
            f"then click Import Session again.\n\n"
            f"Detail: {detail}"
        )

    state = {"cookies": cookies, "origins": []}
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return True, f"Imported {len(cookies)} cookies from {source}."


def _convert_cookie(c) -> dict:
    """Convert a browser_cookie3 cookie to Playwright storage_state format."""
    domain = c.domain or ""
    if domain and not domain.startswith("."):
        domain = "." + domain
    cookie = {
        "name":     c.name,
        "value":    c.value,
        "domain":   domain,
        "path":     c.path or "/",
        "secure":   bool(c.secure),
        "httpOnly": False,
        "sameSite": "Lax",
    }
    if c.expires:
        cookie["expires"] = float(c.expires)
    return cookie


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
        user_agent=_USER_AGENT,
    )
    return browser, ctx
