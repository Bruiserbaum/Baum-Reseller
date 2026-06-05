import os
import datetime
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QScrollArea, QMessageBox,
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
            "Recommended: click 'Login (Browser)' — a browser window opens, log in normally "
            "(MFA, Google SSO, and captcha all work), session saves automatically.\n"
            "Alternative: log in to each platform in your normal browser, then click "
            "'Import Session'.\n"
            "If Import Session fails (Chrome 127+ App-Bound Encryption), use "
            "'Import from File' with the 'Get cookies.txt LOCALLY' Chrome extension."
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
            f"\U0001F512  Your data (database, credentials, browser sessions) is stored at:\n"
            f"    {data_dir}\n"
            f"    This folder is NOT modified by app reinstalls or updates — "
            "your settings are always preserved."
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
        label = PLATFORM_DISPLAY[platform]

        container = QFrame()
        container.setObjectName("platformRow")

        # Two sub-rows: status line + action buttons
        col = QVBoxLayout(container)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(4)

        # ── Row 1: name · dot · status · last-sync ────────────────────────
        top = QHBoxLayout()
        name_lbl = QLabel(label)
        name_lbl.setFixedWidth(90)
        top.addWidget(name_lbl)

        dot = QLabel("●")
        dot.setObjectName("statusDotUnknown")
        dot.setFixedWidth(20)
        top.addWidget(dot)

        status_lbl = QLabel("Not connected")
        status_lbl.setMinimumWidth(260)
        top.addWidget(status_lbl)

        last_lbl = QLabel("")
        last_lbl.setStyleSheet("color:#585b70; font-size:11px;")
        top.addWidget(last_lbl)
        top.addStretch()
        col.addLayout(top)

        # ── Row 2: action buttons ─────────────────────────────────────────
        btns = QHBoxLayout()

        login_btn = QPushButton("Login (Browser)")
        login_btn.setObjectName("primaryButton")
        login_btn.setToolTip(
            "Opens a Chromium browser window — log in however you like.\n"
            "MFA, Google SSO, and captcha all work normally.\n"
            "Session is saved automatically. No cookie decryption needed.\n\n"
            "Use this if 'Import Session' fails due to Chrome encryption."
        )
        login_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot, lb=login_btn:
            self._login_playwright(p, sl, sd, lb)
        )
        btns.addWidget(login_btn)

        open_btn = QPushButton("Open in Browser")
        open_btn.setToolTip(
            f"Opens {label} in your normal browser. Log in if needed, "
            "then click 'Import Session'."
        )
        open_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl:
            self._open_in_browser(p, sl)
        )
        btns.addWidget(open_btn)

        import_btn = QPushButton("Import Session")
        import_btn.setToolTip(
            "Reads your existing login cookies from Chrome/Edge/Firefox automatically.\n"
            "If this fails with a permissions or encryption error, use 'Login (Browser)' instead."
        )
        import_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot, ib=import_btn:
            self._import_session(p, sl, sd, ib)
        )
        btns.addWidget(import_btn)

        file_btn = QPushButton("Import from File…")
        file_btn.setToolTip(
            "Import cookies from a file exported by the 'Get cookies.txt LOCALLY' "
            "or 'Cookie-Editor' extension. No admin rights needed."
        )
        file_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot:
            self._import_from_file(p, sl, sd)
        )
        btns.addWidget(file_btn)

        btns.addStretch()

        test_btn = QPushButton("Test")
        test_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot:
            self._test_platform_async(p, sl, sd)
        )
        btns.addWidget(test_btn)

        logout_btn = QPushButton("Log Out")
        logout_btn.clicked.connect(
            lambda checked=False, p=platform, sl=status_lbl, sd=dot:
            self._logout_platform(p, sl, sd)
        )
        btns.addWidget(logout_btn)

        col.addLayout(btns)

        return {"widget": container, "status_label": status_lbl,
                "status_dot": dot, "last_sync": last_lbl}

    # ── Settings loading ──────────────────────────────────────────────────

    def _load_settings(self):
        for p, row in self._platform_rows.items():
            last_p = get_setting(f"last_sync_{p}", "")
            row["last_sync"].setText(f"Last synced: {last_p}" if last_p else "")
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

    # ── Login (Playwright browser) — the primary fix ──────────────────────

    def _login_playwright(self, platform: str, status_lbl: QLabel,
                          dot: QLabel, login_btn: QPushButton):
        """Open Playwright's own headed browser for login. Works with MFA/2FA."""
        svc = self._get_service(platform)
        if not hasattr(svc, "login"):
            QMessageBox.information(
                self, f"{PLATFORM_DISPLAY[platform]} Login",
                f"{PLATFORM_DISPLAY[platform]} uses 'Open in Browser' + 'Import Session'.\n"
                "Log in in your system browser, then click Import Session."
            )
            return

        login_btn.setEnabled(False)
        login_btn.setText("Browser opening…")
        status_lbl.setText("Waiting — log in and the app will detect it automatically…")
        self._set_dot(dot, "unknown")

        def _done(ok: bool, msg: str | None):
            def _update():
                login_btn.setEnabled(True)
                login_btn.setText("Login (Browser)")
                if ok:
                    self._set_dot(dot, "ok")
                    status_lbl.setText("Connected — session saved")
                    QMessageBox.information(
                        self,
                        f"{PLATFORM_DISPLAY[platform]} — Logged In",
                        f"Session saved successfully.\n\nYou can now click Sync to fetch your listings.",
                    )
                else:
                    self._set_dot(dot, "error")
                    status_lbl.setText("Login failed — see error")
                    QMessageBox.warning(
                        self, f"{PLATFORM_DISPLAY[platform]} — Login Failed",
                        str(msg or "Unknown error"),
                    )
            post_to_main(_update)

        svc.login(done_cb=_done)

    # ── Open in browser (system browser fallback) ─────────────────────────

    def _open_in_browser(self, platform: str, status_lbl: QLabel):
        svc = self._get_service(platform)
        svc.open_in_browser()
        status_lbl.setText(
            f"Opened in your browser — log in, then click Import Session"
        )

    # ── Import session ────────────────────────────────────────────────────

    def _import_session(self, platform: str, status_lbl: QLabel,
                        dot: QLabel, import_btn: QPushButton):
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

    def _import_from_file(self, platform: str, status_lbl: QLabel, dot: QLabel):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Import {PLATFORM_DISPLAY[platform]} Cookies",
            "",
            "Cookie Files (*.txt *.json);;All Files (*)"
        )
        if not path:
            return

        svc = self._get_service(platform)
        ok, msg = svc.import_from_file(path)
        if ok:
            self._set_dot(dot, "ok")
            status_lbl.setText("Connected — session imported from file")
            QMessageBox.information(
                self, f"{PLATFORM_DISPLAY[platform]} — Imported",
                f"✓ {msg}\n\nYou can now click Sync to fetch your listings."
            )
        else:
            self._set_dot(dot, "error")
            status_lbl.setText("Import failed")
            QMessageBox.warning(self, f"{PLATFORM_DISPLAY[platform]} — Import Failed", msg)

    def _logout_platform(self, platform: str, status_lbl: QLabel, dot: QLabel):
        svc = self._get_service(platform)
        if hasattr(svc, "clear_session"):
            svc.clear_session()
        self._set_dot(dot, "unknown")
        status_lbl.setText("Logged out — click Login (Browser) or Import Session to reconnect")

    # ── Test (runs in background thread) ─────────────────────────────────

    def _test_platform_async(self, platform: str, status_lbl: QLabel, dot: QLabel):
        status_lbl.setText("Testing…")
        self._set_dot(dot, "unknown")

        def _run():
            svc = self._get_service(platform)
            try:
                ok, msg = svc.test_connection()
            except Exception as e:
                ok, msg = False, str(e)
            post_to_main(lambda: self._on_test_done(ok, msg, platform, status_lbl, dot))

        threading.Thread(target=_run, daemon=True).start()

    def _on_test_done(self, ok: bool, msg: str, platform: str,
                      status_lbl: QLabel, dot: QLabel):
        self._set_dot(dot, "ok" if ok else "error")
        status_lbl.setText(msg)
        if ok:
            QMessageBox.information(
                self, f"{PLATFORM_DISPLAY[platform]} — Connection Test", f"✓ {msg}"
            )
        else:
            QMessageBox.warning(
                self, f"{PLATFORM_DISPLAY[platform]} — Connection Test", f"✗ {msg}"
            )

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
                post_to_main(lambda: self._on_update_check(available, latest, error))

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
        from app.services.updater_service import (
            get_latest_release, find_update_asset, download_and_apply_update
        )
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
                post_to_main(lambda: self._on_backup_done(ok, err))
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
