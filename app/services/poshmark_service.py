"""
Poshmark integration — Playwright browser automation.
Supports email/password AND Google SSO (user completes auth in a real browser window).
Session state is saved to disk so future syncs run headless.
"""
import os
import re
import json
import threading
import keyring

SERVICE = "baum-reseller-poshmark"
STATE_FILE = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")
LOGIN_URL = "https://poshmark.com/login"
FEED_URL = "https://poshmark.com/feed"


class PoshmarkService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = ""):
        keyring.set_password(SERVICE, "credentials",
                             json.dumps({"email": email, "password": password}))

    def has_session(self) -> bool:
        return os.path.exists(STATE_FILE)

    def clear_session(self):
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    # ── Browser login (headed — handles email/pass AND Google SSO) ────────

    def login_browser(self, done_cb=None):
        """
        Open a visible Chromium window so the user can log in however they like
        (email/password or 'Continue with Google'). Saves session state on success.
        """
        def _worker():
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
                creds = self.get_credentials()

                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=False, slow_mo=50)
                    context = browser.new_context()
                    page = context.new_page()

                    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

                    # Auto-fill saved email/password if present
                    if creds.get("email"):
                        try:
                            page.fill('input[id="email_address"]', creds["email"], timeout=3_000)
                            if creds.get("password"):
                                page.fill('input[type="password"]', creds["password"], timeout=3_000)
                        except Exception:
                            pass

                    # Wait for the user to complete login (up to 3 min)
                    try:
                        page.wait_for_url(
                            lambda url: "poshmark.com" in url and "/login" not in url,
                            timeout=180_000
                        )
                    except PWTimeout:
                        browser.close()
                        if done_cb:
                            done_cb(False, "Login timed out — window was open for 3 minutes.")
                        return

                    context.storage_state(path=STATE_FILE)
                    browser.close()

                if done_cb:
                    done_cb(True, None)

            except Exception as e:
                if done_cb:
                    done_cb(False, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "Not logged in — click Login to authenticate."
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(storage_state=STATE_FILE)
                page = ctx.new_page()
                page.goto(FEED_URL, wait_until="domcontentloaded", timeout=20_000)
                url = page.url
                browser.close()
            if "/login" in url:
                self.clear_session()
                return False, "Session expired — click Login to re-authenticate."
            return True, "Connected"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def _get_username(self, page) -> str:
        """Extract the logged-in Poshmark username from the nav."""
        for selector in [
            'a[href*="/closet/"][class*="user"]',
            'a[href*="/closet/"]',
            '[data-et-name="my_closet"]',
        ]:
            try:
                el = page.query_selector(selector)
                if el:
                    href = el.get_attribute("href") or ""
                    m = re.search(r"/closet/([^/?&#]+)", href)
                    if m:
                        return m.group(1)
            except Exception:
                continue

        # Fallback: parse from page source
        try:
            content = page.content()
            m = re.search(r'"username"\s*:\s*"([^"]+)"', content)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not self.has_session():
            raise ValueError("Not logged in to Poshmark. Click Login in Settings first.")

        from playwright.sync_api import sync_playwright

        results = []
        intercepted: list[dict] = []

        def _on_response(response):
            """Capture Poshmark's internal API responses for listing data."""
            url = response.url
            if "vm-rest" in url and ("posts" in url or "listings" in url):
                try:
                    body = response.json()
                    intercepted.append(body)
                except Exception:
                    pass

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=STATE_FILE)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking session…")

            page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30_000)

            if "/login" in page.url:
                browser.close()
                self.clear_session()
                raise ValueError("Poshmark session expired. Please re-authenticate.")

            username = self._get_username(page)
            if not username:
                browser.close()
                raise ValueError("Could not determine Poshmark username from session.")

            if progress_cb:
                progress_cb(f"Fetching closet for @{username}…")

            # Load closet — this triggers the internal API calls we intercept
            page.goto(
                f"https://poshmark.com/closet/{username}",
                wait_until="networkidle", timeout=30_000
            )

            # Scroll to trigger lazy-loaded listings
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_200)

            # Also load sold items
            page.goto(
                f"https://poshmark.com/closet/{username}?availability=sold_out",
                wait_until="networkidle", timeout=30_000
            )
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            browser.close()

        # Parse intercepted API JSON
        if intercepted:
            results = _parse_api_responses(intercepted)
        else:
            # Fallback: nothing intercepted (API structure changed), return empty
            pass

        return results


def _parse_api_responses(responses: list[dict]) -> list[dict]:
    """Parse Poshmark internal API JSON into our listing format."""
    seen = set()
    results = []

    for resp in responses:
        # Poshmark wraps data in {"data": [...]} or {"data": {"items": [...]}}
        data = resp.get("data", resp)
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (data.get("listings") or data.get("items") or
                     data.get("posts") or [])

        for item in items:
            if not isinstance(item, dict):
                continue
            lid = str(item.get("id") or item.get("listing_id") or "")
            if not lid or lid in seen:
                continue
            seen.add(lid)

            status_raw = str(item.get("inventory", {}).get("status") or
                             item.get("status") or "available").lower()
            status = "sold" if "sold" in status_raw or "not_for_sale" in status_raw else "active"

            price_data = item.get("price_amount") or item.get("price") or {}
            price = float(price_data.get("val", 0)) if isinstance(price_data, dict) else float(price_data or 0)

            results.append({
                "listing_id": lid,
                "title": item.get("title") or item.get("name") or "Untitled",
                "url": f"https://poshmark.com/listing/{lid}",
                "price": price,
                "status": status,
                "img_url": (item.get("picture_url") or
                            item.get("cover_shot", {}).get("url_small") or ""),
            })

    return results
