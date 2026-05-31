"""
Poshmark integration — Playwright browser automation.
Supports email/password AND Google SSO via a visible browser window.
Falls back to system Chrome/Edge if Playwright's Chromium isn't present.
"""
import os
import re
import json
import threading
import keyring

SERVICE    = "baum-reseller-poshmark"
STATE_FILE = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")
LOGIN_URL  = "https://poshmark.com/login"
FEED_URL   = "https://poshmark.com/feed"


def _launch_browser(playwright, headless: bool):
    """
    Try to launch a browser in order:
      1. System Google Chrome
      2. System Microsoft Edge
      3. Playwright's bundled Chromium
    Raises RuntimeError if none are found.
    """
    attempts = [
        {"channel": "chrome",  "headless": headless, "slow_mo": 50 if not headless else 0},
        {"channel": "msedge",  "headless": headless, "slow_mo": 50 if not headless else 0},
        {"headless": headless, "slow_mo": 50 if not headless else 0},   # bundled Chromium
    ]
    last_err = None
    for kwargs in attempts:
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as e:
            last_err = e
    raise RuntimeError(
        f"No browser found (Chrome, Edge, or Playwright Chromium).\n"
        f"Install Chrome or run:  py -m playwright install chromium\n\n"
        f"Detail: {last_err}"
    )


class PoshmarkService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = "") -> str | None:
        """Save credentials. Returns error message on failure, None on success."""
        try:
            keyring.set_password(SERVICE, "credentials",
                                 json.dumps({"email": email, "password": password}))
            return None
        except Exception as e:
            return str(e)

    def has_session(self) -> bool:
        return os.path.exists(STATE_FILE)

    def clear_session(self):
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    # ── Browser login ─────────────────────────────────────────────────────

    def login_browser(self, done_cb=None):
        """
        Open a visible browser so the user can log in with any method.
        Calls done_cb(ok: bool, err: str | None) when finished.
        Guaranteed to call done_cb — even on failure.
        """
        def _worker():
            ok, err = False, None
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
                creds = self.get_credentials()

                with sync_playwright() as p:
                    browser = _launch_browser(p, headless=False)
                    ctx  = browser.new_context()
                    page = ctx.new_page()

                    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                    page.bring_to_front()

                    # Auto-fill saved credentials if present
                    if creds.get("email"):
                        try:
                            page.fill('input[id="email_address"]', creds["email"], timeout=3_000)
                            if creds.get("password"):
                                page.fill('input[type="password"]', creds["password"], timeout=3_000)
                        except Exception:
                            pass

                    # Wait up to 3 minutes for user to complete login
                    try:
                        page.wait_for_url(
                            lambda url: "poshmark.com" in url and "/login" not in url,
                            timeout=180_000
                        )
                    except PWTimeout:
                        raise RuntimeError(
                            "Login window timed out after 3 minutes without detecting a successful login."
                        )

                    ctx.storage_state(path=STATE_FILE)
                    browser.close()
                ok = True

            except Exception as e:
                err = str(e)
            finally:
                if done_cb:
                    from app.utils.qt_thread import post_to_main
                    post_to_main(lambda: done_cb(ok, err))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "Not logged in — click 'Login with Browser' to authenticate."
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = _launch_browser(p, headless=True)
                ctx  = browser.new_context(storage_state=STATE_FILE)
                page = ctx.new_page()
                page.goto(FEED_URL, wait_until="domcontentloaded", timeout=20_000)
                url  = page.url
                browser.close()
            if "/login" in url:
                self.clear_session()
                return False, "Session expired — click 'Login with Browser' to re-authenticate."
            return True, "Connected to Poshmark ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def _get_username(self, page) -> str:
        for selector in ['a[href*="/closet/"]', '[data-et-name="my_closet"]']:
            try:
                el = page.query_selector(selector)
                if el:
                    href = el.get_attribute("href") or ""
                    m = re.search(r"/closet/([^/?&#]+)", href)
                    if m:
                        return m.group(1)
            except Exception:
                continue
        try:
            m = re.search(r'"username"\s*:\s*"([^"]+)"', page.content())
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not self.has_session():
            raise ValueError("Not logged in to Poshmark. Click 'Login with Browser' in Settings first.")

        from playwright.sync_api import sync_playwright
        intercepted: list[dict] = []

        def _on_response(response):
            if "vm-rest" in response.url and ("posts" in response.url or "listings" in response.url):
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=True)
            ctx  = browser.new_context(storage_state=STATE_FILE)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking Poshmark session…")

            page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30_000)
            if "/login" in page.url:
                browser.close()
                self.clear_session()
                raise ValueError("Poshmark session expired — please re-authenticate.")

            username = self._get_username(page)
            if not username:
                browser.close()
                raise ValueError("Could not determine Poshmark username from saved session.")

            if progress_cb:
                progress_cb(f"Loading closet for @{username}…")

            page.goto(f"https://poshmark.com/closet/{username}",
                      wait_until="networkidle", timeout=30_000)
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_200)

            page.goto(f"https://poshmark.com/closet/{username}?availability=sold_out",
                      wait_until="networkidle", timeout=30_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            browser.close()

        return _parse_api_responses(intercepted)


def _parse_api_responses(responses: list[dict]) -> list[dict]:
    seen, results = set(), []
    for resp in responses:
        data  = resp.get("data", resp)
        items = (data if isinstance(data, list) else
                 data.get("listings") or data.get("items") or data.get("posts") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            lid = str(item.get("id") or item.get("listing_id") or "")
            if not lid or lid in seen:
                continue
            seen.add(lid)
            status_raw = str(item.get("inventory", {}).get("status") or
                             item.get("status") or "available").lower()
            status = "sold" if ("sold" in status_raw or "not_for_sale" in status_raw) else "active"
            price_data = item.get("price_amount") or item.get("price") or {}
            price = float(price_data.get("val", 0)) if isinstance(price_data, dict) else float(price_data or 0)
            results.append({
                "listing_id": lid,
                "title":      item.get("title") or item.get("name") or "Untitled",
                "url":        f"https://poshmark.com/listing/{lid}",
                "price":      price,
                "status":     status,
                "img_url":    (item.get("picture_url") or
                               item.get("cover_shot", {}).get("url_small") or ""),
            })
    return results
