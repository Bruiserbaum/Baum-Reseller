"""
eBay integration.
Login: real Chrome/Edge subprocess — no automation flags, supports Google SSO + 2FA.
Sync:  headless Playwright using saved session cookies (eBay Seller Hub).
"""
import os
import re
import json
import keyring

SERVICE    = "baum-reseller-ebay"
DOMAIN     = "ebay.com"
SESSION    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "ebay_session.json")
PROFILE    = os.path.join(os.path.expanduser("~"), ".baum-reseller", "ebay_profile")
LOGIN_URL  = "https://www.ebay.com/signin/"

# Primary: Seller Hub.  Fallback: legacy My eBay (some accounts use this).
ACTIVE_URL        = "https://www.ebay.com/sh/lst/active"
SOLD_URL          = "https://www.ebay.com/sh/lst/sold"
MYEBAY_ACTIVE_URL = "https://www.ebay.com/mye/myebay/active"
MYEBAY_SOLD_URL   = "https://www.ebay.com/mye/myebay/sold"

# URL patterns that indicate an auth/captcha redirect
_AUTH = ("/signin", "/verify", "/challenge", "/otp", "/confirm",
         "/security", "/two-factor", "/auth/", "/captcha",
         "signin.ebay.com")


def _is_logged_in(url: str) -> bool:
    return "ebay.com" in url and not any(a in url for a in _AUTH)


def _is_blocked(page) -> bool:
    """
    Return True if eBay served a captcha / bot-block page.
    Checks both the final URL and live DOM elements.
    """
    url = page.url
    if any(a in url for a in _AUTH):
        return True
    try:
        # hCaptcha / reCaptcha overlay or eBay's own captcha widget
        blocked = page.evaluate("""
            () => !!(
                document.querySelector('[class*="captcha"], [id*="captcha"]') ||
                document.querySelector('iframe[src*="hcaptcha"], iframe[src*="recaptcha"]') ||
                document.querySelector('[class*="bot-check"], [class*="robot-check"]') ||
                (document.title || '').toLowerCase().includes('captcha') ||
                (document.title || '').toLowerCase().includes('robot')
            )
        """)
        return bool(blocked)
    except Exception:
        return False


class EbayService:

    # ── Credentials ───────────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str = "", password: str = "") -> str | None:
        try:
            keyring.set_password(SERVICE, "credentials",
                                 json.dumps({"email": email, "password": password}))
            from app.utils.config import set_value
            set_value("ebay_email", email)
            return None
        except Exception as e:
            return str(e)

    def has_session(self) -> bool:
        if os.path.exists(SESSION):
            return True
        return os.path.exists(os.path.join(PROFILE, "Default", "Cookies"))

    def clear_session(self):
        import shutil
        if os.path.exists(SESSION):
            os.remove(SESSION)
        if os.path.exists(PROFILE):
            shutil.rmtree(PROFILE, ignore_errors=True)

    # ── Browser login ─────────────────────────────────────────────────────

    def open_in_browser(self):
        from app.utils.browser import open_in_system_browser
        open_in_system_browser(LOGIN_URL)

    def import_session(self, done_cb=None):
        from app.utils.browser import import_cookies_from_browser
        import_cookies_from_browser(DOMAIN, SESSION, done_cb=done_cb)

    def import_from_file(self, file_path: str) -> tuple[bool, str]:
        from app.utils.browser import import_cookies_from_file
        return import_cookies_from_file(file_path, SESSION)

    def login(self, done_cb=None):
        """
        Open Playwright's own headed Chromium browser for direct eBay login.
        Supports MFA, captcha, and any sign-in flow.  Session saved automatically.
        """
        from app.services.session_manager import open_login_browser
        open_login_browser(
            platform="ebay",
            start_url=LOGIN_URL,
            success_glob="https://www.ebay.com/",
            done_cb=done_cb,
        )

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "Not logged in — click 'Login with Browser'."
        if not os.path.exists(SESSION):
            return False, "Session file missing — click 'Login with Browser'."
        try:
            from playwright.sync_api import sync_playwright
            from app.utils.browser import headless_context
            with sync_playwright() as p:
                browser, ctx = headless_context(p, SESSION)
                page = ctx.new_page()
                page.goto(ACTIVE_URL, wait_until="domcontentloaded", timeout=20_000)
                blocked = _is_blocked(page)
                browser.close()
            if blocked:
                self.clear_session()
                return False, "Session expired — click 'Login with Browser' again."
            return True, "Connected to eBay ✓"
        except Exception as e:
            return False, str(e)

    # ── Sync ──────────────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        if not os.path.exists(SESSION):
            raise ValueError("Not logged in to eBay. Click 'Login with Browser' first.")

        from playwright.sync_api import sync_playwright
        from app.utils.browser import headless_context
        intercepted: list[dict] = []

        def _on_response(response):
            rtype = response.request.resource_type
            if rtype not in ("xhr", "fetch"):
                return
            # Accept any XHR/fetch JSON response — the eBay Seller Hub SPA can
            # call many different API hostnames (/sh/api, api.ebay.com, etc.).
            # _extract_item_list() filters out non-listing responses by content.
            try:
                data = response.json()
                if isinstance(data, dict):
                    intercepted.append({"url": response.url, "body": data})
            except Exception:
                pass

        with sync_playwright() as p:
            browser, ctx = headless_context(p, SESSION)
            page = ctx.new_page()
            page.on("response", _on_response)

            if progress_cb:
                progress_cb("Checking eBay session…")

            # ── Try Seller Hub first, fall back to My eBay Active ─────────
            page.goto(ACTIVE_URL, wait_until="load", timeout=30_000)

            if _is_blocked(page):
                # Seller Hub blocked — try the legacy My eBay interface
                if progress_cb:
                    progress_cb("Seller Hub blocked, trying My eBay Active…")
                page.goto(MYEBAY_ACTIVE_URL, wait_until="load", timeout=30_000)
                if _is_blocked(page):
                    browser.close()
                    self.clear_session()
                    raise ValueError(
                        "eBay session expired — please re-authenticate.\n\n"
                        "Go to Settings → eBay → Login (Browser) to refresh your session."
                    )

            # Wait for first listing links to appear before paginating
            try:
                page.wait_for_selector(
                    'a[href*="/itm/"], '
                    '[class*="shui-dt-row"], [class*="sh-llt__row"], '
                    '[class*="listing-row"], [data-testid*="listing"]',
                    timeout=10_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2_000)

            if progress_cb:
                progress_cb("Loading eBay active listings…")

            # Paginate through all active pages (up to 20 pages × 25-100/page)
            active_dom = _paginate_and_scrape(
                page, status="active", progress_cb=progress_cb, label="active"
            )

            if progress_cb:
                progress_cb("Loading eBay sold listings…")

            # Navigate to matching sold URL (Seller Hub or My eBay)
            sold_target = (
                MYEBAY_SOLD_URL if MYEBAY_ACTIVE_URL in page.url else SOLD_URL
            )
            page.goto(sold_target, wait_until="load", timeout=30_000)
            if _is_blocked(page):
                page.goto(MYEBAY_SOLD_URL, wait_until="load", timeout=30_000)

            try:
                page.wait_for_selector(
                    'a[href*="/itm/"], '
                    '[class*="shui-dt-row"], [class*="sh-llt__row"], '
                    '[class*="listing-row"], [data-testid*="listing"]',
                    timeout=10_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2_000)

            # Paginate sold pages too (fewer — typically 1-3 pages)
            sold_dom = _paginate_and_scrape(
                page, status="sold", progress_cb=progress_cb, label="sold", max_pages=10
            )

            # Enrich new items with high-res images + descriptions (capped at 25)
            _enrich_new_items(page, active_dom + sold_dom, progress_cb=progress_cb)

            browser.close()

        dom_results = active_dom + sold_dom
        parsed = _parse_intercepted(intercepted)
        result = parsed if parsed else dom_results

        # ── Debug log ─────────────────────────────────────────────────────
        # Written after every sync so we can diagnose XHR/DOM capture issues.
        import json as _json, datetime as _dt
        _debug = {
            "timestamp":              _dt.datetime.now().isoformat(),
            "platform":               "ebay",
            "xhr_responses_captured": len(intercepted),
            "xhr_urls":               [r.get("url", "") for r in intercepted[:20]],
            "xhr_body_top_keys":      [list(r.get("body", {}).keys())[:8]
                                       for r in intercepted[:10]],
            "xhr_items_parsed":       len(parsed),
            "dom_active_items":       len(active_dom),
            "dom_sold_items":         len(sold_dom),
            "total_returned":         len(result),
            "sample_items":           result[:3],
        }
        _debug_path = os.path.join(
            os.path.expanduser("~"), ".baum-reseller", "debug_ebay_sync.json"
        )
        try:
            with open(_debug_path, "w", encoding="utf-8") as _f:
                _json.dump(_debug, _f, indent=2, default=str)
        except Exception:
            pass

        return result


_BUY_IT_NOW_RE = re.compile(
    r'[\s·•\-]*Buy\s+It\s+Now[\s·•\-]*\d*\s*$', re.IGNORECASE
)
_ITEM_ID_SUFFIX_RE = re.compile(r'[\s·•]+\d{10,}\s*$')


def _clean_title(title: str) -> str:
    """Strip eBay-injected text (Buy It Now · NNNNN) from DOM link text."""
    title = _BUY_IT_NOW_RE.sub('', title)
    title = _ITEM_ID_SUFFIX_RE.sub('', title)
    title = title.strip(' ·•-')
    return title or 'Untitled'


def _try_next_page(page) -> bool:
    """
    Click the Next-page control on eBay Seller Hub or My eBay Active.
    Returns True if a button was found and clicked, False otherwise.
    """
    return bool(page.evaluate("""
        () => {
            const candidates = [
                '[aria-label="Go to next page"]',
                '[aria-label="Next page"]',
                'button[title="Next page"]',
                'button[title="Next"]',
                'a[aria-label="Next"]',
                '.pag-next:not(.pag-next--disabled)',
                '[class*="pagination-next"]:not([disabled]):not([aria-disabled="true"])',
                '[class*="next-page"]:not([disabled]):not([aria-disabled="true"])',
            ];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (el && !el.disabled && el.getAttribute('aria-disabled') !== 'true'
                        && !el.closest('[disabled]')) {
                    el.click();
                    return true;
                }
            }
            // Last resort: any pagination button whose visible text is ">" or "›"
            for (const el of document.querySelectorAll(
                '[class*="pagination"] button, [class*="pagination"] a, [class*="paging"] button'
            )) {
                const t = el.textContent.trim();
                if ((t === '>' || t === '›' || t === '»') && !el.disabled
                        && el.getAttribute('aria-disabled') !== 'true') {
                    el.click();
                    return true;
                }
            }
            return false;
        }
    """))


def _paginate_and_scrape(page, status: str, progress_cb=None,
                          label: str = "active", max_pages: int = 20) -> list[dict]:
    """
    Scrape the current page then keep clicking Next until there are no more pages
    or max_pages is reached.  Returns all unique listings.
    """
    all_results: list[dict] = []
    seen_ids: set[str] = set()

    for pg_num in range(max_pages):
        if progress_cb and pg_num > 0:
            progress_cb(f"Loading eBay {label} listings… (page {pg_num + 1})")

        # Scroll to ensure lazy-loaded rows are rendered
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(600)

        items = _scrape_seller_hub(page, status=status)
        new_items = [i for i in items if i["listing_id"] not in seen_ids]
        if new_items:
            seen_ids.update(i["listing_id"] for i in new_items)
            all_results.extend(new_items)
        elif pg_num > 0:
            break  # No new items on this page — we've reached the end

        if not _try_next_page(page):
            break  # No Next button visible

        # Wait for the next page to finish loading
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        page.wait_for_timeout(1_500)

    return all_results


import datetime as _dt


def _parse_ebay_date(raw: str) -> str:
    """Parse eBay date strings like 'Dec 5, 2025' or 'Sold Dec 5' → ISO date."""
    if not raw:
        return ""
    # Strip common prefixes
    raw = re.sub(r'^(sold|ended|on)\s*', '', raw.strip(), flags=re.IGNORECASE).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%y", "%m/%d/%Y", "%b %d %Y"):
        try:
            return _dt.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _scrape_seller_hub(page, status: str = "active") -> list[dict]:
    """
    DOM fallback: walk every /itm/ anchor in the current page and harvest
    title, price, thumbnail, and sold date from the nearest container.
    Works regardless of which CSS classes eBay uses on a given day.
    """
    raw = page.evaluate(r"""
        () => {
            const seen = new Set();
            const results = [];
            for (const link of document.querySelectorAll('a[href*="/itm/"]')) {
                const m = link.href.match(/\/itm\/(\d+)/);
                if (!m) continue;
                const lid = m[1];
                if (seen.has(lid)) continue;
                seen.add(lid);

                // Walk up to find the row / card container
                const container = link.closest(
                    'tr, [class*="row"], [class*="item"], [class*="listing"], li'
                ) || link.parentElement;

                // Title: prefer a dedicated title element; fall back to link text
                let title = '';
                if (container) {
                    const tel = container.querySelector(
                        '[class*="title"], [data-testid*="title"], h3, h4, h2'
                    );
                    if (tel) title = tel.textContent.trim();
                }
                if (!title) title = link.textContent.trim();
                if (!title) title = 'Untitled';

                // Price
                let price = '0';
                if (container) {
                    const pel = container.querySelector('[class*="price"]');
                    if (pel) {
                        const pm = pel.textContent.match(/[\d,]+\.?\d*/);
                        if (pm) price = pm[0].replace(/,/g, '');
                    }
                }

                // Thumbnail image — prefer eBay CDN urls; upscale to s-l400
                let img_url = '';
                if (container) {
                    const imgEl = container.querySelector(
                        'img[src*="ebayimg"], img[src*="ebaystatic"], img[src]'
                    );
                    if (imgEl && imgEl.src && !imgEl.src.startsWith('data:')) {
                        img_url = imgEl.src
                            .replace(/s-l\d+/, 's-l400')
                            .replace('/thumbs/', '/00/');
                    }
                }

                // Sold / end date (seller hub sold page shows date in a cell)
                let sold_date = '';
                if (container) {
                    for (const sel of [
                        '[class*="sold-date"]', '[class*="end-date"]',
                        '[data-testid*="date"]', 'td:nth-child(5)', 'td:nth-child(4)'
                    ]) {
                        const dateEl = container.querySelector(sel);
                        if (dateEl) {
                            const txt = dateEl.textContent.trim();
                            // Only accept values that look like dates
                            if (/\d/.test(txt) && txt.length < 30) {
                                sold_date = txt;
                                break;
                            }
                        }
                    }
                }

                results.push({ url: link.href, title, price, img_url, sold_date });
            }
            return results;
        }
    """)
    results = []
    seen: set[str] = set()
    for item in (raw or []):
        m = re.search(r"/itm/(\d+)", item.get("url", ""))
        lid = m.group(1) if m else ""
        if not lid or lid in seen:
            continue
        seen.add(lid)
        try:
            price_val = float(item.get("price") or 0)
        except (ValueError, TypeError):
            price_val = 0.0
        results.append({
            "listing_id": lid,
            "title":      _clean_title(item.get("title", "")),
            "url":        item.get("url", ""),
            "price":      price_val,
            "status":     status,
            "img_url":    item.get("img_url", ""),
            "sold_date":  _parse_ebay_date(item.get("sold_date", "")),
            "description": "",
        })
    return results


def _enrich_new_items(page, listings: list[dict],
                      progress_cb=None, max_items: int = 25):
    """
    Visit individual eBay listing pages to extract high-res images, description
    text, and sold date.  Modifies each listing dict in-place.

    Priority order for enrichment:
      1. New items (not yet in DB at all) — always enriched
      2. Existing items whose description is empty — enriched up to remaining budget

    Description strategy: eBay puts the seller's HTML description inside an iframe
    (#desc_div) whose content is NOT accessible via innerText.  Instead we collect:
      • og:description  — page-level meta, usually the item title + condition
      • meta[name=description] — standard description meta
      • Item specifics table (.ux-labels-values) — condition, brand, size, etc.
      • Short description / highlights section
    """
    from app.database.models import get_item_id_for_listing
    from app.database.connection import get_connection

    to_enrich: list[dict] = []
    for listing in listings:
        if len(to_enrich) >= max_items:
            break
        item_id = get_item_id_for_listing("ebay", listing["listing_id"])
        if item_id is None:
            # Brand-new item — definitely enrich
            to_enrich.append(listing)
        else:
            # Existing item — enrich only if description is still empty
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT description FROM items WHERE id=?", (item_id,)
                ).fetchone()
            if row and not (row["description"] or "").strip():
                to_enrich.append(listing)

    if not to_enrich:
        return

    if progress_cb:
        progress_cb(f"Fetching details for {len(to_enrich)} listings…")

    for listing in to_enrich:
        url = listing.get("url", "")
        if not url:
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=18_000)
            page.wait_for_timeout(800)

            data = page.evaluate("""
                () => {
                    // ── Image ──────────────────────────────────────────────
                    const ogImg = document.querySelector('meta[property="og:image"]');
                    let img_url = ogImg ? ogImg.content : '';
                    if (!img_url) {
                        for (const sel of [
                            '#icImg', '[class*="ux-image"] img',
                            'img[itemprop="image"]', '.vim-image img'
                        ]) {
                            const el = document.querySelector(sel);
                            if (el && el.src && !el.src.startsWith('data:')) {
                                img_url = el.src;
                                break;
                            }
                        }
                    }

                    // ── Description ────────────────────────────────────────
                    // Strategy: avoid the #desc_div iframe (content not accessible).
                    // Pull from meta tags + item specifics table instead.
                    let descParts = [];

                    // 1. og:description meta
                    const ogDesc = document.querySelector('meta[property="og:description"]');
                    if (ogDesc && ogDesc.content) descParts.push(ogDesc.content.trim());

                    // 2. standard meta description (often richer)
                    const metaDesc = document.querySelector('meta[name="description"]');
                    if (metaDesc && metaDesc.content) {
                        const md = metaDesc.content.trim();
                        if (md && !descParts.some(p => p.includes(md.substring(0, 40))))
                            descParts.push(md);
                    }

                    // 3. Item specifics: grab label→value pairs from the spec table
                    const specRows = document.querySelectorAll('.ux-labels-values__labels-content, .ux-labels-values');
                    const specPairs = [];
                    specRows.forEach(row => {
                        const labels = row.querySelectorAll('.ux-labels-values__labels');
                        const values = row.querySelectorAll('.ux-labels-values__values');
                        for (let i = 0; i < Math.min(labels.length, values.length); i++) {
                            const lbl = labels[i].textContent.trim();
                            const val = values[i].textContent.trim();
                            if (lbl && val) specPairs.push(lbl + ': ' + val);
                        }
                    });
                    if (specPairs.length) descParts.push(specPairs.join(' | '));

                    // 4. Short highlights / condition description
                    for (const sel of [
                        '.d-shortdescription', '.ux-section-highlights',
                        '[data-testid="x-item-condition-text"]',
                        '.condText', '.viSNotesCnt'
                    ]) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const t = el.innerText.trim();
                            if (t) { descParts.push(t); break; }
                        }
                    }

                    const description = descParts.join('\\n').trim().substring(0, 1500);

                    // ── Sold date ──────────────────────────────────────────
                    let sold_date = '';
                    for (const sel of [
                        '.ux-timer', '[class*="sold-on"]', '[class*="end-date"]',
                        '[class*="timeLeft"]', '.vi-statusB'
                    ]) {
                        const el = document.querySelector(sel);
                        if (el) { sold_date = el.textContent.trim(); break; }
                    }

                    return { img_url, description, sold_date };
                }
            """)

            # Back-fill only missing/empty fields
            if data.get("img_url") and not listing.get("img_url"):
                listing["img_url"] = data["img_url"]
            if data.get("description") and not listing.get("description"):
                listing["description"] = data["description"]
            if data.get("sold_date") and listing.get("status") == "sold" \
                    and not listing.get("sold_date"):
                parsed = _parse_ebay_date(data["sold_date"])
                if parsed:
                    listing["sold_date"] = parsed

        except Exception:
            pass  # Never abort the sync for a single enrichment failure


def _extract_item_list(body: dict) -> list:
    """
    Search a JSON response body for a list that looks like listing records.
    Returns the first non-empty list found, or [].
    """
    if not isinstance(body, dict):
        return []

    # Direct top-level keys
    for key in ("items", "listings", "itemCollection", "itemList",
                "entries", "activeListings", "soldListings", "results",
                "listingCollection"):
        val = body.get(key)
        if isinstance(val, list) and val:
            return val
        if isinstance(val, dict):
            for subkey in ("items", "listing", "entries", "results"):
                sub = val.get(subkey)
                if isinstance(sub, list) and sub:
                    return sub

    # One level deep under common wrapper keys
    for wrapper in ("data", "result", "payload", "response",
                    "searchResult", "body", "content"):
        sub = body.get(wrapper)
        if not isinstance(sub, dict):
            continue
        for key in ("items", "listings", "item", "entries", "results",
                    "activeListings", "soldListings", "listingCollection"):
            val = sub.get(key)
            if isinstance(val, list) and val:
                return val
            if isinstance(val, dict):
                for subkey in ("items", "listing", "entries"):
                    deep = val.get(subkey)
                    if isinstance(deep, list) and deep:
                        return deep

    return []


def _parse_intercepted(responses: list[dict]) -> list[dict]:
    seen, results = set(), []
    for resp in responses:
        items = _extract_item_list(resp.get("body", {}))
        for item in items:
            if not isinstance(item, dict):
                continue

            # Item ID — try several field names
            lid = str(
                item.get("itemId") or item.get("id") or
                item.get("listingId") or item.get("listing_id") or ""
            ).strip()
            if not lid or lid in seen:
                continue
            seen.add(lid)

            # Status
            status_raw = str(
                item.get("listingStatus") or item.get("status") or
                item.get("sellingState") or "active"
            ).lower()
            status = (
                "sold"
                if any(s in status_raw for s in ("sold", "completed", "ended"))
                else "active"
            )

            # Price — may be nested {value, currencyCode} or a bare number
            price_obj = (
                item.get("currentPrice") or item.get("price") or
                item.get("buyItNowPrice") or item.get("soldPrice") or {}
            )
            if isinstance(price_obj, dict):
                price_val = float(
                    price_obj.get("value") or price_obj.get("amount") or 0
                )
            else:
                try:
                    price_val = float(price_obj or 0)
                except (ValueError, TypeError):
                    price_val = 0.0

            title = (
                item.get("title") or item.get("name") or
                item.get("itemTitle") or "Untitled"
            )
            url = (
                item.get("viewItemURL") or item.get("url") or
                item.get("listingUrl") or f"https://www.ebay.com/itm/{lid}"
            )
            img = (
                item.get("galleryURL") or item.get("pictureUrl") or
                item.get("imageUrl") or item.get("thumbnail") or ""
            )

            results.append({
                "listing_id": lid,
                "title":      title,
                "url":        url,
                "price":      price_val,
                "status":     status,
                "img_url":    img,
            })
    return results
