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
        except Exception as e:
            msg = str(e)
        finally:
            if done_cb:
                from app.utils.qt_thread import post_to_main
                post_to_main(lambda: done_cb(ok, msg))

    threading.Thread(target=_worker, daemon=True).start()


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
        return False, (
            f"Could not read cookies automatically.\n\n"
            f"This usually means the app needs elevated permissions to decrypt "
            f"Chrome/Edge's cookie database.\n\n"
            f"Use 'Import from File' instead:\n"
            f"  1. Install the 'Get cookies.txt LOCALLY' Chrome extension\n"
            f"     (search Chrome Web Store, it's free)\n"
            f"  2. Go to {domain} while logged in\n"
            f"  3. Click the extension → Export cookies → Save the .txt file\n"
            f"  4. Click 'Import from File' and select that file\n\n"
            f"Technical detail: {detail}"
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


# ── Headless sync context ─────────────────────────────────────────────────

def headless_context(playwright, state_file: str):
    """
    Return (browser, context) for headless scraping using saved session cookies.
    Caller must call browser.close() when done.
    """
    browser = playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        storage_state=state_file,
        user_agent=_USER_AGENT,
    )
    return browser, ctx
