"""
Thread-safe bridge for posting work back to the Qt main thread from any thread.

Usage (from any background thread):
    from app.utils.qt_thread import post_to_main
    post_to_main(lambda: my_widget.setText("done"))

The bridge QObject is created lazily on first use and must first be
initialised on the main thread via init_bridge() — called in main.py.
"""
from PySide6.QtCore import QObject, Signal, Qt

_bridge: "MainThreadBridge | None" = None


class MainThreadBridge(QObject):
    _invoke = Signal(object)

    def __init__(self):
        super().__init__()
        # QueuedConnection guarantees delivery to this object's thread (main)
        self._invoke.connect(self._run, Qt.QueuedConnection)

    def _run(self, fn):
        fn()

    def post(self, fn):
        self._invoke.emit(fn)


def init_bridge():
    """Call once from the main thread before any background threads start."""
    global _bridge
    if _bridge is None:
        _bridge = MainThreadBridge()


def post_to_main(fn):
    """Post fn() to run on the Qt main thread. Safe to call from any thread."""
    if _bridge is None:
        raise RuntimeError("qt_thread bridge not initialised — call init_bridge() first")
    _bridge.post(fn)
