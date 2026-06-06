import base64
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QSplitter, QStyledItemDelegate,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor, QPixmap, QImage
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog

from app.database.connection import get_connection
from app.utils.qt_thread import post_to_main
from app.views.item_detail_view import ItemDetailDialog

_IMG_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bin-img")

_THUMB_COL = 0
_TITLE_COL = 1
_THUMB_W = 60
_ROW_H = 58


def _days_listed_str(first_date) -> str:
    if not first_date:
        return "—"
    try:
        d = date.fromisoformat(str(first_date)[:10])
        n = (date.today() - d).days
        if n < 0:
            return "—"
        if n == 0:
            return "Today"
        if n < 7:
            return f"{n}d"
        if n < 30:
            return f"{n // 7}w"
        if n < 365:
            return f"{round(n / 30.4)}mo"
        return f"{round(n / 365.25)}y"
    except Exception:
        return "—"


_NO_BIN_LABEL = "(No Bin)"


def _fetch_bins() -> list[dict]:
    with get_connection() as conn:
        # Named bins
        rows = conn.execute("""
            SELECT bin_location, COUNT(*) AS item_count
            FROM items
            WHERE bin_location != '' AND bin_location IS NOT NULL
            GROUP BY bin_location
            ORDER BY bin_location
        """).fetchall()
        result = [dict(r) for r in rows]
        # Items with no bin location
        no_bin = conn.execute("""
            SELECT COUNT(*) AS item_count FROM items
            WHERE bin_location IS NULL OR bin_location = ''
        """).fetchone()
        if no_bin and no_bin["item_count"] > 0:
            result.append({"bin_location": "", "item_count": no_bin["item_count"]})
    return result


def _fetch_bin_items(bin_location: str) -> list[dict]:
    if bin_location == "":
        where  = "WHERE (i.bin_location IS NULL OR i.bin_location = '')"
        params: list = []
    else:
        where  = "WHERE i.bin_location = ?"
        params = [bin_location]
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT i.id, i.title, i.description, i.category,
                   i.purchase_cost, i.is_missing,
                   (SELECT COALESCE(NULLIF(img.local_path, ''), NULLIF(img.source_url, ''))
                    FROM images img
                    WHERE img.item_id = i.id
                    ORDER BY img.is_primary DESC, img.id ASC
                    LIMIT 1) AS first_image_path,
                   COUNT(DISTINCT CASE WHEN l.status = 'active' THEN l.id END) AS listing_count,
                   COUNT(DISTINCT CASE WHEN l.status = 'sold'   THEN l.id END) AS sold_count,
                   MIN(l.listed_date) AS first_listed_date
            FROM items i
            LEFT JOIN listings l ON l.item_id = i.id
            {where}
            GROUP BY i.id
            ORDER BY i.title
        """, params).fetchall()
        return [dict(r) for r in rows]


def _bins_containing(query: str) -> set[str]:
    """Return bin_location values that have at least one item whose title matches."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT bin_location
            FROM items
            WHERE bin_location != '' AND bin_location IS NOT NULL
              AND LOWER(title) LIKE ?
        """, (f"%{query.lower()}%",)).fetchall()
        return {r["bin_location"] for r in rows}


class _ThumbnailDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        r = option.rect.adjusted(3, 3, -3, -3)
        pm = index.data(Qt.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            scaled = pm.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = r.x() + (r.width() - scaled.width()) // 2
            y = r.y() + (r.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillRect(r, QColor("#313244"))

    def sizeHint(self, option, index):
        return QSize(_THUMB_W, _ROW_H)


class ContainersView(QWidget):
    def __init__(self):
        super().__init__()
        self._bins: list[dict] = []
        self._current_bin: str | None = None
        self._current_items: list[dict] = []
        self._pixmap_cache: dict[int, QPixmap | None] = {}
        self._dirty = False
        self._build_ui()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header row
        header = QHBoxLayout()
        title = QLabel("Containers")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search by item title…")
        self._search_box.setFixedWidth(260)
        self._search_box.textChanged.connect(self._on_search_changed)
        header.addWidget(self._search_box)

        self._print_btn = QPushButton("Print")
        self._print_btn.setObjectName("primaryButton")
        self._print_btn.clicked.connect(self._print_bin)
        self._print_btn.setEnabled(False)
        header.addWidget(self._print_btn)

        root.addLayout(header)

        # Splitter: left bin list | right item table
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #313244; }")

        # ── Left panel ─────────────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(6)

        bins_lbl = QLabel("BINS")
        bins_lbl.setStyleSheet(
            "color: #585b70; font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )
        ll.addWidget(bins_lbl)

        self._bin_list = QListWidget()
        self._bin_list.currentItemChanged.connect(self._on_bin_selected)
        ll.addWidget(self._bin_list, 1)

        splitter.addWidget(left)

        # ── Right panel ────────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.setSpacing(8)

        self._bin_title = QLabel("Select a bin")
        self._bin_title.setStyleSheet(
            "color: #a6adc8; font-size: 15px; font-weight: bold;"
        )
        rl.addWidget(self._bin_title)

        self._item_table = QTableWidget()
        self._item_table.setColumnCount(6)
        self._item_table.setHorizontalHeaderLabels(
            ["", "Title", "Category", "Cost", "Status", "Days"]
        )

        hdr = self._item_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(_THUMB_COL, QHeaderView.Fixed)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(40)
        hdr.sectionResized.connect(
            lambda col, _o, _n: self._fit_title_col() if col != _TITLE_COL else None
        )

        self._item_table.setColumnWidth(_THUMB_COL, _THUMB_W)
        self._item_table.setColumnWidth(2, 120)  # Category
        self._item_table.setColumnWidth(3, 80)   # Cost
        self._item_table.setColumnWidth(4, 100)  # Status
        self._item_table.setColumnWidth(5, 70)   # Days

        self._item_table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._thumb_delegate = _ThumbnailDelegate(self._item_table)
        self._item_table.setItemDelegateForColumn(_THUMB_COL, self._thumb_delegate)
        self._item_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._item_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._item_table.setAlternatingRowColors(True)
        self._item_table.verticalHeader().setVisible(False)
        self._item_table.doubleClicked.connect(self._open_item)

        rl.addWidget(self._item_table, 1)

        self._item_count_lbl = QLabel("")
        self._item_count_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        rl.addWidget(self._item_count_lbl)
        splitter.addWidget(right)

        splitter.setSizes([220, 900])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, 1)

    # ── Resize ────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_title_col)

    def _fit_title_col(self):
        vp = self._item_table.viewport().width()
        other = sum(
            self._item_table.columnWidth(c)
            for c in range(self._item_table.columnCount())
            if c != _TITLE_COL
        )
        self._item_table.setColumnWidth(_TITLE_COL, max(100, vp - other))

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh(self):
        self._dirty = False

        def _work():
            bins = _fetch_bins()
            post_to_main(lambda: self._on_bins_loaded(bins))

        threading.Thread(target=_work, daemon=True).start()

    def mark_dirty(self):
        self._dirty = True

    def lazy_refresh(self):
        if self._dirty:
            self.refresh()

    def _on_bins_loaded(self, bins: list[dict]):
        self._bins = bins
        self._populate_bin_list(bins)

    def _populate_bin_list(self, bins: list[dict]):
        current_bin = self._current_bin
        self._bin_list.blockSignals(True)
        self._bin_list.clear()
        for b in bins:
            loc   = b["bin_location"]
            count = b["item_count"]
            label = _NO_BIN_LABEL if loc == "" else loc
            litem = QListWidgetItem(f"📦  {label}  ({count})")
            litem.setData(Qt.UserRole, loc)
            self._bin_list.addItem(litem)
            if loc == current_bin:
                self._bin_list.setCurrentItem(litem)
        self._bin_list.blockSignals(False)

    def _on_bin_selected(self, current: QListWidgetItem | None, _prev):
        if current is None:
            return
        bin_loc = current.data(Qt.UserRole)
        self._current_bin = bin_loc
        label = _NO_BIN_LABEL if bin_loc == "" else bin_loc
        self._bin_title.setText(label)
        self._print_btn.setEnabled(True)
        self._item_table.setRowCount(0)

        def _work():
            items = _fetch_bin_items(bin_loc)
            post_to_main(lambda: self._on_items_loaded(items))

        threading.Thread(target=_work, daemon=True).start()

    def _on_items_loaded(self, items: list[dict]):
        self._current_items = items
        # Apply any in-progress search filter
        q = self._search_box.text().strip().lower()
        visible = [i for i in items if q in i.get("title", "").lower()] if q else items
        self._render_items(visible)

    def _render_items(self, items: list[dict]):
        self._item_table.setSortingEnabled(False)
        self._item_table.setUpdatesEnabled(False)
        self._item_table.setRowCount(len(items))

        missing_color = QColor("#f38ba8")

        for row, item in enumerate(items):
            item_id = item.get("id")

            # Col 0: thumbnail
            thumb = QTableWidgetItem()
            thumb.setData(Qt.UserRole, item_id)
            thumb.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            cached = self._pixmap_cache.get(item_id)
            if cached:
                thumb.setData(Qt.DecorationRole, cached)
            self._item_table.setItem(row, _THUMB_COL, thumb)

            img_path = item.get("first_image_path") or ""
            if img_path and item_id not in self._pixmap_cache:
                self._load_image_async(item_id, img_path)

            # Status
            is_missing = bool(item.get("is_missing", 0))
            title_text = ("❓ " if is_missing else "") + item.get("title", "")
            active = item.get("listing_count", 0) or 0
            sold = item.get("sold_count", 0) or 0
            if active:
                status = f"{active} active"
            elif sold:
                status = f"Sold ({sold})"
            else:
                status = "Unlisted"

            text_cells = [
                (_TITLE_COL, title_text),
                (2, item.get("category") or ""),
                (3, f"${(item.get('purchase_cost') or 0):.2f}"),
                (4, status),
                (5, _days_listed_str(item.get("first_listed_date"))),
            ]
            for col, text in text_cells:
                cell = QTableWidgetItem(str(text))
                cell.setData(Qt.UserRole, item_id)
                if is_missing:
                    cell.setForeground(missing_color)
                self._item_table.setItem(row, col, cell)

        self._item_table.setUpdatesEnabled(True)
        self._item_table.setSortingEnabled(True)
        n = len(items)
        self._item_count_lbl.setText(f"{n} item{'s' if n != 1 else ''}")
        QTimer.singleShot(0, self._fit_title_col)

    # ── Image loading ─────────────────────────────────────────────────────

    def _load_image_async(self, item_id: int, path: str):
        self._pixmap_cache[item_id] = None

        def _work():
            img: QImage | None = None
            try:
                if path.startswith("http"):
                    req = urllib.request.Request(
                        path,
                        headers={"User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        )},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = resp.read()
                    img = QImage()
                    if not img.loadFromData(data):
                        img = None
                elif os.path.exists(path):
                    img = QImage(path)
                    if img.isNull():
                        img = None
            except Exception:
                img = None

            def _apply():
                pm = QPixmap.fromImage(img) if (img and not img.isNull()) else None
                self._pixmap_cache[item_id] = pm
                if pm is None:
                    return
                for r in range(self._item_table.rowCount()):
                    cell = self._item_table.item(r, _THUMB_COL)
                    if cell and cell.data(Qt.UserRole) == item_id:
                        cell.setData(Qt.DecorationRole, pm)
                        break

            post_to_main(_apply)

        _IMG_POOL.submit(_work)

    # ── Search ────────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        query = text.strip().lower()

        if self._current_bin is not None:
            # Filter items within the currently selected bin
            visible = (
                [i for i in self._current_items if query in i.get("title", "").lower()]
                if query else list(self._current_items)
            )
            self._render_items(visible)
            return

        # No bin selected — filter the bin list to show bins containing matches
        if not query:
            self._populate_bin_list(self._bins)
            return

        def _work():
            matching = _bins_containing(query)
            filtered = [b for b in self._bins if b["bin_location"] in matching]
            post_to_main(lambda: self._populate_bin_list(filtered))

        threading.Thread(target=_work, daemon=True).start()

    # ── Double-click to open item ─────────────────────────────────────────

    def _open_item(self, index):
        cell = self._item_table.item(index.row(), _TITLE_COL)
        if not cell:
            return
        item_id = cell.data(Qt.UserRole)
        if not item_id:
            return
        dlg = ItemDetailDialog(item_id, parent=self)
        if dlg.exec():
            # Reload the current bin's items after any edits
            if self._current_bin is not None:
                def _work():
                    items = _fetch_bin_items(self._current_bin)
                    post_to_main(lambda: self._on_items_loaded(items))
                threading.Thread(target=_work, daemon=True).start()
            self.refresh()   # also refresh the bin list counts

    # ── Print ─────────────────────────────────────────────────────────────

    def _print_bin(self):
        if self._current_bin is None:
            return
        printer = QPrinter(QPrinter.HighResolution)
        preview = QPrintPreviewDialog(printer, self)
        preview.paintRequested.connect(self._do_print)
        preview.exec()

    def _do_print(self, printer: QPrinter):
        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setHtml(self._build_print_html())
        doc.print_(printer)

    def _build_print_html(self) -> str:
        items = self._current_items
        bin_name = _NO_BIN_LABEL if self._current_bin == "" else (self._current_bin or "")

        rows = ""
        for item in items:
            item_id = item.get("id")
            cost = f"${item.get('purchase_cost', 0):.2f}"
            title = item.get("title", "")
            desc = (item.get("description") or "")[:200]
            category = item.get("category") or ""

            img_html = ""
            pm = self._pixmap_cache.get(item_id)
            if pm and not pm.isNull():
                try:
                    from PySide6.QtCore import QBuffer, QIODevice
                    scaled = pm.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    buf = QBuffer()
                    buf.open(QIODevice.WriteOnly)
                    scaled.save(buf, "PNG")
                    buf.close()
                    b64 = base64.b64encode(bytes(buf.data())).decode()
                    img_html = (
                        f'<img src="data:image/png;base64,{b64}" '
                        f'width="60" height="60" style="object-fit:contain;">'
                    )
                except Exception:
                    pass

            desc_html = (
                f"<br><span style='color:#666;font-size:9pt;'>{desc}</span>"
                if desc else ""
            )
            rows += (
                f"<tr>"
                f"<td style='width:70px;text-align:center;'>{img_html}</td>"
                f"<td><strong>{title}</strong>{desc_html}</td>"
                f"<td>{category}</td>"
                f"<td>{cost}</td>"
                f"</tr>"
            )

        count = len(items)
        return f"""<html>
<head><style>
body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 10pt; color: #111; }}
h1 {{ font-size: 16pt; margin-bottom: 2px; }}
.sub {{ color: #666; margin-top: 0; font-size: 9pt; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 14px; }}
th {{ background: #f0f0f0; text-align: left; padding: 6px 8px; border-bottom: 2px solid #bbb; }}
td {{ padding: 6px 8px; border-bottom: 1px solid #e0e0e0; vertical-align: middle; }}
tr:nth-child(even) td {{ background: #fafafa; }}
</style></head>
<body>
<h1>Bin: {bin_name}</h1>
<p class="sub">{count} item{"s" if count != 1 else ""}</p>
<table>
<thead><tr><th></th><th>Title / Description</th><th>Category</th><th>Cost</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""
