"""
Trending service — fetches resale-market trend insights.

Primary:  Claude AI (Anthropic API) — requires API key in Settings.
Fallback: eBay public sold-listing scraper (no key needed, less reliable).

Results are cached for 7 days; call fetch_trending(force=True) to bypass.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, date

CACHE_FILE    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "trending_cache.json")
CACHE_TTL_DAYS = 7

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
    """Return cached data without any network call."""
    return _load_cache()


def fetch_trending(force: bool = False, progress_cb=None,
                   api_key: str | None = None) -> dict:
    """
    Fetch trending data.  Uses cache unless force=True or cache is >7 days old.
    progress_cb(message: str) is called with status updates.
    api_key: pass a pre-fetched Anthropic key to avoid keyring calls in a
             background thread (which can block on Windows Credential Manager).
    Returns the full trending dict (also saved to cache).
    """
    cache = _load_cache()
    if not force and _cache_is_fresh(cache):
        return cache

    # Use the pre-fetched key if provided; only hit keyring as a fallback.
    if api_key is None:
        from app.services.anthropic_key import has_key, get_key
        api_key = get_key() if has_key() else None

    if api_key:
        if progress_cb:
            progress_cb("Asking Claude AI for market insights…")
        data = _fetch_with_claude(api_key, progress_cb)
    else:
        if progress_cb:
            progress_cb("No AI key configured — falling back to eBay scraper…")
        data = _fetch_with_ebay(progress_cb)

    _save_cache(data)
    return data


# ── Claude AI path ────────────────────────────────────────────────────────────

# Ordered fallback list — tried in sequence; first 200-OK response wins.
# Anthropic periodically deprecates dated snapshots; newer IDs are tried first
# so the app keeps working after each deprecation without a code release.
# If ALL fail the user sees a clear error; they can update the app for a fresh list.
_CLAUDE_MODEL_PRIORITY = [
    "claude-haiku-4-5",             # 2025 haiku series (fast, cheap)
    "claude-sonnet-4-5",            # 2025 sonnet series
    "claude-3-7-sonnet-20250219",   # Feb 2025 snapshot
    "claude-3-5-haiku-20241022",    # Oct 2024 snapshot
    "claude-3-haiku-20240307",      # Mar 2024 snapshot
    "claude-3-5-sonnet-20241022",   # Oct 2024 — deprecated Jun 2026
    "claude-3-sonnet-20240229",     # Feb 2024 — deprecated
]
_CLAUDE_MODEL = _CLAUDE_MODEL_PRIORITY[0]   # used for display / test

# Error keywords that indicate a model is gone/deprecated → skip to the next one.
# We check all of these so the logic survives Anthropic changing their error format.
_MODEL_SKIP_ERRORS = (
    "not_found", "404", "does_not_exist",
    "model_not_found", "invalid_model", "deprecated",
    "model_not_supported", "unknown_model",
)

_TRENDING_PROMPT = """\
You are a reselling market intelligence expert specialising in secondhand marketplaces \
(eBay, Mercari, Poshmark, Depop, Facebook Marketplace).

Today's date: {today}

Generate a current resale market trends report that a reseller can act on right now.

Return ONLY a valid JSON object — no markdown, no code fences, no commentary, just raw JSON:

{{
  "categories": [
    {{
      "label": "Category Name",
      "emoji": "single emoji",
      "top_brands": [["Brand Name", score], ["Brand Name", score], ...],
      "top_styles": [["Style keyword", score], ["Style keyword", score], ...],
      "insight": "One concrete, actionable sentence for a reseller."
    }}
  ]
}}

Include exactly these 6 categories in this order:
1. Women's Clothing  (emoji 👗)
2. Men's Clothing    (emoji 👔)
3. Women's Shoes     (emoji 👠)
4. Men's Shoes       (emoji 👟)
5. Streetwear & Sneakers (emoji 🔥)
6. Accessories & Bags    (emoji 👜)

Rules:
- top_brands: exactly 5 entries, scores are integers 1–100 (100 = most in-demand in resale)
- top_styles: exactly 5 entries, trending aesthetics/keywords, scores 1–100
- insight: one sentence with a specific price range, demand signal, or sourcing tip
- Base everything on {year} resale market knowledge — be specific and current\
"""


def _fetch_with_claude(api_key: str, progress_cb=None) -> dict:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package is not installed.\n"
            "Run: pip install anthropic"
        )

    today = date.today().strftime("%B %d, %Y")
    prompt = _TRENDING_PROMPT.format(today=today, year=date.today().year)

    # 40 s per-model timeout — long enough for a real response, short enough
    # that a hung / rate-limited model doesn't freeze the UI for minutes.
    client = anthropic.Anthropic(api_key=api_key, timeout=40.0)

    # Try each model in priority order; skip deprecated/not-found ones.
    message = None
    used_model = _CLAUDE_MODEL
    last_error: Exception | None = None
    for model in _CLAUDE_MODEL_PRIORITY:
        if progress_cb:
            progress_cb(f"Contacting Anthropic API ({model})…")
        try:
            message = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            used_model = model
            break
        except Exception as exc:
            err_str = str(exc).lower()
            if any(kw in err_str for kw in _MODEL_SKIP_ERRORS):
                last_error = exc
                continue   # try next model in the list
            raise          # real errors (auth, network, rate-limit) bubble up

    if message is None:
        raise RuntimeError(
            f"No available Claude model found. Tried: {_CLAUDE_MODEL_PRIORITY}.\n"
            f"Last error: {last_error}"
        )

    if progress_cb:
        progress_cb("Parsing AI response…")

    text = message.content[0].text.strip()

    # Strip markdown code fences if the model wraps them anyway
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Claude returned invalid JSON: {exc}\n\nRaw response:\n{text[:400]}")

    categories = parsed.get("categories", [])
    if not categories:
        raise RuntimeError("Claude response contained no categories.")

    # Normalise — ensure fields the UI expects always exist
    for cat in categories:
        cat.setdefault("group", "")
        cat.setdefault("recent_sold", [])    # AI path: no live listings
        cat.setdefault("insight", "")
        cat.setdefault("top_brands", [])
        cat.setdefault("top_styles", [])

    return {
        "fetched_at": datetime.now().isoformat(),
        "source": "claude",
        "model": used_model,
        "categories": categories,
    }


def test_claude_key(api_key: str) -> tuple[bool, str]:
    """Make a minimal API call to verify the key works. Returns (ok, message)."""
    try:
        import anthropic
    except ImportError:
        return False, "Package 'anthropic' not installed — run: pip install anthropic"
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        reply = msg.content[0].text.strip()
        return True, f"Connected OK  (model: {_CLAUDE_MODEL}, reply: '{reply}')"
    except Exception as exc:
        return False, str(exc)


# ── eBay fallback path ────────────────────────────────────────────────────────

EBAY_CATEGORIES = [
    {"id": "15724", "label": "Women's Clothing", "group": "clothing", "emoji": "👗"},
    {"id": "1059",  "label": "Men's Clothing",   "group": "clothing", "emoji": "👔"},
    {"id": "3034",  "label": "Women's Shoes",    "group": "shoes",    "emoji": "👠"},
    {"id": "93427", "label": "Men's Shoes",      "group": "shoes",    "emoji": "👟"},
]

CLOTHING_BRANDS = [
    "Levi's", "Levis", "Wrangler", "Lee", "Lucky Brand", "Madewell",
    "Gap", "J.Crew", "Banana Republic", "Ralph Lauren", "Polo Ralph Lauren",
    "Tommy Hilfiger", "Calvin Klein", "Free People", "Lululemon", "Athleta",
    "Under Armour", "Columbia", "Patagonia", "North Face", "The North Face",
    "Carhartt", "Dickies", "Michael Kors", "Kate Spade", "Tory Burch",
]

SHOE_BRANDS = [
    "Nike", "Adidas", "New Balance", "Vans", "Converse", "Puma", "Reebok",
    "UGG", "Timberland", "Dr. Martens", "Steve Madden", "Sam Edelman",
    "Clarks", "Birkenstock", "Skechers", "ASICS", "Brooks", "Hoka",
    "On Running", "Cole Haan", "Coach", "Michael Kors", "Kate Spade",
]

ALL_BRANDS = list({b.lower(): b for b in CLOTHING_BRANDS + SHOE_BRANDS}.values())

STYLE_KEYWORDS = {
    "clothing": [
        "jeans", "denim", "leggings", "joggers", "sweatpants", "hoodie",
        "sweatshirt", "blouse", "dress", "skirt", "shorts", "blazer",
        "jacket", "coat", "vest", "cardigan", "sweater", "crop top",
    ],
    "shoes": [
        "sneakers", "running shoes", "boots", "ankle boots", "heels",
        "pumps", "sandals", "slides", "loafers", "flats", "wedges",
        "high tops", "slip-ons", "clogs", "platforms",
    ],
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_EBAY_SESSION = os.path.join(
    os.path.expanduser("~"), ".baum-reseller", "ebay_session.json"
)


def _fetch_with_ebay(progress_cb=None) -> dict:
    results = []
    for i, cat in enumerate(EBAY_CATEGORIES):
        if progress_cb:
            progress_cb(f"Fetching {cat['label']}… ({i + 1}/{len(EBAY_CATEGORIES)})")
        try:
            entry = _scrape_category(cat)
        except Exception as exc:
            entry = {
                "label": cat["label"], "group": cat["group"],
                "emoji": cat["emoji"], "error": str(exc),
                "top_brands": [], "top_styles": [], "recent_sold": [],
                "insight": "",
            }
        results.append(entry)

    return {
        "fetched_at": datetime.now().isoformat(),
        "source": "ebay",
        "categories": results,
    }


def _scrape_category(cat: dict) -> dict:
    url = (
        f"https://www.ebay.com/sch/i.html"
        f"?_sacat={cat['id']}&LH_Sold=1&LH_Complete=1&_sop=13&_ipg=48&rt=nc"
    )
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
        "insight":     "",
    }


def _fetch_listings(url: str) -> list[dict]:
    try:
        return _fetch_with_requests(url)
    except Exception:
        pass
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
        if os.path.exists(_EBAY_SESSION):
            from app.utils.browser import headless_context
            browser, ctx = headless_context(p, _EBAY_SESSION)
        else:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=_UA, viewport={"width": 1280, "height": 800}, locale="en-US",
            )

        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=25_000)
        try:
            page.wait_for_selector(".s-item__title", timeout=8_000)
        except Exception:
            pass

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
        html = page.content() if not raw else ""
        browser.close()

    if raw:
        return [{"title": i["title"], "price": i["price"], "url": i["url"]} for i in raw]
    return _parse_ebay_html(html)


def _parse_ebay_html(html: str) -> list[dict]:
    listings = []
    seen: set[str] = set()
    chunks = re.split(r'(?=<li[^>]*\bs-item\b[^>]*>)', html)
    for chunk in chunks:
        if "Shop on eBay" in chunk:
            continue
        url_m = re.search(r'<a[^>]+class="[^"]*s-item__link[^"]*"[^>]*href="([^"]+)"', chunk)
        if not url_m:
            continue
        item_url = url_m.group(1).split("?")[0]

        title: str | None = None
        for pattern in [
            r'<h3[^>]+s-item__title[^>]*>.*?<span[^>]*role=["\']heading["\'][^>]*>([^<]+)</span>',
            r'<span[^>]+role=["\']heading["\'][^>]*>([^<]{6,})</span>',
            r'<span[^>]+class="[^"]*BOLD[^"]*"[^>]*>([^<]+)</span>',
        ]:
            m = re.search(pattern, chunk, re.DOTALL | re.IGNORECASE)
            if m:
                title = m.group(1).strip()
                break
        if not title:
            m = re.search(r'<h3[^>]+s-item__title[^>]*>(.*?)</h3>', chunk, re.DOTALL)
            if m:
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()

        if not title or len(title) < 6 or title in seen:
            continue
        title = re.sub(r'\s+', ' ', title)
        seen.add(title)
        price_m = re.search(r'\$([\d,]+\.?\d*)', chunk)
        price = f"${price_m.group(1)}" if price_m else ""
        listings.append({"title": title, "price": price, "url": item_url})

    return listings
