"""
Poshmark integration — Playwright browser automation.
Uses a persistent Chrome/Edge profile so Google Sign-In works and sessions survive
between app restarts without re-authentication.
"""
import os
import re
import json
import threading
import keyring

SERVICE     = "baum-reseller-poshmark"
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_profile")
LOGIN_URL   = "https://poshmark.com/login"
FEED_URL    = "https://poshmark.com/feed"

# Script injected into every page to hide automation fingerprints from Google / sites
_ANTI_DETECT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
    window.chrome = { runtime: {} };
"""


def _launch_persistent_context(playwright, headless: bool):
    """
    Launch a persistent browser context (profile survives restarts).
    Tries Chrome → Edge → Playwright Chromium.
    Automation detection flags are stripped so Google Sign-In works.
    """
    os.makedirs(PROFILE_DIR, exist_ok=True)

    base_kwargs = dict(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--start-maximized",
        ],
        ignore_default_args=["--enable-automation"],
        viewport=None,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    if not headless:
        base_kwargs["slow_mo"] = 50

    last_err = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = dict(base_kwargs)
            if channel:
                kwargs["channel"] = channel
            return playwright.chromium.launch_persistent_context(PROFILE_DIR, **kwargs)
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"No usable browser found (Chrome, Edge, or Playwright Chromium).\n"
        f"Install Chrome/Edge or run:  py -m playwright install chromium\n\n{last_err}"
    )


class PoshmarkService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = "") -> str | None:
        try:
            keyring.set_password(SERVICE, "credentials",
                                 json.dumps({"email": email, "password": password}))
            # Also persist email (non-sensitive) in config.json so it survives reinstalls
            from app.utils.config import set_value
            set_value("poshmark_email", email)
            return None
        except Exception as e:
            return str(e)

    def has_session(self) -> bool:
        """
        Session exists if the persistent profile has been written to disk.
        Also accepts the legacy poshmark_state.json from v1.1.x so users
        who authenticated before v1.1.2 aren't forced to re-login.
        """
        # Current: persistent Chrome profile
        cookies = os.path.join(PROFILE_DIR, "Default", "Cookies")
        if os.path.exists(PROFILE_DIR) and os.path.exists(cookies):
            return True
        # Legacy (v1.1.1 and earlier): storage_state JSON file
        legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")
        return os.path.exists(legacy)

    def clear_session(self):
        import shutil
        if os.path.exists(PROFILE_DIR):
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        # Also remove legacy state file if present
        legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "poshmark_state.json")
        if os.path.exists(legacy):
            os.remove(legacy)

    # ── Browser login ─────────────────────────────────────────────────────

    def login_browser(self, done_cb=None):
        """
        Open a visible browser with a persistent profile.
        The user can sign in with email/password OR Google — no automation
        blocking because webdriver flags are stripped.
        """
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

                    # Auto-fill email/password if saved (user can still choose Google SSO)
                    if creds.get("email"):
                        try:
                            page.fill('input[id="email_address"]', creds["email"], timeout=3_000)
                            if creds.get("password"):
                                page.fill('input[type="password"]', creds["password"], timeout=3_000)
                        except Exception:
                            pass

                    # Wait for a known post-login page — NOT just "not /login",
                    # because 2FA pages (/verify-otp, /two-step, etc.) also
                    # satisfy that condition and would close the browser too early.
                    _LOGGED_IN = ("/feed", "/news", "/home", "/closet", "/dashboard", "/account")

                    try:
                        page.wait_for_url(
                            lambda url: (
                                "poshmark.com" in url and
                                any(path in url for path in _LOGGED_IN)
                            ),
                            timeout=300_000   # 5 min — extra time for 2FA
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
                page.goto(FEED_URL, wait_until="domcontentloaded", timeout=20_000)
                url  = page.url
                ctx.close()
            if "/login" in url:
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to Poshmark ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

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
        if not self.has_session():
            raise ValueError("Not logged in to Poshmark. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
        intercepted: list[dict] = []

        def _on_response(response):
            if "vm-rest" in response.url and ("posts" in response.url or "listings" in response.url):
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        with sync_playwright() as p:
            ctx  = _launch_persistent_context(p, headless=True)
            page = ctx.new_page()
            page.add_init_script(_ANTI_DETECT_SCRIPT)
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking Poshmark session…")

            page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30_000)
            if "/login" in page.url:
                ctx.close()
                return []

            username = self._get_username(page)
            if not username:
                ctx.close()
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

            ctx.close()

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
