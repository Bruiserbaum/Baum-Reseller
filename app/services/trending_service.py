"""
Trending service — scrapes eBay public sold-listings to surface top-selling
brands and styles in Clothing & Shoes. No API key or login required.
Results are cached for 7 days; call fetch_trending(force=True) to bypass.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta

CACHE_FILE = os.path.join(os.path.expanduser("~"), ".baum-reseller", "trending_cache.json")
CACHE_TTL_DAYS = 7

# eBay category IDs — public sold listings, no login needed
EBAY_CATEGORIES = [
    {"id": "15724", "label": "Women's Clothing", "group": "clothing", "emoji": "👗"},
    {"id": "1059",  "label": "Men's Clothing",   "group": "clothing", "emoji": "👔"},
    {"id": "3034",  "label": "Women's Shoes",    "group": "shoes",    "emoji": "👠"},
    {"id": "93427", "label": "Men's Shoes",      "group": "shoes",    "emoji": "👟"},
]

# Brands to detect — matched case-insensitively against listing titles
CLOTHING_BRANDS = [
    "Levi's", "Levis", "Wrangler", "Lee", "Lucky Brand", "AG Jeans",
    "7 For All Mankind", "True Religion", "Citizens of Humanity", "Madewell",
    "Gap", "Old Navy", "J.Crew", "Banana Republic", "Ralph Lauren", "Polo Ralph Lauren",
    "Tommy Hilfiger", "Calvin Klein", "Free People", "Anthropologie",
    "Urban Outfitters", "Zara", "H&M", "Lululemon", "Athleta",
    "Under Armour", "Columbia", "Patagonia", "North Face", "The North Face",
    "Carhartt", "Dickies", "Filson", "LL Bean", "L.L. Bean",
    "Michael Kors", "Kate Spade", "Tory Burch", "Ann Taylor",
    "White House Black Market", "Chico's", "Express", "Forever 21",
]

SHOE_BRANDS = [
    "Nike", "Adidas", "New Balance", "Vans", "Converse", "Puma", "Reebok",
    "UGG", "Timberland", "Dr. Martens", "Doc Martens", "Steve Madden",
    "Sam Edelman", "Lucky Brand", "Clarks", "Birkenstock", "Skechers",
    "ASICS", "Brooks", "Hoka", "On Running", "Allbirds", "Cole Haan",
    "Ecco", "Rockport", "Hunter", "Sorel", "Merrell", "Keen", "Salomon",
    "Coach", "Michael Kors", "Kate Spade", "Tory Burch", "Franco Sarto",
    "Jessica Simpson", "Chinese Laundry", "Naturalizer",
]

ALL_BRANDS = list({b.lower(): b for b in CLOTHING_BRANDS + SHOE_BRANDS}.values())

STYLE_KEYWORDS = {
    "clothing": [
        "jeans", "denim", "leggings", "joggers", "sweatpants", "hoodie",
        "sweatshirt", "blouse", "dress", "skirt", "shorts", "blazer",
        "jacket", "coat", "vest", "cardigan", "sweater", "flannel",
        "activewear", "yoga pants", "crop top", "tank top", "t-shirt",
    ],
    "shoes": [
        "sneakers", "running shoes", "boots", "ankle boots", "heels",
        "pumps", "sandals", "slides", "loafers", "flats", "wedges",
        "mules", "high tops", "slip-ons", "clogs", "platforms", "oxfords",
    ],
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _cache_is_fresh(cache: dict) -> bool:
    ts = cache.get("fetched_at")
    if not ts:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(ts) < timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return False


def get_cache_age_str(cache: dict) -> str:
    """Human-readable age of the cached data, e.g. '2 days ago'."""
    ts = cache.get("fetched_at")
    if not ts:
        return "never"
    try:
        delta = datetime.now() - datetime.fromisoformat(ts)
        if delta.days == 0:
            hours = delta.seconds // 3600
            return f"{hours} hour(s) ago" if hours else "just now"
        return f"{delta.days} day(s) ago"
    except Exception:
        return "unknown"


# ── Public API ────────────────────────────────────────────────────────────────

def get_cached() -> dict:
    """Return cached data without fetching (may be stale or empty)."""
    return _load_cache()


def fetch_trending(force: bool = False, progress_cb=None) -> dict:
    """
    Fetch trending data. Uses cache unless force=True or cache is >7 days old.
    progress_cb(message: str) is called with status updates.
    Returns the full trending dict (also saved to cache).
    """
    cache = _load_cache()
    if not force and _cache_is_fresh(cache):
        return cache

    results = []
    for i, cat in enumerate(EBAY_CATEGORIES):
        if progress_cb:
            progress_cb(f"Fetching {cat['label']}… ({i + 1}/{len(EBAY_CATEGORIES)})")
        try:
            entry = _scrape_category(cat)
        except Exception as exc:
            entry = {
                "label":      cat["label"],
                "group":      cat["group"],
                "emoji":      cat["emoji"],
                "error":      str(exc),
                "top_brands": [],
                "top_styles": [],
                "recent_sold": [],
            }
        results.append(entry)

    data = {
        "fetched_at": datetime.now().isoformat(),
        "categories": results,
    }
    _save_cache(data)
    return data


# ── Scraping ──────────────────────────────────────────────────────────────────

def _ebay_url(cat_id: str) -> str:
    return (
        f"https://www.ebay.com/sch/i.html"
        f"?_sacat={cat_id}&LH_Sold=1&LH_Complete=1&_sop=13&_ipg=48&rt=nc"
    )


def _scrape_category(cat: dict) -> dict:
    url = _ebay_url(cat["id"])
    listings = _fetch_listings(url)

    brand_counter: Counter = Counter()
    style_counter: Counter = Counter()
    styles = STYLE_KEYWORDS.get(cat["group"], [])

    for item in listings:
        low = item["title"].lower()
        for brand in ALL_BRANDS:
            if brand.lower() in low:
                brand_counter[brand] += 1
        for style in styles:
            if style in low:
                style_counter[style] += 1

    return {
        "label":       cat["label"],
        "group":       cat["group"],
        "emoji":       cat["emoji"],
        "top_brands":  brand_counter.most_common(5),
        "top_styles":  style_counter.most_common(5),
        "recent_sold": listings[:8],
    }


def _fetch_listings(url: str) -> list[dict]:
    """
    Fetch eBay sold-listing search results.
    Tries plain requests first; falls back to Playwright if blocked.
    """
    try:
        return _fetch_with_requests(url)
    except Exception:
        pass
    # Playwright fallback (bundled Chromium handles bot-detection better)
    try:
        return _fetch_with_playwright(url)
    except Exception as exc:
        raise RuntimeError(f"Could not fetch eBay data: {exc}") from exc


def _fetch_with_requests(url: str) -> list[dict]:
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent":      _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    listings = _parse_ebay_html(html)
    if not listings:
        raise RuntimeError("No listings parsed — possibly blocked")
    return listings


def _fetch_with_playwright(url: str) -> list[dict]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=25_000)
        # Wait for listing grid to render (eBay lazy-renders results)
        try:
            page.wait_for_selector(".s-item__title", timeout=8_000)
        except Exception:
            pass

        # Query the live DOM directly — far more reliable than regex-parsing
        # the raw HTML, which breaks whenever eBay changes their markup.
        raw = page.evaluate("""
            () => Array.from(document.querySelectorAll('.s-item')).map(el => {
                const titleEl = el.querySelector(
                    '[role="heading"], .s-item__title span, .s-item__title'
                );
                const priceEl = el.querySelector('.s-item__price');
                const linkEl  = el.querySelector('.s-item__link');
                return {
                    title: titleEl ? titleEl.textContent.trim() : '',
                    price: priceEl ? priceEl.textContent.trim()  : '',
                    url:   linkEl  ? linkEl.href.split('?')[0]   : '',
                };
            }).filter(i =>
                i.url && i.title && i.title !== 'Shop on eBay' && i.title.length > 5
            )
        """)

        # Capture HTML before closing (fallback if DOM eval returns nothing)
        html = page.content() if not raw else ""
        browser.close()

    if raw:
        return [{"title": i["title"], "price": i["price"], "url": i["url"]}
                for i in raw]
    # Last-chance: regex parse the captured HTML
    return _parse_ebay_html(html)


def _parse_ebay_html(html: str) -> list[dict]:
    """
    Parse eBay search-results HTML into a list of {title, price, url} dicts.

    eBay's title markup has changed over time.  We try four patterns in order:
      1. Modern:  <h3 class="s-item__title"><span role="heading">TITLE</span></h3>
      2. Any:     <span role="heading">TITLE</span>  (≥6 chars)
      3. Legacy:  <span class="BOLD">TITLE</span>
      4. Fallback: strip tags from <h3 class="s-item__title">…</h3>
    """
    listings = []
    seen_titles: set[str] = set()

    # Split into per-item chunks on <li class="s-item…"> boundaries
    chunks = re.split(r'(?=<li[^>]*\bs-item\b[^>]*>)', html)

    for chunk in chunks:
        # Skip the promotional "Shop on eBay" ghost item
        if "Shop on eBay" in chunk:
            continue

        # ── Listing URL ───────────────────────────────────────────────────
        url_m = re.search(
            r'<a[^>]+class="[^"]*s-item__link[^"]*"[^>]*href="([^"]+)"', chunk
        )
        if not url_m:
            continue
        item_url = url_m.group(1).split("?")[0]

        # ── Title — four fallback patterns ────────────────────────────────
        title: str | None = None

        # 1. Modern eBay: <h3 class="s-item__title"><span role="heading">TITLE</span>
        m = re.search(
            r'<h3[^>]+s-item__title[^>]*>.*?<span[^>]*role=["\']heading["\'][^>]*>([^<]+)</span>',
            chunk, re.DOTALL | re.IGNORECASE,
        )
        if m:
            title = m.group(1).strip()

        # 2. Any <span role="heading"> with enough text (catches other layouts)
        if not title:
            m = re.search(
                r'<span[^>]+role=["\']heading["\'][^>]*>([^<]{6,})</span>',
                chunk, re.IGNORECASE,
            )
            if m:
                title = m.group(1).strip()

        # 3. Legacy eBay: <span class="BOLD">TITLE</span>
        if not title:
            m = re.search(
                r'<span[^>]+class="[^"]*BOLD[^"]*"[^>]*>([^<]+)</span>', chunk
            )
            if m:
                title = m.group(1).strip()

        # 4. Last resort: strip all tags from the h3 block
        if not title:
            m = re.search(
                r'<h3[^>]+s-item__title[^>]*>(.*?)</h3>', chunk, re.DOTALL
            )
            if m:
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()

        if not title or len(title) < 6:
            continue
        title = re.sub(r'\s+', ' ', title)
        if title in seen_titles:
            continue
        seen_titles.add(title)

        # ── Price ─────────────────────────────────────────────────────────
        price_m = re.search(r'\$([\d,]+\.?\d*)', chunk)
        price = f"${price_m.group(1)}" if price_m else ""

        listings.append({"title": title, "price": price, "url": item_url})

    return listings
