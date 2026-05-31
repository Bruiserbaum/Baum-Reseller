import sys
import os


def main():
    # Ensure data dirs exist before anything else
    base = os.path.join(os.path.expanduser("~"), ".baum-reseller")
    os.makedirs(os.path.join(base, "images"), exist_ok=True)

    from app.database.connection import init_db
    init_db()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Baum Reseller")
    app.setOrganizationName("Baum")

    from app.utils.qt_thread import init_bridge
    init_bridge()

    style_path = os.path.join(os.path.dirname(__file__), "assets", "style.qss")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
