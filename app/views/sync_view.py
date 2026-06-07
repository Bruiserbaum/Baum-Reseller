import json
import os
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame, QScrollArea, QMessageBox,
    QDialog, QTextEdit, QDialogButtonBox, QApplication, QFileDialog,
)
from PySide6.QtCore import Qt, Signal

from app.database.models import get_setting
from app.utils.qt_thread import post_to_main

PLATFORM_DISPLAY = {"ebay": "eBay", "mercari": "Mercari", "poshmark": "Poshmark"}
PLATFORMS = ["ebay", "mercari", "poshmark"]


class SyncView(QWidget):
    # Emitted whenever any platform sync succeeds — connects to inventory.mark_dirty()
    sync_completed = Signal()

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
            row["status_lbl"].setText(
                "Connected — ready to sync" if connected else "Not connected"
            )
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

        # ── Header ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Sync")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        self._sync_all_btn = QPushButton("Force Sync All")
        self._sync_all_btn.setObjectName("primaryButton")
        self._sync_all_btn.clicked.connect(self._force_sync_all)
        header.addWidget(self._sync_all_btn)

        logs_btn = QPushButton("📋 Sync Logs")
        logs_btn.setToolTip("View detailed debug output from the last eBay / Mercari sync")
        logs_btn.clicked.connect(self._show_sync_logs)
        header.addWidget(logs_btn)
        layout.addLayout(header)

        # ── Progress + status ─────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        layout.addWidget(self._status_lbl)

        # ── Connection note ────────────────────────────────────────────────
        note = QLabel(
            "Recommended: click <b>Login (Browser)</b> — a browser window opens, log in normally "
            "(MFA, Google SSO, and captcha all work), session saves automatically.<br>"
            "Alternative: log in in your regular browser and click <b>Import Session</b>. "
            "If that fails (Chrome 127+ App-Bound Encryption), use <b>Import from File</b> "
            "with the <i>Get cookies.txt LOCALLY</i> Chrome extension."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(note)

        # ── Platform rows (connection + sync combined) ────────────────────
        for platform in PLATFORMS:
            row_widget = self._build_platform_row(platform)
            layout.addWidget(row_widget)

        layout.addStretch()
        scroll.setWidget(content)

        # Initial refresh
        self.refresh()

    def _build_platform_row(self, platform: str) -> QFrame:
        """
        Builds one platform section containing:
          • Row 1 — dot  name  status-label  last-synced
          • Row 2 — Login (Browser)  Open in Browser  Import Session  Import from File…
                    ‹stretch›  Test  Log Out  │  Sync ‹Platform›
        """
        label = PLATFORM_DISPLAY[platform]

        frame = QFrame()
        frame.setObjectName("platformRow")

        col = QVBoxLayout(frame)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(6)

        # ── Row 1: dot · name · status · last-sync ────────────────────────
        top = QHBoxLayout()

        dot = QLabel("●")
        dot.setObjectName("statusDotUnknown")
        dot.setFixedWidth(20)
        top.addWidget(dot)

        name_lbl = QLabel(label)
        name_lbl.setFixedWidth(90)
        name_lbl.setStyleSheet("font-weight: bold;")
        top.addWidget(name_lbl)

        status_lbl = QLabel("Not connected")
        status_lbl.setMinimumWidth(240)
        top.addWidget(status_lbl)

        last_lbl = QLabel("Never synced")
        last_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        top.addWidget(last_lbl)
        top.addStretch()
        col.addLayout(top)

        # ── Row 2: connection + sync buttons ──────────────────────────────
        btns = QHBoxLayout()
        btns.setSpacing(6)

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

        # Thin visual separator before the sync button
        sep = QLabel("|")
        sep.setStyleSheet("color: #45475a; padding: 0 4px;")
        btns.addWidget(sep)

        sync_btn = QPushButton(f"Sync {label}")
        sync_btn.clicked.connect(lambda checked=False, p=platform: self._sync_one(p))
        btns.addWidget(sync_btn)

        col.addLayout(btns)

        self._rows[platform] = {
            "dot":        dot,
            "status_lbl": status_lbl,
            "last_lbl":   last_lbl,
            "sync_btn":   sync_btn,
        }
        return frame

    # ── Connection helpers ────────────────────────────────────────────────

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

    def _login_playwright(self, platform: str, status_lbl: QLabel,
                          dot: QLabel, login_btn: QPushButton):
        """Open Playwright's headed browser for login (handles MFA/2FA/captcha)."""
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
                        "Session saved successfully.\n\n"
                        "You can now click Sync to fetch your listings.",
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

    def _open_in_browser(self, platform: str, status_lbl: QLabel):
        svc = self._get_service(platform)
        svc.open_in_browser()
        status_lbl.setText("Opened in your browser — log in, then click Import Session")

    def _import_session(self, platform: str, status_lbl: QLabel,
                        dot: QLabel, import_btn: QPushButton):
        import_btn.setEnabled(False)
        status_lbl.setText("Reading cookies from your browser…")
        self._set_dot(dot, "unknown")

        svc = self._get_service(platform)

        def _done(ok: bool, msg: str):
            post_to_main(
                lambda: self._on_import_done(ok, msg, platform, status_lbl, dot, import_btn)
            )

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
                self, f"{PLATFORM_DISPLAY[platform]} — Import Failed", msg
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
            QMessageBox.warning(
                self, f"{PLATFORM_DISPLAY[platform]} — Import Failed", msg
            )

    def _logout_platform(self, platform: str, status_lbl: QLabel, dot: QLabel):
        svc = self._get_service(platform)
        if hasattr(svc, "clear_session"):
            svc.clear_session()
        self._set_dot(dot, "unknown")
        status_lbl.setText(
            "Logged out — click 'Login (Browser)' or 'Import Session' to reconnect"
        )

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

    # ── Sync helpers ──────────────────────────────────────────────────────

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

            detail = self._debug_summary(platform)
            self._status_lbl.setText(
                f"Synced {count} listing(s) from {PLATFORM_DISPLAY[platform]}.{detail}"
            )
            self.sync_completed.emit()
        else:
            self._status_lbl.setText(f"Sync error: {err}")
            QMessageBox.warning(self, "Sync Error", f"Sync failed:\n\n{err}")

    @staticmethod
    def _debug_summary(platform: str) -> str:
        """Return a short inline summary from the platform's debug log file."""
        path = os.path.join(
            os.path.expanduser("~"), ".baum-reseller", f"debug_{platform}_sync.json"
        )
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            xhr  = d.get("xhr_responses_captured", "?")
            xitm = d.get("xhr_items_parsed", "?")
            dom  = d.get("dom_active_items", 0) + d.get("dom_sold_items", 0)
            return f"  [XHR: {xhr} responses → {xitm} items | DOM: {dom} items]"
        except Exception:
            return ""

    def _show_sync_logs(self):
        """Show a dialog with detailed debug output from the last eBay / Mercari sync."""
        _DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller")
        sections: list[str] = []

        for platform in ("ebay", "mercari"):
            path = os.path.join(_DIR, f"debug_{platform}_sync.json")
            if not os.path.exists(path):
                sections.append(f"── {platform.upper()} ──\nNo log yet — run a sync first.\n")
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)

                urls = "\n".join(f"  {u}" for u in d.get("xhr_urls", [])[:20]) or "  (none)"
                keys = "\n".join(
                    f"  response {i+1}: {k}"
                    for i, k in enumerate(d.get("xhr_body_top_keys", []))
                ) or "  (none)"
                sample = json.dumps(d.get("sample_items", []), indent=4)

                sections.append(
                    f"── {platform.upper()} — {d.get('timestamp', '?')} ──\n"
                    f"XHR responses captured : {d.get('xhr_responses_captured', 0)}\n"
                    f"Items parsed from XHR  : {d.get('xhr_items_parsed', 0)}\n"
                    f"Items from DOM (active): {d.get('dom_active_items', 0)}\n"
                    f"Items from DOM (sold)  : {d.get('dom_sold_items', 0)}\n"
                    f"Total returned to sync : {d.get('total_returned', 0)}\n"
                    f"\nXHR URLs captured:\n{urls}\n"
                    f"\nXHR response body keys:\n{keys}\n"
                    f"\nSample items returned:\n{sample}\n"
                )
            except Exception as exc:
                sections.append(f"── {platform.upper()} ──\nError reading log: {exc}\n")

        body = "\n\n".join(sections)

        dlg = QDialog(self)
        dlg.setWindowTitle("Sync Debug Logs")
        dlg.resize(760, 540)
        layout = QVBoxLayout(dlg)

        lbl = QLabel(
            "This shows exactly what the last sync captured from each platform. "
            "Share this with support if listings aren't appearing."
        )
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #a6adc8; font-size: 11px; margin-bottom: 6px;")
        layout.addWidget(lbl)

        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(body)
        te.setFontFamily("Consolas")
        te.setFontPointSize(9)
        layout.addWidget(te)

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        copy_btn = btns.addButton("📋 Copy to Clipboard", QDialogButtonBox.ActionRole)
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(te.toPlainText())
        )
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)
        dlg.exec()

    def _on_all_done(self, total: int, errors: list):
        self._set_busy(False)
        self.refresh()
        self.sync_completed.emit()
        if errors:
            self._status_lbl.setText(
                f"Sync complete: {total} listing(s). {len(errors)} error(s)."
            )
            QMessageBox.warning(
                self, "Sync Complete with Errors",
                f"{total} listing(s) synced.\n\nErrors:\n" + "\n".join(errors)
            )
        else:
            self._status_lbl.setText(f"All platforms synced — {total} listing(s) total.")
