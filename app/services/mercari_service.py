"""
Mercari integration.
Login: real Chrome/Edge subprocess — no automation flags, supports Google SSO + 2FA.
Sync:  headless Playwright using saved session cookies.
"""
import os
import re
import json
import keyring

SERVICE   = "baum-reseller-mercari"
DOMAIN    = "mercari.com"
SESSION   = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_session.json")
PROFILE   = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_profile")
LOGIN_URL = "https://www.mercari.com/login/"
HOME_URL  = "https://www.mercari.com/"

_AUTH = ("/login", "/register", "/verify", "/otp", "/two-step", "/2fa",
         "/confirm", "/auth/", "/signin")


def _is_logged_in(url: str) -> bool:
    return "mercari.com" in url and not any(a in url for a in _AUTH)


class MercariService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = "") -> str | None:
        try:
            keyring.set_password(SERVICE, "credentials",
                                 json.dumps({"email": email, "password": password}))
            from app.utils.config import set_value
            set_value("mercari_email", email)
            return None
        except Exception as e:
            return str(e)

    def has_session(self) -> bool:
        if os.path.exists(SESSION):
            return True
        for legacy in (
            os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_state.json"),
            os.path.join(PROFILE, "Default", "Cookies"),
        ):
            if os.path.exists(legacy):
                return True
        return False

    def clear_session(self):
        import shutil
        for path in (SESSION,
                     os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_state.json")):
            if os.path.exists(path):
                os.remove(path)
        if os.path.exists(PROFILE):
            shutil.rmtree(PROFILE, ignore_errors=True)

    # ── Browser login ─────────────────────────────────────────────────────

    def open_in_browser(self):
        from app.utils.browser import open_in_system_browser
        open_in_system_browser(LOGIN_URL)

    def import_session(self, done_cb=None):
        from app.utils.browser import import_cookies_from_browser
        import_cookies_from_browser(DOMAIN, SESSION, done_cb=done_cb)

    def import_from_file(self, file_path: str) -> tuple[bool, str]:
        from app.utils.browser import import_cookies_from_file
        return import_cookies_from_file(file_path, SESSION)

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "Not logged in — click 'Login with Browser'."
        session_file = SESSION if os.path.exists(SESSION) else None
        if not session_file:
            legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_state.json")
            session_file = legacy if os.path.exists(legacy) else None
        if not session_file:
            return False, "Session file missing — click 'Login with Browser'."
        try:
            from playwright.sync_api import sync_playwright
            from app.utils.browser import headless_context
            with sync_playwright() as p:
                browser, ctx = headless_context(p, session_file)
                page = ctx.new_page()
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20_000)
                url = page.url
                browser.close()
            if any(a in url for a in _AUTH):
                self.clear_session()
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to Mercari ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        session_file = SESSION if os.path.exists(SESSION) else None
        if not session_file:
            raise ValueError("Not logged in to Mercari. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context
        intercepted: list[dict] = []

        def _on_response(response):
            url = response.url
            if ("api.mercari" in url or "mercari.com/v1/" in url) and (
                "items" in url or "listings" in url
            ):
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        with sync_playwright() as p:
            browser, ctx = headless_context(p, session_file)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking Mercari session…")

            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            if any(a in page.url for a in _AUTH):
                browser.close()
                self.clear_session()
                raise ValueError("Mercari session expired — please re-authenticate.")

            if progress_cb:
                progress_cb("Loading Mercari listings…")

            page.goto("https://www.mercari.com/mypage/listings/",
                      wait_until="networkidle", timeout=30_000)
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_200)

            page.goto("https://www.mercari.com/mypage/listings/?status=sold_out",
                      wait_until="networkidle", timeout=30_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            dom_results = _scrape_dom(page) if not intercepted else []
            browser.close()

        return _parse_api_responses(intercepted) if intercepted else dom_results


def _scrape_dom(page) -> list[dict]:
    items = page.evaluate("""
        () => Array.from(document.querySelectorAll(
            '[data-testid="item-cell"], .Item__ItemWrapper, [class*="itemCell"]'
        )).map(c => {
            const link  = c.querySelector('a');
            const img   = c.querySelector('img');
            const name  = c.querySelector('[class*="name"], [class*="title"]');
            const price = c.querySelector('[class*="price"]');
            return {
                url:     link  ? link.href               : '',
                title:   name  ? name.textContent.trim() : '',
                price:   price ? price.textContent.replace(/[^0-9.]/g,'') : '0',
                img_url: img   ? img.src                 : '',
            };
        }).filter(i => i.url && i.title)
    """)
    results = []
    for item in items:
        m = re.search(r"/item/([^/?#]+)", item.get("url", ""))
        lid = m.group(1) if m else item["url"][-20:]
        results.append({
            "listing_id": lid,
            "title":      item["title"],
            "url":        item["url"],
            "price":      float(item.get("price") or 0),
            "status":     "active",
            "img_url":    item.get("img_url", ""),
        })
    return results


def _parse_api_responses(responses: list[dict]) -> list[dict]:
    seen, results = set(), []
    for resp in responses:
        items = (resp.get("items") or resp.get("data", {}).get("items") or
                 resp.get("result") or [])
        if isinstance(items, dict):
            items = items.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            lid = str(item.get("id") or "")
            if not lid or lid in seen:
                continue
            seen.add(lid)
            status_raw = str(item.get("status") or "on_sale").lower()
            status = "sold" if "sold" in status_raw or "trading" in status_raw else "active"
            results.append({
                "listing_id": lid,
                "title":      item.get("name") or "Untitled",
                "url":        f"https://www.mercari.com/item/{lid}/",
                "price":      float(item.get("price") or 0),
                "status":     status,
                "img_url":    (item.get("thumbnails", [{}])[0].get("url", "")
                               if item.get("thumbnails") else ""),
            })
    return results
