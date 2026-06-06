from typing import Optional
from .connection import get_connection


# ── Items ──────────────────────────────────────────────────────────────────

def get_all_items() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT i.*,
                   -- platforms: ALL platforms with any listing (active or sold).
                   -- Falls back to sync_source so items created by sync but missing
                   -- a listing record still show their origin platform.
                   COALESCE(
                       (SELECT GROUP_CONCAT(DISTINCT l2.platform)
                        FROM listings l2 WHERE l2.item_id = i.id),
                       NULLIF(i.sync_source, '')
                   ) AS platforms,
                   -- listing_count: only active listings (drives "X active" badge)
                   COUNT(DISTINCT CASE WHEN l.status = 'active' THEN l.id END) AS listing_count,
                   -- sold_count: sold listings (so we can display "Sold" instead of "Unlisted")
                   COUNT(DISTINCT CASE WHEN l.status = 'sold'   THEN l.id END) AS sold_count,
                   MAX(CASE WHEN l.status = 'active' THEN l.listing_price END) AS listed_price,
                   -- earliest listing date across all platforms
                   MIN(l.listed_date) AS first_listed_date,
                   -- best available image: local file first, then remote URL
                   (SELECT COALESCE(NULLIF(img.local_path, ''), NULLIF(img.source_url, ''))
                    FROM images img
                    WHERE img.item_id = i.id
                    ORDER BY img.is_primary DESC, img.id ASC
                    LIMIT 1) AS first_image_path
            FROM items i
            LEFT JOIN listings l ON l.item_id = i.id
            GROUP BY i.id
            ORDER BY i.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_item(item_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["listings"] = [dict(r) for r in conn.execute(
            "SELECT * FROM listings WHERE item_id = ? ORDER BY platform", (item_id,)
        ).fetchall()]
        item["images"] = [dict(r) for r in conn.execute(
            "SELECT * FROM images WHERE item_id = ? ORDER BY is_primary DESC", (item_id,)
        ).fetchall()]
        return item


def save_item(data: dict) -> int:
    fields = ("title", "description", "bin_location", "category",
              "purchase_cost", "purchase_date", "notes", "sync_source")
    values = tuple(data.get(f, "" if f != "purchase_cost" else 0) for f in fields)
    with get_connection() as conn:
        if data.get("id"):
            conn.execute(
                f"UPDATE items SET {', '.join(f+'=?' for f in fields)} WHERE id=?",
                values + (data["id"],)
            )
            return data["id"]
        cur = conn.execute(
            f"INSERT INTO items ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
            values
        )
        return cur.lastrowid


def update_sync_source_if_empty(item_id: int, source: str):
    """Stamp sync_source on an existing item that doesn't have one yet."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE items SET sync_source = ? "
            "WHERE id = ? AND (sync_source IS NULL OR sync_source = '')",
            (source, item_id),
        )


def delete_item(item_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


# ── Listings ───────────────────────────────────────────────────────────────

def upsert_listing(data: dict) -> int:
    fields = ("item_id", "platform", "listing_id", "url",
              "listing_price", "status", "listed_date", "sold_date", "sold_price")
    values = tuple(data.get(f, "") for f in fields)
    with get_connection() as conn:
        cur = conn.execute(f"""
            INSERT INTO listings ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})
            ON CONFLICT(platform, listing_id) DO UPDATE SET
                url=excluded.url,
                listing_price=excluded.listing_price,
                status=excluded.status,
                sold_date=excluded.sold_date,
                sold_price=excluded.sold_price,
                updated_at=datetime('now')
        """, values)
        return cur.lastrowid


def delete_listing(listing_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,))


# ── Images ─────────────────────────────────────────────────────────────────

def save_image(data: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO images (item_id, local_path, image_hash, source_url, is_primary) VALUES (?,?,?,?,?)",
            (data["item_id"], data["local_path"], data.get("image_hash", ""),
             data.get("source_url", ""), data.get("is_primary", 0))
        )
        return cur.lastrowid


def upsert_image_url(item_id: int, source_url: str):
    """Record a remote image URL for an item if it isn't already stored."""
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT id FROM images WHERE item_id=? AND source_url=?",
            (item_id, source_url)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO images (item_id, local_path, image_hash, source_url, is_primary)"
                " VALUES (?, '', '', ?, 0)",
                (item_id, source_url)
            )


def get_items_by_hash(image_hash: str) -> list[dict]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT DISTINCT item_id FROM images WHERE image_hash = ?", (image_hash,)
        ).fetchall()]


def get_item_id_for_listing(platform: str, listing_id: str) -> Optional[int]:
    """Return the item_id already associated with this platform listing, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT item_id FROM listings WHERE platform=? AND listing_id=?",
            (platform, listing_id)
        ).fetchone()
        return row["item_id"] if row else None


# ── Sales ──────────────────────────────────────────────────────────────────

def save_sale(data: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO sales (item_id, listing_id, platform, sale_price, platform_fees, shipping_cost, sale_date) VALUES (?,?,?,?,?,?,?)",
            (data["item_id"], data.get("listing_id"), data["platform"],
             data["sale_price"], data.get("platform_fees", 0),
             data.get("shipping_cost", 0), data["sale_date"])
        )
        return cur.lastrowid


def get_sales(year: Optional[int] = None, month: Optional[int] = None) -> list[dict]:
    where, params = [], []
    if year:
        where.append("strftime('%Y', s.sale_date) = ?")
        params.append(str(year))
    if month:
        where.append("strftime('%m', s.sale_date) = ?")
        params.append(f"{month:02d}")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT s.*,
                   i.title,
                   i.purchase_cost,
                   (s.sale_price - s.platform_fees - s.shipping_cost - COALESCE(i.purchase_cost,0)) AS profit
            FROM sales s
            JOIN items i ON i.id = s.item_id
            {clause}
            ORDER BY s.sale_date DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


def get_monthly_platform_totals(year: int) -> dict:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT platform,
                   CAST(strftime('%m', sale_date) AS INTEGER) AS month,
                   SUM(sale_price - platform_fees - shipping_cost) AS revenue,
                   SUM(sale_price - platform_fees - shipping_cost - COALESCE(
                       (SELECT purchase_cost FROM items WHERE id = sales.item_id), 0
                   )) AS profit
            FROM sales
            WHERE strftime('%Y', sale_date) = ?
            GROUP BY platform, month
            ORDER BY month
        """, (str(year),)).fetchall()
        return [dict(r) for r in rows]


# ── Settings ───────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )
