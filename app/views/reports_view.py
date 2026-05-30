import datetime
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame
)
from PySide6.QtCore import Qt

from app.database.models import get_sales, get_monthly_platform_totals

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PLATFORMS = ["ebay", "mercari", "poshmark"]
PLATFORM_COLORS = {"ebay": "#e53238", "mercari": "#d43e37", "poshmark": "#7b2d8b"}


class ReportsView(QWidget):
    def __init__(self):
        super().__init__()
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

        # Summary cards
        self.cards_layout = QHBoxLayout()
        self.card_revenue = self._make_card("Revenue", "$0.00")
        self.card_profit = self._make_card("Profit", "$0.00")
        self.card_items = self._make_card("Items Sold", "0")
        self.card_margin = self._make_card("Avg Margin", "0%")
        for c in (self.card_revenue, self.card_profit, self.card_items, self.card_margin):
            self.cards_layout.addWidget(c)
        layout.addLayout(self.cards_layout)

        # Tabs
        tabs = QTabWidget()

        # Sales table
        self.sales_table = QTableWidget()
        self.sales_table.setColumnCount(7)
        self.sales_table.setHorizontalHeaderLabels(
            ["Date", "Item", "Platform", "Sale Price", "Fees", "Shipping", "Profit"]
        )
        self.sales_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.sales_table.setAlternatingRowColors(True)
        self.sales_table.verticalHeader().setVisible(False)
        self.sales_table.setEditTriggers(QTableWidget.NoEditTriggers)
        tabs.addTab(self.sales_table, "Sales")

        # Chart tab (matplotlib canvas)
        self.chart_widget = ChartWidget()
        tabs.addTab(self.chart_widget, "Platform Chart")

        layout.addWidget(tabs, 1)

    def _make_card(self, label: str, value: str) -> QFrame:
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
        card.setProperty("valueLabel", val_label)
        return card

    def refresh(self):
        year = int(self.year_combo.currentText())
        month_idx = self.month_combo.currentIndex()
        month = month_idx if month_idx > 0 else None

        sales = get_sales(year=year, month=month)
        self._render_sales_table(sales)
        self._render_summary(sales)
        self.chart_widget.render(year)

    def _render_sales_table(self, sales: list[dict]):
        self.sales_table.setRowCount(len(sales))
        for row, s in enumerate(sales):
            profit = s.get("profit") or 0
            cells = [
                s.get("sale_date", "")[:10],
                s.get("title", ""),
                s.get("platform", "").capitalize(),
                f"${(s.get('sale_price') or 0):.2f}",
                f"${(s.get('platform_fees') or 0):.2f}",
                f"${(s.get('shipping_cost') or 0):.2f}",
                f"${profit:.2f}",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 6:
                    item.setForeground(Qt.green if profit >= 0 else Qt.red)
                self.sales_table.setItem(row, col, item)

    def _render_summary(self, sales: list[dict]):
        revenue = sum(s.get("sale_price") or 0 for s in sales)
        profit = sum(s.get("profit") or 0 for s in sales)
        margin = (profit / revenue * 100) if revenue else 0

        self.card_revenue.property("valueLabel").setText(f"${revenue:.2f}")
        self.card_profit.property("valueLabel").setText(f"${profit:.2f}")
        self.card_items.property("valueLabel").setText(str(len(sales)))
        self.card_margin.property("valueLabel").setText(f"{margin:.1f}%")

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
    """Matplotlib bar chart of monthly revenue per platform."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._canvas = None

    def render(self, year: int):
        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            lbl = QLabel("Install matplotlib for charts.")
            lbl.setAlignment(Qt.AlignCenter)
            self._layout.addWidget(lbl)
            return

        if self._canvas:
            self._layout.removeWidget(self._canvas)
            self._canvas.deleteLater()
            self._canvas = None

        data = get_monthly_platform_totals(year)

        by_platform: dict[str, list[float]] = {p: [0.0] * 12 for p in PLATFORMS}
        for row in data:
            p = row["platform"]
            m = row["month"] - 1
            if p in by_platform and 0 <= m < 12:
                by_platform[p][m] = row.get("revenue") or 0

        fig = Figure(figsize=(8, 4), facecolor="#1e1e2e")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#1e1e2e")
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444")

        x = range(12)
        bar_width = 0.28
        offsets = [-bar_width, 0, bar_width]
        for i, platform in enumerate(PLATFORMS):
            color = PLATFORM_COLORS.get(platform, "#888")
            ax.bar([xi + offsets[i] for xi in x], by_platform[platform],
                   width=bar_width, label=platform.capitalize(), color=color, alpha=0.85)

        ax.set_xticks(range(12))
        ax.set_xticklabels(MONTHS, color="white")
        ax.set_ylabel("Revenue ($)", color="white")
        ax.set_title(f"{year} Revenue by Platform", color="white")
        ax.legend(facecolor="#2a2a3e", labelcolor="white")
        fig.tight_layout()

        self._canvas = FigureCanvasQTAgg(fig)
        self._layout.addWidget(self._canvas)
