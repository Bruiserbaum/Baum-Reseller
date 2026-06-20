import os
import datetime
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QScrollArea, QMessageBox,
    QComboBox, QProgressBar, QFileDialog, QLineEdit,
)
from PySide6.QtCore import Qt, Signal
from app.utils.qt_thread import post_to_main

from app.database.models import get_setting, set_setting
from version import VERSION


class SettingsView(QWidget):
    # Emitted after a successful local import so the main window can refresh all views
    data_imported = Signal()

    def __init__(self):
        super().__init__()
        self._build_ui()
        self._load_settings()

    # ── Info button helper ────────────────────────────────────────────────

    def _make_info_btn(self, title: str, body: str) -> QPushButton:
        btn = QPushButton("ℹ")
        btn.setObjectName("infoButton")
        btn.setToolTip(title)
        btn.clicked.connect(lambda: QMessageBox.information(self, title, body))
        return btn

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

        # ── Platform Connections (moved to Sync tab) ──────────────────────
        conn_note = QLabel(
            "🔗  <b>Platform Connections</b> (Login, Import Session, Log Out, Sync) "
            "have moved to the <b>Sync</b> tab — so login and syncing are all in one place."
        )
        conn_note.setTextFormat(Qt.RichText)
        conn_note.setWordWrap(True)
        conn_note.setStyleSheet(
            "color: #cdd6f4; font-size: 12px; "
            "background: #252535; border-left: 4px solid #89b4fa; "
            "border-radius: 4px; padding: 12px;"
        )
        layout.addWidget(conn_note)

        # ── Anthropic API Key ─────────────────────────────────────────────
        ai_group = QGroupBox("Trending & AI Insights")
        ai_layout = QVBoxLayout(ai_group)

        ai_info_row = QHBoxLayout()
        ai_info_row.addStretch()
        ai_info_row.addWidget(self._make_info_btn(
            "About AI Insights",
            "Baum Reseller uses Claude AI (by Anthropic) to generate weekly resale-market "
            "trend reports.\n\n"
            "Your API key is stored securely in Windows Credential Manager and is never "
            "written to disk.\n\n"
            "Get a free key at: platform.anthropic.com → API Keys"
        ))
        ai_layout.addLayout(ai_info_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API Key:"))

        self._api_key_field = QLineEdit()
        self._api_key_field.setPlaceholderText("sk-ant-…  (paste your Anthropic API key)")
        self._api_key_field.setEchoMode(QLineEdit.Password)
        self._api_key_field.setMinimumWidth(340)
        key_row.addWidget(self._api_key_field, 1)

        self._key_show_btn = QPushButton("Show")
        self._key_show_btn.setFixedWidth(52)
        self._key_show_btn.setCheckable(True)
        self._key_show_btn.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self._key_show_btn)

        ai_layout.addLayout(key_row)

        btn_row = QHBoxLayout()
        save_key_btn = QPushButton("Save Key")
        save_key_btn.setObjectName("primaryButton")
        save_key_btn.clicked.connect(self._save_api_key)
        btn_row.addWidget(save_key_btn)

        test_key_btn = QPushButton("Test Key")
        test_key_btn.clicked.connect(self._test_api_key)
        btn_row.addWidget(test_key_btn)

        clear_key_btn = QPushButton("Clear Key")
        clear_key_btn.clicked.connect(self._clear_api_key)
        btn_row.addWidget(clear_key_btn)

        btn_row.addStretch()

        self._api_key_status = QLabel("")
        self._api_key_status.setStyleSheet("color: #a6adc8; font-size: 11px;")
        btn_row.addWidget(self._api_key_status)

        ai_layout.addLayout(btn_row)
        layout.addWidget(ai_group)

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

        # ── Google Drive Backup ───────────────────────────────────────────
        gdrive_group = QGroupBox("Google Drive Backup")
        gdrive_layout = QVBoxLayout(gdrive_group)

        gdrive_hdr = QHBoxLayout()
        self.gdrive_status = QLabel("Google Drive: Not connected")
        gdrive_hdr.addWidget(self.gdrive_status)
        gdrive_hdr.addStretch()
        connect_drive_btn = QPushButton("Connect Google Drive")
        connect_drive_btn.clicked.connect(self._connect_gdrive)
        gdrive_hdr.addWidget(connect_drive_btn)
        gdrive_hdr.addWidget(self._make_info_btn(
            "Google Drive Backup",
            "Requires a free Google Cloud OAuth2 credentials file.\n\n"
            "How to get it:\n"
            "  1. Go to console.cloud.google.com → APIs & Services → Credentials\n"
            "  2. Create Credentials → OAuth 2.0 Client ID → Desktop app\n"
            "  3. Click Download JSON\n\n"
            "Then click Connect Google Drive and select the downloaded JSON file."
        ))
        gdrive_layout.addLayout(gdrive_hdr)

        bk_row = QHBoxLayout()
        self.last_backup_label = QLabel("Last backup: Never")
        bk_row.addWidget(self.last_backup_label)
        bk_row.addStretch()
        backup_now_btn = QPushButton("Backup Now")
        backup_now_btn.setObjectName("primaryButton")
        backup_now_btn.clicked.connect(self._backup_now)
        bk_row.addWidget(backup_now_btn)
        gdrive_layout.addLayout(bk_row)

        sched_row = QHBoxLayout()
        sched_row.addWidget(QLabel("Auto-backup:"))
        self.backup_schedule = QComboBox()
        self.backup_schedule.addItems(["Disabled", "Daily", "Weekly", "Monthly"])
        self.backup_schedule.currentTextChanged.connect(
            lambda t: set_setting("backup_schedule", t)
        )
        sched_row.addWidget(self.backup_schedule)
        sched_row.addStretch()
        gdrive_layout.addLayout(sched_row)

        self.backup_status = QLabel("")
        gdrive_layout.addWidget(self.backup_status)

        layout.addWidget(gdrive_group)

        # ── Manual Backup ─────────────────────────────────────────────────
        manual_group = QGroupBox("Manual Backup")
        manual_layout = QVBoxLayout(manual_group)

        manual_hdr = QHBoxLayout()
        manual_hdr.addStretch()
        manual_hdr.addWidget(self._make_info_btn(
            "Manual Backup / Import",
            "Export Backup (.zip) — saves a complete snapshot of your database "
            "and settings to a zip file on your computer. Use this to move data "
            "to a new machine or keep an offline copy.\n\n"
            "Import Backup (.zip) — restores from a previously exported zip.\n\n"
            "WARNING: importing replaces ALL current data. This cannot be undone."
        ))
        manual_layout.addLayout(manual_hdr)

        io_row = QHBoxLayout()
        export_local_btn = QPushButton("Export Backup (.zip)")
        export_local_btn.clicked.connect(self._export_local)
        io_row.addWidget(export_local_btn)
        import_local_btn = QPushButton("Import Backup (.zip)")
        import_local_btn.clicked.connect(self._import_local)
        io_row.addWidget(import_local_btn)
        io_row.addStretch()
        manual_layout.addLayout(io_row)

        self.manual_status = QLabel("")
        manual_layout.addWidget(self.manual_status)

        layout.addWidget(manual_group)

        # ── Data location info (compact, bottom of page) ──────────────────
        data_dir = os.path.join(os.path.expanduser("~"), ".baum-reseller")
        data_row = QHBoxLayout()
        data_lbl = QLabel("🔒 Data storage location")
        data_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        data_row.addWidget(data_lbl)
        data_row.addWidget(self._make_info_btn(
            "Data Storage",
            f"Your data is stored at:\n\n    {data_dir}\n\n"
            f"This folder is NOT modified by app reinstalls or updates — "
            f"your inventory, settings, and credentials are always preserved."
        ))
        data_row.addStretch()
        layout.addLayout(data_row)
        layout.addStretch()
        scroll.setWidget(content)

    # ── Settings loading ──────────────────────────────────────────────────

    def _load_settings(self):
        # Anthropic API key — show masked version if stored
        from app.services.anthropic_key import get_key, masked
        stored = get_key()
        if stored:
            self._api_key_field.setPlaceholderText(f"Stored: {masked(stored)}")
            self._api_key_status.setText("✓ Key stored")
            self._api_key_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")

        sched = get_setting("backup_schedule", "Disabled")
        idx = self.backup_schedule.findText(sched)
        if idx >= 0:
            self.backup_schedule.setCurrentIndex(idx)

        last_bk = get_setting("last_backup_time", "Never")
        self.last_backup_label.setText(f"Last backup: {last_bk}")

        token = os.path.join(os.path.expanduser("~"), ".baum-reseller", "gdrive_token.pkl")
        if os.path.exists(token):
            self.gdrive_status.setText("Google Drive: Connected")

    # ── Anthropic API key ─────────────────────────────────────────────────

    def _toggle_key_visibility(self, visible: bool):
        self._api_key_field.setEchoMode(
            QLineEdit.Normal if visible else QLineEdit.Password
        )
        self._key_show_btn.setText("Hide" if visible else "Show")

    def _save_api_key(self):
        key = self._api_key_field.text().strip()
        if not key:
            QMessageBox.warning(self, "API Key", "Please paste your Anthropic API key first.")
            return
        if not key.startswith("sk-ant-"):
            reply = QMessageBox.question(
                self, "Unusual Key Format",
                "This doesn't look like an Anthropic key (expected 'sk-ant-…').\n"
                "Save anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        from app.services.anthropic_key import set_key, masked
        set_key(key)
        self._api_key_field.clear()
        self._api_key_field.setPlaceholderText(f"Stored: {masked(key)}")
        self._api_key_status.setText("✓ Key saved")
        self._api_key_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        QMessageBox.information(
            self, "API Key Saved",
            "Your Anthropic API key has been saved securely.\n\n"
            "Go to Trending and click ⟳ Refresh to generate AI-powered insights."
        )

    def _test_api_key(self):
        from app.services.anthropic_key import get_key
        key = self._api_key_field.text().strip() or get_key()
        if not key:
            QMessageBox.warning(self, "No Key", "No API key to test — save one first.")
            return

        self._api_key_status.setText("Testing…")
        self._api_key_status.setStyleSheet("color: #a6adc8; font-size: 11px;")

        def _run():
            from app.services.trending_service import test_claude_key
            ok, msg = test_claude_key(key)
            post_to_main(lambda: self._on_key_test_done(ok, msg))

        threading.Thread(target=_run, daemon=True).start()

    def _on_key_test_done(self, ok: bool, msg: str):
        if ok:
            self._api_key_status.setText(f"✓ {msg}")
            self._api_key_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        else:
            self._api_key_status.setText("✗ Test failed")
            self._api_key_status.setStyleSheet("color: #f38ba8; font-size: 11px;")
            QMessageBox.warning(self, "API Key Test Failed", msg)

    def _clear_api_key(self):
        reply = QMessageBox.question(
            self, "Clear API Key",
            "Remove the stored Anthropic API key?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from app.services.anthropic_key import clear_key
        clear_key()
        self._api_key_field.clear()
        self._api_key_field.setPlaceholderText("sk-ant-…  (paste your Anthropic API key)")
        self._api_key_status.setText("Key cleared")
        self._api_key_status.setStyleSheet("color: #a6adc8; font-size: 11px;")

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
                # Use a non-blocking status update — NOT a QMessageBox.
                # A modal dialog would block the main thread while the background
                # worker calls os._exit(0), causing the dialog to vanish mid-read.
                # The process exits ~1 s after this label appears; the installer
                # then installs and relaunches the app automatically.
                post_to_main(lambda: (
                    self.update_progress.hide(),
                    self.update_status.setText(
                        "✓ Installer started — app will close and reopen with the new version…"
                    ),
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
        import json as _json, shutil
        from app.services.backup_service import GDRIVE_CREDS_PATH, get_drive_service

        # ── Step 1: ensure the OAuth2 credentials file is present ────────
        if not os.path.exists(GDRIVE_CREDS_PATH):
            reply = QMessageBox.information(
                self,
                "Google Drive Setup",
                "To connect Google Drive you need an OAuth2 credentials file "
                "from <b>Google Cloud Console</b>.<br><br>"
                "<b>How to get it:</b><ol>"
                "<li>Go to <tt>console.cloud.google.com</tt> → APIs &amp; Services → Credentials</li>"
                "<li>Click <b>Create Credentials</b> → OAuth 2.0 Client ID</li>"
                "<li>Application type: <b>Desktop app</b></li>"
                "<li>Click <b>Download JSON</b></li></ol>"
                "Then click <b>OK</b> to browse for that downloaded JSON file.",
                QMessageBox.Ok | QMessageBox.Cancel,
            )
            if reply != QMessageBox.Ok:
                return

            src, _ = QFileDialog.getOpenFileName(
                self,
                "Select Google OAuth2 Credentials JSON",
                os.path.expanduser("~"),
                "JSON Files (*.json)",
            )
            if not src:
                return

            # Validate it looks like a real Google OAuth credentials file
            try:
                with open(src, "r", encoding="utf-8") as fh:
                    cred_data = _json.load(fh)
                if "installed" not in cred_data and "web" not in cred_data:
                    QMessageBox.warning(
                        self,
                        "Invalid File",
                        "This doesn't look like a Google OAuth2 credentials file.\n\n"
                        "The JSON must have an 'installed' or 'web' key at the top level.\n"
                        "Please download the correct file from Google Cloud Console → Credentials.",
                    )
                    return
            except Exception as exc:
                QMessageBox.critical(self, "Invalid File",
                                     f"Could not read the JSON file:\n{exc}")
                return

            os.makedirs(os.path.dirname(GDRIVE_CREDS_PATH), exist_ok=True)
            shutil.copy2(src, GDRIVE_CREDS_PATH)

        # ── Step 2: run the OAuth browser flow ────────────────────────────
        try:
            get_drive_service()
            self.gdrive_status.setText("Google Drive: Connected ✓")
            QMessageBox.information(self, "Google Drive", "Connected successfully!")
        except Exception as exc:
            QMessageBox.critical(self, "Google Drive",
                                 f"Connection failed:\n{exc}")

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
            self.data_imported.emit()   # tell main window to reload all views
            QMessageBox.information(
                self, "Import Complete",
                "✓ Data restored successfully — all views have been refreshed.\n\n"
                "Note: The following are NOT included in backups and must be\n"
                "re-entered on a new machine:\n"
                "  • Anthropic API key (Settings → Trending & AI Insights)\n"
                "  • Google Drive credentials (Settings → Connect Google Drive)\n"
                "  • Platform logins (Sync tab → Login)"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))
