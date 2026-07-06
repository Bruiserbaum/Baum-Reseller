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


def _valid_username(s: str) -> bool:
    """Return True only if s looks like a real Poshmark username, not a URL or garbage."""
    return bool(s and 3 <= len(s) <= 40 and ":" not in s and "/" not in s and " " not in s)


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
                    if isinstance(obj, str) and _valid_username(obj):
                        return obj.strip()
        except Exception:
            pass
        # 3. Regex on raw page source — use finditer to skip URL-shaped false positives
        try:
            for m in re.finditer(r'"username"\s*:\s*"([^"]{3,40})"', page.content()):
                candidate = m.group(1)
                if _valid_username(candidate):
                    return candidate
        except Exception:
            pass
        # 4. Follow /closet redirect to extract username from final URL
        try:
            page.goto("https://poshmark.com/closet",
                      wait_until="domcontentloaded", timeout=15_000)
            if not any(a in page.url for a in _AUTH):
                m = re.search(r"poshmark\.com/closet/([^/?&#]+)", page.url)
                if m and _valid_username(m.group(1)):
                    return m.group(1)
                # Client-side routing: URL stayed at /closet, check page content
                for m2 in re.finditer(r'"username"\s*:\s*"([^"]{3,40})"', page.content()):
                    candidate = m2.group(1)
                    if _valid_username(candidate):
                        return candidate
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
                    if m and _valid_username(m.group(1)):
                        return m.group(1)
            except Exception:
                continue
        return ""

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        session_file = self._get_session_file()
        if not session_file:
            raise ValueError("Not logged in to Poshmark. Click 'Login with Browser' first.")

        import datetime as _dt
        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context

        pm_version_found: list[str] = []
        xhr_username: list[str] = []

        def _on_response(response):
            url = response.url
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            if "poshmark.com" not in url:
                return
            # Capture pm_version from the first paginated API call
            if "posts/filtered" in url and not pm_version_found:
                m = re.search(r"pm_version=([^&\s]+)", url)
                if m:
                    pm_version_found.append(m.group(1))
            # Capture username from early XHR responses
            if not xhr_username:
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        for key in ("user", "currentUser", "me", "account"):
                            user = data.get(key)
                            if isinstance(user, dict):
                                uname = user.get("username") or user.get("login") or ""
                                if uname and 2 <= len(str(uname)) <= 60 and "/" not in str(uname):
                                    xhr_username.append(str(uname).strip())
                                    break
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

            try:
                page.wait_for_selector('a[href*="/closet/"]', timeout=8_000)
            except Exception:
                pass

            username = (xhr_username[0] if xhr_username else "") or self._get_username(page)
            if not username:
                browser.close()
                raise ValueError(
                    "Could not determine your Poshmark username.\n\n"
                    "Please click 'Log Out' on the Sync page, then 'Login (Browser)' "
                    "to refresh your session."
                )

            try:
                with open(USERNAME_FILE, "w", encoding="utf-8") as _uf:
                    _uf.write(username)
            except Exception:
                pass

            if progress_cb:
                progress_cb(f"Fetching listings for @{username}…")

            # Load the closet page once so the browser picks up session context and
            # we capture the current pm_version from the first XHR it fires.
            page.goto(f"https://poshmark.com/closet/{username}",
                      wait_until="load", timeout=30_000)
            page.wait_for_timeout(2_000)

            pm_version = pm_version_found[0] if pm_version_found else "2026.27.01"

            # Paginate the full closet via direct API calls — no DOM scrolling
            active_listings = _fetch_all_via_api(
                page, username, inventory_status="all",
                pm_version=pm_version, progress_cb=progress_cb,
            )

            if progress_cb:
                progress_cb(f"Fetching sold listings for @{username}…")

            sold_listings = _fetch_all_via_api(
                page, username, inventory_status="sold_out",
                pm_version=pm_version, progress_cb=progress_cb,
            )

            browser.close()

        all_listings = active_listings + sold_listings

        _debug = {
            "timestamp":      _dt.datetime.now().isoformat(),
            "platform":       "poshmark",
            "username":       username,
            "pm_version":     pm_version,
            "api_active":     len(active_listings),
            "api_sold":       len(sold_listings),
            "total_returned": len(all_listings),
            "sample_items":   all_listings[:3],
        }
        _debug_path = os.path.join(
            os.path.expanduser("~"), ".baum-reseller", "debug_poshmark_sync.json"
        )
        try:
            with open(_debug_path, "w", encoding="utf-8") as _f:
                json.dump(_debug, _f, indent=2, default=str)
        except Exception:
            pass

        return all_listings


def _scrape_dom(page, status: str = "active") -> list[dict]:
    """
    Walk every /listing/ or /edit/listing/ link — handles both the public view
    and the owner's edit view of a Poshmark closet.
    """
    raw = page.evaluate(r"""
        () => {
            const seen = new Set();
            const results = [];
            // Match both public /listing/ and owner /edit/listing/ links
            const selector = 'a[href*="/listing/"], a[href*="/edit/listing/"]';
            for (const link of document.querySelectorAll(selector)) {
                // Extract listing ID from either URL format
                const m = link.href.match(/\/(?:edit\/)?listing\/([^/?#]+)/);
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
                    url:     'https://poshmark.com/listing/' + lid,
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


def _fetch_all_via_api(page, username: str, inventory_status: str = "all",
                       pm_version: str = "2026.27.01", progress_cb=None) -> list[dict]:
    """
    Paginate the Poshmark closet API directly from within the browser session.
    Uses page.evaluate(fetch(...)) so session cookies are automatically included.
    Returns all listings without any DOM scroll limit.
    """
    from urllib.parse import quote

    results: list[dict] = []
    seen: set[str] = set()
    max_id = None
    page_num = 0
    base_status = "sold" if inventory_status == "sold_out" else "active"

    while True:
        page_num += 1
        req: dict = {
            "filters": {"department": "All", "inventory_status": [inventory_status]},
            "sort_by": "added_desc",
            "experience": "all",
            "count": 48,
            "static_facets": False,
            "shouldFetchFacetsForClosetRerank": False,
            "shouldFetchFacetsForMobile": False,
        }
        if max_id is not None:
            req["max_id"] = max_id

        api_url = (
            f"https://poshmark.com/vm-rest/users/{username}/posts/filtered"
            f"?request={quote(json.dumps(req, separators=(',', ':')))}"
            f"&summarize=true&app_version=2.55&pm_version={pm_version}"
        )

        try:
            data = page.evaluate("(url) => fetch(url).then(r => r.json())", api_url)
        except Exception:
            break

        if not isinstance(data, dict):
            break

        items = data.get("data") or []
        if not isinstance(items, list) or not items:
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            lid = str(item.get("id") or "")
            if not lid or lid in seen:
                continue
            seen.add(lid)

            inventory = item.get("inventory") or {}
            status_raw = str(
                inventory.get("status") or item.get("status") or "available"
            ).lower()
            item_status = "sold" if ("sold" in status_raw or "not_for_sale" in status_raw) else base_status

            price_data = item.get("price_amount") or item.get("price") or {}
            price = (
                float(price_data.get("val", 0)) if isinstance(price_data, dict)
                else float(price_data or 0)
            )
            cover = item.get("cover_shot") or {}
            results.append({
                "listing_id": lid,
                "title":      item.get("title") or item.get("name") or "Untitled",
                "url":        f"https://poshmark.com/listing/{lid}",
                "price":      price,
                "status":     item_status,
                "img_url":    item.get("picture_url") or cover.get("url_small") or "",
            })

        if progress_cb and page_num % 5 == 0:
            progress_cb(f"Fetched {len(results)} {base_status} listings…")

        more = data.get("more")
        if not more:
            break
        max_id = more

    return results
