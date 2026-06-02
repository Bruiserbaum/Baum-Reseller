"""
Mercari integration — Playwright browser automation.
Uses a persistent Chrome/Edge profile so Google Sign-In works and sessions survive
between app restarts without re-authentication.
"""
import os
import re
import json
import threading
import keyring

SERVICE     = "baum-reseller-mercari"
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_profile")
LOGIN_URL   = "https://www.mercari.com/login/"
HOME_URL    = "https://www.mercari.com/"

_ANTI_DETECT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
    window.chrome = { runtime: {} };
"""


def _launch_persistent_context(playwright, headless: bool):
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
        cookies = os.path.join(PROFILE_DIR, "Default", "Cookies")
        if os.path.exists(PROFILE_DIR) and os.path.exists(cookies):
            return True
        legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_state.json")
        return os.path.exists(legacy)

    def clear_session(self):
        import shutil
        if os.path.exists(PROFILE_DIR):
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        legacy = os.path.join(os.path.expanduser("~"), ".baum-reseller", "mercari_state.json")
        if os.path.exists(legacy):
            os.remove(legacy)

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

                    if creds.get("email"):
                        try:
                            page.fill('input[type="email"]', creds["email"], timeout=3_000)
                            if creds.get("password"):
                                page.fill('input[type="password"]', creds["password"], timeout=3_000)
                        except Exception:
                            pass

                    # Wait for a known post-login page — NOT just "not /login"
                    # because 2FA/OTP pages also satisfy that condition.
                    _BLOCKED = ("/login", "/register", "/verify", "/otp",
                                "/two-step", "/2fa", "/confirm", "/auth/")

                    try:
                        page.wait_for_url(
                            lambda url: (
                                "mercari.com" in url and
                                not any(x in url for x in _BLOCKED)
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
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20_000)
                url  = page.url
                ctx.close()
            if "/login" in url:
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to Mercari ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not self.has_session():
            raise ValueError("Not logged in to Mercari. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
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
            ctx  = _launch_persistent_context(p, headless=True)
            page = ctx.new_page()
            page.add_init_script(_ANTI_DETECT_SCRIPT)
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking Mercari session…")

            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            if "/login" in page.url:
                ctx.close()
                return []

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
            ctx.close()

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
