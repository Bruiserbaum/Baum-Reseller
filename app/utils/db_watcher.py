"""
Watches the active DB file for external modifications (e.g. Google Drive / OneDrive
delivering a version synced from another machine) and fires a callback so the UI can
refresh inventory without the user having to manually re-sync.

Usage:
    watcher = DbWatcher(on_change=lambda: inventory_view.reload())
    watcher.start()   # call once at startup
    watcher.stop()    # call on app close
"""

import os
import threading
import time


class DbWatcher:
    """
    Polls the DB file's mtime every `interval` seconds.  When an external write is
    detected (mtime newer than what this process last wrote), calls `on_change()`.

    We use polling rather than inotify/FSEvents because cloud sync clients (Google
    Drive, OneDrive) often update files by replacing them atomically — which can
    confuse event-based watchers — and polling is simpler cross-platform.
    """

    def __init__(self, on_change, interval: float = 10.0):
        self._on_change = on_change
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_known_mtime: float = 0.0
        self._suppress_until: float = 0.0  # ignore changes we caused ourselves

    def start(self):
        from app.database.connection import get_db_path
        path = get_db_path()
        try:
            self._last_known_mtime = os.path.getmtime(path)
        except OSError:
            self._last_known_mtime = 0.0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def suppress(self, seconds: float = 30.0):
        """Call before a write so the watcher ignores the resulting mtime bump."""
        self._suppress_until = time.monotonic() + seconds

    def _run(self):
        from app.database.connection import get_db_path
        while not self._stop_event.wait(self._interval):
            try:
                path = get_db_path()
                mtime = os.path.getmtime(path)
                if mtime > self._last_known_mtime:
                    self._last_known_mtime = mtime
                    if time.monotonic() > self._suppress_until:
                        try:
                            self._on_change()
                        except Exception:
                            pass
            except OSError:
                pass
