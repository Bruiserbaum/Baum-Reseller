import datetime
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame,
    QDialog, QFormLayout, QDoubleSpinBox, QDateEdit, QDialogButtonBox,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor

from app.database.models import get_sales, get_sale_by_id, get_monthly_platform_totals, update_sale, delete_sale

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PLATFORMS = ["ebay", "mercari", "poshmark"]
PLATFORM_COLORS = {"ebay": "#e53238", "mercari": "#d43e37", "poshmark": "#7b2d8b"}

# Column indices for the sales table
_COL_DATE    = 0
_COL_ITEM    = 1
_COL_PLAT    = 2
_COL_PRICE   = 3
_COL_FEES    = 4
_COL_SHIP    = 5
_COL_COG     = 6
_COL_PROFIT  = 7
_COL_ROI     = 8


class SaleEditDialog(QDialog):
    """Modal dialog for editing or deleting a single sales record."""

    def __init__(self, sale: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Sale")
        self.setMinimumWidth(360)
        self._sale_id  = sale["id"]
        self._deleted  = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # ── Sale Date ─────────────────────────────────────────────────────
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("MM/dd/yyyy")
        raw_date = sale.get("sale_date", "") or ""
        try:
            d = datetime.date.fromisoformat(raw_date[:10])
            self._date_edit.setDate(QDate(d.year, d.month, d.day))
        except Exception:
            self._date_edit.setDate(QDate.currentDate())
        form.addRow("Sale Date:", self._date_edit)

        # ── Sale Price ────────────────────────────────────────────────────
        self._price = self._make_spin(sale.get("sale_price") or 0)
        form.addRow("Sale Price ($):", self._price)

        # ── Platform Fees ─────────────────────────────────────────────────
        self._fees = self._make_spin(sale.get("platform_fees") or 0)
        form.addRow("Platform Fees ($):", self._fees)

        # ── Shipping ──────────────────────────────────────────────────────
        self._ship = self._make_spin(sale.get("shipping_cost") or 0)
        form.addRow("Shipping Cost ($):", self._ship)

        layout.addLayout(form)

        # ── Info: COG (read-only) ─────────────────────────────────────────
        cog = sale.get("purchase_cost") or 0
        cog_lbl = QLabel(f"Cost of Goods: ${cog:.2f}  (edit on the item record)")
        cog_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        layout.addWidget(cog_lbl)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        del_btn = QPushButton("Delete Sale")
        del_btn.setStyleSheet("color: #f38ba8;")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(del_btn)

        btn_row.addStretch()

        std = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        std.accepted.connect(self._on_save)
        std.rejected.connect(self.reject)
        btn_row.addWidget(std)

        layout.addLayout(btn_row)

    @staticmethod
    def _make_spin(value: float) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setPrefix("$")
        sp.setDecimals(2)
        sp.setMaximum(999_999.99)
        sp.setValue(float(value))
        return sp

    def _on_save(self):
        qd = self._date_edit.date()
        sale_date = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"
        update_sale(self._sale_id, {
            "sale_price":    self._price.value(),
            "platform_fees": self._fees.value(),
            "shipping_cost": self._ship.value(),
            "sale_date":     sale_date,
        })
        self.accept()

    def _on_delete(self):
        reply = QMessageBox.question(
            self, "Delete Sale",
            "Remove this sale record? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            delete_sale(self._sale_id)
            self._deleted = True
            self.accept()


class ReportsView(QWidget):
    def __init__(self):
        super().__init__()
        self._sales_data: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Reports")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        header.addWidget(QLabel("Year:"))
        self.year_combo = QComboBox()
        current_year = datetime.date.today().year
        for y in range(current_year, current_year - 5, -1):
            self.year_combo.addItem(str(y))
        self.year_combo.currentTextChanged.connect(self.refresh)
        header.addWidget(self.year_combo)

        header.addWidget(QLabel("Month:"))
        self.month_combo = QComboBox()
        self.month_combo.addItem("Full Year")
        for m in MONTHS:
            self.month_combo.addItem(m)
        self.month_combo.currentTextChanged.connect(self.refresh)
        header.addWidget(self.month_combo)

        export_btn = QPushButton("Export PDF")
        export_btn.setObjectName("primaryButton")
        export_btn.clicked.connect(self._export_pdf)
        header.addWidget(export_btn)
        layout.addLayout(header)

        hint = QLabel("Double-click any sale row to edit or delete it.")
        hint.setStyleSheet("color: #585b70; font-size: 11px;")
        layout.addWidget(hint)

        # Summary cards — value labels stored as instance attrs for reliable updates
        cards_row = QHBoxLayout()
        for attr, label in [
            ("_val_revenue", "Revenue"),
            ("_val_cog",     "Cost of Goods"),
            ("_val_profit",  "Profit"),
            ("_val_items",   "Items Sold"),
            ("_val_roi",     "Avg ROI"),
        ]:
            card, val_lbl = self._make_card(label, "$0.00" if attr != "_val_items" else "0")
            setattr(self, attr, val_lbl)
            cards_row.addWidget(card)
        layout.addLayout(cards_row)

        # Tabs
        tabs = QTabWidget()

        # Sales table — 9 columns
        self.sales_table = QTableWidget()
        self.sales_table.setColumnCount(9)
        self.sales_table.setHorizontalHeaderLabels([
            "Date", "Item", "Platform", "Sale Price",
            "Fees", "Shipping", "COG", "Profit", "ROI %",
        ])
        hdr = self.sales_table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_ITEM, QHeaderView.Stretch)
        for col, w in [(_COL_DATE, 90), (_COL_PLAT, 80), (_COL_PRICE, 85),
                       (_COL_FEES, 75), (_COL_SHIP, 80),
                       (_COL_COG, 75), (_COL_PROFIT, 80), (_COL_ROI, 70)]:
            self.sales_table.setColumnWidth(col, w)

        self.sales_table.setAlternatingRowColors(True)
        self.sales_table.verticalHeader().setVisible(False)
        self.sales_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sales_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sales_table.doubleClicked.connect(self._on_row_double_clicked)
        tabs.addTab(self.sales_table, "Sales")

        # Chart tab (matplotlib canvas)
        self.chart_widget = ChartWidget()
        tabs.addTab(self.chart_widget, "Platform Chart")

        layout.addWidget(tabs, 1)

    def _make_card(self, label: str, value: str):
        """Returns (card_frame, value_label) — caller stores the label."""
        card = QFrame()
        card.setObjectName("reportCard")
        v = QVBoxLayout(card)
        val_label = QLabel(value)
        val_label.setObjectName("cardValue")
        val_label.setAlignment(Qt.AlignCenter)
        lbl = QLabel(label)
        lbl.setObjectName("cardLabel")
        lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(val_label)
        v.addWidget(lbl)
        return card, val_label

    def refresh(self):
        year = int(self.year_combo.currentText())
        month_idx = self.month_combo.currentIndex()
        month = month_idx if month_idx > 0 else None

        self._sales_data = get_sales(year=year, month=month)
        self._render_sales_table(self._sales_data)
        self._render_summary(self._sales_data)
        self.chart_widget.render(year)

    def _render_sales_table(self, sales: list[dict]):
        self.sales_table.setSortingEnabled(False)
        self.sales_table.setRowCount(len(sales))
        green = QColor("#a6e3a1")
        red   = QColor("#f38ba8")

        for row, s in enumerate(sales):
            sale_price = s.get("sale_price") or 0
            fees       = s.get("platform_fees") or 0
            shipping   = s.get("shipping_cost") or 0
            cog        = s.get("purchase_cost") or 0
            profit     = sale_price - fees - shipping - cog
            roi_str    = f"{profit / cog * 100:.0f}%" if cog > 0 else "—"
            colour     = green if profit >= 0 else red

            cells = [
                (s.get("sale_date", "")[:10], Qt.AlignCenter),
                (s.get("title", ""),           Qt.AlignLeft),
                (s.get("platform", "").capitalize(), Qt.AlignCenter),
                (f"${sale_price:.2f}",         Qt.AlignRight),
                (f"${fees:.2f}",               Qt.AlignRight),
                (f"${shipping:.2f}",           Qt.AlignRight),
                (f"${cog:.2f}",               Qt.AlignRight),
                (f"${profit:.2f}",            Qt.AlignRight),
                (roi_str,                      Qt.AlignCenter),
            ]
            for col, (text, align) in enumerate(cells):
                itm = QTableWidgetItem(str(text))
                itm.setTextAlignment(align | Qt.AlignVCenter)
                # Store sale_id on every cell for the edit dialog
                itm.setData(Qt.UserRole, s.get("id"))
                if col in (_COL_PROFIT, _COL_ROI):
                    itm.setForeground(colour)
                self.sales_table.setItem(row, col, itm)

        self.sales_table.setSortingEnabled(True)

    def _render_summary(self, sales: list[dict]):
        revenue = sum(s.get("sale_price") or 0 for s in sales)
        cog_tot = sum(s.get("purchase_cost") or 0 for s in sales)
        fees    = sum((s.get("platform_fees") or 0) + (s.get("shipping_cost") or 0) for s in sales)
        profit  = revenue - fees - cog_tot
        roi     = (profit / cog_tot * 100) if cog_tot else 0

        self._val_revenue.setText(f"${revenue:.2f}")
        self._val_cog.setText(f"${cog_tot:.2f}")
        self._val_profit.setText(f"${profit:.2f}")
        self._val_items.setText(str(len(sales)))
        self._val_roi.setText(f"{roi:.1f}%" if cog_tot else "—")

    def _on_row_double_clicked(self, index):
        # Retrieve the sale_id from the clicked cell directly (works regardless
        # of which column was double-clicked and regardless of sort order).
        sale_id = index.data(Qt.UserRole)
        if sale_id is None:
            return
        # Look up the full sale record — first try the in-memory list for speed,
        # then fall back to a direct DB fetch in case the table was re-sorted or
        # the in-memory list is stale.
        sale = next((s for s in self._sales_data if s.get("id") == sale_id), None)
        if sale is None:
            sale = get_sale_by_id(sale_id)
        if sale is None:
            return
        try:
            dlg = SaleEditDialog(sale, parent=self)
            if dlg.exec():
                self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Edit Error", f"Could not open edit dialog:\n{exc}")

    def _export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", f"report_{self.year_combo.currentText()}.pdf",
            "PDF Files (*.pdf)"
        )
        if not path:
            return
        try:
            year = int(self.year_combo.currentText())
            month_idx = self.month_combo.currentIndex()
            month = month_idx if month_idx > 0 else None
            from app.services.report_service import generate_pdf
            generate_pdf(path, year=year, month=month)
            QMessageBox.information(self, "Export", f"Report saved to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))


class ChartWidget(QWidget):
    """
    Matplotlib bar chart of monthly revenue per platform.

    Uses the Agg (non-interactive) backend and converts the rendered figure to a
    QPixmap.  This is fully reliable in a PyInstaller frozen bundle — the Qt
    backend (backend_qtagg) requires Qt to be fully initialised before matplotlib
    imports, which is fragile in frozen environments.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._img_label)

    def render(self, year: int):
        try:
            import matplotlib
            matplotlib.use("Agg")          # non-interactive, no Qt dependency
            import matplotlib.pyplot as plt
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            import io
            from PySide6.QtGui import QImage, QPixmap
        except Exception as exc:
            self._img_label.setText(
                f"Chart unavailable — matplotlib error:\n{type(exc).__name__}: {exc}\n\n"
                "If you just installed the app, try closing and reopening it."
            )
            return

        data = get_monthly_platform_totals(year)
        by_platform: dict[str, list[float]] = {p: [0.0] * 12 for p in PLATFORMS}
        for row in data:
            p = row["platform"]
            m = row["month"] - 1
            if p in by_platform and 0 <= m < 12:
                by_platform[p][m] = row.get("revenue") or 0

        fig = Figure(figsize=(10, 4), dpi=100, facecolor="#1e1e2e")
        ax  = fig.add_subplot(111)
        ax.set_facecolor("#1e1e2e")
        ax.tick_params(colors="#cdd6f4")
        for spine in ax.spines.values():
            spine.set_edgecolor("#45475a")

        x         = list(range(12))
        bar_width = 0.26
        offsets   = [-bar_width, 0, bar_width]
        for i, platform in enumerate(PLATFORMS):
            color = PLATFORM_COLORS.get(platform, "#888")
            ax.bar([xi + offsets[i] for xi in x], by_platform[platform],
                   width=bar_width, label=platform.capitalize(),
                   color=color, alpha=0.88)

        ax.set_xticks(x)
        ax.set_xticklabels(MONTHS, color="#cdd6f4", fontsize=9)
        ax.set_ylabel("Revenue ($)", color="#cdd6f4")
        ax.set_title(f"{year} Revenue by Platform", color="#cdd6f4", pad=10)
        ax.legend(facecolor="#313244", labelcolor="#cdd6f4", framealpha=0.8)
        ax.yaxis.label.set_color("#cdd6f4")
        fig.tight_layout(pad=1.5)

        # Render to PNG bytes → QPixmap (no Qt widget needed)
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        buf.seek(0)
        png_bytes = buf.read()
        plt.close(fig)

        qimg = QImage.fromData(png_bytes)
        pix  = QPixmap.fromImage(qimg)
        self._img_label.setPixmap(pix)
