import os
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QFileDialog, QMessageBox, QFrame,
)
from PySide6.QtCore import Qt, Signal

from app.utils.qt_thread import post_to_main


class ImportView(QWidget):
    # Emitted after a successful import — connects to inventory.mark_dirty()
    import_completed = Signal()

    def __init__(self):
        super().__init__()
        self._csv_rows: list[dict] = []
        self._csv_headers: list[str] = []
        self._build_ui()

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

        # ── Title ─────────────────────────────────────────────────────────
        title = QLabel("Import")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # Info icon replaces the verbose description block
        _IMPORT_HELP = (
            "Import historical inventory and sales data from a CSV spreadsheet.\n\n"
            "Each row represents one listing. Download the template to see the exact "
            "column format, fill it in with your data, then import it below.\n\n"
            "Tip: the template includes example rows — delete them before importing "
            "your real data."
        )
        import_info_row = QHBoxLayout()
        import_info_row.addStretch()
        import_info_btn = QPushButton("ℹ")
        import_info_btn.setObjectName("infoButton")
        import_info_btn.setToolTip("How to use CSV import")
        import_info_btn.clicked.connect(
            lambda: QMessageBox.information(self, "About CSV Import", _IMPORT_HELP)
        )
        import_info_row.addWidget(import_info_btn)
        layout.addLayout(import_info_row)

        # ── Template section ──────────────────────────────────────────────
        tmpl_frame = QFrame()
        tmpl_frame.setObjectName("platformRow")
        tmpl_layout = QHBoxLayout(tmpl_frame)
        tmpl_layout.setContentsMargins(14, 14, 14, 14)

        tmpl_text = QVBoxLayout()
        tmpl_lbl = QLabel("CSV Template")
        tmpl_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        tmpl_text.addWidget(tmpl_lbl)
        tmpl_sub = QLabel(
            "Download a pre-filled template with all column headers and example rows. "
            "Opens directly in Excel, Google Sheets, or any spreadsheet app."
        )
        tmpl_sub.setWordWrap(True)
        tmpl_sub.setStyleSheet("color: #a6adc8; font-size: 11px;")
        tmpl_text.addWidget(tmpl_sub)
        tmpl_layout.addLayout(tmpl_text, 1)

        dl_btn = QPushButton("⬇  Download Template")
        dl_btn.setObjectName("primaryButton")
        dl_btn.setMinimumWidth(160)
        dl_btn.clicked.connect(self._download_template)
        tmpl_layout.addWidget(dl_btn)
        layout.addWidget(tmpl_frame)

        # ── Column reference ──────────────────────────────────────────────
        from app.services.import_service import TEMPLATE_COLUMNS
        cols_note = QLabel(
            "<b>Columns:</b> " + " &nbsp;·&nbsp; ".join(
                f"<code>{c}</code>" for c in TEMPLATE_COLUMNS
            ) + "<br><br>"
            "<b>platform</b> values: <code>ebay</code>, <code>mercari</code>, <code>poshmark</code><br>"
            "<b>status</b> values: <code>active</code>, <code>sold</code><br>"
            "If <code>listing_id</code> is blank a unique ID is generated automatically.<br>"
            "Sold rows also create a sale record when <code>sold_price</code> + <code>sale_date</code> are filled in."
        )
        cols_note.setWordWrap(True)
        cols_note.setTextFormat(Qt.RichText)
        cols_note.setStyleSheet(
            "background: #252535; border-radius: 6px; padding: 10px; "
            "color: #a6adc8; font-size: 11px;"
        )
        layout.addWidget(cols_note)

        # ── File picker ───────────────────────────────────────────────────
        file_row = QHBoxLayout()
        select_btn = QPushButton("Select CSV File…")
        select_btn.clicked.connect(self._select_file)
        file_row.addWidget(select_btn)

        self._file_lbl = QLabel("No file selected")
        self._file_lbl.setStyleSheet("color: #585b70; font-size: 12px;")
        file_row.addWidget(self._file_lbl, 1)
        layout.addLayout(file_row)

        # ── Preview table ─────────────────────────────────────────────────
        self._preview_table = QTableWidget(0, 0)
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._preview_table.hide()
        layout.addWidget(self._preview_table)

        self._preview_lbl = QLabel("")
        self._preview_lbl.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(self._preview_lbl)

        # ── Import button + progress ──────────────────────────────────────
        import_row = QHBoxLayout()
        self._import_btn = QPushButton("Import 0 Rows")
        self._import_btn.setObjectName("primaryButton")
        self._import_btn.setMinimumWidth(160)
        self._import_btn.setEnabled(False)
        self._import_btn.hide()
        self._import_btn.clicked.connect(self._run_import)
        import_row.addStretch()
        import_row.addWidget(self._import_btn)
        layout.addLayout(import_row)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._result_lbl = QLabel("")
        self._result_lbl.setWordWrap(True)
        layout.addWidget(self._result_lbl)

        layout.addStretch()
        scroll.setWidget(content)

    # ── Actions ───────────────────────────────────────────────────────────

    def _download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV Template",
            "baum_reseller_import_template.csv",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        try:
            from app.services.import_service import get_template_csv
            with open(path, "w", encoding="utf-8") as f:
                f.write(get_template_csv())
            QMessageBox.information(
                self, "Template Saved",
                f"Template saved to:\n{path}\n\n"
                "Open it in Excel or Google Sheets, fill in your data, "
                "then use 'Select CSV File…' to import it."
            )
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", str(e))

    def _select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Import CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        try:
            from app.services.import_service import parse_csv
            headers, rows = parse_csv(path)
            if not rows:
                QMessageBox.warning(self, "Empty File", "The selected CSV has no data rows.")
                return
            self._csv_headers = headers
            self._csv_rows = rows
            self._file_lbl.setText(
                f"{os.path.basename(path)}  —  {len(rows)} row(s), {len(headers)} column(s)"
            )
            self._populate_preview(headers, rows)
            self._import_btn.setText(f"Import {len(rows)} Row(s)")
            self._import_btn.setEnabled(True)
            self._import_btn.show()
            self._result_lbl.setText("")
        except Exception as e:
            QMessageBox.critical(self, "File Error", f"Could not read file:\n{e}")

    def _populate_preview(self, headers: list[str], rows: list[dict]):
        preview_rows = rows[:10]
        self._preview_table.setRowCount(len(preview_rows))
        self._preview_table.setColumnCount(len(headers))
        self._preview_table.setHorizontalHeaderLabels(headers)
        for r, row in enumerate(preview_rows):
            for c, col in enumerate(headers):
                item = QTableWidgetItem(str(row.get(col) or ""))
                item.setToolTip(col)
                self._preview_table.setItem(r, c, item)
        self._preview_table.show()
        shown = min(len(rows), 10)
        self._preview_lbl.setText(
            f"Preview: showing {shown} of {len(rows)} row(s)"
            + (" — scroll the table to see all columns" if len(headers) > 6 else "")
        )

    def _run_import(self):
        if not self._csv_rows:
            return
        n = len(self._csv_rows)
        reply = QMessageBox.question(
            self, "Confirm Import",
            f"Import {n} row(s) into the database?\n\n"
            "Existing listings with matching (platform, listing_id) will be updated. "
            "All other rows will create new records.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._import_btn.setEnabled(False)
        self._progress.show()
        self._progress.setMaximum(n)
        self._progress.setValue(0)
        self._result_lbl.setText("Importing…")

        rows = list(self._csv_rows)

        def _progress_cb(done: int, total: int):
            post_to_main(lambda: self._progress.setValue(done))

        def _worker():
            from app.services.import_service import import_rows
            imported, skipped, errors = import_rows(rows, progress_cb=_progress_cb)
            post_to_main(lambda: self._on_import_done(imported, skipped, errors))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_import_done(self, imported: int, skipped: int, errors: list[str]):
        self._progress.hide()
        self._import_btn.setEnabled(True)

        parts = [f"✓ Imported {imported} record(s)."]
        if skipped:
            parts.append(f"{skipped} row(s) skipped.")
        if errors:
            parts.append(f"{len(errors)} error(s):\n" + "\n".join(errors[:5]))
            if len(errors) > 5:
                parts.append(f"… and {len(errors) - 5} more.")

        msg = "  ".join(parts)
        self._result_lbl.setText(msg)
        self._result_lbl.setStyleSheet(
            "color: #a6e3a1;" if not errors else "color: #fab387;"
        )

        if errors:
            QMessageBox.warning(self, "Import Complete with Errors", "\n".join(parts))
        else:
            QMessageBox.information(self, "Import Complete",
                                    f"Successfully imported {imported} record(s).")

        if imported:
            self.import_completed.emit()   # tell inventory to refresh on next visit
