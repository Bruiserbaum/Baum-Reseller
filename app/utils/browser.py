"""
Platform browser utilities.

Login flow (new):
  1. open_in_system_browser(url) — opens the platform in the user's everyday
     browser using webbrowser.open(). No automation, no fingerprinting.
     Google SSO, 2FA, and all auth methods work exactly as normal.

  2. import_cookies_from_browser(domain, state_file) — reads the user's
     existing cookies for that domain from Chrome, Edge, or Firefox and
     saves them as a Playwright storage_state JSON. The user just needs to
     be already logged in.

Sync (headless):
  headless_context(playwright, state_file) — loads the saved cookies into
  a Playwright context for scraping without re-authenticating.
"""
import json
import os
import sys
import threading
import webbrowser

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── System-browser helpers ────────────────────────────────────────────────

def open_in_system_browser(url: str):
    """Open url in the user's default browser (Chrome/Edge/Firefox/etc.)."""
    webbrowser.open(url)


def import_cookies_from_browser(
    platform_domain: str,
    state_file: str,
    done_cb=None,
):
    """
    Read cookies for platform_domain from Chrome, Edge, or Firefox and save
    them as a Playwright storage_state JSON at state_file.

    Runs in a background thread. Calls done_cb(ok: bool, message: str).
    """
    def _worker():
        ok, msg = False, ""
        try:
            ok, msg = _do_import(platform_domain, state_file)

            if not ok and msg.startswith("_NEEDS_ELEVATION_:"):
                # First attempt failed with a permissions error — try UAC elevation
                elev_ok, elev_msg = import_cookies_elevated(platform_domain, state_file)
                if elev_ok:
                    ok, msg = True, elev_msg
                else:
                    # Elevated also failed — Chrome 127+ App-Bound Encryption
                    # prevents direct cookie access even as admin.
                    # Strip the sentinel and give clear instructions.
                    raw = elev_msg.removeprefix("_NEEDS_ELEVATION_:")
                    ok  = False
                    msg = (
                        "Chrome's App-Bound Encryption (Chrome 127+) prevents direct "
                        "cookie access even with admin rights.\n\n"
                        "Use 'Import from File' instead — it takes about 30 seconds:\n\n"
                        "  1. In Chrome, open the Web Store and install\n"
                        "     'Get cookies.txt LOCALLY' (free, search that name)\n"
                        "  2. Go to the platform website while logged in\n"
                        "  3. Click the extension icon → Export cookies → save the file\n"
                        "  4. Click 'Import from File...' in this app and select it\n\n"
                        f"Technical detail: {raw}"
                    )

        except Exception as e:
            msg = str(e)
        finally:
            if done_cb:
                from app.utils.qt_thread import post_to_main
                post_to_main(lambda: done_cb(ok, msg))

    threading.Thread(target=_worker, daemon=True).start()


_ADMIN_KEYWORDS = ("admin", "access is denied", "access denied",
                   "permission", "privilege", "key for cookie", "decrypt")


def _needs_elevation(error_text: str) -> bool:
    low = error_text.lower()
    return any(kw in low for kw in _ADMIN_KEYWORDS)


def import_cookies_elevated(domain: str, state_file: str) -> tuple[bool, str]:
    """
    Re-run this exe as admin (UAC prompt) to extract cookies that require
    elevated permissions. Runs silently — no window appears.
    Returns (ok, message) once the elevated process finishes.
    """
    import ctypes
    import tempfile
    import time

    result_path = tempfile.mktemp(prefix="baum_cookies_", suffix=".json")

    # Build the command: run the same executable with --cookie-extract args
    exe = sys.executable   # works for both PyInstaller bundle and plain Python

    if getattr(sys, "frozen", False):
        # PyInstaller bundle: sys.executable IS BaumReseller.exe
        args = f'--cookie-extract "{domain}" "{state_file}" "{result_path}"'
    else:
        # Running as a Python script
        script = os.path.abspath(sys.argv[0])
        args = f'"{script}" --cookie-extract "{domain}" "{state_file}" "{result_path}"'

    # ShellExecuteW with "runas" triggers the UAC prompt
    ret = ctypes.windll.shell32.ShellExecuteW(
        None,     # hwnd  (no parent window)
        "runas",  # verb  — requests elevation / UAC
        exe,      # program
        args,     # parameters
        None,     # working directory
        0,        # SW_HIDE  (no window)
    )

    if ret <= 32:
        return False, (
            "UAC elevation was cancelled or failed (code {ret}).\n"
            "Use 'Import from File' as an alternative."
        )

    # Poll for the result file written by the elevated process (up to 30 s)
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.4)
        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
                return result["ok"], result["msg"]
            except Exception as e:
                return False, f"Could not read extraction result: {e}"
            finally:
                try:
                    os.unlink(result_path)
                except Exception:
                    pass

    try:
        os.unlink(result_path)
    except Exception:
        pass
    return False, "Elevated process timed out. Try 'Import from File' instead."


def _do_import(domain: str, state_file: str) -> tuple[bool, str]:
    try:
        import browser_cookie3
    except ImportError:
        return False, (
            "browser-cookie3 is not installed.\n"
            "Run:  py -m pip install browser-cookie3"
        )

    extractors = [
        ("Chrome",  browser_cookie3.chrome),
        ("Edge",    browser_cookie3.edge),
        ("Firefox", browser_cookie3.firefox),
    ]

    cookies = []
    source = None
    errors = []

    for name, extract in extractors:
        try:
            jar = extract(domain_name=domain)
            found = [_convert_cookie(c) for c in jar]
            if found:
                cookies = found
                source = name
                break
        except Exception as e:
            errors.append(f"{name}: {e}")

    if not cookies:
        detail = "; ".join(errors) if errors else "no cookies found"
        # Return a special marker so the caller can try UAC elevation
        if _needs_elevation(detail):
            return False, f"_NEEDS_ELEVATION_:{detail}"
        return False, (
            f"No session cookies found for {domain}.\n\n"
            f"Make sure you are logged into {domain} in Chrome, Edge, or Firefox, "
            f"then click Import Session again.\n\n"
            f"If that keeps failing, use 'Import from File' with the "
            f"'Get cookies.txt LOCALLY' Chrome extension.\n\n"
            f"Detail: {detail}"
        )

    state = {"cookies": cookies, "origins": []}
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return True, f"Imported {len(cookies)} cookies from {source}."


def _convert_cookie(c) -> dict:
    """Convert a browser_cookie3 cookie to Playwright storage_state format."""
    domain = c.domain or ""
    if domain and not domain.startswith("."):
        domain = "." + domain
    cookie = {
        "name":     c.name,
        "value":    c.value,
        "domain":   domain,
        "path":     c.path or "/",
        "secure":   bool(c.secure),
        "httpOnly": False,
        "sameSite": "Lax",
    }
    if c.expires:
        cookie["expires"] = float(c.expires)
    return cookie


# ── Cookie file import ───────────────────────────────────────────────────

def import_cookies_from_file(file_path: str, state_file: str) -> tuple[bool, str]:
    """
    Import cookies from a file exported by a browser extension.

    Supported formats:
    • Netscape cookies.txt  — "Get cookies.txt LOCALLY" Chrome extension
      (tab-separated: domain  flag  path  secure  expires  name  value)
    • Cookie-Editor JSON    — https://cookie-editor.cgagnier.ca/
      (JSON array of {name, value, domain, path, secure, httpOnly, ...})

    No admin rights or browser decryption needed — just a plain file.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
    except Exception as e:
        return False, f"Could not read file: {e}"

    cookies = []

    # ── Try JSON (Cookie-Editor, EditThisCookie, etc.) ────────────────────
    stripped = content.lstrip("﻿")   # remove BOM if present
    if stripped.startswith("["):
        try:
            raw = json.loads(stripped)
            for c in raw:
                if not isinstance(c, dict):
                    continue
                domain = c.get("domain", "")
                if domain and not domain.startswith("."):
                    domain = "." + domain
                entry = {
                    "name":     str(c.get("name", "")),
                    "value":    str(c.get("value", "")),
                    "domain":   domain,
                    "path":     c.get("path", "/"),
                    "secure":   bool(c.get("secure", False)),
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "sameSite": c.get("sameSite", "Lax"),
                }
                exp = c.get("expirationDate") or c.get("expires")
                if exp:
                    try:
                        entry["expires"] = float(exp)
                    except (ValueError, TypeError):
                        pass
                cookies.append(entry)
        except json.JSONDecodeError:
            pass

    # ── Try Netscape cookies.txt ──────────────────────────────────────────
    if not cookies:
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expires, name, value = parts[:7]
            if domain and not domain.startswith("."):
                domain = "." + domain
            entry = {
                "name":     name,
                "value":    value,
                "domain":   domain,
                "path":     path or "/",
                "secure":   secure.upper() == "TRUE",
                "httpOnly": False,
                "sameSite": "Lax",
            }
            try:
                entry["expires"] = float(expires)
            except (ValueError, TypeError):
                pass
            cookies.append(entry)

    if not cookies:
        return False, (
            "Could not parse the cookie file.\n\n"
            "Make sure you exported in Netscape (.txt) format using "
            "'Get cookies.txt LOCALLY', or JSON format using 'Cookie-Editor'."
        )

    state = {"cookies": cookies, "origins": []}
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return True, f"Imported {len(cookies)} cookies from file."


# ── Browser installation helper ───────────────────────────────────────────

def chromium_is_installed() -> bool:
    """Return True if a Playwright Chromium binary is present in PLAYWRIGHT_BROWSERS_PATH."""
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


def ensure_playwright_browsers(progress_cb=None) -> tuple[bool, str]:
    """
    Install Playwright's Chromium browser into PLAYWRIGHT_BROWSERS_PATH.
    Safe to call multiple times — no-op if already installed.
    progress_cb(msg: str) is called with status text if provided.
    Returns (success, message).
    """
    import subprocess

    browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    env = os.environ.copy()

    if getattr(sys, "frozen", False):
        internal = os.path.join(os.path.dirname(sys.executable), "_internal")
        node_exe = os.path.join(internal, "playwright", "driver", "node.exe")
        cli_js   = os.path.join(internal, "playwright", "driver", "package", "cli.js")
        if not os.path.exists(node_exe):
            return False, f"Playwright driver not found at:\n{node_exe}"
        cmd = [node_exe, cli_js, "install", "chromium"]
    else:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

    if progress_cb:
        progress_cb("Downloading browser (100–200 MB, please wait)…")

    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=600
        )
    except subprocess.TimeoutExpired:
        return False, "Browser download timed out (10 min). Check your internet connection."
    except Exception as e:
        return False, str(e)

    if result.returncode == 0:
        return True, "Browser installed successfully."
    return False, (result.stderr or result.stdout or "Unknown error").strip()


# ── Headless sync context ─────────────────────────────────────────────────

def headless_context(playwright, state_file: str):
    """
    Return (browser, context) for headless scraping using saved session cookies.
    Caller must call browser.close() when done.
    Raises RuntimeError with a clear message if Chromium is not installed.
    """
    try:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except Exception as e:
        msg = str(e)
        if "executable" in msg.lower() or "doesn't exist" in msg.lower():
            raise RuntimeError(
                "Browser not installed.\n\n"
                "Go to the Sync tab and click '⬇ Install Browser' "
                "to download Chromium (100–200 MB, one-time setup)."
            ) from e
        raise
    ctx = browser.new_context(
        storage_state=state_file,
        user_agent=_USER_AGENT,
    )
    return browser, ctx
