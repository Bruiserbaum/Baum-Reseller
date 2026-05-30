from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QDialog, QDialogButtonBox
)
from PySide6.QtCore import Qt, Signal

from app.services.notification_service import (
    get_active_notifications, dismiss_notification, dismiss_all,
    mark_shipped, mark_found, mark_missing
)

TYPE_ICONS = {
    "unshipped":    ("📦", "#fab387"),   # orange
    "still_listed": ("⚠️",  "#f9e2af"),   # yellow
    "missing":      ("❓", "#f38ba8"),   # red
}
TYPE_LABELS = {
    "unshipped":    "Unshipped Sale",
    "still_listed": "Still Listed",
    "missing":      "Missing Item",
}


class NotificationsView(QWidget):
    """Full alerts page — shown when user clicks the Alerts nav item."""

    badge_changed = Signal(int)   # emits new unread count

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Alerts")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        dismiss_all_btn = QPushButton("Dismiss All")
        dismiss_all_btn.clicked.connect(self._dismiss_all)
        header.addWidget(dismiss_all_btn)
        layout.addLayout(header)

        # Scroll area for notification cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_container)
        layout.addWidget(scroll, 1)

        self.empty_label = QLabel("No active alerts — all clear!")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_label)
        self.empty_label.hide()

    def refresh(self):
        # Clear existing cards (keep the stretch at end)
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        notifications = get_active_notifications()
        self.empty_label.setVisible(len(notifications) == 0)
        self.cards_container.setVisible(len(notifications) > 0)

        for n in notifications:
            card = self._make_card(n)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        from app.services.notification_service import get_unread_count
        self.badge_changed.emit(get_unread_count())

    def _make_card(self, n: dict) -> QFrame:
        ntype = n.get("type", "")
        icon, color = TYPE_ICONS.get(ntype, ("ℹ️", "#89b4fa"))
        label = TYPE_LABELS.get(ntype, ntype.replace("_", " ").title())

        card = QFrame()
        card.setObjectName("notificationCard")
        card.setStyleSheet(
            f"QFrame#notificationCard {{ border-left: 4px solid {color}; "
            f"background: #252535; border-radius: 6px; }}"
        )
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 12, 12)

        # Icon + text
        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(24)
        row.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        type_lbl = QLabel(label)
        type_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 11px;")
        text_col.addWidget(type_lbl)
        msg_lbl = QLabel(n.get("message", ""))
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("color: #cdd6f4;")
        text_col.addWidget(msg_lbl)
        ts = n.get("created_at", "")[:16]
        ts_lbl = QLabel(ts)
        ts_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        text_col.addWidget(ts_lbl)
        row.addLayout(text_col, 1)

        # Action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        if ntype == "unshipped" and n.get("sale_id"):
            ship_btn = QPushButton("Mark Shipped")
            ship_btn.setObjectName("primaryButton")
            ship_btn.clicked.connect(lambda _, sid=n["sale_id"]: self._mark_shipped(sid))
            btn_col.addWidget(ship_btn)

        if ntype == "still_listed" and n.get("item_id"):
            view_btn = QPushButton("View Item")
            view_btn.clicked.connect(lambda _, iid=n["item_id"]: self._open_item(iid))
            btn_col.addWidget(view_btn)

        if ntype == "missing" and n.get("item_id"):
            found_btn = QPushButton("Mark Found")
            found_btn.setObjectName("primaryButton")
            found_btn.clicked.connect(lambda _, iid=n["item_id"]: self._mark_found(iid))
            btn_col.addWidget(found_btn)

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(lambda _, nid=n["id"]: self._dismiss(nid))
        btn_col.addWidget(dismiss_btn)

        row.addLayout(btn_col)
        return card

    # ── Actions ───────────────────────────────────────────────────────────

    def _dismiss(self, notif_id: int):
        dismiss_notification(notif_id)
        self.refresh()

    def _dismiss_all(self):
        dismiss_all()
        self.refresh()

    def _mark_shipped(self, sale_id: int):
        dlg = ShipDialog(self)
        if dlg.exec():
            mark_shipped(sale_id, tracking=dlg.tracking)
            self.refresh()

    def _mark_found(self, item_id: int):
        mark_found(item_id)
        self.refresh()

    def _open_item(self, item_id: int):
        from app.views.item_detail_view import ItemDetailDialog
        dlg = ItemDetailDialog(item_id, parent=self)
        if dlg.exec():
            self.refresh()


class ShipDialog(QDialog):
    """Simple dialog to optionally enter a tracking number when marking shipped."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mark as Shipped")
        self.setFixedSize(340, 140)
        self.tracking = ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tracking number (optional):"))
        self.tracking_edit = QLineEdit()
        self.tracking_edit.setPlaceholderText("e.g. 9400111899223450827315")
        layout.addWidget(self.tracking_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        self.tracking = self.tracking_edit.text().strip()
        self.accept()
