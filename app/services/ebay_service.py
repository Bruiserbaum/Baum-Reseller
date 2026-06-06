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
DOMAIN     = "ebay.com"
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
            rtype = response.request.resource_type
            if rtype not in ("xhr", "fetch"):
                return
            # Accept any XHR/fetch JSON response — the eBay Seller Hub SPA can
            # call many different API hostnames (/sh/api, api.ebay.com, etc.).
            # _extract_item_list() filters out non-listing responses by content.
            try:
                data = response.json()
                if isinstance(data, dict):
                    intercepted.append({"url": response.url, "body": data})
            except Exception:
                pass

        with sync_playwright() as p:
            browser, ctx = headless_context(p, SESSION)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking eBay session…")

            # "load" + explicit selector wait is more reliable than "networkidle"
            # (eBay fires background analytics pings indefinitely).
            page.goto(ACTIVE_URL, wait_until="load", timeout=30_000)

            if any(a in page.url for a in _AUTH):
                browser.close()
                self.clear_session()
                raise ValueError("eBay session expired — please re-authenticate.")

            # Wait for the listing table to appear, then give XHR extra time
            try:
                page.wait_for_selector(
                    '[class*="shui-dt-row"], [class*="sh-llt__row"], '
                    '[class*="listing-row"], [data-testid*="listing"]',
                    timeout=10_000,
                )
            except Exception:
                pass  # proceed even if selector not found
            page.wait_for_timeout(2_000)

            if progress_cb:
                progress_cb("Loading eBay active listings…")

            # Scroll to trigger lazy pagination
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)

            # Capture DOM before navigating away
            active_dom = _scrape_seller_hub(page, status="active")

            if progress_cb:
                progress_cb("Loading eBay sold listings…")

            page.goto(SOLD_URL, wait_until="load", timeout=30_000)
            try:
                page.wait_for_selector(
                    '[class*="shui-dt-row"], [class*="sh-llt__row"], '
                    '[class*="listing-row"], [data-testid*="listing"]',
                    timeout=10_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2_000)

            for _ in range(4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)

            sold_dom = _scrape_seller_hub(page, status="sold")
            browser.close()

        dom_results = active_dom + sold_dom
        parsed = _parse_intercepted(intercepted)
        return parsed if parsed else dom_results


def _scrape_seller_hub(page, status: str = "active") -> list[dict]:
    """
    DOM fallback: walk every /itm/ anchor in the current page and harvest
    title + price from its nearest container.  Works regardless of which
    CSS classes eBay uses on a given day.
    """
    raw = page.evaluate(r"""
        () => {
            const seen = new Set();
            const results = [];
            for (const link of document.querySelectorAll('a[href*="/itm/"]')) {
                const m = link.href.match(/\/itm\/(\d+)/);
                if (!m) continue;
                const lid = m[1];
                if (seen.has(lid)) continue;
                seen.add(lid);

                // Walk up to find the row / card container
                const container = link.closest(
                    'tr, [class*="row"], [class*="item"], [class*="listing"], li'
                ) || link.parentElement;

                // Title: prefer a dedicated title element; fall back to link text
                let title = '';
                if (container) {
                    const tel = container.querySelector(
                        '[class*="title"], [data-testid*="title"], h3, h4, h2'
                    );
                    if (tel) title = tel.textContent.trim();
                }
                if (!title) title = link.textContent.trim();
                if (!title) title = 'Untitled';

                // Price
                let price = '0';
                if (container) {
                    const pel = container.querySelector('[class*="price"]');
                    if (pel) {
                        const pm = pel.textContent.match(/[\d,]+\.?\d*/);
                        if (pm) price = pm[0].replace(/,/g, '');
                    }
                }

                results.push({ url: link.href, title, price });
            }
            return results;
        }
    """)
    results = []
    seen: set[str] = set()
    for item in (raw or []):
        m = re.search(r"/itm/(\d+)", item.get("url", ""))
        lid = m.group(1) if m else ""
        if not lid or lid in seen:
            continue
        seen.add(lid)
        try:
            price_val = float(item.get("price") or 0)
        except (ValueError, TypeError):
            price_val = 0.0
        results.append({
            "listing_id": lid,
            "title":      item.get("title", "Untitled"),
            "url":        item.get("url", ""),
            "price":      price_val,
            "status":     status,
            "img_url":    "",
        })
    return results


def _extract_item_list(body: dict) -> list:
    """
    Search a JSON response body for a list that looks like listing records.
    Returns the first non-empty list found, or [].
    """
    if not isinstance(body, dict):
        return []

    # Direct top-level keys
    for key in ("items", "listings", "itemCollection", "itemList",
                "entries", "activeListings", "soldListings", "results",
                "listingCollection"):
        val = body.get(key)
        if isinstance(val, list) and val:
            return val
        if isinstance(val, dict):
            for subkey in ("items", "listing", "entries", "results"):
                sub = val.get(subkey)
                if isinstance(sub, list) and sub:
                    return sub

    # One level deep under common wrapper keys
    for wrapper in ("data", "result", "payload", "response",
                    "searchResult", "body", "content"):
        sub = body.get(wrapper)
        if not isinstance(sub, dict):
            continue
        for key in ("items", "listings", "item", "entries", "results",
                    "activeListings", "soldListings", "listingCollection"):
            val = sub.get(key)
            if isinstance(val, list) and val:
                return val
            if isinstance(val, dict):
                for subkey in ("items", "listing", "entries"):
                    deep = val.get(subkey)
                    if isinstance(deep, list) and deep:
                        return deep

    return []


def _parse_intercepted(responses: list[dict]) -> list[dict]:
    seen, results = set(), []
    for resp in responses:
        items = _extract_item_list(resp.get("body", {}))
        for item in items:
            if not isinstance(item, dict):
                continue

            # Item ID — try several field names
            lid = str(
                item.get("itemId") or item.get("id") or
                item.get("listingId") or item.get("listing_id") or ""
            ).strip()
            if not lid or lid in seen:
                continue
            seen.add(lid)

            # Status
            status_raw = str(
                item.get("listingStatus") or item.get("status") or
                item.get("sellingState") or "active"
            ).lower()
            status = (
                "sold"
                if any(s in status_raw for s in ("sold", "completed", "ended"))
                else "active"
            )

            # Price — may be nested {value, currencyCode} or a bare number
            price_obj = (
                item.get("currentPrice") or item.get("price") or
                item.get("buyItNowPrice") or item.get("soldPrice") or {}
            )
            if isinstance(price_obj, dict):
                price_val = float(
                    price_obj.get("value") or price_obj.get("amount") or 0
                )
            else:
                try:
                    price_val = float(price_obj or 0)
                except (ValueError, TypeError):
                    price_val = 0.0

            title = (
                item.get("title") or item.get("name") or
                item.get("itemTitle") or "Untitled"
            )
            url = (
                item.get("viewItemURL") or item.get("url") or
                item.get("listingUrl") or f"https://www.ebay.com/itm/{lid}"
            )
            img = (
                item.get("galleryURL") or item.get("pictureUrl") or
                item.get("imageUrl") or item.get("thumbnail") or ""
            )

            results.append({
                "listing_id": lid,
                "title":      title,
                "url":        url,
                "price":      price_val,
                "status":     status,
                "img_url":    img,
            })
    return results
