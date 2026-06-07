import threading

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QLabel, QStatusBar
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QIcon, QPixmap

from app.database.models import get_setting
from app.views.inventory_view import InventoryView
from app.views.containers_view import ContainersView
from app.views.sync_view import SyncView
from app.views.import_view import ImportView
from app.views.trending_view import TrendingView
from app.views.reports_view import ReportsView
from app.views.settings_view import SettingsView
from app.views.notifications_view import NotificationsView
from version import VERSION

NAV = [
    ("Inventory",  "inventory",   0),
    ("Containers", "containers",  1),
    ("Sync",       "sync",        2),
    ("Trending",   "trending",    3),
    ("Import",     "import",      4),
    ("Reports",    "reports",     5),
    ("Alerts",     "alerts",      6),
    ("Settings",   "settings",    7),
]

_GITHUB_REPO = "Bruiserbaum/Baum-Reseller"
_UPDATE_CHECK_INTERVAL_MS = 10 * 60 * 1000   # 10 minutes


class _UpdateSignals(QObject):
    """Tiny helper so the background thread can signal the UI thread."""
    update_found = Signal(str)   # emits the new version string


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Baum Reseller")
        self.setMinimumSize(1100, 680)
        self.resize(1340, 820)

        # ── Window icon ────────────────────────────────────────────────────
        import os
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for _name in ("icon.ico", "icon.png"):
            _p = os.path.join(_base, "assets", _name)
            if os.path.exists(_p):
                self.setWindowIcon(QIcon(_p))
                break

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.inventory_view    = InventoryView()
        self.containers_view   = ContainersView()
        self.sync_view         = SyncView()
        self.trending_view     = TrendingView()
        self.import_view       = ImportView()
        self.reports_view      = ReportsView()
        self.notifications_view = NotificationsView()
        self.settings_view     = SettingsView()

        self.stack.addWidget(self.inventory_view)      # 0
        self.stack.addWidget(self.containers_view)     # 1
        self.stack.addWidget(self.sync_view)           # 2
        self.stack.addWidget(self.trending_view)       # 3
        self.stack.addWidget(self.import_view)         # 4
        self.stack.addWidget(self.reports_view)        # 5
        self.stack.addWidget(self.notifications_view)  # 6
        self.stack.addWidget(self.settings_view)       # 7
        root.addWidget(self.stack, 1)

        # ── Status bar ────────────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Permanent right-side label for update badge (hidden until needed)
        self._update_lbl = QLabel()
        self._update_lbl.setObjectName("updateBadge")
        self._update_lbl.setStyleSheet(
            "color: #89b4fa; font-weight: bold; padding: 0 8px;"
        )
        self._update_lbl.hide()
        self.status_bar.addPermanentWidget(self._update_lbl)

        self._refresh_status()

        # ── Signal wiring ─────────────────────────────────────────────────
        # Propagate badge changes from notifications view
        self.notifications_view.badge_changed.connect(self._update_alert_badge)

        # Sync / import → inventory + containers dirty, status message
        self.sync_view.sync_completed.connect(self.inventory_view.mark_dirty)
        self.sync_view.sync_completed.connect(self.containers_view.mark_dirty)
        self.sync_view.sync_completed.connect(self._on_sync_done)

        self.import_view.import_completed.connect(self.inventory_view.mark_dirty)
        self.import_view.import_completed.connect(self.containers_view.mark_dirty)
        self.import_view.import_completed.connect(self._on_import_done)

        # Trending view signals completion via optional attribute
        if hasattr(self.trending_view, "trending_updated"):
            self.trending_view.trending_updated.connect(self._on_trending_done)

        # ── Timers ────────────────────────────────────────────────────────
        status_timer = QTimer(self)
        status_timer.timeout.connect(self._refresh_status)
        status_timer.start(60_000)

        # Update checker: first check 30s after launch, then every 10 min
        self._update_sigs = _UpdateSignals()
        self._update_sigs.update_found.connect(self._show_update_badge)
        QTimer.singleShot(30_000, self._check_for_update)
        update_timer = QTimer(self)
        update_timer.timeout.connect(self._check_for_update)
        update_timer.start(_UPDATE_CHECK_INTERVAL_MS)

        # ── Background services ───────────────────────────────────────────
        from app.services.enrich_service import start_idle_enrichment
        start_idle_enrichment()

        QTimer.singleShot(2_000, self._backfill_categories)

        QTimer.singleShot(5_000, self._run_checks)
        check_timer = QTimer(self)
        check_timer.timeout.connect(self._run_checks)
        check_timer.start(4 * 60 * 60 * 1000)

        # 3-hour maintenance: backfill migration, report refresh, update check
        maint_timer = QTimer(self)
        maint_timer.timeout.connect(self._run_maintenance)
        maint_timer.start(3 * 60 * 60 * 1000)   # 3 hours

        self._activate("inventory")

    # ── Sidebar ──────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        import os
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _logo_path = os.path.join(_base, "assets", "icon.png")
        if os.path.exists(_logo_path):
            logo_label = QLabel()
            logo_label.setAlignment(Qt.AlignCenter)
            pix = QPixmap(_logo_path).scaled(72, 72, Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation)
            logo_label.setPixmap(pix)
            logo_label.setContentsMargins(0, 16, 0, 4)
            layout.addWidget(logo_label)

        title = QLabel("Baum\nReseller")
        title.setObjectName("sidebarTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self._nav_btns: dict[str, QPushButton] = {}
        for label, name, _ in NAV:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, n=name: self._activate(n))
            layout.addWidget(btn)
            self._nav_btns[name] = btn

        layout.addStretch()

        ver = QLabel(f"v{VERSION}")
        ver.setObjectName("versionLabel")
        ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(ver)

        return sidebar

    def _activate(self, name: str):
        idx = {n: i for _, n, i in NAV}
        self.stack.setCurrentIndex(idx[name])
        for n, btn in self._nav_btns.items():
            btn.setChecked(n == name)
        if name == "inventory":
            self.inventory_view.lazy_refresh()
        elif name == "containers":
            self.containers_view.lazy_refresh()
        elif name == "sync":
            self.sync_view.refresh()
        elif name == "alerts":
            self.notifications_view.refresh()
        elif name == "reports":
            self.reports_view.refresh()

    # ── Alert badge ───────────────────────────────────────────────────────────

    def _update_alert_badge(self, count: int):
        btn = self._nav_btns.get("alerts")
        if not btn:
            return
        if count:
            btn.setText(f"Alerts  ({count})")
            btn.setStyleSheet(
                "QPushButton { color: #f38ba8; font-weight: bold; }"
                "QPushButton:checked { color: #f38ba8; border-left: 3px solid #f38ba8; }"
            )
            # Also flash in status bar for a few seconds
            self.status_bar.showMessage(
                f"🔔  {count} unread alert{'s' if count != 1 else ''}", 6_000
            )
        else:
            btn.setText("Alerts")
            btn.setStyleSheet("")

    # ── Sync / import / trending notifications ────────────────────────────────

    def _on_sync_done(self):
        self._refresh_status()
        self.status_bar.showMessage("✓  Sync complete", 8_000)
        # Refresh reports if they are currently visible
        if self.stack.currentWidget() is self.reports_view:
            self.reports_view.refresh()

    def _on_import_done(self):
        self.status_bar.showMessage("✓  Import complete", 8_000)

    def _on_trending_done(self):
        self.status_bar.showMessage("✓  Trending data updated", 8_000)

    # ── Update checker ────────────────────────────────────────────────────────

    def _check_for_update(self):
        """Fire-and-forget background thread that pings GitHub releases API."""
        def _worker():
            try:
                import urllib.request, json as _json
                url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": "BaumReseller"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = _json.loads(resp.read().decode())
                tag = data.get("tag_name", "").lstrip("v")
                if tag and _is_newer(tag, VERSION):
                    self._update_sigs.update_found.emit(tag)
            except Exception:
                pass   # silently ignore — no internet, rate limit, etc.

        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_badge(self, new_version: str):
        """Called on the main thread when a newer release is found."""
        text = f"🔔  Update v{new_version} available — github.com/{_GITHUB_REPO}/releases"
        self._update_lbl.setText(text)
        self._update_lbl.show()
        # Also flash in the transient area
        self.status_bar.showMessage(f"⬆  Update available: v{new_version}", 15_000)

    # ── Periodic helpers ──────────────────────────────────────────────────────

    def _run_checks(self):
        from app.services.notification_service import run_checks_async

        def _done(count: int):
            if count:
                from app.services.notification_service import get_unread_count
                QTimer.singleShot(0, lambda: self._update_alert_badge(get_unread_count()))

        run_checks_async(done_cb=_done)

    def _backfill_categories(self):
        def _worker():
            from app.services.enrich_service import infer_category
            from app.database.connection import get_connection
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, title FROM items WHERE category IS NULL OR category = ''"
                ).fetchall()
                updates = [
                    (infer_category(r["title"]), r["id"])
                    for r in rows
                    if infer_category(r["title"])
                ]
                if updates:
                    conn.executemany("UPDATE items SET category=? WHERE id=?", updates)
            if updates:
                from app.utils.qt_thread import post_to_main
                post_to_main(self.inventory_view.mark_dirty)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_maintenance(self):
        """3-hour background maintenance pass: backfill sales, refresh UI, check updates."""
        def _worker():
            # Re-run the backfill migration (idempotent INSERT NOT EXISTS)
            try:
                from app.database.connection import get_connection
                with get_connection() as conn:
                    conn.execute("""
                        INSERT INTO sales
                               (item_id, platform, sale_price, platform_fees, shipping_cost,
                                sale_date, ext_listing_id)
                        SELECT l.item_id, l.platform, l.sold_price, 0, 0,
                               CASE WHEN l.sold_date IS NOT NULL AND l.sold_date != ''
                                    THEN l.sold_date ELSE date('now') END,
                               l.listing_id
                        FROM listings l
                        WHERE l.status = 'sold'
                          AND l.sold_price > 0
                          AND NOT EXISTS (
                              SELECT 1 FROM sales s
                              WHERE s.item_id = l.item_id
                                AND s.platform = l.platform
                                AND s.ext_listing_id = l.listing_id
                          )
                    """)
            except Exception:
                pass

            # Store backfill timestamp so the Sync status panel can show it
            try:
                import datetime
                from app.database.models import set_setting
                set_setting("last_sales_backfill",
                            datetime.datetime.now().isoformat(timespec="seconds"))
            except Exception:
                pass

            from app.utils.qt_thread import post_to_main
            # Refresh reports if visible
            post_to_main(self._maintenance_ui_refresh)

        threading.Thread(target=_worker, daemon=True).start()
        # Also trigger update check on each maintenance cycle
        self._check_for_update()
        # Also run notification checks
        self._run_checks()

    def _maintenance_ui_refresh(self):
        """UI-thread portion of maintenance: refresh any open live views."""
        if self.stack.currentWidget() is self.reports_view:
            self.reports_view.refresh()
        self._refresh_status()

    def _refresh_status(self):
        last = get_setting("last_sync_time", "Never")
        self.status_bar.showMessage(f"Last synced: {last}  |  Baum Reseller v{VERSION}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_newer(remote: str, local: str) -> bool:
    """Return True if remote version string is strictly newer than local."""
    try:
        def _parts(v: str):
            return tuple(int(x) for x in v.split("."))
        return _parts(remote) > _parts(local)
    except Exception:
        return False
