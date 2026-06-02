"""
eBay integration.
Login: real Chrome/Edge subprocess — no automation flags, supports Google SSO + 2FA.
Sync:  headless Playwright using saved session cookies (eBay Seller Hub).
"""
import os
import re
import json
import keyring

SERVICE    = "baum-reseller-ebay"
SESSION    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "ebay_session.json")
PROFILE    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "ebay_profile")
LOGIN_URL  = "https://www.ebay.com/signin/"
ACTIVE_URL = "https://www.ebay.com/sh/lst/active"
SOLD_URL   = "https://www.ebay.com/sh/lst/sold"

_AUTH = ("/signin", "/verify", "/challenge", "/otp", "/confirm",
         "/security", "/two-factor", "/auth/")


def _is_logged_in(url: str) -> bool:
    return "ebay.com" in url and not any(a in url for a in _AUTH)


class EbayService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = "") -> str | None:
        try:
            keyring.set_password(SERVICE, "credentials",
                                 json.dumps({"email": email, "password": password}))
            from app.utils.config import set_value
            set_value("ebay_email", email)
            return None
        except Exception as e:
            return str(e)

    def has_session(self) -> bool:
        if os.path.exists(SESSION):
            return True
        return os.path.exists(os.path.join(PROFILE, "Default", "Cookies"))

    def clear_session(self):
        import shutil
        if os.path.exists(SESSION):
            os.remove(SESSION)
        if os.path.exists(PROFILE):
            shutil.rmtree(PROFILE, ignore_errors=True)

    # ── Browser login ─────────────────────────────────────────────────────

    def login_browser(self, done_cb=None):
        from app.utils.browser import launch_login_window
        launch_login_window(
            login_url=LOGIN_URL,
            profile_dir=PROFILE,
            is_logged_in=_is_logged_in,
            state_file=SESSION,
            done_cb=done_cb,
        )

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "Not logged in — click 'Login with Browser'."
        if not os.path.exists(SESSION):
            return False, "Session file missing — click 'Login with Browser'."
        try:
            from playwright.sync_api import sync_playwright
            from app.utils.browser import headless_context
            with sync_playwright() as p:
                browser, ctx = headless_context(p, SESSION)
                page = ctx.new_page()
                page.goto(ACTIVE_URL, wait_until="domcontentloaded", timeout=20_000)
                url = page.url
                browser.close()
            if any(a in url for a in _AUTH):
                self.clear_session()
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to eBay ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not os.path.exists(SESSION):
            raise ValueError("Not logged in to eBay. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context
        intercepted: list[dict] = []

        def _on_response(response):
            url = response.url
            if "ebay.com/sh/" in url and response.request.resource_type in ("xhr", "fetch"):
                try:
                    intercepted.append({"url": url, "body": response.json()})
                except Exception:
                    pass

        with sync_playwright() as p:
            browser, ctx = headless_context(p, SESSION)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking eBay session…")

            page.goto(ACTIVE_URL, wait_until="networkidle", timeout=30_000)

            if any(a in page.url for a in _AUTH):
                browser.close()
                self.clear_session()
                raise ValueError("eBay session expired — please re-authenticate.")

            if progress_cb:
                progress_cb("Loading eBay active listings…")

            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            page.goto(SOLD_URL, wait_until="networkidle", timeout=30_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            dom_results = _scrape_seller_hub(page)
            browser.close()

        return _parse_intercepted(intercepted) or dom_results


def _scrape_seller_hub(page) -> list[dict]:
    items = page.evaluate("""
        () => Array.from(document.querySelectorAll(
            '.sh-llt__row, [class*="listing-row"], .shui-dt-row'
        )).map(row => {
            const link  = row.querySelector('a[href*="/itm/"]');
            const title = row.querySelector('[class*="title"], [class*="item-title"]');
            const price = row.querySelector('[class*="price"]');
            return {
                url:   link  ? link.href               : '',
                title: title ? title.textContent.trim() : '',
                price: price ? price.textContent.replace(/[^0-9.]/g, '') : '0',
            };
        }).filter(i => i.url)
    """)
    results = []
    for item in items:
        m = re.search(r"/itm/(\d+)", item.get("url", ""))
        lid = m.group(1) if m else ""
        if not lid:
            continue
        results.append({
            "listing_id": lid,
            "title":      item.get("title", "Untitled"),
            "url":        item.get("url", ""),
            "price":      float(item.get("price") or 0),
            "status":     "active",
            "img_url":    "",
        })
    return results


def _parse_intercepted(responses: list[dict]) -> list[dict]:
    seen, results = set(), []
    for resp in responses:
        body = resp.get("body", {})
        items = (body.get("items") or body.get("listings") or
                 body.get("data", {}).get("items") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            lid = str(item.get("itemId") or item.get("id") or "")
            if not lid or lid in seen:
                continue
            seen.add(lid)
            status_raw = str(item.get("listingStatus") or item.get("status") or "active").lower()
            status = "sold" if "sold" in status_raw or "completed" in status_raw else "active"
            price = item.get("currentPrice", {}) or item.get("price", {})
            price_val = float(price.get("value", 0)) if isinstance(price, dict) else float(price or 0)
            results.append({
                "listing_id": lid,
                "title":      item.get("title") or "Untitled",
                "url":        item.get("viewItemURL") or f"https://www.ebay.com/itm/{lid}",
                "price":      price_val,
                "status":     status,
                "img_url":    item.get("galleryURL") or item.get("pictureUrl") or "",
            })
    return results
