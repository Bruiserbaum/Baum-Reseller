"""
Mercari integration — Playwright browser automation.
Supports email/password AND Google SSO.
Session state saved to disk for headless reuse.
"""
import os
import re
import json
import threading
import keyring

SERVICE = "baum-reseller-mercari"
STATE_FILE = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_state.json")
LOGIN_URL = "https://www.mercari.com/login/"
HOME_URL = "https://www.mercari.com/"


class MercariService:

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

    # ── Browser login ─────────────────────────────────────────────────────

    def login_browser(self, done_cb=None):
        """
        Open a visible browser so the user can log in via any method
        (email/password or 'Continue with Google').
        """
        def _worker():
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
                creds = self.get_credentials()

                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=False, slow_mo=50)
                    ctx = browser.new_context()
                    page = ctx.new_page()

                    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

                    # Auto-fill if saved credentials exist
                    if creds.get("email"):
                        try:
                            page.fill('input[type="email"]', creds["email"], timeout=3_000)
                            if creds.get("password"):
                                page.fill('input[type="password"]', creds["password"], timeout=3_000)
                        except Exception:
                            pass

                    # Wait for login to complete (up to 3 min)
                    try:
                        page.wait_for_url(
                            lambda url: "mercari.com" in url and "/login" not in url,
                            timeout=180_000
                        )
                    except PWTimeout:
                        browser.close()
                        if done_cb:
                            done_cb(False, "Login timed out — window was open for 3 minutes.")
                        return

                    ctx.storage_state(path=STATE_FILE)
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
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20_000)
                # Check for logged-in indicator
                logged_in = page.query_selector('[data-testid="thumbnail"], .UserThumbnail, [class*="userThumbnail"]') is not None
                url = page.url
                browser.close()
            if "/login" in url or not logged_in:
                self.clear_session()
                return False, "Session expired — click Login to re-authenticate."
            return True, "Connected"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not self.has_session():
            raise ValueError("Not logged in to Mercari. Click Login in Settings first.")

        from playwright.sync_api import sync_playwright

        results = []
        intercepted: list[dict] = []

        def _on_response(response):
            """Capture Mercari's internal API responses."""
            url = response.url
            if ("api.mercari" in url or "mercari.com/v1/" in url) and (
                "items" in url or "listings" in url
            ):
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
                progress_cb("Checking Mercari session…")

            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)

            if "/login" in page.url:
                browser.close()
                self.clear_session()
                raise ValueError("Mercari session expired. Please re-authenticate.")

            if progress_cb:
                progress_cb("Fetching Mercari listings…")

            # Mercari seller dashboard / my listings page
            page.goto(
                "https://www.mercari.com/mypage/listings/",
                wait_until="networkidle", timeout=30_000
            )

            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_200)

            # Also load sold items
            page.goto(
                "https://www.mercari.com/mypage/listings/?status=sold_out",
                wait_until="networkidle", timeout=30_000
            )
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            # DOM fallback if API interception found nothing
            if not intercepted:
                results = _scrape_dom(page)

            browser.close()

        if intercepted:
            results = _parse_api_responses(intercepted)

        return results


def _scrape_dom(page) -> list[dict]:
    """DOM fallback — extract listing cards from the page."""
    items = page.evaluate("""
        () => {
            const cards = document.querySelectorAll(
                '[data-testid="item-cell"], .Item__ItemWrapper, [class*="itemCell"]'
            );
            return Array.from(cards).map(c => {
                const link = c.querySelector('a');
                const img  = c.querySelector('img');
                const name = c.querySelector('[class*="name"], [class*="title"]');
                const price = c.querySelector('[class*="price"]');
                return {
                    url:   link  ? link.href              : '',
                    title: name  ? name.textContent.trim() : '',
                    price: price ? price.textContent.replace(/[^0-9.]/g, '') : '0',
                    img_url: img ? img.src : '',
                };
            }).filter(i => i.url && i.title);
        }
    """)

    results = []
    for item in items:
        m = re.search(r"/item/([^/?#]+)", item.get("url", ""))
        lid = m.group(1) if m else item["url"][-20:]
        results.append({
            "listing_id": lid,
            "title": item["title"],
            "url": item["url"],
            "price": float(item.get("price") or 0),
            "status": "active",
            "img_url": item.get("img_url", ""),
        })
    return results


def _parse_api_responses(responses: list[dict]) -> list[dict]:
    """Parse Mercari internal API JSON into our listing format."""
    seen = set()
    results = []

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
                "title": item.get("name") or "Untitled",
                "url": f"https://www.mercari.com/item/{lid}/",
                "price": float(item.get("price") or 0),
                "status": status,
                "img_url": item.get("thumbnails", [{}])[0].get("url", "") if item.get("thumbnails") else "",
            })

    return results
