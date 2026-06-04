"""
Poshmark integration — Playwright browser automation with persistent sessions.

Login flow is identical to Mercari: headed browser → manual login → session saved.
Data fetching uses Poshmark's own internal REST API (called via in-page fetch() so
the session cookies are sent automatically — far more reliable than DOM scraping).
"""

import json
import keyring
from .session_manager import has_session, clear_session, open_login_browser, headless_page

SERVICE = "baum-reseller-poshmark"


class PoshmarkService:
    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str, password: str):
        keyring.set_password(
            SERVICE, "credentials",
            json.dumps({"email": email, "password": password}),
        )

    def has_session(self) -> bool:
        return has_session("poshmark")

    def clear_session(self):
        clear_session("poshmark")

    def login(self, done_cb=None):
        """Open headed browser for manual Poshmark login. Saves session on success."""
        open_login_browser(
            platform="poshmark",
            start_url="https://poshmark.com/login",
            success_glob="https://poshmark.com/feed",
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
        """Fetch active + sold listings via Poshmark's internal REST API."""
        with headless_page("poshmark") as page:
            page.goto("https://poshmark.com/feed", wait_until="networkidle", timeout=30_000)
            if "/login" in page.url:
                self.clear_session()
                raise RuntimeError(
                    "Poshmark session expired — click Login in Settings to re-authenticate."
                )

            username = _get_username(page)
            if not username:
                raise RuntimeError(
                    "Could not determine your Poshmark username. "
                    "Try re-logging in from Settings."
                )

            if progress_cb:
                progress_cb(20)

            listings = []

            # ── Active listings ─────────────────────────────────────────────
            active = _fetch_via_api(page, username, "available")
            listings.extend(active)

            if progress_cb:
                progress_cb(60)

            # ── Sold listings ───────────────────────────────────────────────
            sold = _fetch_via_api(page, username, "sold")
            listings.extend(sold)

        return listings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_username(page) -> str:
    """Extract the logged-in Poshmark username from the nav profile link."""
    return page.evaluate("""
        () => {
            // Poshmark embeds the closet URL in several nav elements
            const sel = 'a[href*="/closet/"], a[data-et-name="closet"]';
            const el = document.querySelector(sel);
            if (!el) return '';
            const m = el.href.match(/\\/closet\\/([^/?#]+)/);
            return m ? m[1] : '';
        }
    """) or ""


def _fetch_via_api(page, username: str, listing_status: str) -> list[dict]:
    """
    Call Poshmark's internal listing API using the browser's authenticated session.
    Paginates until all results are fetched.
    """
    status_map = {"available": "active", "sold": "sold"}
    output_status = status_map.get(listing_status, listing_status)
    items = []
    max_id = ""

    for _ in range(50):  # safety limit: 50 pages × ~48 items = 2400 items max
        url = (
            f"https://poshmark.com/vm-rest/users/{username}/listings"
            f"?listing_status={listing_status}&experience=poshmark_web"
            + (f"&max_id={max_id}" if max_id else "")
        )
        result = page.evaluate(
            """
            async (url) => {
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    if (!r.ok) return {error: r.status};
                    return r.json();
                } catch (e) {
                    return {error: String(e)};
                }
            }
            """,
            url,
        )

        if result.get("error"):
            break

        page_items = result.get("data", [])
        if not page_items:
            break

        for item in page_items:
            listing_id = str(item.get("id", ""))
            if not listing_id:
                continue
            price_data = item.get("price_amount", {})
            # Poshmark stores price in cents as an integer in .val
            raw_val = price_data.get("val", 0)
            try:
                price = float(raw_val) / 100
            except (TypeError, ValueError):
                price = 0.0
            items.append({
                "listing_id": listing_id,
                "title": item.get("title", "").strip(),
                "price": price,
                "url": f"https://poshmark.com/listing/{listing_id}",
                "status": output_status,
                "listed_date": item.get("created_at", ""),
            })

        # Poshmark uses cursor-based pagination via "next_max_id" or "more"
        next_id = result.get("next_max_id", result.get("more", ""))
        if not next_id:
            break
        max_id = next_id

    return items
