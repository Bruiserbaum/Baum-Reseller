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

    def login(self, done_cb=None):
        """
        Open Playwright's own headed Chromium browser for direct login.
        The user logs in manually (MFA, Google SSO, captcha all work normally).
        Session is saved automatically on success — no cookie decryption needed.
        Preferred over Import Session when Chrome 127+ encryption blocks it.
        """
        from app.services.session_manager import open_login_browser
        open_login_browser(
            platform="mercari",
            start_url=LOGIN_URL,
            success_glob="https://www.mercari.com/",
            done_cb=done_cb,
        )

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
            rtype = response.request.resource_type
            if rtype not in ("xhr", "fetch"):
                return
            # Accept any XHR/fetch JSON — Mercari's SPA may call api.mercari.jp,
            # api.mercari.com, or internal paths. Filter by content, not URL.
            try:
                data = response.json()
                if isinstance(data, (dict, list)):
                    intercepted.append(data)
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
                progress_cb("Loading Mercari active listings…")

            # Mercari fires continuous analytics/ad pings — "load" + explicit
            # selector wait is more reliable than waiting for networkidle.
            page.goto("https://www.mercari.com/mypage/listings/",
                      wait_until="load", timeout=30_000)
            try:
                page.wait_for_selector(
                    '[data-testid="item-cell"], [class*="ItemCell"], a[href*="/item/"]',
                    timeout=8_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2_000)
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            # Always scrape DOM (XHR alone is unreliable)
            active_dom = _scrape_dom(page, status="active")

            if progress_cb:
                progress_cb("Loading Mercari sold listings…")

            page.goto("https://www.mercari.com/mypage/listings/?status=sold_out",
                      wait_until="load", timeout=30_000)
            try:
                page.wait_for_selector(
                    '[data-testid="item-cell"], [class*="ItemCell"], a[href*="/item/"]',
                    timeout=8_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

            sold_dom = _scrape_dom(page, status="sold")
            browser.close()

        dom_results = active_dom + sold_dom
        api_results = _parse_api_responses(intercepted)
        result = api_results if len(api_results) >= len(dom_results) else dom_results

        # ── Debug log ─────────────────────────────────────────────────────
        import json as _json, datetime as _dt
        _debug = {
            "timestamp":              _dt.datetime.now().isoformat(),
            "platform":               "mercari",
            "xhr_responses_captured": len(intercepted),
            "xhr_items_parsed":       len(api_results),
            "dom_active_items":       len(active_dom),
            "dom_sold_items":         len(sold_dom),
            "total_returned":         len(result),
            "sample_items":           result[:3],
        }
        _debug_path = os.path.join(
            os.path.expanduser("~"), ".baum-reseller", "debug_mercari_sync.json"
        )
        try:
            with open(_debug_path, "w", encoding="utf-8") as _f:
                _json.dump(_debug, _f, indent=2, default=str)
        except Exception:
            pass

        return result


def _scrape_dom(page, status: str = "active") -> list[dict]:
    """
    Walk every /item/ link in the page — class-agnostic so it survives
    Mercari's hashed styled-component class names.
    """
    raw = page.evaluate(r"""
        () => {
            const seen = new Set();
            const results = [];
            for (const link of document.querySelectorAll('a[href*="/item/"]')) {
                const m = link.href.match(/\/item\/([^/?#]+)/);
                if (!m) continue;
                const lid = m[1];
                if (seen.has(lid) || lid.includes('/')) continue;
                seen.add(lid);

                const container = link.closest(
                    '[data-testid], [class*="item"], [class*="Item"], li, article'
                ) || link;
                const img   = container.querySelector('img');
                const price = container.querySelector(
                    '[class*="price"], [class*="Price"]'
                );
                const name  = container.querySelector(
                    '[class*="name"], [class*="Name"], [class*="title"], [class*="Title"]'
                );
                let title = name ? name.textContent.trim() : link.textContent.trim();
                if (!title) title = 'Untitled';

                results.push({
                    url:     link.href,
                    title,
                    price:   price ? price.textContent.replace(/[^0-9.]/g, '') : '0',
                    img_url: img   ? img.src : '',
                });
            }
            return results;
        }
    """)
    results = []
    seen: set[str] = set()
    for item in (raw or []):
        m = re.search(r"/item/([^/?#]+)", item.get("url", ""))
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
            "img_url":    item.get("img_url", ""),
        })
    return results


def _parse_api_responses(responses: list) -> list[dict]:
    seen, results = set(), []
    for resp in responses:
        # Normalise: response may be a bare list or a dict with various item keys
        if isinstance(resp, list):
            items = resp
        elif isinstance(resp, dict):
            items = (
                resp.get("items") or
                resp.get("data", {}).get("items") or
                resp.get("result") or
                resp.get("listings") or
                []
            )
            if isinstance(items, dict):
                items = items.get("items") or []
        else:
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            lid = str(item.get("id") or "")
            if not lid or lid in seen:
                continue
            seen.add(lid)
            status_raw = str(item.get("status") or "on_sale").lower()
            status = "sold" if ("sold" in status_raw or "trading" in status_raw) else "active"
            try:
                price_val = float(item.get("price") or 0)
            except (ValueError, TypeError):
                price_val = 0.0
            img = ""
            thumbs = item.get("thumbnails")
            if isinstance(thumbs, list) and thumbs:
                img = thumbs[0].get("url", "") if isinstance(thumbs[0], dict) else ""
            results.append({
                "listing_id": lid,
                "title":      item.get("name") or item.get("title") or "Untitled",
                "url":        f"https://www.mercari.com/item/{lid}/",
                "price":      price_val,
                "status":     status,
                "img_url":    img,
            })
    return results
