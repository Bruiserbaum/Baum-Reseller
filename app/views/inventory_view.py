import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QHeaderView, QAbstractItemView, QComboBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from app.database.models import get_all_items
from app.utils.qt_thread import post_to_main
from app.views.item_detail_view import ItemDetailDialog

# Default column widths (user can drag to resize — saved per session by Qt)
_COL_WIDTHS = [380, 110, 70, 110, 70, 90, 80]


class InventoryView(QWidget):
    def __init__(self):
        super().__init__()
        self._all_items: list[dict] = []
        self._dirty = False   # False because refresh() is called immediately below
        self._loading = False
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

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
        layout.addLayout(header)

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

        # Hide Unlisted toggle
        self._hide_unlisted_btn = QPushButton("Hide Unlisted")
        self._hide_unlisted_btn.setCheckable(True)
        self._hide_unlisted_btn.setChecked(False)
        self._hide_unlisted_btn.toggled.connect(self._apply_filter)
        fbar.addWidget(self._hide_unlisted_btn)

        fbar.addStretch()
        self.count_label = QLabel("0 items")
        fbar.addWidget(self.count_label)
        layout.addLayout(fbar)

        # ── Table ─────────────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Title", "Platforms", "Bin", "Category", "Cost", "Listed At", "Status"]
        )

        # All columns user-resizable with sensible defaults
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
        layout.addWidget(self.table)

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh(self):
        """Fetch all items on a background thread, then render on the main thread."""
        if self._loading:
            return
        self._loading = True
        self._dirty = False

        def _fetch():
            items = get_all_items()
            post_to_main(lambda: self._on_data_loaded(items))

        threading.Thread(target=_fetch, daemon=True).start()

    def lazy_refresh(self):
        """Refresh only when data has been marked dirty since the last load."""
        if self._dirty:
            self.refresh()

    def mark_dirty(self):
        """Mark data as stale (called after sync / import / enrichment)."""
        self._dirty = True

    def _on_data_loaded(self, items: list[dict]):
        self._loading = False
        self._all_items = items
        self._refresh_category_filter()
        self._apply_filter()

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
        search   = self.search_box.text().lower()
        platform = self.plat_filter.currentText()
        category = self.cat_filter.currentText()
        hide_unlisted = self._hide_unlisted_btn.isChecked()

        rows = self._all_items
        if search:
            rows = [i for i in rows if search in i.get("title", "").lower()]
        if platform != "All":
            rows = [i for i in rows if platform.lower() in (i.get("platforms") or "").lower()]
        if category != "All":
            rows = [i for i in rows if i.get("category", "") == category]
        if hide_unlisted:
            rows = [i for i in rows if (i.get("listing_count") or 0) > 0]

        self._render(rows)

    def _render(self, items: list[dict]):
        # Disable repaints and sorting during fill to avoid O(n²) Qt overhead
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(items))

        _missing_color = QColor("#f38ba8")

        for row, item in enumerate(items):
            platforms_raw = item.get("platforms") or ""
            platform_str = " | ".join(p.capitalize() for p in platforms_raw.split(",") if p)

            listed_price = item.get("listed_price")
            price_str = f"${listed_price:.2f}" if listed_price else "—"

            count = item.get("listing_count", 0) or 0
            status = f"{count} active" if count else "Unlisted"
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
