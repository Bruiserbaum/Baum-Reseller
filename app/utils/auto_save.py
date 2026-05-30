from PySide6.QtCore import QTimer


class AutoSave:
    """Debounced auto-save: waits `delay_ms` after last change before calling `save_fn`."""

    def __init__(self, save_fn, delay_ms: int = 800):
        self._save_fn = save_fn
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._fire)
        self._dirty = False

    def mark_dirty(self):
        self._dirty = True
        self._timer.start()

    def flush(self):
        if self._dirty:
            self._timer.stop()
            self._fire()

    def _fire(self):
        if self._dirty:
            self._dirty = False
            self._save_fn()
