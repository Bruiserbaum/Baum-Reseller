import os
import datetime
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QScrollArea, QMessageBox,
    QComboBox, QProgressBar, QFileDialog, QFrame
)
from PySide6.QtCore import Qt, QTimer

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
            "eBay uses API keys. Mercari and Poshmark use browser-based login — "
            "works with email/password or Google Sign-In."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #a6adc8; font-size: 11px;")
        conn_layout.addWidget(note)

        self._platform_rows: dict[str, dict] = {}
        self._platform_rows["ebay"]     = self._build_ebay_row()
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
        backup_group = QGroupBox("Backup & Restore")
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
        layout.addStretch()
        scroll.setWidget(content)

    # ── Platform row builders ─────────────────────────────────────────────

    def _build_ebay_row(self) -> dict:
        """eBay uses API key credentials, tested via OAuth token request."""
        container = QFrame()
        container.setObjectName("platformRow")
        row = QHBoxLayout(container)
        row.setContentsMargins(10, 10, 10, 10)

        name_lbl = QLabel("eBay")
        name_lbl.setFixedWidth(90)
        row.addWidget(name_lbl)

        dot = QLabel("●")
        dot.setObjectName("statusDotUnknown")
        dot.setFixedWidth(20)
        row.addWidget(dot)

        status_lbl = QLabel("Not configured")
        status_lbl.setMinimumWidth(180)
        row.addWidget(status_lbl)

        last_lbl = QLabel("")
        last_lbl.setStyleSheet("color:#585b70; font-size:11px;")
        row.addWidget(last_lbl)
        row.addStretch()

        id_edit = QLineEdit()
        id_edit.setPlaceholderText("Client ID")
        id_edit.setEchoMode(QLineEdit.Password)
        id_edit.setFixedWidth(150)
        row.addWidget(id_edit)

        secret_edit = QLineEdit()
        secret_edit.setPlaceholderText("Client Secret")
        secret_edit.setEchoMode(QLineEdit.Password)
        secret_edit.setFixedWidth(150)
        row.addWidget(secret_edit)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(lambda: self._save_ebay_creds(id_edit.text(), secret_edit.text()))
        row.addWidget(save_btn)

        test_btn = QPushButton("Test")
        test_btn.clicked.connect(lambda: self._test_platform_async("ebay", status_lbl, dot))
        row.addWidget(test_btn)

        sync_btn = QPushButton("Sync")
        sync_btn.clicked.connect(lambda: self._sync_platform("ebay"))
        row.addWidget(sync_btn)

        return {"widget": container, "status_label": status_lbl,
                "status_dot": dot, "last_sync": last_lbl}

    def _build_browser_row(self, platform: str) -> dict:
        """Mercari / Poshmark — browser-based auth (email/pass or Google SSO)."""
        label = PLATFORM_DISPLAY[platform]

        container = QFrame()
        container.setObjectName("platformRow")
        col = QVBoxLayout(container)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(6)

        # Top row: status
        top = QHBoxLayout()
        name_lbl = QLabel(label)
        name_lbl.setFixedWidth(90)
        top.addWidget(name_lbl)

        dot = QLabel("●")
        dot.setObjectName("statusDotUnknown")
        dot.setFixedWidth(20)
        top.addWidget(dot)

        status_lbl = QLabel("Not logged in")
        status_lbl.setMinimumWidth(200)
        top.addWidget(status_lbl)

        last_lbl = QLabel("")
        last_lbl.setStyleSheet("color:#585b70; font-size:11px;")
        top.addWidget(last_lbl)
        top.addStretch()
        col.addLayout(top)

        # Bottom row: optional credential pre-fill + action buttons
        bot = QHBoxLayout()
        bot.addSpacing(110)  # indent to align under status

        email_edit = QLineEdit()
        email_edit.setPlaceholderText("Email (optional pre-fill)")
        email_edit.setFixedWidth(180)
        bot.addWidget(email_edit)

        pw_edit = QLineEdit()
        pw_edit.setPlaceholderText("Password (optional)")
        pw_edit.setEchoMode(QLineEdit.Password)
        pw_edit.setFixedWidth(140)
        bot.addWidget(pw_edit)

        save_btn = QPushButton("Save")
        save_btn.setToolTip("Save email/password for browser auto-fill")
        save_btn.clicked.connect(
            lambda p=platform, e=email_edit, pw=pw_edit:
            self._save_platform_creds(p, e.text(), pw.text())
        )
        bot.addWidget(save_btn)

        login_btn = QPushButton("Login with Browser")
        login_btn.setObjectName("primaryButton")
        login_btn.setToolTip(
            "Opens a browser window — log in with email/password or 'Continue with Google'"
        )
        login_btn.clicked.connect(
            lambda p=platform, sl=status_lbl, sd=dot, lb=login_btn:
            self._browser_login(p, sl, sd, lb)
        )
        bot.addWidget(login_btn)

        test_btn = QPushButton("Test")
        test_btn.clicked.connect(lambda p=platform, sl=status_lbl, sd=dot:
                                 self._test_platform_async(p, sl, sd))
        bot.addWidget(test_btn)

        sync_btn = QPushButton("Sync")
        sync_btn.clicked.connect(lambda p=platform: self._sync_platform(p))
        bot.addWidget(sync_btn)

        logout_btn = QPushButton("Log Out")
        logout_btn.clicked.connect(
            lambda p=platform, sl=status_lbl, sd=dot: self._logout_platform(p, sl, sd)
        )
        bot.addWidget(logout_btn)

        col.addLayout(bot)

        return {"widget": container, "status_label": status_lbl,
                "status_dot": dot, "last_sync": last_lbl}

    # ── Settings loading ──────────────────────────────────────────────────

    def _load_settings(self):
        last = get_setting("last_sync_time", "Never")
        self.last_sync_label.setText(f"Last sync: {last}")

        for p, row in self._platform_rows.items():
            last_p = get_setting(f"last_sync_{p}", "")
            row["last_sync"].setText(f"Last synced: {last_p}" if last_p else "")

            # Reflect session state for browser-auth platforms
            if p in ("mercari", "poshmark"):
                svc = self._get_service(p)
                if svc.has_session():
                    self._set_dot(row["status_dot"], "ok")
                    row["status_label"].setText("Session saved — ready to sync")

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

    # ── Credential save ───────────────────────────────────────────────────

    def _save_ebay_creds(self, client_id: str, secret: str):
        if not client_id or not secret:
            QMessageBox.warning(self, "eBay", "Enter both Client ID and Client Secret.")
            return
        from app.services.ebay_service import EbayService
        EbayService().save_credentials(client_id, secret)
        QMessageBox.information(self, "eBay", "Credentials saved.")

    def _save_platform_creds(self, platform: str, email: str, password: str):
        svc = self._get_service(platform)
        svc.save_credentials(email, password)
        QMessageBox.information(
            self, PLATFORM_DISPLAY[platform],
            "Credentials saved for browser auto-fill.\n"
            "Click 'Login with Browser' to authenticate."
        )

    # ── Browser login ─────────────────────────────────────────────────────

    def _browser_login(self, platform: str, status_lbl: QLabel,
                       dot: QLabel, login_btn: QPushButton):
        login_btn.setEnabled(False)
        login_btn.setText("Browser open…")
        status_lbl.setText("Complete login in the browser window…")
        self._set_dot(dot, "unknown")

        svc = self._get_service(platform)

        def _done(ok: bool, err: str | None):
            QTimer.singleShot(0, lambda: self._on_browser_login_done(
                ok, err, platform, status_lbl, dot, login_btn
            ))

        svc.login_browser(done_cb=_done)

    def _on_browser_login_done(self, ok: bool, err: str | None, platform: str,
                               status_lbl: QLabel, dot: QLabel, login_btn: QPushButton):
        login_btn.setEnabled(True)
        login_btn.setText("Login with Browser")
        if ok:
            self._set_dot(dot, "ok")
            status_lbl.setText("Logged in — session saved")
            self._platform_rows[platform]["last_sync"].setText("")
        else:
            self._set_dot(dot, "error")
            status_lbl.setText("Login failed")
            QMessageBox.critical(
                self, f"{PLATFORM_DISPLAY[platform]} Login Failed",
                f"Could not complete login:\n\n{err}"
            )

    def _logout_platform(self, platform: str, status_lbl: QLabel, dot: QLabel):
        svc = self._get_service(platform)
        if hasattr(svc, "clear_session"):
            svc.clear_session()
        self._set_dot(dot, "unknown")
        status_lbl.setText("Logged out")

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
            QTimer.singleShot(0, lambda: self._on_test_done(ok, msg, platform, status_lbl, dot))

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
            QTimer.singleShot(0, lambda: self._on_sync_done(ok, count, err))
        )

    def _force_sync(self):
        self.sync_btn.setEnabled(False)
        self.sync_progress.show()
        self.sync_progress.setRange(0, 0)
        self.sync_status.setText("Syncing all platforms…")

        from app.services.sync_service import sync_all
        sync_all(done_cb=lambda total, errors:
                 QTimer.singleShot(0, lambda: self._on_all_sync_done(total, errors)))

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
            from app.services.updater_service import check_for_update
            available, latest = check_for_update()
            QTimer.singleShot(0, lambda: self._on_update_check(available, latest))

        threading.Thread(target=_check, daemon=True).start()

    def _on_update_check(self, available: bool, latest: str):
        self.update_btn.setEnabled(True)
        if available:
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
        from app.services.updater_service import get_latest_release, download_and_apply_update
        release = get_latest_release()
        if not release:
            QMessageBox.critical(self, "Update", "Could not fetch release info.")
            return
        assets = release.get("assets", [])
        zip_asset = next((a for a in assets if a["name"].endswith(".zip")), None)
        if not zip_asset:
            QMessageBox.critical(self, "Update", "No zip asset found in release.")
            return

        self.update_progress.show()
        self.update_progress.setValue(0)

        def _progress(pct):
            QTimer.singleShot(0, lambda: self.update_progress.setValue(pct))

        def _done(ok, err):
            QTimer.singleShot(0, lambda: (
                self.update_progress.hide(),
                QMessageBox.information(self, "Update", "Update applied. Restarting…") if ok
                else QMessageBox.critical(self, "Update Failed", str(err))
            ))

        download_and_apply_update(zip_asset["browser_download_url"],
                                  progress_cb=_progress, done_cb=_done)

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
                QTimer.singleShot(0, lambda: self._on_backup_done(ok, err))
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
