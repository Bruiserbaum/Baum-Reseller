"""
CSV import service — bulk-import historical listings/sales from a spreadsheet.
"""
import csv
import io
import uuid

# ── Template ──────────────────────────────────────────────────────────────────

TEMPLATE_COLUMNS = [
    "title", "description", "category", "bin_location",
    "purchase_cost", "purchase_date", "notes",
    "platform", "listing_id", "url",
    "listing_price", "status", "listed_date",
    "sold_date", "sold_price", "platform_fees", "shipping_cost", "sale_date",
]

TEMPLATE_EXAMPLES = [
    ["Blue Denim Jacket Size M", "Great condition, no stains", "Clothing", "Shelf A-1",
     "15.00", "2024-01-15", "Thrift store find",
     "ebay", "123456789", "https://www.ebay.com/itm/123456789",
     "45.00", "sold", "2024-01-20",
     "2024-02-01", "42.00", "4.20", "5.50", "2024-02-01"],

    ["Red Nike Sneakers Size 10", "Worn twice, original box", "Shoes", "Shelf B-3",
     "25.00", "2024-01-20", "",
     "poshmark", "abc123def456", "https://poshmark.com/listing/abc123def456",
     "65.00", "active", "2024-01-25",
     "", "", "", "", ""],

    ["Vintage Polaroid Camera", "Tested and working", "Electronics", "Drawer 1",
     "30.00", "2024-02-01", "Includes film pack",
     "mercari", "", "",
     "80.00", "sold", "2024-02-05",
     "2024-02-15", "75.00", "7.50", "8.00", "2024-02-15"],
]


def get_template_csv() -> str:
    """Return the CSV template as a string (UTF-8, with BOM for Excel compat)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerows(TEMPLATE_EXAMPLES)
    return "﻿" + buf.getvalue()   # BOM so Excel opens it correctly


# ── Import ────────────────────────────────────────────────────────────────────

VALID_PLATFORMS = {"ebay", "mercari", "poshmark"}
VALID_STATUSES  = {"active", "sold"}


def _safe_float(val) -> float:
    try:
        return float(str(val).replace("$", "").replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def parse_csv(file_path: str) -> tuple[list[str], list[dict]]:
    """
    Read a CSV file and return (headers, rows_as_dicts).
    Handles UTF-8 with or without BOM.
    """
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [dict(r) for r in reader]
    return list(headers), rows


def import_rows(rows: list[dict], progress_cb=None) -> tuple[int, int, list[str]]:
    """
    Import rows into the database.
    Returns (imported, skipped, error_messages).

    Rules:
    • Each row creates one item + (if platform given) one listing.
    • If status == 'sold' and sold_price > 0 and sale_date present → also creates a sale record.
    • listing_id: if blank, a synthetic import_<hex> id is generated so the UNIQUE
      (platform, listing_id) constraint is satisfied.
    • Rows with no title are skipped.
    """
    from app.database.models import save_item, upsert_listing, save_sale

    imported, skipped = 0, 0
    errors: list[str] = []

    total = len(rows)
    for i, row in enumerate(rows):
        if progress_cb:
            progress_cb(i + 1, total)
        try:
            title = (row.get("title") or "").strip()
            if not title:
                skipped += 1
                continue

            platform = (row.get("platform") or "").strip().lower()
            status   = (row.get("status")   or "active").strip().lower()
            if status not in VALID_STATUSES:
                status = "active"

            # ── Item ─────────────────────────────────────────────────────
            item_id = save_item({
                "title":         title,
                "description":   (row.get("description")  or "").strip(),
                "category":      (row.get("category")     or "").strip(),
                "bin_location":  (row.get("bin_location") or "").strip(),
                "purchase_cost": _safe_float(row.get("purchase_cost")),
                "purchase_date": (row.get("purchase_date") or "").strip(),
                "notes":         (row.get("notes")         or "").strip(),
            })

            # ── Listing ───────────────────────────────────────────────────
            if platform:
                listing_id = (row.get("listing_id") or "").strip()
                if not listing_id:
                    listing_id = f"import_{uuid.uuid4().hex[:14]}"

                sold_price = _safe_float(row.get("sold_price"))
                upsert_listing({
                    "item_id":       item_id,
                    "platform":      platform,
                    "listing_id":    listing_id,
                    "url":           (row.get("url")          or "").strip(),
                    "listing_price": _safe_float(row.get("listing_price")),
                    "status":        status,
                    "listed_date":   (row.get("listed_date")  or "").strip(),
                    "sold_date":     (row.get("sold_date")    or "").strip(),
                    "sold_price":    sold_price,
                })

                # ── Sale record (only if sold + has price + has date) ─────
                if status == "sold":
                    sale_date = (row.get("sale_date") or row.get("sold_date") or "").strip()
                    if sold_price > 0 and sale_date:
                        save_sale({
                            "item_id":       item_id,
                            "platform":      platform,
                            "sale_price":    sold_price,
                            "platform_fees": _safe_float(row.get("platform_fees")),
                            "shipping_cost": _safe_float(row.get("shipping_cost")),
                            "sale_date":     sale_date,
                        })

            imported += 1

        except Exception as exc:
            errors.append(f"Row {i + 2}: {exc}")
            skipped += 1

    return imported, skipped, errors
