"""
Background enrichment — slowly populates description, category, and images
for items that came in via sync (where only title/price/status were captured).

Two modes
---------
1. Idle loop  : every IDLE_INTERVAL_S seconds, pick one un-enriched item and
                scrape its public listing page with urllib (no browser needed).
2. On-demand  : call enrich_item(item_id, done_cb) to enrich a specific item
                immediately — used when the user opens the detail dialog.

"Un-enriched" = description is empty AND at least one listing URL exists.
"""

import re
import json
import threading
import urllib.request

IDLE_INTERVAL_S = 300   # 5 minutes between idle enrichment attempts

_running = False
_lock = threading.Lock()

# ── Category keyword table ──────────────────────────────────────────────────
# Evaluated top-to-bottom; first match wins.  Pad the title with spaces so
# short keywords only match whole words (e.g. "tie" won't match "active").
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Boots",      ["boot"]),
    ("Shoes",      ["shoe", "sneaker", "heel", "loafer", "sandal", " flat ",
                    "mule", "wedge", "oxford", "pump", "clog", "espadrille",
                    "slipper", "moccasin"]),
    ("Blue Jeans", ["jean", "denim"]),
    ("Dress",      ["dress", "gown", "romper", "jumpsuit"]),
    ("Skirt",      ["skirt"]),
    ("Shorts",     ["shorts"]),
    ("Pants",      ["pant", "trouser", "legging", "jogger", "slack", "chino",
                    "capri"]),
    ("T-Shirt",    ["t-shirt", "tshirt", "graphic tee", " tee "]),
    ("Shirt",      ["shirt", "blouse", "button-down", "chambray", "flannel",
                    "henley", "tunic"]),
    ("Polo",       ["polo"]),
    ("Sweater",    ["sweater", "hoodie", "sweatshirt", "cardigan", "pullover",
                    "crewneck", "knit"]),
    ("Jacket",     ["jacket", "blazer", "bomber", "windbreaker", "anorak"]),
    ("Coat",       ["coat", "parka", "peacoat", "trench", "puffer", "overcoat"]),
    ("Vest",       ["vest"]),
    ("Top",        ["top", "tank", "camisole", "cami", "crop", "tube"]),
    ("Scarf",      ["scarf", "shawl"]),
    ("Tie",        [" tie ", "necktie", "bow tie", "bowtie"]),
    ("Tote Bag",   ["tote bag", "handbag", "crossbody", "clutch", "purse",
                    "backpack", "satchel", "shoulder bag"]),
]


def infer_category(title: str) -> str:
    """Return a category inferred from the listing title, or '' if unknown."""
    t = f" {title.lower()} "   # pad so short keywords match whole words
    for category, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in t:
                return category
    return ""


# ── Page scraping ────────────────────────────────────────────────────────────

def _fetch_page_details(url: str, platform: str) -> dict:
    """
    Fetch a public listing page and extract description + image URLs.
    Returns dict with optional keys: description (str), images (list[str]).
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

    result: dict = {}

    # JSON-LD — works for eBay, Poshmark, Mercari
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(m.group(1))
            if not isinstance(data, dict):
                continue
            if data.get("description") and not result.get("description"):
                result["description"] = _clean(str(data["description"]))
            imgs = data.get("image", [])
            if isinstance(imgs, str):
                imgs = [imgs]
            if imgs and not result.get("images"):
                result["images"] = [i for i in imgs if str(i).startswith("http")]
        except Exception:
            pass

    # og:description / meta description fallback
    if not result.get("description"):
        for pat in (
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{10,})["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{10,})["\']',
            r'<meta[^>]+content=["\']([^"\']{10,})["\'][^>]+name=["\']description["\']',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                result["description"] = _clean(m.group(1))
                break

    # og:image fallback
    if not result.get("images"):
        imgs = re.findall(
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if imgs:
            result["images"] = [i for i in imgs if i.startswith("http")]

    # Poshmark Next.js / embedded JSON fallback
    if platform == "poshmark" and not result.get("description"):
        m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){10,})"', html)
        if m:
            try:
                result["description"] = _clean(json.loads(f'"{m.group(1)}"'))
            except Exception:
                pass

    return result


def _clean(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Core enrichment logic ────────────────────────────────────────────────────

def _do_enrich(item_id: int) -> bool:
    """
    Enrich one item synchronously (call from a worker thread).
    Returns True if any data was updated.
    """
    from app.database.connection import get_connection
    from app.database.models import upsert_image_url

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, description, category FROM items WHERE id=?",
            (item_id,)
        ).fetchone()
        if not row:
            return False
        item = dict(row)

        listings = [dict(r) for r in conn.execute(
            "SELECT url, platform FROM listings"
            " WHERE item_id=? AND url!='' ORDER BY updated_at DESC",
            (item_id,)
        ).fetchall()]

    if not listings:
        return False

    # ── Try to infer category from title right now (no network needed) ────
    updates: dict = {}
    if not item.get("category"):
        cat = infer_category(item["title"])
        if cat:
            updates["category"] = cat

    # ── Scrape the listing page ───────────────────────────────────────────
    details: dict = {}
    for lst in listings:
        details = _fetch_page_details(lst["url"], lst["platform"])
        if details:
            break

    new_desc = details.get("description", "")
    if new_desc and not item.get("description"):
        updates["description"] = new_desc

    # Don't overwrite category inferred from title with a scraped one unless
    # title inference found nothing
    new_cat = details.get("category", "")
    if new_cat and not item.get("category") and "category" not in updates:
        updates["category"] = new_cat

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [item_id]
        with get_connection() as conn:
            conn.execute(f"UPDATE items SET {set_clause} WHERE id=?", values)

    updated = bool(updates)
    for img_url in details.get("images", []):
        if img_url:
            upsert_image_url(item_id, img_url)
            updated = True

    return updated


# ── Public API ───────────────────────────────────────────────────────────────

def enrich_item(item_id: int, done_cb=None):
    """
    Enrich a specific item in a background thread.
    done_cb(updated: bool) is called on the Qt main thread when finished.
    """
    def _worker():
        updated = _do_enrich(item_id)
        if done_cb:
            from app.utils.qt_thread import post_to_main
            post_to_main(lambda: done_cb(updated))

    threading.Thread(target=_worker, daemon=True).start()


def _get_next_unenriched_item() -> int | None:
    """Return the id of one item that still needs enrichment, or None."""
    from app.database.connection import get_connection
    with get_connection() as conn:
        row = conn.execute("""
            SELECT i.id
            FROM items i
            JOIN listings l ON l.item_id = i.id AND l.url != ''
            WHERE (i.description IS NULL OR i.description = '')
               OR (i.category IS NULL OR i.category = '')
            ORDER BY l.updated_at DESC
            LIMIT 1
        """).fetchone()
        return row["id"] if row else None


def _idle_loop():
    """Daemon thread — enrich one item every IDLE_INTERVAL_S seconds."""
    import time
    while True:
        time.sleep(IDLE_INTERVAL_S)
        try:
            item_id = _get_next_unenriched_item()
            if item_id:
                _do_enrich(item_id)
        except Exception:
            pass


def start_idle_enrichment():
    """Start the background enrichment loop (idempotent — safe to call multiple times)."""
    global _running
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=_idle_loop, daemon=True).start()
