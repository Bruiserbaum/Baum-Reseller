"""
Poshmark integration.
Login: opens poshmark.com in the user's own browser; imports existing cookies.
Sync:  headless Playwright using saved session cookies.
"""
import os
import re
import json
import keyring

SERVICE        = "baum-reseller-poshmark"
DOMAIN         = "poshmark.com"
SESSION        = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_session.json")
PROFILE        = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_profile")
USERNAME_FILE  = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_username.txt")
LOGIN_URL      = "https://poshmark.com/login"
FEED_URL       = "https://poshmark.com/feed"

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

        def _cache_username(page):
            """Called right after session is saved, browser still open."""
            try:
                # Navigate to /closet — redirects to /closet/{username} when logged in
                page.goto("https://poshmark.com/closet",
                          wait_until="domcontentloaded", timeout=15_000)
                m = re.search(r"poshmark\.com/closet/([^/?&#]+)", page.url)
                if not m:
                    # Client-side routing: URL stays at /closet, but content has username
                    try:
                        page.wait_for_selector('a[href*="/listing/"]', timeout=5_000)
                    except Exception:
                        pass
                    m = re.search(r'"username"\s*:\s*"([^"]{2,40})"', page.content())
                if m:
                    uname = m.group(1)
                    with open(USERNAME_FILE, "w", encoding="utf-8") as f:
                        f.write(uname)
            except Exception:
                pass

        open_login_browser(
            platform="poshmark",
            start_url=LOGIN_URL,
            # ** wildcard matches feed?login=true and any other query params
            success_glob="https://poshmark.com/feed**",
            done_cb=done_cb,
            post_save_cb=_cache_username,
        )

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

    def _get_username_from_session(self) -> str:
        """Extract username from cache file or session cookies — no page load needed."""
        # Fastest path: cached username file written at login time
        if os.path.exists(USERNAME_FILE):
            try:
                with open(USERNAME_FILE, "r", encoding="utf-8") as f:
                    uname = f.read().strip()
                    if uname:
                        return uname
            except Exception:
                pass

        session_file = self._get_session_file()
        if not session_file:
            return ""
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            cookies = {c.get("name", ""): c.get("value", "") for c in data.get("cookies", [])}

            # Legacy / explicit username cookies
            for name in ("poshmark_username", "_poshmark_login", "pm_username"):
                if cookies.get(name, "").strip():
                    return cookies[name].strip()

            # Decode the JWT payload (base64, no signature needed)
            jwt_val = cookies.get("jwt", "")
            if jwt_val:
                try:
                    import base64 as _b64
                    parts = jwt_val.split(".")
                    if len(parts) >= 2:
                        pad = parts[1] + "=" * (-len(parts[1]) % 4)
                        payload = json.loads(_b64.b64decode(pad).decode("utf-8", errors="ignore"))
                        uname = (payload.get("username") or payload.get("sub") or
                                 payload.get("handle") or payload.get("login") or "")
                        if uname and 2 <= len(uname) <= 60 and "/" not in uname:
                            return uname.strip()
                except Exception:
                    pass

            # __ps_lu cookie sometimes holds "username" directly (URL-encoded)
            for name in ("__ps_lu", "__ps_slu"):
                val = cookies.get(name, "")
                if val:
                    try:
                        from urllib.parse import unquote
                        val = unquote(val)
                        if 2 <= len(val) <= 40 and " " not in val and "=" not in val:
                            return val.strip()
                    except Exception:
                        pass
        except Exception:
            pass
        return ""

    def _get_username(self, page) -> str:
        # 1. Try session cookies first (JWT decode etc.)
        uname = self._get_username_from_session()
        if uname:
            return uname
        # 2. Parse __NEXT_DATA__ script tag (Poshmark embeds user info there)
        try:
            el = page.query_selector("script#__NEXT_DATA__")
            if el:
                nd = json.loads(el.inner_text())
                for path in (
                    ("props", "pageProps", "currentUser", "username"),
                    ("props", "pageProps", "user", "username"),
                    ("props", "pageProps", "vm", "user", "username"),
                    ("props", "pageProps", "userProfile", "username"),
                ):
                    obj = nd
                    for key in path:
                        obj = obj.get(key, {}) if isinstance(obj, dict) else {}
                    if isinstance(obj, str) and obj:
                        return obj.strip()
        except Exception:
            pass
        # 3. Regex on raw page source
        try:
            m = re.search(r'"username"\s*:\s*"([^"]{2,40})"', page.content())
            if m:
                return m.group(1)
        except Exception:
            pass
        # 4. Follow /closet redirect to extract username from final URL
        try:
            page.goto("https://poshmark.com/closet",
                      wait_until="domcontentloaded", timeout=15_000)
            m = re.search(r"poshmark\.com/closet/([^/?&#]+)", page.url)
            if m:
                return m.group(1)
        except Exception:
            pass
        # 5. DOM selectors as last resort
        for selector in (
            'a[href*="/closet/"]',
            '[data-et-name="my_closet"]',
            '[href*="/closet/"][class*="nav"]',
            'a[data-testid*="closet"]',
        ):
            try:
                el = page.query_selector(selector)
                if el:
                    href = el.get_attribute("href") or ""
                    m = re.search(r"/closet/([^/?&#]+)", href)
                    if m:
                        return m.group(1)
            except Exception:
                continue
        return ""

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        session_file = self._get_session_file()
        if not session_file:
            raise ValueError("Not logged in to Poshmark. Click 'Login with Browser' first.")

        import datetime as _dt
        import json as _json
        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context

        intercepted: list[dict] = []
        xhr_urls: list[str] = []

        def _on_response(response):
            url = response.url
            rtype = response.request.resource_type
            if rtype not in ("xhr", "fetch"):
                return
            xhr_urls.append(url)
            # Broader filter: any Poshmark API or listing-shaped response
            is_poshmark_api = (
                "poshmark.com" in url and (
                    "vm-rest" in url or
                    "/api/" in url or
                    "listings" in url or
                    "closet" in url or
                    "posts" in url or
                    "catalog" in url
                )
            )
            if is_poshmark_api:
                try:
                    data = response.json()
                    if isinstance(data, (dict, list)):
                        intercepted.append({"url": url, "body": data})
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

            # Wait for the nav to hydrate so closet links are present in the DOM
            try:
                page.wait_for_selector('a[href*="/closet/"]', timeout=8_000)
            except Exception:
                pass

            username = self._get_username(page)
            if not username:
                browser.close()
                raise ValueError(
                    "Could not determine your Poshmark username.\n\n"
                    "Please click 'Log Out' on the Sync page, then 'Login (Browser)' "
                    "to refresh your session."
                )

            if progress_cb:
                progress_cb(f"Loading closet for @{username}…")

            # sort_by=added puts newest listings first — ensures recent items are captured
            page.goto(f"https://poshmark.com/closet/{username}?sort_by=added",
                      wait_until="load", timeout=30_000)
            page.wait_for_timeout(3_000)
            prev_count = 0
            for _ in range(30):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_200)
                cur = page.evaluate(
                    "document.querySelectorAll('a[href*=\"/listing/\"]').length"
                )
                if cur == prev_count:
                    break
                prev_count = cur

            active_dom = _scrape_dom(page, status="active")

            if progress_cb:
                progress_cb(f"Loading sold items for @{username}…")

            page.goto(f"https://poshmark.com/closet/{username}?availability=sold_out&sort_by=added",
                      wait_until="load", timeout=30_000)
            page.wait_for_timeout(3_000)
            prev_count = 0
            for _ in range(15):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)
                cur = page.evaluate(
                    "document.querySelectorAll('a[href*=\"/listing/\"]').length"
                )
                if cur == prev_count:
                    break
                prev_count = cur

            sold_dom = _scrape_dom(page, status="sold")
            browser.close()

        api_results = _parse_api_responses([e["body"] for e in intercepted])
        dom_results = active_dom + sold_dom
        result = api_results if len(api_results) >= len(dom_results) else dom_results

        # ── Debug log ─────────────────────────────────────────────────────
        _debug = {
            "timestamp":              _dt.datetime.now().isoformat(),
            "platform":               "poshmark",
            "username":               username,
            "xhr_responses_captured": len(intercepted),
            "xhr_urls":               xhr_urls[:20],
            "xhr_body_top_keys":      [
                list(e["body"].keys())[:6] if isinstance(e["body"], dict) else []
                for e in intercepted[:10]
            ],
            "xhr_items_parsed":       len(api_results),
            "dom_active_items":       len(active_dom),
            "dom_sold_items":         len(sold_dom),
            "total_returned":         len(result),
            "sample_items":           result[:3],
        }
        _debug_path = os.path.join(
            os.path.expanduser("~"), ".baum-reseller", "debug_poshmark_sync.json"
        )
        try:
            with open(_debug_path, "w", encoding="utf-8") as _f:
                _json.dump(_debug, _f, indent=2, default=str)
        except Exception:
            pass

        return result


def _scrape_dom(page, status: str = "active") -> list[dict]:
    """
    Walk every /listing/ link in the page — class-agnostic, survives UI changes.
    """
    raw = page.evaluate(r"""
        () => {
            const seen = new Set();
            const results = [];
            for (const link of document.querySelectorAll('a[href*="/listing/"]')) {
                const m = link.href.match(/\/listing\/([^/?#]+)/);
                if (!m) continue;
                const lid = m[1];
                if (seen.has(lid) || lid.length < 5) continue;
                seen.add(lid);
                const container = link.closest(
                    '[data-et-name], [class*="listing"], [class*="Listing"], li, article'
                ) || link;
                const img   = container.querySelector('img');
                const price = container.querySelector(
                    '[class*="price"], [class*="Price"], [data-et-name*="price"]'
                );
                const name  = container.querySelector(
                    '[class*="title"], [class*="Title"], [class*="name"], [class*="Name"]'
                );
                let title = name ? name.textContent.trim() : link.textContent.trim();
                if (!title) title = 'Untitled';
                results.push({
                    url:     link.href,
                    title,
                    price:   price ? price.textContent.replace(/[^0-9.]/g, '') : '0',
                    img_url: img ? img.src : '',
                });
            }
            return results;
        }
    """)
    results = []
    seen: set[str] = set()
    for item in (raw or []):
        m = re.search(r"/listing/([^/?#]+)", item.get("url", ""))
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
