import sys
import os

# ── Playwright browser path (MUST be set before any Playwright import) ────────
# Frozen (installed) builds: browsers are pre-bundled inside _internal by CI.
# Development builds: browsers live in the user profile (downloaded on demand).
if getattr(sys, "frozen", False):
    # Browsers shipped alongside the exe inside _internal/playwright-browsers/
    _PLAYWRIGHT_BROWSERS = os.path.join(
        os.path.dirname(sys.executable), "_internal", "playwright-browsers"
    )
    # Fallback: if somehow browsers are missing from the bundle, use the user
    # profile so the background install can still write somewhere writable.
    if not os.path.isdir(_PLAYWRIGHT_BROWSERS):
        _PLAYWRIGHT_BROWSERS = os.path.join(
            os.path.expanduser("~"), ".baum-reseller", "playwright-browsers"
        )
else:
    _PLAYWRIGHT_BROWSERS = os.path.join(
        os.path.expanduser("~"), ".baum-reseller", "playwright-browsers"
    )
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _PLAYWRIGHT_BROWSERS


def main():
    # ── Silent cookie-extraction mode (runs as admin via UAC, no UI) ──────
    if "--cookie-extract" in sys.argv:
        _run_cookie_extract()
        return

    # ── Normal startup ────────────────────────────────────────────────────
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

    # ── Window / taskbar icon ──────────────────────────────────────────────
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    from app.utils.qt_thread import init_bridge
    init_bridge()

    style_path = os.path.join(os.path.dirname(__file__), "assets", "style.qss")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()

    # ── Playwright browser check (background, silent) ─────────────────────
    # Install Chromium into ~/.baum-reseller/playwright-browsers if missing.
    # Runs silently; user only sees an error if they try to sync and it failed.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(4_000, _ensure_playwright_browsers_bg)

    sys.exit(app.exec())


def _ensure_playwright_browsers_bg():
    """
    Background Playwright browser install — called once at startup.
    Silently installs Chromium into PLAYWRIGHT_BROWSERS_PATH if it is missing.
    """
    import threading

    def _worker():
        try:
            if _chromium_installed():
                return
            _install_playwright_browsers()
        except Exception:
            pass  # Errors will surface when the user tries to sync

    threading.Thread(target=_worker, daemon=True).start()


def _chromium_installed() -> bool:
    """Return True if a Playwright Chromium binary exists in the browsers dir."""
    import glob
    browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if not browsers_dir or not os.path.isdir(browsers_dir):
        return False
    patterns = [
        os.path.join(browsers_dir, "chromium*", "**", "chrome.exe"),
        os.path.join(browsers_dir, "chromium*", "**", "chrome-headless-shell.exe"),
        os.path.join(browsers_dir, "chromium*", "**", "chromium.exe"),
    ]
    return any(glob.glob(p, recursive=True) for p in patterns)


def _install_playwright_browsers() -> tuple[bool, str]:
    """Run `playwright install chromium` and return (success, message)."""
    import subprocess

    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = _PLAYWRIGHT_BROWSERS

    if getattr(sys, "frozen", False):
        # PyInstaller bundle: use the playwright.exe driver bundled in _internal
        internal = os.path.join(os.path.dirname(sys.executable), "_internal")
        driver = os.path.join(internal, "playwright", "driver", "playwright.exe")
        if not os.path.exists(driver):
            return False, f"Playwright driver not found: {driver}"
        cmd = [driver, "install", "chromium"]
    else:
        # Development: use the installed playwright package
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode == 0:
        return True, "Chromium installed."
    return False, (result.stderr or result.stdout or "Unknown error").strip()


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
