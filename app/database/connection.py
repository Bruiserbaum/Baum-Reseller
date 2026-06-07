import sqlite3
import os

DB_PATH = os.path.join(os.path.expanduser("~"), ".baum-reseller", "baum_reseller.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    bin_location TEXT DEFAULT '',
    category TEXT DEFAULT '',
    purchase_cost REAL DEFAULT 0,
    purchase_date TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    url TEXT DEFAULT '',
    listing_price REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    listed_date TEXT DEFAULT '',
    sold_date TEXT DEFAULT '',
    sold_price REAL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
    UNIQUE(platform, listing_id)
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    local_path TEXT NOT NULL,
    image_hash TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    is_primary INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    listing_id INTEGER,
    platform TEXT NOT NULL,
    sale_price REAL NOT NULL DEFAULT 0,
    platform_fees REAL DEFAULT 0,
    shipping_cost REAL DEFAULT 0,
    sale_date TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS items_updated_at
    AFTER UPDATE ON items
BEGIN
    UPDATE items SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS listings_updated_at
    AFTER UPDATE ON listings
BEGIN
    UPDATE listings SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""


MIGRATIONS = [
    # Add shipped_date + tracking to sales
    "ALTER TABLE sales ADD COLUMN shipped_date TEXT DEFAULT NULL",
    "ALTER TABLE sales ADD COLUMN tracking_number TEXT DEFAULT ''",
    # Add missing flag to items
    "ALTER TABLE items ADD COLUMN is_missing INTEGER DEFAULT 0",
    "ALTER TABLE items ADD COLUMN missing_notes TEXT DEFAULT ''",
    # Notifications table
    """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        item_id INTEGER,
        sale_id INTEGER,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        dismissed INTEGER DEFAULT 0,
        FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
    )""",
    # Indexes for get_all_items() GROUP BY + JOIN performance
    "CREATE INDEX IF NOT EXISTS idx_listings_item_id ON listings(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_listings_status   ON listings(status)",
    "CREATE INDEX IF NOT EXISTS idx_items_created_at  ON items(created_at DESC)",
    # Track which platform originally created each item (even before listings exist)
    "ALTER TABLE items ADD COLUMN sync_source TEXT DEFAULT ''",
    # Backfill sync_source for existing items that already have listing records
    """UPDATE items
       SET sync_source = (
           SELECT platform FROM listings
           WHERE listings.item_id = items.id
           ORDER BY listings.created_at ASC LIMIT 1
       )
       WHERE (sync_source IS NULL OR sync_source = '')
       AND EXISTS (SELECT 1 FROM listings WHERE listings.item_id = items.id)""",
    # ext_listing_id links a sales record back to the platform's own listing ID,
    # used to prevent duplicate sale entries on re-sync.
    "ALTER TABLE sales ADD COLUMN ext_listing_id TEXT DEFAULT ''",
    # Index to make the dupe-check fast
    "CREATE INDEX IF NOT EXISTS idx_sales_ext ON sales(item_id, platform, ext_listing_id)",
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL mode lets readers and writers proceed concurrently — critical so that
    # a running import (writes) doesn't block the inventory query (reads).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")   # safe with WAL, much faster
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        for migration in MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column/table already exists
