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
            # eBay also exposes sold orders via a dedicated method
            if platform == "ebay" and hasattr(svc, "fetch_sold_orders"):
                try:
                    sold = svc.fetch_sold_orders()
                    listings.extend(sold)
                except Exception:
                    pass  # don't abort active-listing sync if orders fail
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
                if p == "ebay" and hasattr(svc, "fetch_sold_orders"):
                    try:
                        listings.extend(svc.fetch_sold_orders())
                    except Exception:
                        pass
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
    from app.database.models import (
        upsert_listing, save_item,
        get_item_id_for_listing, upsert_image_url,
        update_sync_source_if_empty,
        update_item_description_if_empty,
        upsert_sale_from_listing,
    )
    for l in listings:
        # Look up an existing item via the listings table before creating a new one.
        # Without this, every sync run would create duplicate items because the
        # scrapers don't carry a DB item_id.
        item_id = get_item_id_for_listing(platform, l["listing_id"])
        if not item_id:
            from app.services.enrich_service import infer_category
            title = l.get("title", "Untitled")
            item_id = save_item({
                "title":       title,
                "description": l.get("description", ""),
                "category":    infer_category(title),
                "sync_source": platform,
            })
        else:
            # Existing item — backfill sync_source and description if blank
            update_sync_source_if_empty(item_id, platform)
            if l.get("description"):
                update_item_description_if_empty(item_id, l["description"])

        sold_price = l.get("sold_price",
                           l.get("price", 0) if l.get("status") == "sold" else 0)

        upsert_listing({
            "item_id":       item_id,
            "platform":      platform,
            "listing_id":    l["listing_id"],
            "url":           l.get("url", ""),
            "listing_price": l.get("price", 0),
            "status":        l.get("status", "active"),
            "listed_date":   l.get("listed_date", ""),
            "sold_date":     l.get("sold_date", ""),
            "sold_price":    sold_price,
        })

        # Auto-populate the sales/reports table for sold listings
        if l.get("status") == "sold":
            listing_with_price = dict(l)
            listing_with_price["sold_price"] = sold_price
            upsert_sale_from_listing(platform, item_id, listing_with_price)

        # Persist the thumbnail URL returned by the scraper (if any).
        img_url = l.get("img_url", "")
        if img_url:
            upsert_image_url(item_id, img_url)
