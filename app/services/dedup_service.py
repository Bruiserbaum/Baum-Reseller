"""
Deduplication service — detects items that are the same physical product
listed on multiple platforms (eBay, Mercari, Poshmark) and offers to merge
their database records into a single unified entry.

Detection methods (both are checked):
  1. Image hash: items sharing the same perceptual hash → almost certain match
  2. Title similarity: Jaccard coefficient on normalised word sets (stop words
     and size/colour tokens stripped); threshold ≥ 0.75

A candidate pair only qualifies if the two items have *no platform overlap*
(otherwise they'd already be under the same listing).

The merge operation is atomic:
  • All listings from 'drop' item → reassigned to 'keep' item
  • All images  from 'drop' item → reassigned to 'keep' item
  • All sales   from 'drop' item → reassigned to 'keep' item
  • Text fields merged: take the longer description, keep non-zero purchase cost
  • 'drop' item deleted

Public API
----------
  find_candidates(threshold=0.75) -> list[Candidate]
  merge(keep_id, drop_id)
  run_background_scan(auto_threshold=0.90, done_cb=None)
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

# ── Stop-word and noise lists ─────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "for", "of", "in", "with",
    "to", "by", "at", "from", "on", "is", "it", "as", "be",
    "new", "nwt", "nwot", "vtg", "vintage", "used", "pre-owned",
    "women", "womens", "women's", "men", "mens", "men's",
    "girls", "boys", "kids", "juniors",
})

# Common size/colour tokens we strip before comparing — they're important for
# the listing but create false positives when the same item is listed in
# different sizes on different platforms.
_SIZE_RE  = re.compile(
    r'\b(xs|s|m|l|xl|xxl|xxxl|0|2|4|6|8|10|12|14|16|18|20|'
    r'one[\s-]size|os|plus|petite|tall|regular|slim|fit)\b',
    re.IGNORECASE,
)
_COLOUR_RE = re.compile(
    r'\b(black|white|gray|grey|red|blue|green|yellow|orange|pink|'
    r'purple|brown|tan|beige|navy|teal|cream|ivory|gold|silver|'
    r'multi|multicolor|floral|stripe|plaid|print)\b',
    re.IGNORECASE,
)


# ── Candidate dataclass ───────────────────────────────────────────────────────

@dataclass
class Candidate:
    id_a:    int
    id_b:    int
    title_a: str
    title_b: str
    plats_a: str   # comma-sep platform names
    plats_b: str
    score:   float         # 0.0–1.0
    method:  str           # "title" | "image_hash"
    # populated lazily by the UI
    img_a:   str = field(default="")
    img_b:   str = field(default="")


# ── Title normalisation ───────────────────────────────────────────────────────

def _normalise(title: str) -> frozenset[str]:
    t = title.lower()
    t = _SIZE_RE.sub("", t)
    t = _COLOUR_RE.sub("", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = frozenset(t.split()) - _STOP_WORDS
    return tokens


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on normalised word sets. Returns 0.0–1.0."""
    wa, wb = _normalise(a), _normalise(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ── Core scanner ─────────────────────────────────────────────────────────────

def find_candidates(threshold: float = 0.75) -> list[Candidate]:
    """
    Scan the database for likely duplicate items and return scored pairs.
    Runs entirely in the calling thread — use run_background_scan() for
    non-blocking use.
    """
    from app.database.connection import get_connection

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT i.id,
                   i.title,
                   COALESCE(
                       (SELECT GROUP_CONCAT(DISTINCT l.platform)
                        FROM listings l WHERE l.item_id = i.id),
                       NULLIF(i.sync_source, '')
                   ) AS platforms,
                   (SELECT img.source_url FROM images img
                    WHERE img.item_id = i.id
                    ORDER BY img.is_primary DESC, img.id LIMIT 1) AS img_url,
                   (SELECT img.image_hash FROM images img
                    WHERE img.item_id = i.id AND img.image_hash != ''
                    ORDER BY img.is_primary DESC, img.id LIMIT 1) AS img_hash
            FROM items i
            WHERE i.title IS NOT NULL AND i.title != ''
            ORDER BY i.id
        """).fetchall()

    items = [dict(r) for r in rows]
    seen_pairs: set[tuple[int, int]] = set()
    candidates: list[Candidate] = []

    # ── Pass 1: image-hash exact matches ─────────────────────────────────────
    hash_buckets: dict[str, list[dict]] = {}
    for item in items:
        h = item.get("img_hash") or ""
        if h:
            hash_buckets.setdefault(h, []).append(item)

    for h, group in hash_buckets.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if _platforms_overlap(a, b):
                    continue
                pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append(_make_candidate(a, b, 1.0, "image_hash"))

    # ── Pass 2: title-similarity scan ────────────────────────────────────────
    # Use a bucket approach: group by first two significant words to avoid O(n²)
    buckets: dict[str, list[dict]] = {}
    for item in items:
        key = _bucket_key(item["title"])
        buckets.setdefault(key, []).append(item)

    for bucket_items in buckets.values():
        for i in range(len(bucket_items)):
            for j in range(i + 1, len(bucket_items)):
                a, b = bucket_items[i], bucket_items[j]
                if _platforms_overlap(a, b):
                    continue
                pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if pair in seen_pairs:
                    continue
                score = title_similarity(a["title"], b["title"])
                if score >= threshold:
                    seen_pairs.add(pair)
                    candidates.append(_make_candidate(a, b, score, "title"))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _platforms_overlap(a: dict, b: dict) -> bool:
    pa = set((a.get("platforms") or "").split(",")) - {""}
    pb = set((b.get("platforms") or "").split(",")) - {""}
    return bool(pa & pb)


def _bucket_key(title: str) -> str:
    words = sorted(_normalise(title))[:2]
    return " ".join(words)


def _make_candidate(a: dict, b: dict, score: float, method: str) -> Candidate:
    return Candidate(
        id_a=a["id"], id_b=b["id"],
        title_a=a["title"], title_b=b["title"],
        plats_a=a.get("platforms") or "—",
        plats_b=b.get("platforms") or "—",
        score=score, method=method,
        img_a=a.get("img_url") or "",
        img_b=b.get("img_url") or "",
    )


# ── Merge operation ───────────────────────────────────────────────────────────

def merge(keep_id: int, drop_id: int) -> None:
    """
    Atomically merge drop_id into keep_id.
    All listings, images and sales from drop_id are reassigned to keep_id.
    Text fields are merged (longer description wins; non-zero purchase_cost wins).
    drop_id is then deleted.
    """
    from app.database.connection import get_connection

    with get_connection() as conn:
        keep = conn.execute("SELECT * FROM items WHERE id=?", (keep_id,)).fetchone()
        drop = conn.execute("SELECT * FROM items WHERE id=?", (drop_id,)).fetchone()
        if not keep or not drop:
            return

        # Merge text fields
        keep_desc  = keep["description"] or ""
        drop_desc  = drop["description"] or ""
        best_desc  = drop_desc if len(drop_desc) > len(keep_desc) else keep_desc

        keep_cost  = float(keep["purchase_cost"] or 0)
        drop_cost  = float(drop["purchase_cost"] or 0)
        best_cost  = drop_cost if drop_cost > 0 and keep_cost == 0 else keep_cost

        keep_notes = keep["notes"] or ""
        drop_notes = drop["notes"] or ""
        merged_notes = "\n".join(filter(None, [keep_notes, drop_notes]))[:2000]

        # Re-assign all child records to keep_id
        conn.execute("UPDATE listings       SET item_id=? WHERE item_id=?", (keep_id, drop_id))
        conn.execute("UPDATE images         SET item_id=? WHERE item_id=?", (keep_id, drop_id))
        conn.execute("UPDATE sales          SET item_id=? WHERE item_id=?", (keep_id, drop_id))
        conn.execute("UPDATE notifications  SET item_id=? WHERE item_id=?", (keep_id, drop_id))

        # Update keep with merged fields
        conn.execute(
            "UPDATE items SET description=?, purchase_cost=?, notes=? WHERE id=?",
            (best_desc, best_cost, merged_notes, keep_id),
        )

        # Delete the now-empty duplicate
        conn.execute("DELETE FROM items WHERE id=?", (drop_id,))


# ── Background runner ─────────────────────────────────────────────────────────

def run_background_scan(
    auto_threshold: float = 0.92,
    done_cb=None,
) -> None:
    """
    Scan for duplicates in a daemon thread.
    Pairs with score ≥ auto_threshold are auto-merged silently.
    done_cb(candidates: list[Candidate]) is called on completion (may be on
    the background thread — use post_to_main if updating UI).
    """
    def _worker():
        try:
            candidates = find_candidates(threshold=0.75)
            merged: list[Candidate] = []

            for c in candidates:
                if c.score >= auto_threshold:
                    try:
                        merge(c.id_a, c.id_b)
                        merged.append(c)
                    except Exception:
                        pass

            # Remove auto-merged from the review list
            merged_ids = {(c.id_a, c.id_b) for c in merged}
            pending = [c for c in candidates
                       if (c.id_a, c.id_b) not in merged_ids]

            # Persist scan timestamp and pending count for the Sync status panel
            try:
                import datetime
                from app.database.models import set_setting
                set_setting("last_dedup_scan",
                            datetime.datetime.now().isoformat(timespec="seconds"))
                set_setting("dedup_pending_count", str(len(pending)))
            except Exception:
                pass

            if done_cb:
                done_cb(pending)
        except Exception:
            if done_cb:
                done_cb([])

    threading.Thread(target=_worker, daemon=True).start()
