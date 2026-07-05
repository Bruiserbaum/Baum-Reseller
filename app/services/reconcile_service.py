"""
Post-sync reconciliation pass.

After a scroll-based sync, listings that were active in the DB but didn't appear
in the scraped results may have been missed due to scroll limits, or they may have
been sold / deleted on the platform.

This pass takes up to `max_check` such listings (oldest last_seen first), visits
each one's URL, and updates the DB based on what it finds:

  • Page loads fine, listing is still active  → touch last_seen (no other change)
  • Page redirects to a sold confirmation     → mark sold
  • Page 404s / redirects to search / gone   → mark unlisted (status = 'unlisted')

The pass runs in a background thread and is capped so it doesn't add too much
time to a sync.  Over multiple syncs, full closet coverage accumulates.
"""

import re


def reconcile_platform(platform: str, session_file: str,
                       max_check: int = 50, progress_cb=None):
    """
    Check up to `max_check` stale active listings for `platform`.
    Returns (verified, sold, unlisted) counts.
    """
    from app.database.models import get_stale_listings
    from app.database.connection import get_connection

    stale = get_stale_listings(platform, days=5)[:max_check]
    if not stale:
        return 0, 0, 0

    if progress_cb:
        progress_cb(f"Verifying {len(stale)} listings not seen in recent syncs…")

    verified = sold = unlisted = 0

    try:
        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context

        with sync_playwright() as pw:
            browser, ctx = headless_context(pw, session_file)
            page = ctx.new_page()

            for row in stale:
                url = row.get("url", "")
                lid = row["listing_id"]
                db_id = row["id"]
                if not url:
                    continue
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    final_url = page.url
                    status_code = resp.status if resp else 0

                    if status_code == 404 or _is_not_found(final_url, platform):
                        _set_status(db_id, "unlisted")
                        unlisted += 1
                    elif _is_sold(final_url, page, platform):
                        _set_status(db_id, "sold")
                        sold += 1
                    else:
                        _touch_last_seen(db_id)
                        verified += 1
                except Exception:
                    pass

            browser.close()
    except Exception:
        pass

    return verified, sold, unlisted


def _is_not_found(url: str, platform: str) -> bool:
    if platform == "poshmark":
        return "/search" in url or "poshmark.com/login" in url
    if platform == "ebay":
        return "rover.ebay.com" in url or "/404" in url
    if platform == "mercari":
        return "/search" in url or "/404" in url
    return False


def _is_sold(url: str, page, platform: str) -> bool:
    if platform == "poshmark":
        try:
            content = page.content()
            return "SOLD" in content or "sold-out" in content.lower()
        except Exception:
            return False
    if platform == "ebay":
        return "ended" in url or "completed" in url
    return False


def _set_status(listing_db_id: int, status: str):
    from app.database.connection import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE listings SET status=?, last_seen=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (status, listing_db_id)
        )


def _touch_last_seen(listing_db_id: int):
    from app.database.connection import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE listings SET last_seen=datetime('now') WHERE id=?",
            (listing_db_id,)
        )
