"""Non-destructive startup diagnostic for the actual frozen application."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _loaded_dlls():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    rows = {}
    for name in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "icuuc.dll"):
        handle = kernel.GetModuleHandleW(name)
        if handle:
            buffer = ctypes.create_unicode_buffer(32768)
            if kernel.GetModuleFileNameW(handle, buffer, len(buffer)):
                rows[name] = buffer.value
    return rows


def run_startup_check(host, report_path: str) -> int:
    """Create the real window and open checkup through Toolbox using temporary settings.

    No window is shown; scheduled actions, update downloads, tray registration,
    and cleanup are never started. Method replacements are plain Python
    functions because replacing Qt methods with MagicMock can crash Shiboken.
    """
    report = dict(success=False, version=host.CURRENT_VERSION, frozen=bool(getattr(sys, "frozen", False)))
    window = None
    replacements = {}
    try:
        from PySide6.QtCore import QCoreApplication, QEvent, qVersion
        app = host.QApplication.instance() or host.QApplication([sys.argv[0]])
        with tempfile.TemporaryDirectory(prefix="cleaner-startup-") as temporary:
            methods = {
                "_load_config_dir": lambda self: temporary,
                "_should_offer_legacy_migration": lambda self: False,
                "check_updates": lambda *args, **kwargs: None,
                "_download_language_pack_async": lambda *args, **kwargs: None,
                "_init_tray": lambda self: None,
            }
            for name, replacement in methods.items():
                replacements[name] = getattr(host.MainWindow, name)
                setattr(host.MainWindow, name, replacement)
            try:
                window = host.MainWindow()
                app.processEvents()
                if window.stackedWidget.currentWidget() is not window.pg_clean:
                    raise RuntimeError("Regular Cleanup is not the default page")
                toolbox = window._ensure_lazy_page("pg_toolbox")
                window.switchTo(toolbox)
                page = toolbox._show_checkup_tool()
                app.processEvents()
                if toolbox.stack.currentWidget() is not page:
                    raise RuntimeError("Toolbox checkup entry did not open")
                if page.objectName() != "checkupPage" or page.mode.count() != 2:
                    raise RuntimeError("Checkup navigation did not initialize")
                report.update(success=True, qt_version=qVersion(), checkup_views=page.mode.count(), loaded_dlls=_loaded_dlls())
            finally:
                if window is not None:
                    window.close()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
                for name, original in replacements.items():
                    setattr(host.MainWindow, name, original)
    except Exception:
        report.update(success=False, traceback=traceback.format_exc())
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["success"] else 1
