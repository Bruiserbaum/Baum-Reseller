"""
Orchestrates syncing listings from all connected platforms.
Each platform service implements: login(creds), fetch_listings() -> list[dict], logout()
"""
import datetime
import threading

from app.database.models import set_setting, get_setting


PLATFORMS = ["ebay", "mercari", "poshmark"]


def _get_service(platform: str):
    if platform == "ebay":
        from .ebay_service import EbayService
        return EbayService()
    if platform == "mercari":
        from .mercari_service import MercariService
        return MercariService()
    if platform == "poshmark":
        from .poshmark_service import PoshmarkService
        return PoshmarkService()
    raise ValueError(f"Unknown platform: {platform}")


def sync_platform(platform: str, progress_cb=None, done_cb=None):
    """Sync a single platform in a background thread."""
    def _worker():
        try:
            svc = _get_service(platform)
            listings = svc.fetch_listings(progress_cb=progress_cb)
            _persist_listings(platform, listings)
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            set_setting(f"last_sync_{platform}", ts)
            set_setting("last_sync_time", ts)
            if done_cb:
                done_cb(True, len(listings), None)
        except Exception as e:
            if done_cb:
                done_cb(False, 0, str(e))

    threading.Thread(target=_worker, daemon=True).start()


def sync_all(progress_cb=None, done_cb=None):
    """Sync all platforms sequentially in a background thread."""
    def _worker():
        total = 0
        errors = []
        for p in PLATFORMS:
            try:
                svc = _get_service(p)
                listings = svc.fetch_listings(progress_cb=progress_cb)
                _persist_listings(p, listings)
                total += len(listings)
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                set_setting(f"last_sync_{p}", ts)
            except Exception as e:
                errors.append(f"{p}: {e}")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        set_setting("last_sync_time", ts)
        if done_cb:
            done_cb(total, errors)

    threading.Thread(target=_worker, daemon=True).start()


def _persist_listings(platform: str, listings: list[dict]):
    from app.database.models import upsert_listing, save_item
    for l in listings:
        item_id = l.get("item_id")
        if not item_id:
            item_id = save_item({"title": l.get("title", "Untitled")})
        upsert_listing({
            "item_id": item_id,
            "platform": platform,
            "listing_id": l["listing_id"],
            "url": l.get("url", ""),
            "listing_price": l.get("price", 0),
            "status": l.get("status", "active"),
            "listed_date": l.get("listed_date", ""),
        })
