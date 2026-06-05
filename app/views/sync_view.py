import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame, QScrollArea, QMessageBox
)
from PySide6.QtCore import Qt

from app.database.models import get_setting
from app.utils.qt_thread import post_to_main

PLATFORM_DISPLAY = {"ebay": "eBay", "mercari": "Mercari", "poshmark": "Poshmark"}
PLATFORMS = ["ebay", "mercari", "poshmark"]


class SyncView(QWidget):
    def __init__(self):
        super().__init__()
        self._rows: dict[str, dict] = {}
        self._syncing = False
        self._build_ui()

    def refresh(self):
        """Called when the tab becomes active — refresh status dots & timestamps."""
        for platform, row in self._rows.items():
            svc = self._get_service(platform)
            connected = svc.has_session()
            self._set_dot(row["dot"], "ok" if connected else "unknown")
            row["status_lbl"].setText("Connected" if connected else "Not connected")
            last = get_setting(f"last_sync_{platform}", "")
            row["last_lbl"].setText(f"Last synced: {last}" if last else "Never synced")

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(16)

        # Title + Force Sync All
        header = QHBoxLayout()
        title = QLabel("Sync")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        self._sync_all_btn = QPushButton("Force Sync All")
        self._sync_all_btn.setObjectName("primaryButton")
        self._sync_all_btn.clicked.connect(self._force_sync_all)
        header.addWidget(self._sync_all_btn)
        layout.addLayout(header)

        # Progress + status
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        layout.addWidget(self._status_lbl)

        # Platform rows
        for platform in PLATFORMS:
            row_widget = self._build_platform_row(platform)
            layout.addWidget(row_widget)

        layout.addStretch()
        scroll.setWidget(content)

        # Initial refresh
        self.refresh()

    def _build_platform_row(self, platform: str) -> QFrame:
        label = PLATFORM_DISPLAY[platform]

        frame = QFrame()
        frame.setObjectName("platformRow")
        row_layout = QHBoxLayout(frame)
        row_layout.setContentsMargins(14, 12, 14, 12)

        dot = QLabel("●")
        dot.setObjectName("statusDotUnknown")
        dot.setFixedWidth(20)
        row_layout.addWidget(dot)

        name_lbl = QLabel(label)
        name_lbl.setFixedWidth(100)
        name_lbl.setStyleSheet("font-weight: bold;")
        row_layout.addWidget(name_lbl)

        status_lbl = QLabel("Not connected")
        status_lbl.setMinimumWidth(140)
        row_layout.addWidget(status_lbl)

        last_lbl = QLabel("Never synced")
        last_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        row_layout.addWidget(last_lbl)

        row_layout.addStretch()

        sync_btn = QPushButton(f"Sync {label}")
        sync_btn.clicked.connect(lambda checked=False, p=platform: self._sync_one(p))
        row_layout.addWidget(sync_btn)

        self._rows[platform] = {
            "dot": dot,
            "status_lbl": status_lbl,
            "last_lbl": last_lbl,
            "sync_btn": sync_btn,
        }
        return frame

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _get_service(platform: str):
        if platform == "ebay":
            from app.services.ebay_service import EbayService
            return EbayService()
        if platform == "mercari":
            from app.services.mercari_service import MercariService
            return MercariService()
        from app.services.poshmark_service import PoshmarkService
        return PoshmarkService()

    def _set_dot(self, dot: QLabel, state: str):
        name = {"ok": "statusDotOk", "error": "statusDotError"}.get(state, "statusDotUnknown")
        dot.setObjectName(name)
        dot.style().unpolish(dot)
        dot.style().polish(dot)
        dot.update()

    def _set_busy(self, busy: bool):
        self._syncing = busy
        self._sync_all_btn.setEnabled(not busy)
        for row in self._rows.values():
            row["sync_btn"].setEnabled(not busy)
        if busy:
            self._progress.show()
            self._progress.setRange(0, 0)
        else:
            self._progress.hide()

    # ── Sync actions ──────────────────────────────────────────────────────

    def _sync_one(self, platform: str):
        if self._syncing:
            return
        self._set_busy(True)
        self._status_lbl.setText(f"Syncing {PLATFORM_DISPLAY[platform]}…")

        from app.services.sync_service import sync_platform

        def _progress(msg: str):
            post_to_main(lambda: self._status_lbl.setText(msg))

        sync_platform(
            platform,
            progress_cb=_progress,
            done_cb=lambda ok, count, err:
                post_to_main(lambda: self._on_sync_done(platform, ok, count, err))
        )

    def _force_sync_all(self):
        if self._syncing:
            return
        self._set_busy(True)
        self._status_lbl.setText("Syncing all platforms…")

        from app.services.sync_service import sync_all

        def _progress(msg: str):
            post_to_main(lambda: self._status_lbl.setText(msg))

        sync_all(
            progress_cb=_progress,
            done_cb=lambda total, errors:
                post_to_main(lambda: self._on_all_done(total, errors))
        )

    def _on_sync_done(self, platform: str, ok: bool, count: int, err: str | None):
        self._set_busy(False)
        if ok:
            from app.database.models import get_setting as gs
            last = gs(f"last_sync_{platform}", "")
            row = self._rows[platform]
            row["last_lbl"].setText(f"Last synced: {last}" if last else "")
            self._status_lbl.setText(f"Synced {count} listing(s) from {PLATFORM_DISPLAY[platform]}.")
        else:
            self._status_lbl.setText(f"Sync error: {err}")
            QMessageBox.warning(self, "Sync Error", f"Sync failed:\n\n{err}")

    def _on_all_done(self, total: int, errors: list):
        self._set_busy(False)
        self.refresh()
        if errors:
            self._status_lbl.setText(f"Sync complete: {total} listing(s). {len(errors)} error(s).")
            QMessageBox.warning(self, "Sync Complete with Errors",
                                f"{total} listing(s) synced.\n\nErrors:\n" + "\n".join(errors))
        else:
            self._status_lbl.setText(f"All platforms synced — {total} listing(s) total.")
