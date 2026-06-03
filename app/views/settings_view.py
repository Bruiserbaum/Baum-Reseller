import os
import datetime
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QScrollArea, QMessageBox,
    QComboBox, QProgressBar, QFileDialog, QFrame
)
from PySide6.QtCore import Qt
from app.utils.qt_thread import post_to_main

from app.database.models import get_setting, set_setting
from version import VERSION


PLATFORM_DISPLAY = {"ebay": "eBay", "mercari": "Mercari", "poshmark": "Poshmark"}


class SettingsView(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()
        self._load_settings()

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
        layout.setSpacing(20)

        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # ── Platform Connections ──────────────────────────────────────────
        conn_group = QGroupBox("Platform Connections")
        conn_layout = QVBoxLayout(conn_group)

        note = QLabel(
            "Click Open in Browser to open the platform in your normal browser (Chrome, Edge, etc.), "
            "then log in as usual — Google Sign-In, 2FA, and any other method all work. "
            "Once you're logged in, click Import Session and the app will read your cookies automatically."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #a6adc8; font-size: 11px;")
        conn_layout.addWidget(note)

        self._platform_rows: dict[str, dict] = {}
        self._platform_rows["ebay"]     = self._build_browser_row("ebay")
        self._platform_rows["mercari"]  = self._build_browser_row("mercari")
        self._platform_rows["poshmark"] = self._build_browser_row("poshmark")

        for row in self._platform_rows.values():
            conn_layout.addWidget(row["widget"])

        layout.addWidget(conn_group)

        # ── Sync ──────────────────────────────────────────────────────────
        sync_group = QGroupBox("Sync")
        sync_layout = QVBoxLayout(sync_group)

        sync_row = QHBoxLayout()
        self.last_sync_label = QLabel("Last sync: Never")
        sync_row.addWidget(self.last_sync_label)
        sync_row.addStretch()

        self.sync_btn = QPushButton("Force Sync All")
        self.sync_btn.setObjectName("primaryButton")
        self.sync_btn.clicked.connect(self._force_sync)
        sync_row.addWidget(self.sync_btn)
        sync_layout.addLayout(sync_row)

        self.sync_progress = QProgressBar()
        self.sync_progress.setTextVisible(False)
        self.sync_progress.hide()
        sync_layout.addWidget(self.sync_progress)

        self.sync_status = QLabel("")
        sync_layout.addWidget(self.sync_status)
        layout.addWidget(sync_group)

        # ── Auto-Update ───────────────────────────────────────────────────
        update_group = QGroupBox("App Updates")
        update_layout = QVBoxLayout(update_group)

        ver_row = QHBoxLayout()
        self.version_label = QLabel(f"Current version: {VERSION}")
        ver_row.addWidget(self.version_label)
        ver_row.addStretch()

        self.update_btn = QPushButton("Check for Updates")
        self.update_btn.clicked.connect(self._check_update)
        ver_row.addWidget(self.update_btn)
        update_layout.addLayout(ver_row)

        self.update_status = QLabel("")
        update_layout.addWidget(self.update_status)

        self.update_progress = QProgressBar()
        self.update_progress.setTextVisible(True)
        self.update_progress.hide()
        update_layout.addWidget(self.update_progress)

        layout.addWidget(update_group)

        # ── Backup ────────────────────────────────────────────────────────
        backup_group = QGroupBox("Backup && Restore")
        backup_layout = QVBoxLayout(backup_group)

        gdrive_row = QHBoxLayout()
        self.gdrive_status = QLabel("Google Drive: Not connected")
        gdrive_row.addWidget(self.gdrive_status)
        gdrive_row.addStretch()
        connect_drive_btn = QPushButton("Connect Google Drive")
        connect_drive_btn.clicked.connect(self._connect_gdrive)
        gdrive_row.addWidget(connect_drive_btn)
        backup_layout.addLayout(gdrive_row)

        bk_row = QHBoxLayout()
        self.last_backup_label = QLabel("Last backup: Never")
        bk_row.addWidget(self.last_backup_label)
        bk_row.addStretch()
        backup_now_btn = QPushButton("Backup Now")
        backup_now_btn.setObjectName("primaryButton")
        backup_now_btn.clicked.connect(self._backup_now)
        bk_row.addWidget(backup_now_btn)
        backup_layout.addLayout(bk_row)

        sched_row = QHBoxLayout()
        sched_row.addWidget(QLabel("Auto-backup:"))
        self.backup_schedule = QComboBox()
        self.backup_schedule.addItems(["Disabled", "Daily", "Weekly", "Monthly"])
        self.backup_schedule.currentTextChanged.connect(
            lambda t: set_setting("backup_schedule", t)
        )
        sched_row.addWidget(self.backup_schedule)
        sched_row.addStretch()
        backup_layout.addLayout(sched_row)

        io_row = QHBoxLayout()
        export_local_btn = QPushButton("Export Backup (.zip)")
        export_local_btn.clicked.connect(self._export_local)
        io_row.addWidget(export_local_btn)
        import_local_btn = QPushButton("Import Backup (.zip)")
        import_local_btn.clicked.connect(self._import_local)
        io_row.addWidget(import_local_btn)
        io_row.addStretch()
        backup_layout.addLayout(io_row)

        self.backup_status = QLabel("")
        backup_layout.addWidget(self.backup_status)

        layout.addWidget(backup_group)

        # ── Data Location Notice ──────────────────────────────────────────
        data_dir = os.path.join(os.path.expanduser("~"), ".baum-reseller")
        data_notice = QLabel(
            f"🔒  Your data (database, credentials, browser sessions) is stored at:\n"
            f"    {data_dir}\n"
            f"    This folder is NOT modified by app reinstalls or updates — your settings are always preserved."
        )
        data_notice.setWordWrap(True)
        data_notice.setStyleSheet(
            "color: #a6adc8; font-size: 11px; "
            "background: #252535; border-radius: 6px; padding: 10px;"
        )
        layout.addWidget(data_notice)
        layout.addStretch()
        scroll.setWidget(content)

    # ── Platform row builder ──────────────────────────────────────────────

    def _build_browser_row(self, platform: str) -> dict:
        """All platforms: Open in Browser → Import Session → Test → Sync."""
        label = PLATFORM_DISPLAY[platform]

        container = QFrame()
        container.setObjectName("platformRow")
        row = QHBoxLayout(container)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(6)

        name_lbl = QLabel(label)
        name_lbl.setFixedWidth(90)
        row.addWidget(name_lbl)

        dot = QLabel("●")
        dot.setObjectName("statusDotUnknown")
        dot.setFixedWidth(20)
        row.addWidget(dot)

        status_lbl = QLabel("Not connected")
        status_lbl.setMinimumWidth(220)
        row.addWidget(status_lbl)

        last_lbl = QLabel("")
        last_lbl.setStyleSheet("color:#585b70; font-size:11px;")
        row.addWidget(last_lbl)
        row.addStretch()

        open_btn = QPushButton("Open in Browser")
        open_btn.setToolTip(
            f"Opens {label} in your normal browser. Log in however you like, "
            "then click Import Session."
        )
        open_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl:
            self._open_in_browser(p, sl)
        )
        row.addWidget(open_btn)

        import_btn = QPushButton("Import Session")
        import_btn.setObjectName("primaryButton")
        import_btn.setToolTip(
            "Reads your existing login cookies from Chrome/Edge/Firefox. "
            "You must be logged in to this platform in your browser."
        )
        import_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot, ib=import_btn:
            self._import_session(p, sl, sd, ib)
        )
        row.addWidget(import_btn)

        test_btn = QPushButton("Test")
        test_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot:
            self._test_platform_async(p, sl, sd)
        )
        row.addWidget(test_btn)

        sync_btn = QPushButton("Sync")
        sync_btn.clicked.connect(lambda checked=False, p=platform: self._sync_platform(p))
        row.addWidget(sync_btn)

        logout_btn = QPushButton("Log Out")
        logout_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot:
            self._logout_platform(p, sl, sd)
        )
        row.addWidget(logout_btn)

        return {"widget": container, "status_label": status_lbl,
                "status_dot": dot, "last_sync": last_lbl}

    # ── Settings loading ──────────────────────────────────────────────────

    def _load_settings(self):
        from app.utils.config import get as cfg_get

        last = get_setting("last_sync_time", "Never")
        self.last_sync_label.setText(f"Last sync: {last}")

        for p, row in self._platform_rows.items():
            last_p = get_setting(f"last_sync_{p}", "")
            row["last_sync"].setText(f"Last synced: {last_p}" if last_p else "")

            if p in ("ebay", "mercari", "poshmark"):
                svc = self._get_service(p)
                if svc.has_session():
                    self._set_dot(row["status_dot"], "ok")
                    row["status_label"].setText("Connected — ready to sync")

        sched = get_setting("backup_schedule", "Disabled")
        idx = self.backup_schedule.findText(sched)
        if idx >= 0:
            self.backup_schedule.setCurrentIndex(idx)

        last_bk = get_setting("last_backup_time", "Never")
        self.last_backup_label.setText(f"Last backup: {last_bk}")

        token = os.path.join(os.path.expanduser("~"), ".baum-reseller", "gdrive_token.pkl")
        if os.path.exists(token):
            self.gdrive_status.setText("Google Drive: Connected")

    # ── Platform helpers ──────────────────────────────────────────────────

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

    # ── Open in browser ───────────────────────────────────────────────────

    def _open_in_browser(self, platform: str, status_lbl: QLabel):
        """Open the platform in the user's default browser."""
        svc = self._get_service(platform)
        svc.open_in_browser()
        status_lbl.setText(
            f"Opened in your browser — log in if needed, then click Import Session"
        )

    # ── Import session ────────────────────────────────────────────────────

    def _import_session(self, platform: str, status_lbl: QLabel,
                        dot: QLabel, import_btn: QPushButton):
        """Read cookies from the system browser and save the session."""
        import_btn.setEnabled(False)
        status_lbl.setText("Reading cookies from your browser…")
        self._set_dot(dot, "unknown")

        svc = self._get_service(platform)

        def _done(ok: bool, msg: str):
            post_to_main(lambda: self._on_import_done(ok, msg, platform, status_lbl, dot, import_btn))

        svc.import_session(done_cb=_done)

    def _on_import_done(self, ok: bool, msg: str, platform: str,
                        status_lbl: QLabel, dot: QLabel, import_btn: QPushButton):
        import_btn.setEnabled(True)
        if ok:
            self._set_dot(dot, "ok")
            status_lbl.setText("Connected — session imported")
            QMessageBox.information(
                self, f"{PLATFORM_DISPLAY[platform]} — Session Imported",
                f"✓ {msg}\n\nYou can now click Sync to fetch your listings."
            )
        else:
            self._set_dot(dot, "error")
            status_lbl.setText("Import failed — see error for details")
            QMessageBox.warning(
                self, f"{PLATFORM_DISPLAY[platform]} — Import Failed",
                msg
            )

    def _logout_platform(self, platform: str, status_lbl: QLabel, dot: QLabel):
        svc = self._get_service(platform)
        if hasattr(svc, "clear_session"):
            svc.clear_session()
        self._set_dot(dot, "unknown")
        status_lbl.setText("Logged out — click Import Session to reconnect")

    # ── Test (runs in background thread, shows popup with result) ─────────

    def _test_platform_async(self, platform: str, status_lbl: QLabel, dot: QLabel):
        status_lbl.setText("Testing…")
        self._set_dot(dot, "unknown")

        def _run():
            svc = self._get_service(platform)
            try:
                ok, msg = svc.test_connection()
            except Exception as e:
                ok, msg = False, str(e)
            post_to_main(lambda:self._on_test_done(ok, msg, platform, status_lbl, dot))

        threading.Thread(target=_run, daemon=True).start()

    def _on_test_done(self, ok: bool, msg: str, platform: str,
                      status_lbl: QLabel, dot: QLabel):
        self._set_dot(dot, "ok" if ok else "error")
        status_lbl.setText(msg)
        icon = QMessageBox.Information if ok else QMessageBox.Warning
        QMessageBox.information(self, f"{PLATFORM_DISPLAY[platform]} — Connection Test",
                                f"{'✓ ' if ok else '✗ '}{msg}") if ok else \
        QMessageBox.warning(self, f"{PLATFORM_DISPLAY[platform]} — Connection Test",
                            f"✗ {msg}")

    # ── Sync ──────────────────────────────────────────────────────────────

    def _sync_platform(self, platform: str):
        self.sync_btn.setEnabled(False)
        self.sync_progress.show()
        self.sync_progress.setRange(0, 0)
        self.sync_status.setText(f"Syncing {PLATFORM_DISPLAY[platform]}…")

        from app.services.sync_service import sync_platform
        sync_platform(
            platform,
            done_cb=lambda ok, count, err:
            post_to_main(lambda:self._on_sync_done(ok, count, err))
        )

    def _force_sync(self):
        self.sync_btn.setEnabled(False)
        self.sync_progress.show()
        self.sync_progress.setRange(0, 0)
        self.sync_status.setText("Syncing all platforms…")

        from app.services.sync_service import sync_all
        sync_all(done_cb=lambda total, errors:
                 post_to_main(lambda:self._on_all_sync_done(total, errors)))

    def _on_sync_done(self, ok: bool, count: int, err: str | None):
        self.sync_btn.setEnabled(True)
        self.sync_progress.hide()
        self.sync_status.setText(f"Synced {count} listing(s)." if ok else f"Error: {err}")
        if not ok:
            QMessageBox.warning(self, "Sync Error", f"Sync failed:\n\n{err}")
        self._load_settings()

    def _on_all_sync_done(self, total: int, errors: list):
        self.sync_btn.setEnabled(True)
        self.sync_progress.hide()
        msg = f"Sync complete: {total} listing(s)."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors)
            QMessageBox.warning(self, "Sync Complete with Errors", msg)
        else:
            self.sync_status.setText(msg)
        self._load_settings()

    # ── Update ────────────────────────────────────────────────────────────

    def _check_update(self):
        self.update_btn.setEnabled(False)
        self.update_status.setText("Checking…")

        def _check():
            available, latest, error = False, VERSION, None
            try:
                from app.services.updater_service import check_for_update
                available, latest = check_for_update()
            except Exception as e:
                error = str(e)
            finally:
                # Always fires — even if an exception was raised
                post_to_main(lambda:self._on_update_check(available, latest, error))

        threading.Thread(target=_check, daemon=True).start()

    def _on_update_check(self, available: bool, latest: str, error: str | None):
        self.update_btn.setEnabled(True)
        if error:
            self.update_status.setText("Could not check for updates.")
            QMessageBox.warning(self, "Update Check Failed",
                                f"Could not reach GitHub:\n\n{error}")
        elif available:
            self.update_status.setText(f"Update available: v{latest}")
            reply = QMessageBox.question(
                self, "Update Available",
                f"Version {latest} is available. Download and install now?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._do_update()
        else:
            self.update_status.setText(f"You're up to date (v{VERSION}).")

    def _do_update(self):
        from app.services.updater_service import get_latest_release, find_update_asset, download_and_apply_update
        release = get_latest_release()
        if not release:
            QMessageBox.critical(self, "Update", "Could not fetch release info.")
            return
        asset = find_update_asset(release)
        if not asset:
            QMessageBox.critical(self, "Update",
                                 "No installer found in the release assets.\n"
                                 "Please download manually from GitHub.")
            return

        self.update_progress.show()
        self.update_progress.setValue(0)

        def _progress(pct):
            post_to_main(lambda: self.update_progress.setValue(pct))

        def _done(ok, err):
            if ok:
                post_to_main(lambda: (
                    self.update_progress.hide(),
                    QMessageBox.information(
                        self, "Update",
                        "The installer is running in the background.\n"
                        "The app will close and reopen when the update finishes."
                    )
                ))
            else:
                post_to_main(lambda: (
                    self.update_progress.hide(),
                    QMessageBox.critical(self, "Update Failed", str(err))
                ))

        download_and_apply_update(
            asset["browser_download_url"],
            asset["name"],
            progress_cb=_progress,
            done_cb=_done,
        )

    # ── Backup ────────────────────────────────────────────────────────────

    def _connect_gdrive(self):
        try:
            from app.services.backup_service import get_drive_service
            get_drive_service()
            self.gdrive_status.setText("Google Drive: Connected")
            QMessageBox.information(self, "Google Drive", "Connected successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Google Drive", f"Connection failed:\n{e}")

    def _backup_now(self):
        self.backup_status.setText("Creating backup…")
        tmp = os.path.join(os.path.expanduser("~"), ".baum-reseller", "backup_upload.zip")
        try:
            from app.services.backup_service import export_to_zip, upload_backup_to_drive
            export_to_zip(tmp)
            self.backup_status.setText("Uploading to Google Drive…")
            upload_backup_to_drive(
                tmp,
                done_cb=lambda ok, err:
                post_to_main(lambda:self._on_backup_done(ok, err))
            )
        except Exception as e:
            self.backup_status.setText(f"Backup failed: {e}")

    def _on_backup_done(self, ok: bool, err: str | None):
        if ok:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            self.last_backup_label.setText(f"Last backup: {ts}")
            self.backup_status.setText("Backup uploaded to Google Drive.")
        else:
            self.backup_status.setText(f"Upload failed: {err}")

    def _export_local(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Backup", "baum_reseller_backup.zip", "Zip Files (*.zip)"
        )
        if not path:
            return
        try:
            from app.services.backup_service import export_to_zip
            export_to_zip(path)
            QMessageBox.information(self, "Export", f"Backup saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _import_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Backup", "", "Zip Files (*.zip)"
        )
        if not path:
            return
        reply = QMessageBox.warning(
            self, "Import Backup",
            "This will REPLACE all current data. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        try:
            from app.services.backup_service import import_from_zip
            import_from_zip(path)
            QMessageBox.information(self, "Import", "Data restored successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))
