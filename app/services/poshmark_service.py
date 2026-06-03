"""
Poshmark integration.
Login: opens poshmark.com in the user's own browser; imports existing cookies.
Sync:  headless Playwright using saved session cookies.
"""
import os
import re
import json
import keyring

SERVICE    = "baum-reseller-poshmark"
DOMAIN     = "poshmark.com"
SESSION    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_session.json")
PROFILE    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_profile")
LOGIN_URL  = "https://poshmark.com/login"
FEED_URL   = "https://poshmark.com/feed"

# Pages that indicate we are still mid-authentication — keep waiting while on these
_AUTH = ("/login", "/verify", "/otp", "/two-step", "/challenge", "/auth/", "/sign")


def _is_logged_in(url: str) -> bool:
    """True once we land on any poshmark.com page that isn't an auth/login page."""
    return "poshmark.com" in url and not any(a in url for a in _AUTH)


class PoshmarkService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = "") -> str | None:
        try:
            keyring.set_password(SERVICE, "credentials",
                                 json.dumps({"email": email, "password": password}))
            from app.utils.config import set_value
            set_value("poshmark_email", email)
            return None
        except Exception as e:
            return str(e)

    def has_session(self) -> bool:
        if os.path.exists(SESSION):
            return True
        # Legacy paths from earlier versions
        for legacy in (
            os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json"),
            os.path.join(PROFILE, "Default", "Cookies"),
        ):
            if os.path.exists(legacy):
                return True
        return False

    def clear_session(self):
        import shutil
        for path in (SESSION,
                     os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")):
            if os.path.exists(path):
                os.remove(path)
        if os.path.exists(PROFILE):
            shutil.rmtree(PROFILE, ignore_errors=True)

    # ── Browser login (real browser, no automation flags) ─────────────────

    def open_in_browser(self):
        """Open the Poshmark login page in the user's default browser."""
        from app.utils.browser import open_in_system_browser
        open_in_system_browser(LOGIN_URL)

    def import_session(self, done_cb=None):
        """Read Poshmark cookies from the system browser and save the session."""
        from app.utils.browser import import_cookies_from_browser
        import_cookies_from_browser(DOMAIN, SESSION, done_cb=done_cb)

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "Not logged in — click 'Login with Browser'."
        # Find the session file that exists
        session_file = SESSION
        if not os.path.exists(session_file):
            legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")
            if os.path.exists(legacy):
                session_file = legacy
            else:
                return False, "Session file missing — click 'Login with Browser'."
        try:
            from playwright.sync_api import sync_playwright
            from app.utils.browser import headless_context
            with sync_playwright() as p:
                browser, ctx = headless_context(p, session_file)
                page = ctx.new_page()
                page.goto(FEED_URL, wait_until="domcontentloaded", timeout=20_000)
                url = page.url
                browser.close()
            if any(a in url for a in _AUTH):
                self.clear_session()
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to Poshmark ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def _get_session_file(self) -> str | None:
        if os.path.exists(SESSION):
            return SESSION
        legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")
        return legacy if os.path.exists(legacy) else None

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
        session_file = self._get_session_file()
        if not session_file:
            raise ValueError("Not logged in to Poshmark. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context
        intercepted: list[dict] = []

        def _on_response(response):
            if "vm-rest" in response.url and ("posts" in response.url or "listings" in response.url):
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        with sync_playwright() as p:
            browser, ctx = headless_context(p, session_file)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking Poshmark session…")

            page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30_000)
            if any(a in page.url for a in _AUTH):
                browser.close()
                self.clear_session()
                raise ValueError("Poshmark session expired — please re-authenticate.")

            username = self._get_username(page)
            if not username:
                browser.close()
                raise ValueError("Could not determine Poshmark username.")

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
            status_raw = str(
                item.get("inventory", {}).get("status") or item.get("status") or "available"
            ).lower()
            status = "sold" if ("sold" in status_raw or "not_for_sale" in status_raw) else "active"
            price_data = item.get("price_amount") or item.get("price") or {}
            price = (float(price_data.get("val", 0)) if isinstance(price_data, dict)
                     else float(price_data or 0))
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
