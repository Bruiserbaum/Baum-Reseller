"""
Background notification checks:
  1. unshipped  — sale recorded 3+ days ago with no shipped_date
  2. still_listed — item sold on one platform but still active on another
  3. missing    — items flagged as missing (surfaced on startup)
"""
import threading
from app.database.connection import get_connection

UNSHIPPED_DAYS = 3


# ── Queries ────────────────────────────────────────────────────────────────

def _query_unshipped() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.id AS sale_id, s.item_id, s.platform, s.sale_date,
                   i.title,
                   CAST(julianday('now') - julianday(s.sale_date) AS INTEGER) AS days_ago
            FROM sales s
            JOIN items i ON i.id = s.item_id
            WHERE s.shipped_date IS NULL
              AND julianday('now') - julianday(s.sale_date) >= ?
            ORDER BY s.sale_date
        """, (UNSHIPPED_DAYS,)).fetchall()
        return [dict(r) for r in rows]


def _query_still_listed() -> list[dict]:
    """Items with a 'sold' listing on one platform and an 'active' listing on another."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT i.id AS item_id, i.title,
                   sold_l.platform AS sold_on,
                   active_l.platform AS still_on
            FROM items i
            JOIN listings sold_l  ON sold_l.item_id  = i.id AND sold_l.status  = 'sold'
            JOIN listings active_l ON active_l.item_id = i.id AND active_l.status = 'active'
            WHERE sold_l.platform != active_l.platform
        """).fetchall()
        return [dict(r) for r in rows]


def _query_missing() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id AS item_id, title, missing_notes
            FROM items
            WHERE is_missing = 1
        """).fetchall()
        return [dict(r) for r in rows]


# ── Persistence ────────────────────────────────────────────────────────────

def _notification_exists(ntype: str, item_id: int, sale_id: int | None = None) -> bool:
    with get_connection() as conn:
        if sale_id:
            row = conn.execute(
                "SELECT id FROM notifications WHERE type=? AND sale_id=? AND dismissed=0",
                (ntype, sale_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM notifications WHERE type=? AND item_id=? AND dismissed=0",
                (ntype, item_id)
            ).fetchone()
        return row is not None


def _create_notification(ntype: str, message: str, item_id: int | None,
                         sale_id: int | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (type, item_id, sale_id, message) VALUES (?,?,?,?)",
            (ntype, item_id, sale_id, message)
        )
        return cur.lastrowid


def get_active_notifications() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM notifications
            WHERE dismissed = 0
            ORDER BY created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def dismiss_notification(notif_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE notifications SET dismissed=1 WHERE id=?", (notif_id,))


def dismiss_all():
    with get_connection() as conn:
        conn.execute("UPDATE notifications SET dismissed=1 WHERE dismissed=0")


def get_unread_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE dismissed=0"
        ).fetchone()
        return row[0]


# ── Mark helpers (called from UI) ──────────────────────────────────────────

def mark_shipped(sale_id: int, tracking: str = ""):
    with get_connection() as conn:
        conn.execute(
            "UPDATE sales SET shipped_date=datetime('now'), tracking_number=? WHERE id=?",
            (tracking, sale_id)
        )
        conn.execute(
            "UPDATE notifications SET dismissed=1 WHERE type='unshipped' AND sale_id=?",
            (sale_id,)
        )


def mark_missing(item_id: int, notes: str = ""):
    with get_connection() as conn:
        conn.execute(
            "UPDATE items SET is_missing=1, missing_notes=? WHERE id=?",
            (notes, item_id)
        )


def mark_found(item_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE items SET is_missing=0, missing_notes='' WHERE id=?", (item_id,))
        conn.execute(
            "UPDATE notifications SET dismissed=1 WHERE type='missing' AND item_id=?",
            (item_id,)
        )


# ── Toast ──────────────────────────────────────────────────────────────────

def _send_toast(title: str, message: str):
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="BOM Reseller",
            timeout=8,
        )
    except Exception:
        pass  # plyer not installed or platform unsupported


# ── Main check runner ──────────────────────────────────────────────────────

def run_checks(toast: bool = True) -> int:
    """Run all background checks. Returns count of new notifications created."""
    new_count = 0

    # 1. Unshipped sales
    for row in _query_unshipped():
        if not _notification_exists("unshipped", row["item_id"], sale_id=row["sale_id"]):
            days = row["days_ago"]
            msg = (f"'{row['title']}' sold on {row['platform'].capitalize()} "
                   f"{days} day{'s' if days != 1 else ''} ago — not yet shipped.")
            _create_notification("unshipped", msg, row["item_id"], sale_id=row["sale_id"])
            if toast:
                _send_toast("Unshipped Sale", msg)
            new_count += 1

    # 2. Still listed after sold
    for row in _query_still_listed():
        if not _notification_exists("still_listed", row["item_id"]):
            msg = (f"'{row['title']}' was sold on {row['sold_on'].capitalize()} "
                   f"but is still active on {row['still_on'].capitalize()}.")
            _create_notification("still_listed", msg, row["item_id"])
            if toast:
                _send_toast("Still Listed", msg)
            new_count += 1

    # 3. Missing items — surface on startup, don't re-notify
    for row in _query_missing():
        if not _notification_exists("missing", row["item_id"]):
            msg = f"'{row['title']}' is marked as missing."
            if row.get("missing_notes"):
                msg += f" Notes: {row['missing_notes']}"
            _create_notification("missing", msg, row["item_id"])
            new_count += 1

    return new_count


def run_checks_async(done_cb=None):
    def _worker():
        count = run_checks(toast=True)
        if done_cb:
            done_cb(count)
    threading.Thread(target=_worker, daemon=True).start()
