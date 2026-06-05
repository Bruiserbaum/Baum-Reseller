from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QLabel, QStatusBar
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap

from app.database.models import get_setting
from app.views.inventory_view import InventoryView
from app.views.sync_view import SyncView
from app.views.import_view import ImportView
from app.views.reports_view import ReportsView
from app.views.settings_view import SettingsView
from app.views.notifications_view import NotificationsView
from version import VERSION

NAV = [
    ("Inventory", "inventory", 0),
    ("Sync",      "sync",      1),
    ("Import",    "import",    2),
    ("Reports",   "reports",   3),
    ("Alerts",    "alerts",    4),
    ("Settings",  "settings",  5),
]


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
        self.inventory_view = InventoryView()
        self.sync_view = SyncView()
        self.import_view = ImportView()
        self.reports_view = ReportsView()
        self.notifications_view = NotificationsView()
        self.settings_view = SettingsView()

        self.stack.addWidget(self.inventory_view)     # 0
        self.stack.addWidget(self.sync_view)          # 1
        self.stack.addWidget(self.import_view)        # 2
        self.stack.addWidget(self.reports_view)       # 3
        self.stack.addWidget(self.notifications_view) # 4
        self.stack.addWidget(self.settings_view)      # 5
        root.addWidget(self.stack, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._refresh_status()

        # Propagate badge changes from notifications view
        self.notifications_view.badge_changed.connect(self._update_alert_badge)

        # Periodic status refresh
        status_timer = QTimer(self)
        status_timer.timeout.connect(self._refresh_status)
        status_timer.start(60_000)

        # Run background notification checks on startup (5s delay) then every 4h
        QTimer.singleShot(5_000, self._run_checks)
        check_timer = QTimer(self)
        check_timer.timeout.connect(self._run_checks)
        check_timer.start(4 * 60 * 60 * 1000)

        self._activate("inventory")

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Logo image ─────────────────────────────────────────────────────
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
            self.inventory_view.refresh()
        elif name == "sync":
            self.sync_view.refresh()
        elif name == "alerts":
            self.notifications_view.refresh()

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
        else:
            btn.setText("Alerts")
            btn.setStyleSheet("")

    def _run_checks(self):
        from app.services.notification_service import run_checks_async

        def _done(count: int):
            if count:
                from app.services.notification_service import get_unread_count
                QTimer.singleShot(0, lambda: self._update_alert_badge(get_unread_count()))

        run_checks_async(done_cb=_done)

    def _refresh_status(self):
        last = get_setting("last_sync_time", "Never")
        self.status_bar.showMessage(f"Last synced: {last}  |  Baum Reseller v{VERSION}")
