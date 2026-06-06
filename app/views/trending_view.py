"""
Trending view — shows top-selling brands and styles in Clothing & Shoes
sourced from eBay public sold listings, cached weekly.
"""
import threading
import webbrowser

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QFrame, QSizePolicy, QGridLayout,
    QMessageBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor

from app.utils.qt_thread import post_to_main


class TrendingView(QWidget):
    def __init__(self):
        super().__init__()
        self._fetching = False
        self._build_ui()
        # Load from cache immediately on startup (no network needed)
        QTimer.singleShot(300, self._load_from_cache)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._content = QWidget()
        self._root_layout = QVBoxLayout(self._content)
        self._root_layout.setContentsMargins(28, 20, 28, 28)
        self._root_layout.setSpacing(16)

        # ── Header row ────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Trending")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        self._age_lbl = QLabel("")
        self._age_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        header.addWidget(self._age_lbl)

        self._refresh_btn = QPushButton("⟳  Refresh")
        self._refresh_btn.setObjectName("primaryButton")
        self._refresh_btn.clicked.connect(self._force_refresh)
        header.addWidget(self._refresh_btn)
        self._root_layout.addLayout(header)

        # Source note (updated dynamically after fetch)
        self._source_note = QLabel(
            "Trend insights powered by Claude AI — updated weekly. "
            "Add your Anthropic API key in Settings to enable AI insights."
        )
        self._source_note.setWordWrap(True)
        self._source_note.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self._root_layout.addWidget(self._source_note)

        # Progress bar (hidden during normal display)
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 0)
        self._progress.hide()
        self._root_layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self._root_layout.addWidget(self._status_lbl)

        # Content area (cards injected here dynamically)
        self._cards_widget = QWidget()
        self._cards_layout = QGridLayout(self._cards_widget)
        self._cards_layout.setSpacing(16)
        self._root_layout.addWidget(self._cards_widget)

        self._root_layout.addStretch()
        scroll.setWidget(self._content)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_from_cache(self):
        from app.services.trending_service import get_cached, get_cache_age_str
        cache = get_cached()
        if cache.get("categories"):
            self._render(cache)
            self._age_lbl.setText(f"Updated {get_cache_age_str(cache)}")
        else:
            self._status_lbl.setText(
                "No data yet — click ⟳ Refresh to fetch trending items."
            )

    def _force_refresh(self):
        if self._fetching:
            return

        # Warn if no API key is configured
        from app.services.anthropic_key import has_key
        if not has_key():
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "No Anthropic API Key",
                "AI-powered trending insights require an Anthropic API key.\n\n"
                "Go to Settings → Trending & AI Insights to add your key.\n\n"
                "Continue anyway using the eBay scraper (slower, less reliable)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._fetching = True
        self._refresh_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("Fetching trending data…")

        def _run():
            from app.services.trending_service import fetch_trending, get_cache_age_str
            try:
                data = fetch_trending(
                    force=True,
                    progress_cb=lambda msg: post_to_main(
                        lambda m=msg: self._status_lbl.setText(m)
                    ),
                )
                post_to_main(lambda: self._on_fetch_done(data))
            except Exception as exc:
                post_to_main(lambda e=exc: self._on_fetch_error(str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def _on_fetch_done(self, data: dict):
        self._fetching = False
        self._refresh_btn.setEnabled(True)
        self._progress.hide()

        from app.services.trending_service import get_cache_age_str
        self._age_lbl.setText(f"Updated {get_cache_age_str(data)}")
        self._status_lbl.setText("")

        source = data.get("source", "ebay")
        model  = data.get("model", "")
        if source == "claude":
            self._source_note.setText(
                f"✨  AI-powered insights from {model} — updated weekly. "
                "Refresh anytime to get the latest market read."
            )
        else:
            self._source_note.setText(
                "Showing top-selling brands and styles from eBay sold listings — "
                "updated weekly. Add an Anthropic API key in Settings for AI insights."
            )
        self._render(data)

    def _on_fetch_error(self, error: str):
        self._fetching = False
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"Error: {error}")
        QMessageBox.warning(
            self, "Trending — Fetch Failed",
            f"Could not retrieve trending data:\n\n{error}\n\n"
            "Check your internet connection and try again."
        )

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render(self, data: dict):
        """Clear and redraw all category cards."""
        # Remove old cards
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        categories = data.get("categories", [])
        for i, cat in enumerate(categories):
            row, col = divmod(i, 2)
            card = self._build_category_card(cat)
            self._cards_layout.addWidget(card, row, col)

        # Make columns equal width
        self._cards_layout.setColumnStretch(0, 1)
        self._cards_layout.setColumnStretch(1, 1)

    def _build_category_card(self, cat: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("platformRow")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # ── Card header ───────────────────────────────────────────────────
        hdr = QHBoxLayout()
        cat_title = QLabel(f"{cat.get('emoji', '')}  {cat['label']}")
        cat_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #cdd6f4;")
        hdr.addWidget(cat_title)
        hdr.addStretch()
        if cat.get("error"):
            err_lbl = QLabel("⚠ fetch failed")
            err_lbl.setStyleSheet("color: #f38ba8; font-size: 11px;")
            hdr.addWidget(err_lbl)
        layout.addLayout(hdr)

        if cat.get("error"):
            layout.addWidget(QLabel(f"Error: {cat['error'][:80]}"))
            return frame

        # ── Top brands ────────────────────────────────────────────────────
        brands_lbl = QLabel("🏷  Top Brands")
        brands_lbl.setStyleSheet("color: #cba6f7; font-size: 12px; font-weight: bold;")
        layout.addWidget(brands_lbl)

        top_brands = cat.get("top_brands", [])
        if top_brands:
            max_count = max(c for _, c in top_brands) or 1
            for brand, count in top_brands:
                layout.addWidget(self._brand_bar(brand, count, max_count))
        else:
            layout.addWidget(QLabel("  No brand data"))

        # ── Top styles ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #313244;")
        layout.addWidget(sep)

        styles_lbl = QLabel("🔖  Top Styles")
        styles_lbl.setStyleSheet("color: #89b4fa; font-size: 12px; font-weight: bold;")
        layout.addWidget(styles_lbl)

        top_styles = cat.get("top_styles", [])
        if top_styles:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for style, count in top_styles:
                chip = QLabel(f"  {style}  ×{count}  ")
                chip.setStyleSheet(
                    "background: #313244; border-radius: 10px; "
                    "color: #cdd6f4; font-size: 11px; padding: 2px 4px;"
                )
                chips.addWidget(chip)
            chips.addStretch()
            layout.addLayout(chips)
        else:
            layout.addWidget(QLabel("  No style data"))

        # ── Bottom section: AI insight OR eBay recent sold ───────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #313244;")
        layout.addWidget(sep2)

        insight  = cat.get("insight", "")
        recent   = cat.get("recent_sold", [])

        if insight:
            # AI-generated insight
            insight_lbl = QLabel("💡  Market Insight")
            insight_lbl.setStyleSheet("color: #fab387; font-size: 12px; font-weight: bold;")
            layout.addWidget(insight_lbl)

            text_lbl = QLabel(insight)
            text_lbl.setWordWrap(True)
            text_lbl.setStyleSheet("color: #cdd6f4; font-size: 12px; padding: 4px 0;")
            layout.addWidget(text_lbl)
        elif recent:
            sold_lbl = QLabel("🛒  Recently Sold on eBay")
            sold_lbl.setStyleSheet("color: #a6e3a1; font-size: 12px; font-weight: bold;")
            layout.addWidget(sold_lbl)
            for item in recent:
                layout.addWidget(self._listing_link(item))
        else:
            sold_lbl = QLabel("🛒  Recently Sold on eBay")
            sold_lbl.setStyleSheet("color: #a6e3a1; font-size: 12px; font-weight: bold;")
            layout.addWidget(sold_lbl)
            no_data = QLabel("  No data — add an Anthropic API key in Settings for AI insights.")
            no_data.setStyleSheet("color: #585b70; font-size: 11px;")
            layout.addWidget(no_data)

        return frame

    def _brand_bar(self, brand: str, count: int, max_count: int) -> QWidget:
        """A small horizontal bar showing brand name, count, and relative popularity."""
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        name_lbl = QLabel(brand)
        name_lbl.setFixedWidth(140)
        name_lbl.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        row.addWidget(name_lbl)

        # Bar
        bar_outer = QFrame()
        bar_outer.setFixedHeight(8)
        bar_outer.setStyleSheet(
            "background: #313244; border-radius: 4px;"
        )
        bar_outer.setMinimumWidth(60)
        bar_outer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(bar_outer, 1)

        # Inner fill — simulate with a label inside
        fill_pct = int(count / max_count * 100)
        bar_inner = QFrame(bar_outer)
        bar_inner.setGeometry(0, 0, 0, 8)  # updated on show
        bar_inner.setStyleSheet(
            "background: #cba6f7; border-radius: 4px;"
        )
        # Use a timer to resize after layout is done
        def _resize():
            w_px = int(bar_outer.width() * fill_pct / 100)
            bar_inner.setFixedWidth(max(w_px, 4))
            bar_inner.setFixedHeight(8)
        QTimer.singleShot(100, _resize)

        count_lbl = QLabel(f"×{count}")
        count_lbl.setFixedWidth(32)
        count_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        row.addWidget(count_lbl)

        return w

    def _listing_link(self, item: dict) -> QLabel:
        """A clickable label that opens the eBay listing in the system browser."""
        price = item.get("price", "")
        title = item.get("title", "")[:72]
        url   = item.get("url",   "")

        display = f'<a href="{url}" style="color:#89b4fa; text-decoration:none;">'
        if price:
            display += f'<span style="color:#a6e3a1; font-size:11px;">{price}</span>  '
        display += f'<span style="font-size:11px;">{title}</span></a>'

        lbl = QLabel(display)
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(False)
        lbl.setOpenExternalLinks(False)
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setCursor(QCursor(Qt.PointingHandCursor))
        lbl.linkActivated.connect(lambda link: webbrowser.open(link))
        return lbl
