"""
Mercari integration — Playwright browser automation with persistent sessions.

First login:  click Login in Settings → headed browser opens → log in manually
              (MFA, captcha, etc. all work) → session saved automatically.
Subsequent:   headless browser reuses saved session to scrape listings.
Session expiry: scraper detects /login redirect, clears stale session, raises error.
"""

import json
import keyring
from .session_manager import has_session, clear_session, open_login_browser, headless_page

SERVICE = "baum-reseller-mercari"


class MercariService:
    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str, password: str):
        keyring.set_password(
            SERVICE, "credentials",
            json.dumps({"email": email, "password": password}),
        )

    def has_session(self) -> bool:
        return has_session("mercari")

    def clear_session(self):
        clear_session("mercari")

    def login(self, done_cb=None):
        """Open headed browser for manual Mercari login. Saves session on success."""
        open_login_browser(
            platform="mercari",
            start_url="https://www.mercari.com/login/",
            # Any non-login Mercari page means the user has logged in
            success_glob="https://www.mercari.com/",
            done_cb=done_cb,
        )

    def test_connection(self) -> tuple[bool, str]:
        if self.has_session():
            return True, "Session active"
        creds = self.get_credentials()
        if creds:
            return False, "Credentials saved — click Login to connect"
        return False, "Not logged in"

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        """Scrape active + sold listings using the saved Playwright session."""
        with headless_page("mercari") as page:
            listings = []

            # ── Active listings ─────────────────────────────────────────────
            page.goto(
                "https://www.mercari.com/mypage/listings/",
                wait_until="networkidle",
                timeout=30_000,
            )
            if "/login" in page.url:
                self.clear_session()
                raise RuntimeError(
                    "Mercari session expired — click Login in Settings to re-authenticate."
                )
            _scroll_to_bottom(page)
            listings.extend(_extract_items(page, "active"))

            if progress_cb:
                progress_cb(50)

            # ── Sold listings ───────────────────────────────────────────────
            page.goto(
                "https://www.mercari.com/mypage/listings/?status=sold_out",
                wait_until="networkidle",
                timeout=30_000,
            )
            _scroll_to_bottom(page)
            listings.extend(_extract_items(page, "sold"))

        return listings


# ── Scraping helpers ──────────────────────────────────────────────────────────

def _extract_items(page, status: str) -> list[dict]:
    """
    Extract item cards from the current Mercari listing page via JavaScript.
    Selects all <a> tags that link to /item/m… — stable across UI changes.
    If Mercari redesigns their URL scheme, update the querySelector below.
    """
    raw = page.evaluate("""
        () => {
            const seen = new Set();
            const results = [];
            document.querySelectorAll('a[href*="/item/m"]').forEach(a => {
                if (seen.has(a.href)) return;
                seen.add(a.href);
                const img = a.querySelector('img');
                const priceEl = a.querySelector(
                    '[class*="price" i], [class*="Price"], [data-testid*="price" i]'
                );
                results.push({
                    href: a.href,
                    title: img ? img.alt : a.textContent.trim().split('\\n')[0],
                    price: priceEl ? priceEl.textContent.trim() : ''
                });
            });
            return results;
        }
    """)

    items = []
    for r in raw:
        href = r.get("href", "")
        parts = href.rstrip("/").split("/")
        listing_id = parts[-1] if parts else ""
        if not listing_id:
            continue
        price_text = (
            r.get("price", "0")
            .replace("$", "").replace(",", "").strip()
        )
        try:
            price = float(price_text)
        except ValueError:
            price = 0.0
        items.append({
            "listing_id": listing_id,
            "title": r.get("title", "").strip(),
            "price": price,
            "url": href,
            "status": status,
            "listed_date": "",
        })
    return items


def _scroll_to_bottom(page, max_scrolls: int = 25):
    """Trigger Mercari's infinite scroll until no new content loads."""
    for _ in range(max_scrolls):
        prev_height = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(900)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == prev_height:
            break
