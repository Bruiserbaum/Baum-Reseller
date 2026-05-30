from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QTextEdit, QDoubleSpinBox, QLabel, QPushButton, QScrollArea,
    QWidget, QGroupBox, QMessageBox, QDateEdit, QSizePolicy,
    QListWidget, QListWidgetItem, QSplitter
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QPixmap

from app.database.models import get_item, save_item, delete_item
from app.utils.auto_save import AutoSave

PLATFORMS = ["ebay", "mercari", "poshmark"]
PLATFORM_LABELS = {"ebay": "eBay", "mercari": "Mercari", "poshmark": "Poshmark"}


class ItemDetailDialog(QDialog):
    def __init__(self, item_id: int | None, parent=None):
        super().__init__(parent)
        self._item_id = item_id
        self._item = get_item(item_id) if item_id else {}
        self._auto_save = AutoSave(self._do_save, delay_ms=800)

        title = self._item.get("title", "New Item") if self._item else "New Item"
        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)
        self.resize(900, 650)

        self._build_ui()
        self._load_data()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # Left: image panel
        img_panel = QWidget()
        img_panel.setObjectName("imagePanel")
        img_layout = QVBoxLayout(img_panel)
        self.img_label = QLabel("No image")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setMinimumSize(240, 240)
        self.img_label.setObjectName("imagePreview")
        img_layout.addWidget(self.img_label)

        self.img_list = QListWidget()
        self.img_list.setMaximumHeight(120)
        self.img_list.currentRowChanged.connect(self._preview_image)
        img_layout.addWidget(self.img_list)
        img_layout.addStretch()
        splitter.addWidget(img_panel)

        # Right: form
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QScrollArea.NoFrame)
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(20, 20, 20, 20)

        # Core fields
        core = QGroupBox("Item Details")
        core_form = QFormLayout(core)
        core_form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Item title")
        self.title_edit.textChanged.connect(self._on_change)
        core_form.addRow("Title *", self.title_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(80)
        self.desc_edit.textChanged.connect(self._on_change)
        core_form.addRow("Description", self.desc_edit)

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText("e.g. Clothing, Electronics")
        self.category_edit.textChanged.connect(self._on_change)
        core_form.addRow("Category", self.category_edit)

        self.bin_edit = QLineEdit()
        self.bin_edit.setPlaceholderText("e.g. A3, Shelf 2")
        self.bin_edit.textChanged.connect(self._on_change)
        core_form.addRow("Bin Location", self.bin_edit)

        self.cost_spin = QDoubleSpinBox()
        self.cost_spin.setPrefix("$")
        self.cost_spin.setMaximum(99999)
        self.cost_spin.setDecimals(2)
        self.cost_spin.valueChanged.connect(self._on_change)
        core_form.addRow("Purchase Cost", self.cost_spin)

        self.purchase_date = QDateEdit()
        self.purchase_date.setCalendarPopup(True)
        self.purchase_date.setDate(QDate.currentDate())
        self.purchase_date.dateChanged.connect(self._on_change)
        core_form.addRow("Purchase Date", self.purchase_date)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setPlaceholderText("Additional notes...")
        self.notes_edit.textChanged.connect(self._on_change)
        core_form.addRow("Notes", self.notes_edit)

        form_layout.addWidget(core)

        # Listings
        listings_group = QGroupBox("Platform Listings")
        listings_layout = QVBoxLayout(listings_group)
        self.listings_widget = ListingsPanel(self._item_id)
        listings_layout.addWidget(self.listings_widget)
        form_layout.addWidget(listings_group)

        form_layout.addStretch()
        form_scroll.setWidget(form_widget)
        splitter.addWidget(form_scroll)
        splitter.setSizes([260, 620])

        # Bottom bar
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 8, 16, 8)

        self.save_indicator = QLabel("")
        self.save_indicator.setObjectName("saveIndicator")
        bar.addWidget(self.save_indicator)
        bar.addStretch()

        if self._item_id:
            self.missing_btn = QPushButton()
            self.missing_btn.clicked.connect(self._toggle_missing)
            bar.addWidget(self.missing_btn)
            self._refresh_missing_btn()

            del_btn = QPushButton("Delete Item")
            del_btn.setObjectName("dangerButton")
            del_btn.clicked.connect(self._delete_item)
            bar.addWidget(del_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._close)
        bar.addWidget(close_btn)

        root.addLayout(bar)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_data(self):
        if not self._item:
            return
        self.title_edit.setText(self._item.get("title", ""))
        self.desc_edit.setPlainText(self._item.get("description", ""))
        self.category_edit.setText(self._item.get("category", ""))
        self.bin_edit.setText(self._item.get("bin_location", ""))
        self.cost_spin.setValue(float(self._item.get("purchase_cost") or 0))
        pd = self._item.get("purchase_date", "")
        if pd:
            self.purchase_date.setDate(QDate.fromString(pd, "yyyy-MM-dd"))
        self.notes_edit.setPlainText(self._item.get("notes", ""))

        for img in self._item.get("images", []):
            self.img_list.addItem(img.get("local_path", ""))
        if self.img_list.count():
            self.img_list.setCurrentRow(0)

    def _preview_image(self, row: int):
        if row < 0:
            return
        path = self.img_list.item(row).text()
        if path and __import__("os").path.exists(path):
            pix = QPixmap(path).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.img_label.setPixmap(pix)

    # ── Auto-save ─────────────────────────────────────────────────────────

    def _on_change(self):
        self.save_indicator.setText("Saving…")
        self._auto_save.mark_dirty()

    def _do_save(self):
        data = {
            "id": self._item_id,
            "title": self.title_edit.text().strip() or "Untitled",
            "description": self.desc_edit.toPlainText(),
            "category": self.category_edit.text().strip(),
            "bin_location": self.bin_edit.text().strip(),
            "purchase_cost": self.cost_spin.value(),
            "purchase_date": self.purchase_date.date().toString("yyyy-MM-dd"),
            "notes": self.notes_edit.toPlainText(),
        }
        new_id = save_item(data)
        if not self._item_id:
            self._item_id = new_id
            self.listings_widget.item_id = new_id
        self.save_indicator.setText("Saved")

    def _refresh_missing_btn(self):
        if not hasattr(self, "missing_btn"):
            return
        is_missing = bool((self._item or {}).get("is_missing", 0))
        if is_missing:
            self.missing_btn.setText("Mark as Found")
            self.missing_btn.setObjectName("primaryButton")
        else:
            self.missing_btn.setText("Mark as Missing")
            self.missing_btn.setObjectName("dangerButton")
        self.missing_btn.style().unpolish(self.missing_btn)
        self.missing_btn.style().polish(self.missing_btn)

    def _toggle_missing(self):
        if not self._item_id:
            return
        from app.services.notification_service import mark_missing, mark_found
        is_missing = bool((self._item or {}).get("is_missing", 0))
        if is_missing:
            mark_found(self._item_id)
            if self._item:
                self._item["is_missing"] = 0
        else:
            from PySide6.QtWidgets import QInputDialog
            notes, ok = QInputDialog.getText(
                self, "Mark as Missing",
                "Optional notes (where you last saw it, etc.):"
            )
            if not ok:
                return
            mark_missing(self._item_id, notes)
            if self._item:
                self._item["is_missing"] = 1
                self._item["missing_notes"] = notes
        self._refresh_missing_btn()

    def _delete_item(self):
        reply = QMessageBox.question(
            self, "Delete Item",
            "Permanently delete this item and all its listings?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            delete_item(self._item_id)
            self.accept()

    def _close(self):
        self._auto_save.flush()
        self.accept()

    def closeEvent(self, event):
        self._auto_save.flush()
        super().closeEvent(event)


class ListingsPanel(QWidget):
    """Shows per-platform listings for an item."""

    def __init__(self, item_id: int | None, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(120)
        layout.addWidget(self.list_widget)

        refresh_btn = QPushButton("Refresh listings")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        self.refresh()

    def refresh(self):
        self.list_widget.clear()
        if not self.item_id:
            return
        item = get_item(self.item_id)
        if not item:
            return
        for listing in item.get("listings", []):
            platform = PLATFORM_LABELS.get(listing["platform"], listing["platform"])
            price = listing.get("listing_price") or 0
            status = listing.get("status", "")
            text = f"{platform}  |  ${price:.2f}  |  {status}"
            self.list_widget.addItem(QListWidgetItem(text))
        if not self.list_widget.count():
            self.list_widget.addItem("No listings yet — sync to populate")
