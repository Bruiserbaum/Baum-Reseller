import datetime
import hashlib
import os
import threading
import urllib.request

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QTextEdit, QDoubleSpinBox, QLabel, QPushButton, QScrollArea,
    QWidget, QGroupBox, QMessageBox, QDateEdit, QSizePolicy,
    QComboBox, QFrame, QSplitter, QDialogButtonBox,
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QPixmap

from app.database.models import get_item, save_item, delete_item
from app.utils.auto_save import AutoSave
from app.utils.qt_thread import post_to_main

_IMAGE_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller", "image_cache")
PLATFORM_LABELS  = {"ebay": "eBay", "mercari": "Mercari", "poshmark": "Poshmark"}


# ── Mark-as-Sold dialog ───────────────────────────────────────────────────────

class MarkSoldDialog(QDialog):
    """Let the user record a manual sale for an existing item."""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mark as Sold")
        self.setMinimumWidth(380)
        self._item = item
        self._saved = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        # Platform dropdown: populate from existing active listings
        self._platform = QComboBox()
        listings = item.get("listings", [])
        for lst in listings:
            lbl = PLATFORM_LABELS.get(lst["platform"], lst["platform"])
            self._platform.addItem(lbl, lst)           # userData = full listing dict
        if not listings:
            for p in ("ebay", "mercari", "poshmark"):
                self._platform.addItem(PLATFORM_LABELS[p], {"platform": p, "id": None})
        form.addRow("Platform:", self._platform)

        # Sale date
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("MM/dd/yyyy")
        self._date.setDate(QDate.currentDate())
        form.addRow("Sale Date:", self._date)

        # Sale price
        self._price = self._spin()
        form.addRow("Sale Price ($):", self._price)

        # Platform fees
        self._fees = self._spin()
        form.addRow("Platform Fees ($):", self._fees)

        # Shipping
        self._ship = self._spin()
        form.addRow("Shipping Cost ($):", self._ship)

        layout.addLayout(form)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    @staticmethod
    def _spin(value: float = 0.0) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setPrefix("$"); sp.setDecimals(2); sp.setMaximum(999_999.99)
        sp.setValue(value)
        return sp

    def _save(self):
        from app.database.models import (
            mark_listing_sold, upsert_sale_from_listing, save_sale
        )
        lst = self._platform.currentData() or {}
        platform  = lst.get("platform", "ebay")
        listing_db_id = lst.get("id")
        qd = self._date.date()
        sale_date = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"
        price = self._price.value()
        fees  = self._fees.value()
        ship  = self._ship.value()

        # Mark the listing as sold
        if listing_db_id:
            mark_listing_sold(listing_db_id, price, sale_date)

        # Create the sales record
        item_id = self._item.get("id")
        if item_id:
            from app.database.connection import get_connection
            import uuid
            ext_id = f"manual-{uuid.uuid4().hex[:8]}"
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO sales
                       (item_id, platform, sale_price, platform_fees, shipping_cost,
                        sale_date, ext_listing_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (item_id, platform, price, fees, ship, sale_date, ext_id),
                )

        self._saved = True
        self.accept()


# ── Main dialog ───────────────────────────────────────────────────────────────

class ItemDetailDialog(QDialog):
    def __init__(self, item_id: int | None, parent=None):
        super().__init__(parent)
        self._item_id = item_id
        self._item    = get_item(item_id) if item_id else {}
        self._auto_save = AutoSave(self._do_save, delay_ms=800)
        self._img_urls: list[str] = []
        self._img_idx  = 0

        title = (self._item or {}).get("title", "New Item")
        self.setWindowTitle(title)
        self.setMinimumSize(1050, 700)
        self.resize(1200, 800)

        self._build_ui()
        self._load_data()

        # Kick off background enrichment if description is missing
        if item_id and not (self._item or {}).get("description"):
            self._start_enrichment()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: image panel ────────────────────────────────────────────────
        img_panel = QWidget()
        img_panel.setObjectName("imagePanel")
        img_panel.setMinimumWidth(300)
        img_v = QVBoxLayout(img_panel)
        img_v.setContentsMargins(12, 12, 12, 12)

        self.img_label = QLabel("No image")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setMinimumSize(280, 300)
        self.img_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.img_label.setObjectName("imagePreview")
        img_v.addWidget(self.img_label, 1)

        # Navigation row
        nav_row = QHBoxLayout()
        self._prev_btn = QPushButton("‹")
        self._prev_btn.setFixedWidth(32)
        self._prev_btn.clicked.connect(self._prev_image)
        self._img_counter = QLabel("")
        self._img_counter.setAlignment(Qt.AlignCenter)
        self._img_counter.setStyleSheet("color: #585b70; font-size: 11px;")
        self._next_btn = QPushButton("›")
        self._next_btn.setFixedWidth(32)
        self._next_btn.clicked.connect(self._next_image)
        nav_row.addWidget(self._prev_btn)
        nav_row.addWidget(self._img_counter, 1)
        nav_row.addWidget(self._next_btn)
        img_v.addLayout(nav_row)

        # Listing quick-info panel (read-only)
        info_box = QGroupBox("Listing Info")
        info_box.setMaximumHeight(200)
        self._info_layout = QVBoxLayout(info_box)
        self._info_layout.setSpacing(3)
        self._info_lbl = QLabel("—")
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet("font-size: 11px; color: #cdd6f4;")
        self._info_layout.addWidget(self._info_lbl)
        img_v.addWidget(info_box)

        splitter.addWidget(img_panel)

        # ── Right: form ──────────────────────────────────────────────────────
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QScrollArea.NoFrame)
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(14)

        # ── Item details ──────────────────────────────────────────────────────
        core = QGroupBox("Item Details")
        core_form = QFormLayout(core)
        core_form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Item title")
        self.title_edit.textChanged.connect(self._on_change)
        core_form.addRow("Title *", self.title_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMinimumHeight(100)
        self.desc_edit.setMaximumHeight(160)
        self.desc_edit.textChanged.connect(self._on_change)
        core_form.addRow("Description", self.desc_edit)

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText("e.g. Clothing, Electronics")
        self.category_edit.textChanged.connect(self._on_change)
        core_form.addRow("Category", self.category_edit)

        self._enrich_lbl = QLabel(
            "Description and Category are fetched automatically in the background. "
            "You can also fill them in manually or via CSV Import."
        )
        self._enrich_lbl.setWordWrap(True)
        self._enrich_lbl.setStyleSheet("color: #585b70; font-size: 10px; font-style: italic;")
        core_form.addRow("", self._enrich_lbl)

        self.bin_edit = QLineEdit()
        self.bin_edit.setPlaceholderText("e.g. A3, Shelf 2")
        self.bin_edit.textChanged.connect(self._on_change)
        core_form.addRow("Bin Location", self.bin_edit)

        self.status_combo = QComboBox()
        self.status_combo.addItem("Active", "active")
        self.status_combo.addItem("⏳ Pending Listing", "pending")
        self.status_combo.setToolTip(
            "Active: item is listed or available on a platform.\n"
            "Pending Listing: acquired but not yet listed — "
            "use to track purchase costs before listing."
        )
        self.status_combo.currentIndexChanged.connect(self._on_change)
        core_form.addRow("Status", self.status_combo)

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

        # ── Platform listings ─────────────────────────────────────────────────
        listings_group = QGroupBox("Platform Listings")
        listings_v = QVBoxLayout(listings_group)
        self.listings_widget = ListingsPanel(self._item_id)
        listings_v.addWidget(self.listings_widget)
        form_layout.addWidget(listings_group)

        form_layout.addStretch()
        form_scroll.setWidget(form_widget)
        splitter.addWidget(form_scroll)
        splitter.setSizes([320, 760])

        # ── Bottom bar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 8, 16, 8)

        self.save_indicator = QLabel("")
        self.save_indicator.setObjectName("saveIndicator")
        bar.addWidget(self.save_indicator)
        bar.addStretch()

        if self._item_id:
            sold_btn = QPushButton("Mark as Sold")
            sold_btn.setObjectName("primaryButton")
            sold_btn.clicked.connect(self._mark_sold)
            bar.addWidget(sold_btn)

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

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_data(self):
        if not self._item:
            return
        self.title_edit.setText(self._item.get("title", ""))
        self.desc_edit.setPlainText(self._item.get("description", ""))
        self.category_edit.setText(self._item.get("category", ""))
        self.bin_edit.setText(self._item.get("bin_location", ""))
        # Status combo
        item_status = self._item.get("item_status") or "active"
        idx = self.status_combo.findData(item_status)
        self.status_combo.blockSignals(True)
        self.status_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.status_combo.blockSignals(False)
        self.cost_spin.setValue(float(self._item.get("purchase_cost") or 0))
        pd = self._item.get("purchase_date", "")
        if pd:
            self.purchase_date.setDate(QDate.fromString(pd, "yyyy-MM-dd"))
        self.notes_edit.setPlainText(self._item.get("notes", ""))

        # Build image list
        self._img_urls = []
        for img in self._item.get("images", []):
            local = img.get("local_path", "")
            url   = img.get("source_url", "")
            path  = local if (local and os.path.exists(local)) else url
            if path:
                self._img_urls.append(path)
        self._img_idx = 0
        self._show_current_image()

        # Listing info
        self._refresh_listing_info()

    def _refresh_listing_info(self):
        listings = (self._item or {}).get("listings", [])
        if not listings:
            self._info_lbl.setText("No listings yet — run sync to populate.")
            return

        parts = []
        today = datetime.date.today()
        for lst in listings:
            plat   = PLATFORM_LABELS.get(lst.get("platform", ""), lst.get("platform", ""))
            status = lst.get("status", "—")
            price  = lst.get("listing_price") or 0
            ld_raw = lst.get("listed_date", "")
            sd_raw = lst.get("sold_date", "")
            sp     = lst.get("sold_price") or 0

            # Days listed
            days_str = "—"
            try:
                ld = datetime.date.fromisoformat(ld_raw[:10])
                end = today
                if status == "sold" and sd_raw:
                    try:
                        end = datetime.date.fromisoformat(sd_raw[:10])
                    except Exception:
                        pass
                days_str = str((end - ld).days)
            except Exception:
                pass

            line = (
                f"<b>{plat}</b>  "
                f"Status: <b>{status}</b>  |  "
                f"Listed: ${price:.2f}  |  "
                f"Days: {days_str}"
            )
            if ld_raw:
                line += f"  |  Since: {ld_raw[:10]}"
            if status == "sold":
                line += f"<br>&nbsp;&nbsp;Sold: ${sp:.2f}"
                if sd_raw:
                    line += f" on {sd_raw[:10]}"
            parts.append(line)

        self._info_lbl.setText("<br>".join(parts))

    # ── Image navigation ──────────────────────────────────────────────────────

    def _show_current_image(self):
        total = len(self._img_urls)
        if total == 0:
            self.img_label.setText("No image")
            self._img_counter.setText("")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            return

        self._prev_btn.setEnabled(self._img_idx > 0)
        self._next_btn.setEnabled(self._img_idx < total - 1)
        self._img_counter.setText(f"{self._img_idx + 1} of {total}")

        path = self._img_urls[self._img_idx]
        if path.startswith("http"):
            self.img_label.setText("Loading…")
            threading.Thread(
                target=self._fetch_remote_image, args=(path,), daemon=True
            ).start()
        elif os.path.exists(path):
            self._display_pixmap(path)
        else:
            self.img_label.setText("Image not found")

    def _prev_image(self):
        if self._img_idx > 0:
            self._img_idx -= 1
            self._show_current_image()

    def _next_image(self):
        if self._img_idx < len(self._img_urls) - 1:
            self._img_idx += 1
            self._show_current_image()

    def _fetch_remote_image(self, url: str):
        try:
            os.makedirs(_IMAGE_CACHE_DIR, exist_ok=True)
            fname = hashlib.md5(url.encode()).hexdigest() + ".jpg"
            cache_path = os.path.join(_IMAGE_CACHE_DIR, fname)
            if not os.path.exists(cache_path):
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                with open(cache_path, "wb") as f:
                    f.write(data)
            post_to_main(lambda p=cache_path: self._display_pixmap(p))
        except Exception:
            post_to_main(lambda: self.img_label.setText("Image unavailable"))

    def _display_pixmap(self, path: str):
        if not os.path.exists(path):
            self.img_label.setText("Image not found")
            return
        size = self.img_label.size()
        w = max(size.width(), 280)
        h = max(size.height(), 300)
        pix = QPixmap(path).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_label.setPixmap(pix)

    # ── Background enrichment ─────────────────────────────────────────────────

    def _start_enrichment(self):
        self._enrich_lbl.setText("⟳  Fetching details from platform listing…")
        from app.services.enrich_service import enrich_item
        enrich_item(self._item_id, done_cb=self._on_enriched)

    def _on_enriched(self, updated: bool):
        if not updated:
            self._enrich_lbl.setText(
                "Could not auto-fetch details — fill them in manually or via CSV Import."
            )
            return
        self._enrich_lbl.setText("✓  Details fetched from platform listing.")
        self._item = get_item(self._item_id)
        if not self._item:
            return

        for w in (self.title_edit, self.desc_edit, self.category_edit):
            w.blockSignals(True)
        self.title_edit.setText(self._item.get("title", ""))
        self.desc_edit.setPlainText(self._item.get("description", ""))
        self.category_edit.setText(self._item.get("category", ""))
        for w in (self.title_edit, self.desc_edit, self.category_edit):
            w.blockSignals(False)

        # Refresh images
        self._img_urls = []
        for img in self._item.get("images", []):
            local = img.get("local_path", "")
            url   = img.get("source_url", "")
            path  = local if (local and os.path.exists(local)) else url
            if path:
                self._img_urls.append(path)
        self._img_idx = 0
        self._show_current_image()

    # ── Auto-save ─────────────────────────────────────────────────────────────

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
            "item_status": self.status_combo.currentData() or "active",
        }
        new_id = save_item(data)
        if not self._item_id:
            self._item_id = new_id
            self.listings_widget.item_id = new_id
        self.save_indicator.setText("Saved")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _mark_sold(self):
        if not self._item_id:
            return
        # Reload item to get fresh listings
        self._item = get_item(self._item_id)
        dlg = MarkSoldDialog(self._item or {}, parent=self)
        if dlg.exec() and dlg._saved:
            QMessageBox.information(self, "Marked as Sold",
                                    "Sale recorded. The item will appear in Reports.")
            # Refresh listing panel
            self._item = get_item(self._item_id)
            self._refresh_listing_info()
            self.listings_widget.refresh()

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


# ── Listings panel ────────────────────────────────────────────────────────────

class ListingsPanel(QWidget):
    """Shows per-platform listings with rich detail."""

    def __init__(self, item_id: int | None, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        layout.addWidget(self._rows_widget)

        refresh_btn = QPushButton("Refresh Listings")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        self.refresh()

    def refresh(self):
        # Clear old rows
        while self._rows_layout.count():
            child = self._rows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.item_id:
            self._rows_layout.addWidget(QLabel("No item ID yet."))
            return

        item = get_item(self.item_id)
        if not item or not item.get("listings"):
            self._rows_layout.addWidget(QLabel("No listings yet — sync to populate."))
            return

        today = datetime.date.today()
        for lst in item.get("listings", []):
            lbl = self._make_row(lst, today)
            self._rows_layout.addWidget(lbl)

    @staticmethod
    def _make_row(lst: dict, today: datetime.date) -> QLabel:
        plat   = PLATFORM_LABELS.get(lst.get("platform", ""), lst.get("platform", ""))
        status = lst.get("status", "—")
        price  = float(lst.get("listing_price") or 0)
        ld_raw = lst.get("listed_date", "")
        sd_raw = lst.get("sold_date", "")
        sp     = float(lst.get("sold_price") or 0)

        # Days listed
        days_str = "—"
        try:
            ld  = datetime.date.fromisoformat(ld_raw[:10])
            end = today
            if status == "sold" and sd_raw:
                try: end = datetime.date.fromisoformat(sd_raw[:10])
                except Exception: pass
            days_str = f"{(end - ld).days}d"
        except Exception:
            pass

        status_color = {"active": "#a6e3a1", "sold": "#89b4fa"}.get(status, "#cdd6f4")
        parts = [
            f"<b style='color:#cdd6f4'>{plat}</b>",
            f"<span style='color:{status_color}'>{status}</span>",
            f"${price:.2f}",
            f"Listed: {ld_raw[:10] if ld_raw else '—'}",
            f"Days: {days_str}",
        ]
        if status == "sold":
            parts.append(f"Sold ${sp:.2f} {'on ' + sd_raw[:10] if sd_raw else ''}")

        lbl = QLabel("  |  ".join(parts))
        lbl.setTextFormat(Qt.RichText)
        lbl.setStyleSheet("font-size: 11px; padding: 2px 0;")
        return lbl
