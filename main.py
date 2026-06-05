import sys
import os


def main():
    # ── Silent cookie-extraction mode (runs as admin via UAC, no UI) ──────
    # When elevated via ShellExecuteW, this exits before Qt ever starts.
    if "--cookie-extract" in sys.argv:
        _run_cookie_extract()
        return

    # ── Normal startup ────────────────────────────────────────────────────
    base = os.path.join(os.path.expanduser("~"), ".baum-reseller")
    os.makedirs(os.path.join(base, "images"), exist_ok=True)

    from app.database.connection import init_db
    init_db()

    from PySide6.QtWidgets import QApplication
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


def _run_cookie_extract():
    """
    Elevated helper mode — extract browser cookies as admin and write result
    to a JSON file, then exit. No UI, no Qt, just pure extraction.

    Called by the main process via ShellExecuteW("runas", ...).
    Args: --cookie-extract <domain> <state_file> <result_file>
    """
    import json

    try:
        idx         = sys.argv.index("--cookie-extract")
        domain      = sys.argv[idx + 1]
        state_file  = sys.argv[idx + 2]
        result_file = sys.argv[idx + 3]
    except (ValueError, IndexError):
        return   # Malformed args — just exit silently

    try:
        from app.utils.browser import _do_import
        ok, msg = _do_import(domain, state_file)
        # Strip sentinel so the main process can distinguish "also failed elevated"
        # from a plain error without trying to elevate again infinitely.
        if not ok and msg.startswith("_NEEDS_ELEVATION_:"):
            msg = "_NEEDS_ELEVATION_:" + msg[len("_NEEDS_ELEVATION_:"):]
    except Exception as e:
        ok, msg = False, str(e)

    try:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump({"ok": ok, "msg": msg}, f)
    except Exception:
        pass


if __name__ == "__main__":
    main()
