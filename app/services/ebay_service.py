"""
eBay integration — Playwright browser automation via eBay Seller Hub.
No API keys or developer account required.
Same browser-based login flow as Poshmark and Mercari — supports email/password,
Google Sign-In, and two-factor authentication.
"""
import os
import re
import json
import threading
import keyring

SERVICE     = "baum-reseller-ebay"
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller", "ebay_profile")
LOGIN_URL   = "https://www.ebay.com/signin/"
ACTIVE_URL  = "https://www.ebay.com/sh/lst/active"
SOLD_URL    = "https://www.ebay.com/sh/lst/sold"

_ANTI_DETECT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
    window.chrome = { runtime: {} };
"""

# Auth-related paths — keep waiting while on any of these
_AUTH_PATHS = ("/signin", "/verify", "/challenge", "/otp", "/confirm",
               "/security", "/two-factor", "/auth/")


def _launch_persistent_context(playwright, headless: bool):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    base = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--start-maximized"],
        ignore_default_args=["--enable-automation"],
        viewport=None,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    if not headless:
        base["slow_mo"] = 50

    last_err = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = dict(base)
            if channel:
                kwargs["channel"] = channel
            return playwright.chromium.launch_persistent_context(PROFILE_DIR, **kwargs)
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"No usable browser found.\nInstall Chrome/Edge or run: py -m playwright install chromium\n\n{last_err}"
    )


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
        cookies = os.path.join(PROFILE_DIR, "Default", "Cookies")
        return os.path.exists(PROFILE_DIR) and os.path.exists(cookies)

    def clear_session(self):
        import shutil
        if os.path.exists(PROFILE_DIR):
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)

    # ── Browser login ─────────────────────────────────────────────────────

    def login_browser(self, done_cb=None):
        def _worker():
            ok, err = False, None
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
                creds = self.get_credentials()

                with sync_playwright() as p:
                    ctx  = _launch_persistent_context(p, headless=False)
                    page = ctx.new_page()
                    page.add_init_script(_ANTI_DETECT_SCRIPT)

                    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                    page.bring_to_front()

                    # Auto-fill saved credentials if present
                    if creds.get("email"):
                        try:
                            page.fill('#userid', creds["email"], timeout=3_000)
                            page.click('#signin-continue-btn', timeout=3_000)
                            page.wait_for_timeout(1_000)
                            if creds.get("password"):
                                page.fill('#pass', creds["password"], timeout=3_000)
                        except Exception:
                            pass

                    # Wait for a real post-login page — stays open during 2FA
                    try:
                        page.wait_for_url(
                            lambda url: (
                                "ebay.com" in url and
                                not any(x in url for x in _AUTH_PATHS)
                            ),
                            timeout=300_000   # 5 min for 2FA
                        )
                    except PWTimeout:
                        raise RuntimeError(
                            "Login timed out. If two-factor authentication was required, "
                            "make sure to complete it in the browser window."
                        )

                    ctx.close()
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
            return False, "Not logged in — click 'Login with Browser'."
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ctx  = _launch_persistent_context(p, headless=True)
                page = ctx.new_page()
                page.add_init_script(_ANTI_DETECT_SCRIPT)
                page.goto(ACTIVE_URL, wait_until="domcontentloaded", timeout=20_000)
                url  = page.url
                ctx.close()
            if any(x in url for x in _AUTH_PATHS):
                self.clear_session()
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to eBay ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not self.has_session():
            raise ValueError("Not logged in to eBay. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
        intercepted: list[dict] = []

        def _on_response(response):
            url = response.url
            if "ebay.com/sh/" in url and response.request.resource_type == "xhr":
                try:
                    intercepted.append({"url": url, "body": response.json()})
                except Exception:
                    pass

        with sync_playwright() as p:
            ctx  = _launch_persistent_context(p, headless=True)
            page = ctx.new_page()
            page.add_init_script(_ANTI_DETECT_SCRIPT)
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking eBay session…")

            page.goto(ACTIVE_URL, wait_until="networkidle", timeout=30_000)

            if any(x in page.url for x in _AUTH_PATHS):
                ctx.close()
                self.clear_session()
                raise ValueError("eBay session expired — please re-authenticate.")

            if progress_cb:
                progress_cb("Loading eBay active listings…")

            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            # Also load sold items
            page.goto(SOLD_URL, wait_until="networkidle", timeout=30_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            dom_results = _scrape_seller_hub(page)
            ctx.close()

        return _parse_intercepted(intercepted) or dom_results


def _scrape_seller_hub(page) -> list[dict]:
    """DOM fallback — scrape eBay Seller Hub listing rows."""
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
