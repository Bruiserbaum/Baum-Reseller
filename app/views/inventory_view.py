import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QHeaderView, QAbstractItemView,
    QComboBox, QProgressBar, QStackedWidget, QStyledItemDelegate,
    QDialog, QScrollArea, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor, QPixmap, QImage

from app.database.models import get_all_items
from app.utils.qt_thread import post_to_main
from app.views.item_detail_view import ItemDetailDialog

# ── Column layout ─────────────────────────────────────────────────────────────
_THUMB_COL = 0    # thumbnail (Fixed, non-resizable)
_TITLE_COL = 1    # title     (Interactive, auto-fills remaining space)
_THUMB_W   = 60   # thumbnail column width in px
_ROW_H     = 58   # row height in px

# Starting widths for columns 2-9
#  2:Platforms  3:Bin  4:Category  5:Cost  6:Listed  7:Days  8:Status  9:Desc
_COL_WIDTHS = [110, 60, 140, 70, 90, 60, 80, 200]

_CATEGORY_COL = 4    # absolute column index for "Category"
_CAT_MIN_W    = 120  # Category column will never shrink below this many px

# Thread pool caps concurrent image downloads at 8; daemon threads won't block exit
_IMG_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="img-loader")


# ── Date helpers ──────────────────────────────────────────────────────────────

def _format_date(date_str) -> str:
    """Return a short date like '6/5/26', or '—' if missing/invalid."""
    if not date_str:
        return "—"
    try:
        d = date.fromisoformat(str(date_str)[:10])
        return f"{d.month}/{d.day}/{d.year % 100:02d}"
    except Exception:
        return str(date_str) if date_str else "—"


def _days_listed_str(first_date) -> str:
    """Return compact duration like '5d', '3w', '2mo', '1y', or '—'."""
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


# ── Thumbnail delegate ────────────────────────────────────────────────────────

class ThumbnailDelegate(QStyledItemDelegate):
    """
    Paints a QPixmap stored in Qt.DecorationRole, scaled to fill the cell
    while preserving aspect ratio.  Falls back to a dark placeholder rect.
    """

    def paint(self, painter, option, index):
        r  = option.rect.adjusted(3, 3, -3, -3)
        pm = index.data(Qt.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            scaled = pm.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = r.x() + (r.width()  - scaled.width())  // 2
            y = r.y() + (r.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Dark placeholder — QColor instantiated at paint time (after QApp exists)
            painter.fillRect(r, QColor("#313244"))

    def sizeHint(self, option, index):
        return QSize(_THUMB_W, _ROW_H)


# ── Inventory view ────────────────────────────────────────────────────────────

class InventoryView(QWidget):
    def __init__(self):
        super().__init__()
        self._all_items: list[dict] = []
        self._dirty   = False
        self._loading = False
        # item_id → QPixmap | None (None = in-flight or failed)
        self._pixmap_cache: dict[int, QPixmap | None] = {}
        self._build_ui()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # ── Header ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Inventory")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search items…")
        self.search_box.setFixedWidth(260)
        self.search_box.textChanged.connect(self._apply_filter)
        header.addWidget(self.search_box)

        add_btn = QPushButton("+ Add Item")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self._add_item)
        header.addWidget(add_btn)
        root.addLayout(header)

        # ── Filter bar ────────────────────────────────────────────────────
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Platform:"))
        self.plat_filter = QComboBox()
        self.plat_filter.addItems(["All", "eBay", "Mercari", "Poshmark"])
        self.plat_filter.currentTextChanged.connect(self._apply_filter)
        fbar.addWidget(self.plat_filter)

        fbar.addWidget(QLabel("Category:"))
        self.cat_filter = QComboBox()
        self.cat_filter.setMinimumWidth(160)   # wide enough to read category names
        self.cat_filter.addItem("All")
        self.cat_filter.currentTextChanged.connect(self._apply_filter)
        fbar.addWidget(self.cat_filter)

        self._hide_unlisted_btn = QPushButton("Hide Unlisted")
        self._hide_unlisted_btn.setCheckable(True)
        self._hide_unlisted_btn.setChecked(False)
        self._hide_unlisted_btn.toggled.connect(self._apply_filter)
        fbar.addWidget(self._hide_unlisted_btn)

        fbar.addStretch()

        self._dedup_btn = QPushButton("Find Duplicates")
        self._dedup_btn.setToolTip(
            "Scan inventory for cross-platform duplicates and offer to merge them"
        )
        self._dedup_btn.clicked.connect(self._run_dedup)
        fbar.addWidget(self._dedup_btn)

        self.count_label = QLabel("0 items")
        fbar.addWidget(self.count_label)
        root.addLayout(fbar)

        # ── Stacked area: loading page ↔ table page ───────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Page 0 — Loading state
        loading_page = QWidget()
        lp_layout = QVBoxLayout(loading_page)
        lp_layout.setAlignment(Qt.AlignCenter)
        lp_layout.setSpacing(16)

        load_icon = QLabel("📦")
        load_icon.setAlignment(Qt.AlignCenter)
        load_icon.setStyleSheet("font-size: 40px;")
        lp_layout.addWidget(load_icon)

        self._load_title = QLabel("Loading Inventory")
        self._load_title.setAlignment(Qt.AlignCenter)
        self._load_title.setStyleSheet(
            "color: #cdd6f4; font-size: 18px; font-weight: bold;"
        )
        lp_layout.addWidget(self._load_title)

        self._load_sub = QLabel("Fetching items from the database…")
        self._load_sub.setAlignment(Qt.AlignCenter)
        self._load_sub.setStyleSheet("color: #a6adc8; font-size: 12px;")
        lp_layout.addWidget(self._load_sub)

        self._load_bar = QProgressBar()
        self._load_bar.setRange(0, 0)
        self._load_bar.setTextVisible(False)
        self._load_bar.setFixedWidth(320)
        self._load_bar.setFixedHeight(6)
        lp_layout.addWidget(self._load_bar, 0, Qt.AlignCenter)

        self._stack.addWidget(loading_page)   # index 0

        # Page 1 — Table
        table_page = QWidget()
        tp_layout = QVBoxLayout(table_page)
        tp_layout.setContentsMargins(0, 0, 0, 0)
        tp_layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "",            # 0: Thumbnail
            "Title",       # 1: auto-stretch
            "Platforms",   # 2
            "Bin",         # 3
            "Category",    # 4
            "Cost",        # 5
            "Listed",      # 6
            "Days",        # 7
            "Status",      # 8
            "Description", # 9
        ])

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)           # all draggable…
        hdr.setSectionResizeMode(_THUMB_COL, QHeaderView.Fixed)    # …except thumbnail
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(40)

        self.table.setColumnWidth(_THUMB_COL, _THUMB_W)
        for col, w in enumerate(_COL_WIDTHS, start=2):
            self.table.setColumnWidth(col, w)

        hdr.sectionResized.connect(self._on_col_resized)

        # Row height & thumbnail painter
        self.table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._thumb_delegate = ThumbnailDelegate(self.table)
        self.table.setItemDelegateForColumn(_THUMB_COL, self._thumb_delegate)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._open_item)
        tp_layout.addWidget(self.table)

        self._stack.addWidget(table_page)    # index 1

    # ── Column auto-fit ───────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_title_col)

    def _on_col_resized(self, col: int, _old: int, new_size: int):
        """When any non-title column is dragged, title absorbs the slack."""
        # Enforce a readable minimum for the Category column
        if col == _CATEGORY_COL and new_size < _CAT_MIN_W:
            hdr = self.table.horizontalHeader()
            hdr.blockSignals(True)
            self.table.setColumnWidth(_CATEGORY_COL, _CAT_MIN_W)
            hdr.blockSignals(False)
        if col != _TITLE_COL:
            self._fit_title_col()

    def _fit_title_col(self):
        """Set title column (col 1) to fill all remaining horizontal space."""
        vp_width = self.table.viewport().width()
        other_width = sum(
            self.table.columnWidth(c)
            for c in range(self.table.columnCount())
            if c != _TITLE_COL
        )
        self.table.setColumnWidth(_TITLE_COL, max(100, vp_width - other_width))

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh(self):
        """Show loading state, fetch all items on a worker thread, then render."""
        if self._loading:
            return
        self._loading = True
        self._dirty = False

        self._load_title.setText("Loading Inventory")
        self._load_sub.setText("Fetching items from the database…")
        self._load_bar.setRange(0, 0)
        self._stack.setCurrentIndex(0)

        def _fetch():
            items = get_all_items()
            post_to_main(lambda: self._on_data_loaded(items))

        threading.Thread(target=_fetch, daemon=True).start()

    def lazy_refresh(self):
        """Refresh only when data has been marked dirty since last load."""
        if self._dirty:
            self.refresh()

    def mark_dirty(self):
        """Mark data as stale (called after sync / import / enrichment)."""
        self._dirty = True

    def _on_data_loaded(self, items: list[dict]):
        self._load_sub.setText(f"Rendering {len(items):,} items…")
        self._loading = False
        self._all_items = items
        self._refresh_category_filter()
        self._apply_filter()
        self._stack.setCurrentIndex(1)

    def _refresh_category_filter(self):
        cats = sorted({i.get("category", "") for i in self._all_items if i.get("category")})
        current = self.cat_filter.currentText()
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItem("All")
        for c in cats:
            self.cat_filter.addItem(c)
        idx = self.cat_filter.findText(current)
        self.cat_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_filter.blockSignals(False)

    def _apply_filter(self):
        search        = self.search_box.text().lower()
        platform      = self.plat_filter.currentText()
        category      = self.cat_filter.currentText()
        hide_unlisted = self._hide_unlisted_btn.isChecked()

        rows = self._all_items
        if search:
            rows = [i for i in rows if
                    search in i.get("title", "").lower() or
                    search in (i.get("description") or "").lower()]
        if platform != "All":
            rows = [i for i in rows if platform.lower() in (i.get("platforms") or "").lower()]
        if category != "All":
            rows = [i for i in rows if i.get("category", "") == category]
        if hide_unlisted:
            rows = [i for i in rows
                    if (i.get("listing_count") or 0) + (i.get("sold_count") or 0) > 0]
        self._render(rows)

    def _render(self, items: list[dict]):
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(items))

        _missing_color = QColor("#f38ba8")

        for row, item in enumerate(items):
            item_id = item.get("id")

            # ── Col 0: Thumbnail ─────────────────────────────────────────
            thumb = QTableWidgetItem()
            thumb.setData(Qt.UserRole, item_id)
            thumb.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            cached_pm = self._pixmap_cache.get(item_id)
            if cached_pm:
                thumb.setData(Qt.DecorationRole, cached_pm)
            self.table.setItem(row, _THUMB_COL, thumb)

            # Kick off background image load if not yet in cache
            img_path = item.get("first_image_path") or ""
            if img_path and item_id not in self._pixmap_cache:
                self._load_image_async(item_id, img_path)

            # ── Cols 1-8: Text cells ─────────────────────────────────────
            platforms_raw = item.get("platforms") or ""
            platform_str  = " | ".join(p.capitalize() for p in platforms_raw.split(",") if p)

            active_count = item.get("listing_count", 0) or 0
            sold_count   = item.get("sold_count", 0) or 0
            if active_count:
                status = f"{active_count} active"
            elif sold_count:
                status = f"Sold ({sold_count})"
            else:
                status = "Unlisted"

            is_missing   = bool(item.get("is_missing", 0))
            title_text   = ("❓ " if is_missing else "") + item.get("title", "")
            first_listed = item.get("first_listed_date")

            # Description: first line only, trimmed to 120 chars for the cell
            desc_full = (item.get("description") or "").strip()
            desc_short = (desc_full.split("\n")[0])[:120] if desc_full else ""

            text_cells = [
                (_TITLE_COL, title_text),
                (2, platform_str),
                (3, item.get("bin_location") or ""),
                (4, item.get("category") or ""),
                (5, f"${(item.get('purchase_cost') or 0):.2f}"),
                (6, _format_date(first_listed)),
                (7, _days_listed_str(first_listed)),
                (8, status),
                (9, desc_short),
            ]
            for col, text in text_cells:
                cell = QTableWidgetItem(str(text))
                cell.setData(Qt.UserRole, item_id)
                if is_missing:
                    cell.setForeground(_missing_color)
                self.table.setItem(row, col, cell)

        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")
        QTimer.singleShot(0, self._fit_title_col)

    # ── Image loading ─────────────────────────────────────────────────────

    def _load_image_async(self, item_id: int, path: str):
        """
        Submit an image load to the shared thread pool.  QImage is created in the
        worker (thread-safe); QPixmap conversion + cell update happen in the main
        thread via post_to_main.  pool caps concurrency at 8 workers.
        """
        self._pixmap_cache[item_id] = None   # mark as in-flight

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
                # Locate the row that still holds this item and update its pixmap
                for r in range(self.table.rowCount()):
                    cell = self.table.item(r, _THUMB_COL)
                    if cell and cell.data(Qt.UserRole) == item_id:
                        cell.setData(Qt.DecorationRole, pm)
                        break

            post_to_main(_apply)

        _IMG_POOL.submit(_work)

    # ── Actions ───────────────────────────────────────────────────────────

    def _add_item(self):
        dlg = ItemDetailDialog(None, parent=self)
        if dlg.exec():
            self.refresh()

    def _open_item(self, index):
        # item_id is stored in UserRole on every cell in the row
        cell = self.table.item(index.row(), _TITLE_COL)
        if cell:
            item_id = cell.data(Qt.UserRole)
            if item_id:
                dlg = ItemDetailDialog(item_id, parent=self)
                if dlg.exec():
                    self.refresh()

    # ── Deduplication ─────────────────────────────────────────────────────────

    def _run_dedup(self):
        from app.services.dedup_service import run_background_scan
        self._dedup_btn.setEnabled(False)
        self._dedup_btn.setText("Scanning…")

        def _done(candidates):
            post_to_main(lambda: self._show_dedup_results(candidates))

        run_background_scan(auto_threshold=0.92, done_cb=_done)

    def _show_dedup_results(self, candidates):
        self._dedup_btn.setEnabled(True)
        self._dedup_btn.setText("Find Duplicates")

        if not candidates:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "No Duplicates Found",
                "No cross-platform duplicate listings were detected.\n\n"
                "Items already linked across platforms (same item record) are not shown.",
            )
            self.refresh()   # pick up any auto-merges
            return

        dlg = DuplicatesDialog(candidates, parent=self)
        dlg.exec()
        self.refresh()


# ── Duplicate review dialog ───────────────────────────────────────────────────

class DuplicatesDialog(QDialog):
    """
    Shows candidate duplicate pairs for review.
    User can Merge (keep left item, delete right) or Skip each pair.
    """

    def __init__(self, candidates, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QScrollArea, QWidget, QFrame,
        )
        super().__init__(parent)
        self.setWindowTitle(f"Duplicate Review — {len(candidates)} candidate pairs")
        self.setMinimumSize(900, 600)
        self.resize(1050, 700)

        root = QVBoxLayout(self)

        info = QLabel(
            f"Found <b>{len(candidates)}</b> possible cross-platform duplicates.  "
            "Items with score ≥ 92% were auto-merged already.\n"
            "Review the remaining pairs below — <b>Merge</b> combines the right item "
            "into the left, <b>Skip</b> leaves them separate."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        root.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        self._rows_layout = QVBoxLayout(container)
        self._rows_layout.setSpacing(6)

        self._candidates = list(candidates)
        self._row_widgets: list[QWidget] = []
        self._merges_done = 0

        for c in self._candidates:
            row = self._build_row(c)
            self._rows_layout.addWidget(row)
            self._row_widgets.append(row)

        self._rows_layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self.accept)
        root.addWidget(done_btn)

    def _build_row(self, c) -> QWidget:
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
        from app.services.dedup_service import Candidate

        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        h = QHBoxLayout(frame)
        h.setSpacing(12)

        # Left item info
        left = QVBoxLayout()
        left.addWidget(QLabel(f"<b>{c.title_a[:70]}</b>"))
        left.addWidget(QLabel(f"<span style='color:#89b4fa'>{c.plats_a}</span>"))
        h.addLayout(left, 3)

        # Score badge
        score_color = "#a6e3a1" if c.score >= 0.85 else "#fab387"
        badge = QLabel(
            f"<b style='color:{score_color}'>{c.score*100:.0f}%</b><br>"
            f"<small style='color:#585b70'>{c.method}</small>"
        )
        badge.setTextFormat(Qt.RichText)
        badge.setAlignment(Qt.AlignCenter)
        h.addWidget(badge, 1)

        # Right item info
        right = QVBoxLayout()
        right.addWidget(QLabel(f"<b>{c.title_b[:70]}</b>"))
        right.addWidget(QLabel(f"<span style='color:#cba6f7'>{c.plats_b}</span>"))
        h.addLayout(right, 3)

        # Action buttons
        btns = QVBoxLayout()

        merge_btn = QPushButton("Merge →")
        merge_btn.setObjectName("primaryButton")
        merge_btn.clicked.connect(lambda _, cand=c, fw=frame: self._do_merge(cand, fw))
        btns.addWidget(merge_btn)

        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(frame.hide)
        btns.addWidget(skip_btn)

        h.addLayout(btns)
        return frame

    def _do_merge(self, c, frame):
        from PySide6.QtWidgets import QMessageBox
        from app.services.dedup_service import merge
        reply = QMessageBox.question(
            self, "Confirm Merge",
            f"Merge:\n  {c.title_b}\ninto:\n  {c.title_a}\n\n"
            "All listings, images and sales will move to the left item. "
            "The right item will be deleted.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                merge(c.id_a, c.id_b)
                self._merges_done += 1
                frame.hide()
            except Exception as exc:
                QMessageBox.critical(self, "Merge Failed", str(exc))
