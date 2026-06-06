import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QHeaderView, QAbstractItemView,
    QComboBox, QProgressBar, QStackedWidget, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from app.database.models import get_all_items
from app.utils.qt_thread import post_to_main
from app.views.item_detail_view import ItemDetailDialog

# Default column widths — user can drag to resize
_COL_WIDTHS = [380, 110, 70, 110, 70, 90, 80]


class InventoryView(QWidget):
    def __init__(self):
        super().__init__()
        self._all_items: list[dict] = []
        self._dirty = False
        self._loading = False
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
        self.cat_filter.addItem("All")
        self.cat_filter.currentTextChanged.connect(self._apply_filter)
        fbar.addWidget(self.cat_filter)

        self._hide_unlisted_btn = QPushButton("Hide Unlisted")
        self._hide_unlisted_btn.setCheckable(True)
        self._hide_unlisted_btn.setChecked(False)
        self._hide_unlisted_btn.toggled.connect(self._apply_filter)
        fbar.addWidget(self._hide_unlisted_btn)

        fbar.addStretch()
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
        self._load_bar.setRange(0, 0)        # indeterminate spinner
        self._load_bar.setTextVisible(False)
        self._load_bar.setFixedWidth(320)
        self._load_bar.setFixedHeight(6)
        lp_layout.addWidget(self._load_bar, 0, Qt.AlignCenter)

        self._stack.addWidget(loading_page)  # index 0

        # Page 1 — Table
        table_page = QWidget()
        tp_layout = QVBoxLayout(table_page)
        tp_layout.setContentsMargins(0, 0, 0, 0)
        tp_layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Title", "Platforms", "Bin", "Category", "Cost", "Listed At", "Status"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(50)
        for col, w in enumerate(_COL_WIDTHS):
            self.table.setColumnWidth(col, w)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._open_item)
        tp_layout.addWidget(self.table)

        self._stack.addWidget(table_page)    # index 1

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh(self):
        """Show loading state, fetch all items on a worker thread, then render."""
        if self._loading:
            return
        self._loading = True
        self._dirty = False

        # Show loading page immediately — user sees feedback right away
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
        count = len(items)
        self._load_sub.setText(f"Rendering {count:,} items…")
        self._load_bar.setRange(0, 0)

        self._loading = False
        self._all_items = items
        self._refresh_category_filter()
        self._apply_filter()   # → _render() — fast thanks to setUpdatesEnabled(False)

        # Switch to table page once render is complete
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
            rows = [i for i in rows if search in i.get("title", "").lower()]
        if platform != "All":
            rows = [i for i in rows if platform.lower() in (i.get("platforms") or "").lower()]
        if category != "All":
            rows = [i for i in rows if i.get("category", "") == category]
        if hide_unlisted:
            # Hide only items with no listings at all — keep active AND sold items
            rows = [i for i in rows
                    if (i.get("listing_count") or 0) + (i.get("sold_count") or 0) > 0]

        self._render(rows)

    def _render(self, items: list[dict]):
        # Disable repaints + sorting while filling to avoid O(n²) Qt overhead
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(items))

        _missing_color = QColor("#f38ba8")

        for row, item in enumerate(items):
            platforms_raw = item.get("platforms") or ""
            platform_str  = " | ".join(p.capitalize() for p in platforms_raw.split(",") if p)

            listed_price = item.get("listed_price")
            price_str    = f"${listed_price:.2f}" if listed_price else "—"

            active_count = item.get("listing_count", 0) or 0
            sold_count   = item.get("sold_count", 0) or 0
            if active_count:
                status = f"{active_count} active"
            elif sold_count:
                status = f"Sold ({sold_count})"
            else:
                status = "Unlisted"
            is_missing = bool(item.get("is_missing", 0))
            title_text = ("❓ " if is_missing else "") + item.get("title", "")

            cells = [
                title_text,
                platform_str,
                item.get("bin_location", ""),
                item.get("category", ""),
                f"${(item.get('purchase_cost') or 0):.2f}",
                price_str,
                status,
            ]
            item_id = item.get("id")
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(str(text))
                cell.setData(Qt.UserRole, item_id)
                if is_missing:
                    cell.setForeground(_missing_color)
                self.table.setItem(row, col, cell)

        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")

    # ── Actions ───────────────────────────────────────────────────────────

    def _add_item(self):
        dlg = ItemDetailDialog(None, parent=self)
        if dlg.exec():
            self.refresh()

    def _open_item(self, index):
        cell = self.table.item(index.row(), 0)
        if cell:
            item_id = cell.data(Qt.UserRole)
            if item_id:
                dlg = ItemDetailDialog(item_id, parent=self)
                if dlg.exec():
                    self.refresh()
