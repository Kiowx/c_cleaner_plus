# -*- coding: utf-8 -*-
"""
C盘强力清理工具 v0.3.6
PySide6 + PySide6-Fluent-Widgets (Fluent2 UI)
包含：常规清理(支持拖拽排序与自定义规则)、大文件扫描、重复文件、空文件夹、无效快捷方式等
"""

import os, sys, time, ctypes, threading, subprocess, queue, json, hashlib, winreg, re, heapq, tempfile
import urllib.request
import webbrowser
from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QObject, QPoint, QMetaObject, Slot, QFileInfo, QSize, QTimer
from PySide6.QtGui import QFont, QIcon, QColor, QPainter, QDrag, QPixmap, QRegion, QTextCursor
from qfluentwidgets import isDarkTheme, themeColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QAbstractItemView, QTableWidgetItem, QStyledItemDelegate,
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QFileIconProvider, QFileDialog
)

from qfluentwidgets import (
    FluentIcon as FIF,
    setTheme, Theme, setThemeColor, setFontFamilies, setFont,
    NavigationItemPosition, FluentWindow,
    PushButton, PrimaryPushButton, ComboBox, SwitchButton,
    CheckBox, SpinBox, ProgressBar,
    TitleLabel, CaptionLabel, StrongBodyLabel,
    IconWidget, TableWidget, TextEdit, CardWidget,
    RoundMenu, Action, MessageBox, InfoBar, InfoBarPosition, ScrollArea,
    SearchLineEdit, MessageBoxBase, LineEdit, ToolButton
)

# ══════════════════════════════════════════════════════════
#  版本与更新配置
# ══════════════════════════════════════════════════════════
CURRENT_VERSION = "0.3.6"
UPDATE_JSON_URL = "https://gitee.com/kio0/c_cleaner_plus/raw/master/update.json"

from qfluentwidgets.components.widgets.table_view import TableItemDelegate

SESSION_LOG_MAX_LINES = 4000
_session_log_lines = []
_session_log_lock = threading.Lock()
_sampled_error_counts = {}
_sampled_error_lock = threading.Lock()

def resource_path(relative_path):
    if getattr(sys, '_MEIPASS', None): return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

def append_session_log_line(text):
    line = str(text or "").rstrip()
    if not line:
        return
    with _session_log_lock:
        _session_log_lines.append(line)
        overflow = len(_session_log_lines) - SESSION_LOG_MAX_LINES
        if overflow > 0:
            del _session_log_lines[:overflow]

def get_session_log_text():
    with _session_log_lock:
        return "\n".join(_session_log_lines)

def format_exception_text(e):
    return f"{type(e).__name__}: {e}"

def log_background_error(context, e):
    line = f"[{time.strftime('%H:%M:%S')}] [{context}] {format_exception_text(e)}"
    append_session_log_line(line)
    print(line, file=sys.stderr)

def log_sampled_background_error(context, e, limit=6):
    key = str(context or "").strip() or "后台异常"
    with _sampled_error_lock:
        count = _sampled_error_counts.get(key, 0)
        _sampled_error_counts[key] = count + 1
        should_log = count < max(1, int(limit))
    if should_log:
        log_background_error(key, e)

def append_error_sample(errors, message, limit=8):
    if len(errors) < limit:
        errors.append(message)

def emit_error_summary(log_fn, prefix, errors, total_count):
    for msg in errors:
        log_fn(f"[{prefix}] {msg}")
    extra = max(0, int(total_count or 0) - len(errors))
    if extra > 0:
        log_fn(f"[{prefix}] 另有 {extra} 条异常未展开")

def write_text_file_atomic(path, text, encoding="utf-8"):
    target = os.path.abspath(os.path.expandvars(path))
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)

    fd = None
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".tmp", dir=parent or None, text=True)
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            fd = None
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

def write_json_file_atomic(path, payload, ensure_ascii=False, indent=2):
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
    write_text_file_atomic(path, text, encoding="utf-8")

def _normalize_version_text(version):
    if not version:
        return ""
    return str(version).strip().lstrip("vV")

def _is_prerelease(version):
    v = _normalize_version_text(version).lower()
    return bool(re.search(r"(alpha|beta|rc|test)", v))

def _version_key(version):
    v = _normalize_version_text(version).lower()
    if not v:
        return ((0, 0, 0), -1, 0)

    base_part, sep, pre_part = v.partition("-")
    nums = [int(x) for x in re.findall(r"\d+", base_part)]
    while len(nums) < 3:
        nums.append(0)
    nums = tuple(nums[:3])

    if not sep:
        return (nums, 3, 0)  # 稳定版权重最高

    pre = pre_part.strip()
    n_match = re.search(r"(\d+)", pre)
    n = int(n_match.group(1)) if n_match else 0
    if "alpha" in pre:
        rank = 0
    elif "beta" in pre:
        rank = 1
    elif "rc" in pre:
        rank = 2
    else:
        rank = 0
    return (nums, rank, n)

def _extract_relaxed_json_string(text, key):
    pattern = rf'"{re.escape(key)}"\s*:\s*"'
    m = re.search(pattern, text, re.S)
    if not m:
        return None

    i = m.end()
    buf = []
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            tail = text[i + 1:]
            if re.match(r"\s*(,|\})", tail, re.S):
                raw = "".join(buf)
                try:
                    return json.loads(f'"{raw}"')
                except Exception:
                    return raw.replace("\\n", "\n").replace('\\"', '"')
            # 宽松模式：把未转义的内部引号视为正文内容
            buf.append('\\"')
            i += 1
            continue
        buf.append(ch)
        i += 1
    return None

def _extract_relaxed_json_bool(text, key):
    m = re.search(rf'"{re.escape(key)}"\s*:\s*(true|false)', text, re.I | re.S)
    if not m:
        return None
    return m.group(1).lower() == "true"

def _load_update_payload(text):
    try:
        return json.loads(text)
    except Exception:
        # 兼容 update.json 中 changelog 混入未转义双引号的情况
        fallback = {}
        for key in ("version", "tag", "name", "url", "download_url", "download", "changelog", "notes", "desc"):
            val = _extract_relaxed_json_string(text, key)
            if val is not None:
                fallback[key] = val
        prerelease = _extract_relaxed_json_bool(text, "prerelease")
        if prerelease is not None:
            fallback["prerelease"] = prerelease
        return fallback if fallback else None

class FluentOnlyCheckDelegate(TableItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipping(True)
        painter.setClipRect(option.rect)
        option.rect.adjust(0, self.margin, 0, -self.margin)

        from qfluentwidgets.common.style_sheet import isDarkTheme
        isHover = self.hoverRow == index.row()
        isPressed = self.pressedRow == index.row()
        isAlternate = index.row() % 2 == 0 and self.parent().alternatingRowColors()
        isDark = isDarkTheme()
        c = 255 if isDark else 0
        alpha = 0
        if index.row() not in self.selectedRows:
            if isPressed: alpha = 9 if isDark else 6
            elif isHover: alpha = 12
            elif isAlternate: alpha = 5
        else:
            if isPressed: alpha = 15 if isDark else 9
            elif isHover: alpha = 25
            else: alpha = 17

        if index.data(Qt.ItemDataRole.BackgroundRole): painter.setBrush(index.data(Qt.ItemDataRole.BackgroundRole))
        else: painter.setBrush(QColor(c, c, c, alpha))
        self._drawBackground(painter, option, index)

        if (index.row() in self.selectedRows and index.column() == 0 and self.parent().horizontalScrollBar().value() == 0):
            self._drawIndicator(painter, option, index)

        if index.data(Qt.ItemDataRole.CheckStateRole) is not None:
            self._drawCheckBox(painter, option, index)

        painter.restore()
        model = index.model()
        orig_check = model.data(index, Qt.ItemDataRole.CheckStateRole)
        if orig_check is not None: model.setData(index, None, Qt.ItemDataRole.CheckStateRole)
        QStyledItemDelegate.paint(self, painter, option, index)
        if orig_check is not None: model.setData(index, orig_check, Qt.ItemDataRole.CheckStateRole)


class LeftAlignedPushButton(PushButton):
    """Keep Fluent button style, but render text left-aligned."""
    def __init__(self, text="", parent=None):
        try:
            super().__init__(parent=parent)
        except TypeError:
            super().__init__("", parent)
        self._display_text = ""
        self.setText(text)

    def setText(self, text):
        self._display_text = text or ""
        super().setText("")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._display_text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(self.palette().buttonText().color())
        rect = self.rect().adjusted(12, 0, -12, 0)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._display_text)


class SizeTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            left = self.data(Qt.ItemDataRole.UserRole)
            right = other.data(Qt.ItemDataRole.UserRole)
            if left is not None and right is not None:
                try:
                    return int(left) < int(right)
                except Exception:
                    pass
        return super().__lt__(other)

# ══════════════════════════════════════════════════════════
#  支持完美拖拽排序的 TableWidget
# ══════════════════════════════════════════════════════════
class DragSortTableWidget(TableWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def startDrag(self, supportedActions):
            row = self.currentRow()
            if row == -1: 
                return

            rect = self.visualRect(self.model().index(row, 0))
            drag_width = min(self.viewport().width(), 550) 
            rect.setWidth(drag_width)
            
            pixmap = QPixmap(rect.size())
            pixmap.fill(Qt.GlobalColor.transparent)
            
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            bg_color = QColor(43, 43, 43, 230) if isDarkTheme() else QColor(255, 255, 255, 230)
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg_color)
            painter.drawRoundedRect(pixmap.rect(), 6, 6)
            
            painter.setClipRect(pixmap.rect())
            self.viewport().render(painter, QPoint(0, 0), QRegion(rect))
            
            painter.setPen(themeColor())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(0, 0, pixmap.width() - 1, pixmap.height() - 1, 6, 6)
            painter.end()

            drag = QDrag(self)
            drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(40, pixmap.height() // 2))
            drag.exec(supportedActions)

    def dropEvent(self, event):
        if event.source() != self:
            super().dropEvent(event)
            return

        source_row = self.currentRow()
        if source_row == -1: 
            event.ignore()
            return

        try: pos = event.position().toPoint()
        except AttributeError: pos = event.pos()

        target_index = self.indexAt(pos)
        if not target_index.isValid():
            target_row = self.rowCount()
        else:
            target_row = target_index.row()
            rect = self.visualRect(target_index)
            if pos.y() > rect.center().y(): target_row += 1

        if source_row == target_row or source_row + 1 == target_row:
            event.ignore(); return

        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

        self.insertRow(target_row)
        insert_source = source_row if target_row > source_row else source_row + 1
            
        for col in range(self.columnCount()):
            item = self.takeItem(insert_source, col)
            if item: self.setItem(target_row, col, item)
        
        self.removeRow(insert_source)
        self.selectRow(target_row if target_row < source_row else target_row - 1)

# ══════════════════════════════════════════════════════════
#  Windows API / 工具
# ══════════════════════════════════════════════════════════
FOF_ALLOWUNDO = 0x0040; FOF_NOCONFIRMATION = 0x0010; FOF_SILENT = 0x0004; FOF_NOERRORUI = 0x0400

class SHFILEOPSTRUCT(ctypes.Structure):
    _fields_ = [("hwnd",ctypes.c_void_p),("wFunc",ctypes.c_uint),("pFrom",ctypes.c_wchar_p),("pTo",ctypes.c_wchar_p),
                ("fFlags",ctypes.c_ushort),("fAnyOperationsAborted",ctypes.c_int),("hNameMappings",ctypes.c_void_p),("lpszProgressTitle",ctypes.c_wchar_p)]

def send_to_recycle_bin(path):
    op=SHFILEOPSTRUCT(); op.hwnd=None; op.wFunc=0x0003; op.pFrom=path+"\0\0"; op.pTo=None
    op.fFlags=FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT|FOF_NOERRORUI
    op.fAnyOperationsAborted=0; op.hNameMappings=None; op.lpszProgressTitle=None
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))==0 and op.fAnyOperationsAborted==0

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()!=0
    except Exception: return False

def human_size(n):
    s=float(n)
    for u in ("B","KB","MB","GB","TB"):
        if s<1024 or u=="TB": return f"{s:.2f} {u}"
        s/=1024
    return f"{n} B"

def safe_getsize(p):
    try: return os.path.getsize(p)
    except Exception: return 0

def dir_size(path, stop_flag=None):
    t=0
    for r,ds,fs in os.walk(path,topdown=True):
        if stop_flag is not None and stop_flag.is_set():
            break
        ds[:]=[d for d in ds if not os.path.islink(os.path.join(r,d))]
        for f in fs:
            if stop_flag is not None and stop_flag.is_set():
                break
            t+=safe_getsize(os.path.join(r,f))
    return t

def estimate_rule_size(entry, stop_flag=None):
    import fnmatch

    parsed = parse_rule_entry(entry)
    if not parsed:
        return 0

    nm, pa, tp, _, nt, _, pattern = parsed
    _ = nm
    if stop_flag is not None and stop_flag.is_set():
        return 0

    try:
        if tp == "dir":
            target = expand_env(pa)
            return dir_size(target, stop_flag=stop_flag) if os.path.isdir(target) else 0
        if tp == "glob":
            target = expand_env(pa)
            if not os.path.isdir(target):
                return 0
            rule_pattern = normalize_rule_pattern(tp, pattern, nt)
            total = 0
            for name in os.listdir(target):
                if stop_flag is not None and stop_flag.is_set():
                    break
                if fnmatch.fnmatch(name.lower(), rule_pattern.lower()):
                    total += safe_getsize(os.path.join(target, name))
            return total
        if tp == "file":
            target = expand_env(pa)
            return safe_getsize(target) if os.path.isfile(target) else 0
    except Exception as e:
        log_sampled_background_error("规则估算", e)
        return 0
    return 0

def delete_path(path, perm, log_fn):
    import shutil
    try:
        if not os.path.exists(path): return True
        if not perm:
            if send_to_recycle_bin(path): log_fn(f"[回收站] {path}"); return True
            log_fn(f"[回收站失败] {path}")
            
        if os.path.isfile(path) or os.path.islink(path):
            try:
                os.remove(path)
            except Exception as e:
                # 核心黑科技：MOVEFILE_DELAY_UNTIL_REBOOT (数值 4)
                # 当文件被内核死锁时，标记它在下次重启时被系统自动删除
                if ctypes.windll.kernel32.MoveFileExW(path, None, 4):
                    log_fn(f"[延期粉碎] 发现内核级锁定，已安排在下次重启时销毁: {os.path.basename(path)}")
                    return True
                raise e
        else:
            def _onerror(func, p, exc_info):
                # 遍历删文件夹遇到顽固驱动文件时触发
                if ctypes.windll.kernel32.MoveFileExW(p, None, 4):
                    log_fn(f"[延期粉碎] 锁定项已安排重启销毁: {os.path.basename(p)}")
                else:
                    pass # 忽略错误，继续删其他能删的
                    
            shutil.rmtree(path, onerror=_onerror)
            
            # 如果文件夹还没被彻底删掉(里面有延期删除的文件)，把文件夹自己也标记上
            if os.path.exists(path):
                ctypes.windll.kernel32.MoveFileExW(path, None, 4)
                
        if not os.path.exists(path):
            log_fn(f"[永久删除] 成功移除: {path}")
        else:
            log_fn(f"[部分挂起] 包含内核驱动保护，请重启电脑完成彻底清理: {path}")
        return True
    except Exception as e: 
        log_fn(f"[失败] {path} -> {e}"); return False

def expand_env(p): return os.path.expandvars(p)

def get_available_drives():
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i): drives.append(chr(65 + i) + ":\\")
    return drives

_VALID_REGISTRY_PATH_RE = re.compile(
    r"^(HKLM|HKCU|HKCR|HKU|HKCC|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER|HKEY_CLASSES_ROOT|HKEY_USERS|HKEY_CURRENT_CONFIG)\\",
    re.IGNORECASE
)

def force_delete_registry(full_path, log_fn):
    """使用 Windows 原生 reg delete 命令进行强制递归删除，穿透力更强"""
    try:
        # 校验注册表路径格式，防止注入非法参数
        path_text = str(full_path or "").strip()
        if not path_text or not _VALID_REGISTRY_PATH_RE.match(path_text):
            log_fn(f"[强删注册表] 路径格式非法，已拒绝: {full_path}")
            return False
        cmd = ['reg', 'delete', path_text, '/f']
        # creationflags=subprocess.CREATE_NO_WINDOW 防止弹黑框
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            log_fn(f"[强删注册表] 成功: {path_text}")
            return True
        else:
            # 如果依然失败，说明是 TrustedInstaller 或 SYSTEM 级死锁保护
            err_msg = r.stderr.strip().replace('\n', ' ')
            log_fn(f"[强删注册表] 权限不足(可能受系统保护): {path_text} -> {err_msg}")
            return False
    except Exception as e:
        log_fn(f"[强删注册表] 异常: {e}")
        return False

def _set_registry_value(root, subkey, name, value, value_type=winreg.REG_SZ):
    with winreg.CreateKey(root, subkey) as key:
        winreg.SetValueEx(key, name, 0, value_type, value)

def restore_default_explorer_associations(log_fn):
    """恢复常见的资源管理器打开动作和可执行文件关联。"""
    try:
        assoc_values = [
            (winreg.HKEY_CLASSES_ROOT, r".exe", "", "exefile"),
            (winreg.HKEY_CLASSES_ROOT, r".exe", "Content Type", "application/x-msdownload"),
            (winreg.HKEY_CLASSES_ROOT, r".bat", "", "batfile"),
            (winreg.HKEY_CLASSES_ROOT, r".cmd", "", "cmdfile"),
            (winreg.HKEY_CLASSES_ROOT, r".com", "", "comfile"),
            (winreg.HKEY_CLASSES_ROOT, r".lnk", "", "lnkfile"),
            (winreg.HKEY_CLASSES_ROOT, r"exefile\shell\open\command", "", '"%1" %*'),
            (winreg.HKEY_CLASSES_ROOT, r"exefile\shell\runas\command", "", '"%1" %*'),
            (winreg.HKEY_CLASSES_ROOT, r"batfile\shell\open\command", "", '"%1" %*'),
            (winreg.HKEY_CLASSES_ROOT, r"cmdfile\shell\open\command", "", '"%1" %*'),
            (winreg.HKEY_CLASSES_ROOT, r"comfile\shell\open\command", "", '"%1" %*'),
            (winreg.HKEY_CLASSES_ROOT, r"lnkfile", "IsShortcut", ""),
            (winreg.HKEY_CLASSES_ROOT, r"Directory\shell", "", "none"),
            (winreg.HKEY_CLASSES_ROOT, r"Folder\shell", "", "none"),
            (winreg.HKEY_CLASSES_ROOT, r"Drive\shell", "", "none"),
        ]

        for root, subkey, name, value in assoc_values:
            _set_registry_value(root, subkey, name, value)
            label = f"{subkey}\\{name}" if name else subkey
            log_fn(f"[恢复关联] 已写入: {label}")

        for path in (
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.exe\UserChoice",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.bat\UserChoice",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.cmd\UserChoice",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.com\UserChoice",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.lnk\UserChoice",
        ):
            force_delete_registry(path, log_fn)

        log_fn("[恢复关联] 默认资源管理器关联已恢复，建议重启资源管理器或重新登录系统")
        return True, "默认资源管理器关联已恢复"
    except Exception as e:
        log_fn(f"[恢复关联] 失败: {e}")
        return False, f"恢复默认资源管理器关联失败: {e}"

SYSTEM_CONTEXT_MENU_VERBS = {
    "open", "opennewwindow", "openinnewprocess", "find", "runas", "cmd",
    "powershell", "pintohome", "pintohomefromtree", "sharing", "share",
    "properties", "includeinlibrary", "restorepreviousversions", "copyaspath",
    "giveaccessto", "takeownership", "openinsandbox"
}

SYSTEM_CONTEXT_MENU_DLL_HINTS = (
    "shell32.dll", "windows.storage.dll", "windows.ui.fileexplorer.dll",
    "propsys.dll", "shdocvw.dll", "zipfldr.dll"
)

def _query_registry_default(root, subkey):
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return str(value or "").strip()
    except OSError:
        return ""

def _query_context_menu_source(target, sub_name):
    shell_key = f"{target}\\{sub_name}"
    command = _query_registry_default(winreg.HKEY_CLASSES_ROOT, shell_key + r"\command")
    if command:
        return command

    clsid = _query_registry_default(winreg.HKEY_CLASSES_ROOT, shell_key)
    if not clsid and re.fullmatch(r"\{[0-9a-fA-F\-]+\}", sub_name or ""):
        clsid = sub_name
    if clsid:
        source = _query_registry_default(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\InprocServer32")
        if source:
            return source
    return ""

def classify_context_menu_entry(target, sub_name):
    source = _query_context_menu_source(target, sub_name)
    lower_name = str(sub_name or "").strip().lower()
    lower_target = str(target or "").strip().lower()
    lower_source = norm_path(source).lower()
    system_root = os.environ.get("SystemRoot", r"C:\Windows").lower()

    is_system = False
    if lower_name in SYSTEM_CONTEXT_MENU_VERBS:
        is_system = True
    elif lower_source and lower_source.startswith(system_root):
        is_system = True
    elif any(hint in lower_source for hint in SYSTEM_CONTEXT_MENU_DLL_HINTS):
        is_system = True
    elif any(token in lower_target for token in ("directory\\shell", "folder\\shell", "drive\\shell")) and lower_name in {"open", "find", "runas"}:
        is_system = True

    if is_system:
        category = "系统"
        source_text = display_path(source) if source else "Windows 内置"
    elif source:
        category = "外部"
        source_text = display_path(source)
    else:
        category = "未知"
        source_text = "来源未识别"

    detail = f"{target} | 来源: {source_text}"
    return category, detail
    
def kill_app_processes(install_dir, log_fn):
    """强力猎杀目标目录下的所有运行中进程、Windows服务 以及 内核驱动"""
    if not install_dir or not os.path.exists(install_dir): return
    try:
        log_fn(f"[内核猎杀] 正在扫描并解除 '{install_dir}' 的进程与驱动锁定...")
        # 通过环境变量传递路径，避免字符串拼接导致的 PowerShell 注入
        ps_script = r"""
        $target = [regex]::Escape($env:_KILL_TARGET)

        # 1. 杀常规进程
        Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -match $target } | Stop-Process -Force -ErrorAction SilentlyContinue

        # 2. 停服务并删除
        Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object { $_.PathName -match $target } | ForEach-Object {
            Stop-Service -Name $_.Name -Force -ErrorAction SilentlyContinue
            & sc.exe delete $_.Name
        }

        # 3. 停内核驱动并删除
        Get-CimInstance Win32_SystemDriver -ErrorAction SilentlyContinue | Where-Object { $_.PathName -match $target } | ForEach-Object {
            & sc.exe stop $_.Name
            & sc.exe delete $_.Name
        }
        """
        env = os.environ.copy()
        env["_KILL_TARGET"] = install_dir
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, env=env)
    except Exception as e:
        log_fn(f"[内核猎杀] 异常: {e}")

def _extract_command_executable(command_text):
    text = str(command_text or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]
    m = re.match(r"([^\s]+)", text)
    return m.group(1) if m else ""

def _looks_like_install_root(path):
    p = norm_path(path)
    if not p:
        return False
    lower = p.lower()
    blacklist = (
        os.environ.get("SystemRoot", r"C:\Windows").lower(),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "system32").lower(),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "installer").lower()
    )
    if lower in blacklist:
        return False
    base = os.path.basename(lower)
    if base in {"uninstall.exe", "unins000.exe", "unins001.exe", "setup.exe", "update.exe"}:
        return False
    return True

def infer_install_location(name="", publisher="", install_location="", uninstall_cmd="", display_icon=""):
    direct = norm_path(install_location)
    if direct and os.path.isdir(direct):
        return direct

    candidates = []
    for raw in (display_icon, uninstall_cmd):
        exe_path = norm_path(_extract_command_executable(raw))
        if exe_path:
            candidates.append(exe_path)

    for candidate in candidates:
        if os.path.isdir(candidate) and _looks_like_install_root(candidate):
            return candidate
        parent = os.path.dirname(candidate)
        if parent and os.path.isdir(parent):
            uninstall_markers = {"uninstall.exe", "unins000.exe", "unins001.exe", "setup.exe", "update.exe"}
            if os.path.basename(candidate).lower() in uninstall_markers:
                if _looks_like_install_root(parent):
                    return parent
            if _looks_like_install_root(parent):
                return parent

    keywords = [str(name or "").strip(), str(publisher or "").strip()]
    keywords = [k for k in keywords if len(k) >= 3]
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for entry in os.scandir(root):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                lower_name = entry.name.lower()
                if any(k.lower() in lower_name or lower_name in k.lower() for k in keywords):
                    return entry.path
        except Exception:
            pass
    return direct

def build_uninstall_command(command_text, prefer_silent=False):
    raw = str(command_text or "").strip()
    if not raw:
        return "", "无命令"
    if not prefer_silent:
        return raw, "标准"

    lower = raw.lower()
    exe_name = os.path.basename(_extract_command_executable(raw)).lower()

    if "msiexec" in lower:
        cmd = re.sub(r"(?i)\s/i(?=\s)", " /x", raw, count=1)
        if not re.search(r"(?i)(/q[nrb]?|/quiet)", cmd):
            cmd += " /qn /norestart"
        return cmd, "静默(MSI)"

    if exe_name.startswith("unins"):
        cmd = raw
        if "/verysilent" not in lower:
            cmd += " /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
        return cmd, "静默(Inno)"

    if "nsis" in exe_name or "uninstall" in exe_name or "uninst" in exe_name:
        if "/s" not in lower:
            return raw + " /S", "静默(NSIS/通用)"
        return raw, "静默(NSIS/通用)"

    if "setup.exe" in exe_name or "installshield" in lower or "isscript" in lower:
        if "/s" not in lower:
            return raw + " /s", "静默(InstallShield)"
        return raw, "静默(InstallShield)"

    if "update.exe" in exe_name and "--uninstall" in lower:
        cmd = raw
        if "--silent" not in lower and "/silent" not in lower:
            cmd += " --silent"
        return cmd, "静默(Squirrel)"

    if "bundle" in exe_name or "burn" in lower or "wix" in lower:
        cmd = raw
        if "/quiet" not in lower:
            cmd += " /quiet /norestart"
        return cmd, "静默(Burn/WiX)"

    return raw, "标准"

def terminate_process_tree(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        pass

def scan_leftover_services(keywords, install_dir=""):
    results = []
    seen = set()
    install_dir_norm = norm_path(install_dir).lower()
    service_root = r"SYSTEM\CurrentControlSet\Services"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, service_root)
    except OSError:
        return results

    try:
        count = winreg.QueryInfoKey(root)[0]
        for i in range(count):
            try:
                service_name = winreg.EnumKey(root, i)
                sub = winreg.OpenKey(root, service_name)
            except OSError:
                continue
            try:
                def _q(name):
                    try:
                        return str(winreg.QueryValueEx(sub, name)[0] or "")
                    except OSError:
                        return ""

                display_name = _q("DisplayName")
                image_path = norm_path(_q("ImagePath")).lower()
                start_name = _q("Start")
                type_raw = _q("Type")
                text_blob = " ".join([service_name.lower(), display_name.lower(), image_path])
                matched = bool(install_dir_norm and image_path and install_dir_norm in image_path)
                if not matched:
                    matched = any(kw in text_blob for kw in keywords if kw)
                if matched:
                    try:
                        type_val = int(type_raw, 0) if type_raw else 0
                    except Exception:
                        type_val = 0
                    service_kind = "驱动服务" if type_val & 0x1 or type_val & 0x2 else "Windows 服务"
                    reg_path = f"HKLM\\{service_root}\\{service_name}"
                    key = (service_name.lower(), reg_path.lower())
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "name": service_name,
                            "display": display_name or service_name,
                            "reg_path": reg_path,
                            "kind": service_kind,
                            "image_path": image_path,
                            "start": start_name
                        })
            finally:
                try:
                    winreg.CloseKey(sub)
                except OSError:
                    pass
    finally:
        try:
            winreg.CloseKey(root)
        except OSError:
            pass
    return results

def scan_leftover_tasks(keywords, install_dir=""):
    install_dir_norm = norm_path(install_dir).lower()
    results = []
    seen = set()
    ps_script = r"""
$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | ForEach-Object {
    [PSCustomObject]@{
        TaskName = $_.TaskName
        TaskPath = $_.TaskPath
        Actions  = (($_.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join '; ')
    }
}
$tasks | ConvertTo-Json -Compress
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if r.returncode != 0 or not r.stdout.strip():
            return results
        payload = json.loads(r.stdout)
        if isinstance(payload, dict):
            payload = [payload]
        for item in payload or []:
            task_name = str(item.get("TaskName", "")).strip()
            task_path = str(item.get("TaskPath", "\\")).strip() or "\\"
            actions = str(item.get("Actions", "")).strip()
            text_blob = " ".join([task_name.lower(), task_path.lower(), actions.lower()])
            matched = bool(install_dir_norm and install_dir_norm in norm_path(actions).lower())
            if not matched:
                matched = any(kw in text_blob for kw in keywords if kw)
            if matched and task_name:
                full_name = f"{task_path}{task_name}" if task_path.endswith("\\") else f"{task_path}\\{task_name}"
                key = full_name.lower()
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "name": task_name,
                        "task_path": task_path,
                        "full_name": full_name,
                        "actions": actions
                    })
    except Exception:
        return results
    return results

def delete_service_entry(service_name, reg_path, log_fn):
    ok = True
    try:
        subprocess.run(
            ["sc", "stop", service_name],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        r = subprocess.run(
            ["sc", "delete", service_name],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if r.returncode == 0:
            log_fn(f"[删除服务] 成功: {service_name}")
        else:
            ok = False
            err = (r.stderr or r.stdout or "").strip()
            log_fn(f"[删除服务] 失败: {service_name} -> {err}")
    except Exception as e:
        ok = False
        log_fn(f"[删除服务] 异常: {service_name} -> {e}")

    reg_ok = force_delete_registry(reg_path, log_fn) if reg_path else True
    return ok and reg_ok

def delete_scheduled_task(full_name, log_fn):
    try:
        r = subprocess.run(
            ["schtasks", "/Delete", "/TN", full_name, "/F"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if r.returncode == 0:
            log_fn(f"[删除计划任务] 成功: {full_name}")
            return True
        err = (r.stderr or r.stdout or "").strip()
        log_fn(f"[删除计划任务] 失败: {full_name} -> {err}")
        return False
    except Exception as e:
        log_fn(f"[删除计划任务] 异常: {full_name} -> {e}")
        return False

# ══════════════════════════════════════════════════════════
#  类型检测 + 缓存
# ══════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.environ.get("TEMP", "."), "cdisk_cleaner_cache.json")

def _normalize_drive_letter(drive_letter="C"):
    text = str(drive_letter or "").strip()
    if not text:
        return "C"
    drive = os.path.splitdrive(text)[0] or text
    drive = drive.rstrip("\\/ ")
    if drive.endswith(":"):
        drive = drive[:-1]
    return (drive[:1] or "C").upper()

def _load_scan_cache():
    try:
        if not os.path.exists(CACHE_FILE):
            return {}
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        drives = raw.get("drives")
        if isinstance(drives, dict):
            return drives
        if "threads" in raw and "dtype" in raw:
            return {
                "C": {
                    "threads": raw.get("threads", 4),
                    "dtype": raw.get("dtype", "Unknown"),
                    "ts": raw.get("ts", 0)
                }
            }
    except Exception as e:
        log_background_error("读取扫描缓存", e)
    return {}

def _save_scan_cache(drives):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"drives": drives}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_background_error("写入扫描缓存", e)

def detect_disk_type(drive_letter="C"):
    drive_letter = _normalize_drive_letter(drive_letter)
    try:
        ps_script = f"""
$partition = Get-Partition -DriveLetter {drive_letter} -ErrorAction SilentlyContinue
if ($partition) {{
    $disk = Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq $partition.DiskNumber }}
    if ($disk) {{ $disk.MediaType }} else {{ "Unknown" }}
}} else {{ "Unknown" }}
"""
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        media = r.stdout.strip()
        if "SSD" in media or "Solid" in media: return "SSD"
        elif "HDD" in media or "Unspecified" in media: return "HDD"
        else: return "Unknown"
    except Exception: return "Unknown"

def get_scan_threads(drive_letter="C"):
    dtype = detect_disk_type(drive_letter)
    return {"SSD": 12, "HDD": 2, "Unknown": 4}.get(dtype, 4), dtype

def get_scan_threads_cached(drive_letter="C"):
    drive_letter = _normalize_drive_letter(drive_letter)
    try:
        drives = _load_scan_cache()
        cache = drives.get(drive_letter, {})
        if time.time() - cache.get("ts", 0) < 86400:
            return cache.get("threads", 4), cache.get("dtype", "Unknown")
    except Exception as e:
        log_background_error("读取线程缓存", e)
    threads, dtype = get_scan_threads(drive_letter)
    try:
        drives = _load_scan_cache()
        drives[drive_letter] = {"threads": threads, "dtype": dtype, "ts": time.time()}
        _save_scan_cache(drives)
    except Exception as e:
        log_background_error("更新线程缓存", e)
    return threads, dtype

def get_scan_threads_for_drives_cached(drives):
    letters = []
    seen = set()
    for drive in drives or []:
        letter = _normalize_drive_letter(drive)
        if letter not in seen:
            seen.add(letter)
            letters.append(letter)

    if not letters:
        return 4, "Unknown"

    stats = [get_scan_threads_cached(letter) for letter in letters]
    if len(stats) == 1:
        return stats[0]

    dtypes = [dtype for _, dtype in stats]
    total_threads = sum(threads for threads, _ in stats)
    threads = min(24, max(max(threads for threads, _ in stats), total_threads))

    if len(set(dtypes)) == 1:
        dtype = dtypes[0]
    elif "SSD" in dtypes and "HDD" in dtypes:
        dtype = "Mixed"
    else:
        dtype = "/".join(sorted(set(dtypes)))

    return threads, dtype

# ══════════════════════════════════════════════════════════
#  默认清理目标 (带 is_custom 标志位)
# ══════════════════════════════════════════════════════════
def default_clean_targets():
    sr = os.environ.get("SystemRoot", r"C:\Windows")
    la = os.environ.get("LOCALAPPDATA", "")
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    up = os.environ.get("USERPROFILE", "")
    J = os.path.join
    
    return [
        ("用户临时文件", expand_env(r"%TEMP%"), "dir", True, "常见垃圾，安全", False),
        ("系统临时文件", J(sr, "Temp"), "dir", True, "可能需管理员", False),
        ("Prefetch", J(sr, "Prefetch"), "dir", False, "影响首次启动", False),
        ("CBS 日志", J(sr, "Logs", "CBS"), "dir", True, "较安全", False),
        ("DISM 日志", J(sr, "Logs", "DISM"), "dir", True, "较安全", False),
        ("LiveKernelReports", J(sr, "LiveKernelReports"), "dir", True, "内核转储", False),
        ("WER(用户)", J(la, "Microsoft", "Windows", "WER"), "dir", True, "崩溃报告", False),
        ("WER(系统)", J(sr, "System32", "config", "systemprofile", "AppData", "Local", "Microsoft", "Windows", "WER"), "dir", False, "需管理员", False),
        ("Minidump", J(sr, "Minidump"), "dir", True, "崩溃转储", False),
        ("MEMORY.DMP", J(sr, "MEMORY.DMP"), "file", False, "确认不调试时勾选", False),
        ("缩略图缓存", J(la, "Microsoft", "Windows", "Explorer"), "glob", True, "资源管理器缩略图数据库缓存", False, "thumbcache*.db"),
        
        ("D3DSCache", J(la, "D3DSCache"), "dir", False, "d3d着色器缓存", False),
        ("NVIDIA DX", J(la, "NVIDIA", "DXCache"), "dir", False, "NV着色器缓存", False),
        ("NVIDIA GL", J(la, "NVIDIA", "GLCache"), "dir", False, "NV OpenGL缓存", False),
        ("NVIDIA Compute", J(la, "NVIDIA", "ComputeCache"), "dir", False, "CUDA", False),
        ("NV_Cache", J(pd, "NVIDIA Corporation", "NV_Cache"), "dir", False, "NV CUDA/计算缓存", False),
        ("AMD DX", J(la, "AMD", "DxCache"), "dir", False, "AMD着色器缓存", False),
        ("AMD GL", J(la, "AMD", "GLCache"), "dir", False, "AMD OpenGL缓存", False),
        ("Steam Shader", J(la, "Steam", "steamapps", "shadercache"), "dir", False, "Steam", False),
        ("Steam 下载临时", J(la, "Steam", "steamapps", "downloading"), "dir", False, "下载残留", False),
        
        ("Edge Cache", J(la, "Microsoft", "Edge", "User Data", "Default", "Cache"), "dir", False, "浏览器", False),
        ("Edge Code", J(la, "Microsoft", "Edge", "User Data", "Default", "Code Cache"), "dir", False, "JS", False),
        ("Chrome Cache", J(la, "Google", "Chrome", "User Data", "Default", "Cache"), "dir", False, "浏览器", False),
        ("Chrome Code", J(la, "Google", "Chrome", "User Data", "Default", "Code Cache"), "dir", False, "JS", False),
        
        ("pip Cache", J(la, "pip", "Cache"), "dir", False, "Python 包缓存", False),
        ("NuGet Cache", J(la, "NuGet", "v3-cache"), "dir", False, ".NET 包缓存", False),
        ("npm Cache", J(la, "npm-cache"), "dir", False, "Node.js 包缓存", False),
        ("Yarn Cache", J(la, "Yarn", "Cache"), "dir", False, "Yarn 全局缓存", False),
        ("pnpm Store", J(la, "pnpm", "store"), "dir", False, "pnpm 内容寻址存储库", False),
        ("Go Build Cache", J(la, "go-build"), "dir", False, "Go 编译缓存", False),
        ("Cargo Cache", J(up, ".cargo", "registry", "cache"), "dir", False, "Rust 包下载缓存", False),
        ("Gradle Cache", J(up, ".gradle", "caches"), "dir", False, "Java/Android 构建缓存", False),
        ("Maven Repository", J(up, ".m2", "repository"), "dir", False, "Java 本地依赖库", False),
        ("Composer Cache", J(la, "Composer"), "dir", False, "PHP 包缓存", False),
        
        ("WU Download", J(sr, "SoftwareDistribution", "Download"), "dir", False, "更新缓存", False),
        ("Delivery Opt", J(sr, "SoftwareDistribution", "DeliveryOptimization"), "dir", False, "需管理员", False),
    ]

DEFAULT_EXCLUDES=[r"C:\Windows\WinSxS",r"C:\Windows\Installer",r"C:\Program Files",r"C:\Program Files (x86)"]
BIGFILE_SKIP_EXT={".sys"}
BIGFILE_OPTIONAL_SKIP_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "memory.dmp"}
BIGFILE_OPTIONAL_SKIP_EXT = {
    ".vhd", ".vhdx", ".avhd", ".avhdx", ".vmdk", ".vdi", ".qcow", ".qcow2", ".ova", ".ovf"
}
DUPLICATE_GROUP_DISPLAY_LIMIT = 200
LOG_MAX_LINES = 1000

def should_exclude(p, prefixes):
    n = os.path.normcase(os.path.abspath(p))
    for e in prefixes:
        if not e:
            continue
        candidate = os.path.normcase(os.path.abspath(e))
        try:
            if os.path.commonpath([n, candidate]) == candidate:
                return True
        except ValueError:
            continue
    return False

# ══════════════════════════════════════════════════════════
#  多线程文件扫描
# ══════════════════════════════════════════════════════════
_SENTINEL = None

def _push_bigfile_result(results, item, result_limit):
    if result_limit and result_limit > 0:
        if len(results) < result_limit:
            heapq.heappush(results, item)
        elif item[0] > results[0][0]:
            heapq.heapreplace(results, item)
    else:
        results.append(item)

def should_skip_bigfile(path, skip_optional=False):
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]
    if ext in BIGFILE_SKIP_EXT:
        return True
    if not skip_optional:
        return False
    if name in BIGFILE_OPTIONAL_SKIP_NAMES:
        return True
    if ext in BIGFILE_OPTIONAL_SKIP_EXT:
        return True
    return False

def _dir_worker(dir_queue, min_b, excl, stop_flag, results, counter, lock, result_limit=None, skip_optional=False):
    while True:
        try: dirpath = dir_queue.get(timeout=0.05)
        except queue.Empty: continue
        if dirpath is _SENTINEL:
            dir_queue.task_done()
            break
        if stop_flag.is_set():
            dir_queue.task_done()
            continue
        try: entries = os.scandir(dirpath)
        except Exception: dir_queue.task_done(); continue
        local_count = 0
        local_results = []
        try:
            for entry in entries:
                if stop_flag.is_set(): break
                try:
                    if entry.is_symlink(): continue
                    if entry.is_dir(follow_symlinks=False):
                        if not should_exclude(entry.path, excl): dir_queue.put(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        if should_skip_bigfile(entry.path, skip_optional=skip_optional): continue
                        st = entry.stat(follow_symlinks=False)
                        local_count += 1
                        if st.st_size >= min_b:
                            _push_bigfile_result(local_results, (st.st_size, entry.path), result_limit)
                except Exception as e:
                    log_sampled_background_error("大文件扫描子项", e)
        finally:
            try: entries.close()
            except Exception as e:
                log_sampled_background_error("关闭目录句柄", e, limit=3)
        if local_count or local_results:
            with lock:
                counter[0] += local_count
                if result_limit and result_limit > 0:
                    for item in local_results:
                        _push_bigfile_result(results, item, result_limit)
                else:
                    results.extend(local_results)
        dir_queue.task_done()

def scan_big_files(roots, min_b, excl, stop, workers=4, result_limit=None, progress_cb=None, skip_optional=False):
    dir_queue = queue.Queue(); results = []; counter = [0]; lock = threading.Lock()
    for root in roots: dir_queue.put(root)
    threads = []
    for _ in range(workers):
        t = threading.Thread(
            target=_dir_worker,
            args=(dir_queue, min_b, excl, stop, results, counter, lock, result_limit, skip_optional),
            daemon=True
        )
        t.start(); threads.append(t)
    join_done = threading.Event()
    threading.Thread(target=lambda: (dir_queue.join(), join_done.set()), daemon=True).start()
    last_report = 0.0
    sent_stop_signal = False

    try:
        while not join_done.wait(0.1):
            now = time.time()
            if progress_cb and now - last_report >= 0.3:
                with lock:
                    scanned = counter[0]
                progress_cb(scanned)
                last_report = now
            if stop.is_set() and not sent_stop_signal:
                for _ in threads:
                    dir_queue.put(_SENTINEL)
                sent_stop_signal = True
    finally:
        # 确保无论正常退出还是异常退出，worker 线程都能收到终止信号
        if not sent_stop_signal:
            for _ in threads:
                dir_queue.put(_SENTINEL)
    for t in threads:
        t.join(timeout=2)
    results.sort(key=lambda x: (-x[0], os.path.normcase(x[1])))
    if progress_cb:
        with lock:
            scanned = counter[0]
        progress_cb(scanned)
    return results

class Sig(QObject):
    log=Signal(str); prog=Signal(int,int); est=Signal(int, object)
    big_clr=Signal(); big_add=Signal(str,str); done=Signal(str)
    clean_log=Signal(str); clean_prog=Signal(int,int); clean_done=Signal(str)
    big_log=Signal(str)
    uninst_log=Signal(str); uninst_prog=Signal(int,int); uninst_done=Signal(str)
    more_log=Signal(str); more_prog=Signal(int,int); more_done=Signal(str)
    big_prog=Signal(int,int); big_done=Signal(str, str)
    big_scan_count=Signal(int)
    disk_ready=Signal(str,int); update_found=Signal(str, str, str)
    update_status=Signal(str, str, str)
    update_latest=Signal(str)
    more_clr=Signal(); more_add=Signal(bool, str, str, str, str)
    uninst_clr=Signal(); uninst_add=Signal(object)

def style_table(tbl: TableWidget):
    setFont(tbl, 12, QFont.Weight.Normal)
    setFont(tbl.horizontalHeader(), 12, QFont.Weight.DemiBold)
    tbl.verticalHeader().setDefaultSectionSize(30)
    tbl.setItemDelegate(FluentOnlyCheckDelegate(tbl))

def append_capped_log(text_edit, text, max_lines=LOG_MAX_LINES):
    if text_edit is None:
        return

    text_edit.append(text)
    doc = text_edit.document()
    overflow = doc.blockCount() - max_lines
    if overflow <= 0:
        return

    cursor = QTextCursor(doc)
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    for _ in range(overflow):
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deleteChar()

def norm_path(text):
    if not text: return ""
    p=text.split(" |",1)[0].strip().strip('"').strip("'")
    p=expand_env(p).replace("/","\\")
    try: p=os.path.normpath(p)
    except Exception as e:
        log_sampled_background_error("规范化路径", e, limit=3)
    return p

def display_path(text):
    if not text:
        return ""
    p = str(text)
    if len(p) >= 2 and p[1] == ":":
        p = p[0].upper() + p[1:]
    return p

def open_explorer(p):
    p=norm_path(p)
    if not p: return
    try:
        if os.path.isfile(p): subprocess.Popen(["explorer","/select,",p])
        elif os.path.isdir(p): subprocess.Popen(["explorer",p])
        else:
            par=os.path.dirname(p)
            subprocess.Popen(["explorer",par if par and os.path.isdir(par) else p])
    except Exception as e:
        log_background_error("打开资源管理器", e)

def make_ctx(parent, table, pos, col):
    idx=table.indexAt(pos)
    if not idx.isValid(): return
    raw=table.item(idx.row(),col).text() if table.item(idx.row(),col) else ""
    n=norm_path(raw); ex=bool(n) and os.path.exists(n)
    m=RoundMenu(parent=parent)
    def _copy_path():
        QApplication.clipboard().setText(raw)
        InfoBar.success("复制成功", raw, orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2000, parent=parent.window())
    a1=Action(FIF.COPY,"复制");a1.triggered.connect(_copy_path);a1.setEnabled(bool(raw));m.addAction(a1); m.addSeparator()
    a2=Action(FIF.DOCUMENT,"打开"); a2.triggered.connect(lambda:subprocess.Popen(["explorer",n]) if n else None); a2.setEnabled(ex and os.path.isfile(n)); m.addAction(a2)
    a3=Action(FIF.FOLDER,"定位"); a3.triggered.connect(lambda:open_explorer(n)); a3.setEnabled(ex); m.addAction(a3)
    m.exec(table.viewport().mapToGlobal(pos))

def make_check_item(checked=False):
    item = QTableWidgetItem()
    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item

def is_row_checked(table, row): return table.item(row, 0) is not None and table.item(row, 0).checkState() == Qt.CheckState.Checked
def set_row_checked(table, row, checked):
    if table.item(row, 0): table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

def make_title_row(icon: FIF, text: str):
    row = QHBoxLayout(); row.setSpacing(8)
    iw = IconWidget(icon); iw.setFixedSize(24, 24); row.addWidget(iw)
    lbl = TitleLabel(text); setFont(lbl, 22, QFont.Weight.Bold); row.addWidget(lbl)
    row.addStretch(); return row

RULE_GLOB_DEFAULT_PATTERN = "thumbcache*.db"
HIGH_RISK_GLOB_EXTENSIONS = {
    ".exe", ".dll", ".sys", ".msi", ".bat", ".cmd", ".ps1",
    ".reg", ".com", ".scr", ".drv", ".ocx"
}

def normalize_rule_pattern(tp, pattern="", note=""):
    if tp != "glob":
        return ""

    raw = str(pattern or "").strip()
    if raw:
        return raw

    note_text = str(note or "").strip()
    if any(ch in note_text for ch in ("*", "?", "[")):
        return note_text

    return RULE_GLOB_DEFAULT_PATTERN

def parse_rule_entry(entry, force_custom=None):
    if not isinstance(entry, (list, tuple)) or len(entry) < 5:
        return None

    nm, pa, tp, en, nt = entry[0], entry[1], entry[2], entry[3], entry[4]
    if force_custom is None:
        is_custom = bool(entry[5]) if len(entry) >= 6 else False
    else:
        is_custom = bool(force_custom)

    pattern = normalize_rule_pattern(tp, entry[6] if len(entry) >= 7 else "", nt)
    return (nm, pa, tp, bool(en), nt, is_custom, pattern)

def serialize_rule_entry(entry):
    parsed = parse_rule_entry(entry)
    if not parsed:
        return None
    nm, pa, tp, en, nt, is_custom, pattern = parsed
    if tp == "glob":
        return [nm, pa, tp, en, nt, is_custom, pattern]
    return [nm, pa, tp, en, nt, is_custom]

def make_rule_key(nm, pa, tp, pattern=""):
    return (nm, pa, tp, normalize_rule_pattern(tp, pattern, ""))

def rule_display_target(pa, tp, pattern=""):
    if tp == "glob":
        return f"{pa} | {normalize_rule_pattern(tp, pattern, '')}"
    return pa

def get_rule_runtime_risk(entry):
    parsed = parse_rule_entry(entry)
    if not parsed:
        return ""

    nm, pa, tp, _, _, _, pattern = parsed
    raw_path = norm_path(pa)
    if not raw_path:
        return ""

    dump_rule_names = {"livekernelreports", "minidump", "memory.dmp"}
    if str(nm or "").strip().lower() in dump_rule_names:
        return f"{nm}：诊断转储文件，删除后会影响蓝屏或内核故障排查"

    drive, tail = os.path.splitdrive(raw_path)
    if drive and tail in ("\\", ""):
        return f"{nm}：目标指向磁盘根目录 {display_path(raw_path)}"

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    user_root = os.path.join(os.path.splitdrive(raw_path)[0] + "\\", "Users") if drive else r"C:\Users"

    dangerous_roots = [
        system_root,
        program_files,
        program_files_x86,
        os.environ.get("USERPROFILE", ""),
        user_root
    ]

    norm_raw = os.path.normcase(os.path.abspath(raw_path))
    for candidate in dangerous_roots:
        if not candidate:
            continue
        norm_candidate = os.path.normcase(os.path.abspath(candidate))
        if norm_raw == norm_candidate:
            return f"{nm}：目标指向高风险目录 {display_path(raw_path)}"

    if tp == "glob":
        rule_pattern = normalize_rule_pattern(tp, pattern, "")
        lower_pattern = rule_pattern.lower()
        if any(ext in lower_pattern for ext in HIGH_RISK_GLOB_EXTENSIONS):
            return f"{nm}：匹配模式可能命中可执行或系统文件 ({rule_pattern})"

    return ""

def load_rule_keys(raw_items):
    keys = set()
    for item in raw_items or []:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            nm, pa, tp = item[0], item[1], item[2]
            pattern = item[3] if len(item) >= 4 else ""
            keys.add(make_rule_key(nm, pa, tp, pattern))
    return keys

def app_root_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

SYSTEM_SOFTWARE_NAME_KEYWORDS = (
    "microsoft windows", "windows update", "update for microsoft windows", "security update",
    "hotfix", "service pack", "windows driver package", "驱动程序", "驱动包",
    "chipset", "firmware", "bios", "uefi", "management engine", "serial io",
    "rapid storage", "bluetooth driver", "wireless lan driver", "audio driver",
    "display driver", "graphics driver"
)

SYSTEM_SOFTWARE_PUBLISHER_KEYWORDS = (
    "microsoft windows", "intel", "advanced micro devices", "amd", "nvidia",
    "realtek", "qualcomm", "mediatek"
)

SYSTEM_IMPACT_NAME_KEYWORDS = (
    "visual c++", "redistributable", ".net", "desktop runtime", "runtime",
    "webview2", "directx", "driver", "security", "defender", "antivirus",
    "firewall", "endpoint", "vpn"
)

SYSTEM_IMPACT_PUBLISHER_KEYWORDS = (
    "microsoft", "intel", "amd", "nvidia", "realtek", "eset", "kaspersky",
    "bitdefender", "symantec", "mcafee", "vmware", "virtualbox"
)

UNINSTALL_PROTECTION_BLOCK_KEYWORDS = (
    "bitlocker", "manage-bde", "fvevol", "fvenotify", "fveapi",
    "trusted platform module", "trustedplatformmodule", "tpm",
    "device encryption", "disk encryption"
)

UNINSTALL_PROTECTION_HIGH_KEYWORDS = (
    "rapid storage", "intel rst", "storage controller", "storage filter",
    "nvme", "encryption", "encrypt", "firmware", "secure boot",
    "security", "protector"
)

UNINSTALL_PROTECTION_DRIVER_PATH_HINTS = (
    r"\windows\system32\drivers",
    r"\windows\system32\driverstore",
    r"\windows\system32\drivers\etc",
    r"\efi\\",
)

UNINSTALL_PROTECTION_SERVICE_REG_HINT = r"\system\currentcontrolset\services\\"

def _contains_any_keyword(text, keywords):
    blob = str(text or "").lower()
    return any(keyword in blob for keyword in keywords if keyword)

def classify_uninstall_leftover(item_kind, name="", path="", detail="", source="explicit", service_kind=""):
    name_text = str(name or "").strip()
    path_text = str(path or "").strip()
    detail_text = str(detail or "").strip()
    source_text = str(source or "explicit").strip().lower() or "explicit"
    service_kind_text = str(service_kind or "").strip()

    norm_text = norm_path(path_text)
    lower_path = (norm_text or path_text).lower().replace("/", "\\")
    blob = " ".join([
        str(item_kind or ""),
        name_text,
        path_text,
        detail_text,
        service_kind_text
    ]).lower()

    has_block_keyword = _contains_any_keyword(blob, UNINSTALL_PROTECTION_BLOCK_KEYWORDS)
    has_high_keyword = _contains_any_keyword(blob, UNINSTALL_PROTECTION_HIGH_KEYWORDS)
    is_driver_service = "驱动" in service_kind_text or "driver" in blob
    is_system_driver_path = any(token in lower_path for token in UNINSTALL_PROTECTION_DRIVER_PATH_HINTS)
    is_service_reg = UNINSTALL_PROTECTION_SERVICE_REG_HINT in lower_path

    if has_block_keyword:
        return {
            "tier": "blocked",
            "default_checked": False,
            "reason": "命中 BitLocker、TPM 或磁盘加密相关关键字，已禁止强力删除"
        }

    if has_high_keyword and (is_driver_service or is_system_driver_path or is_service_reg):
        return {
            "tier": "blocked",
            "default_checked": False,
            "reason": "命中存储驱动、固件或系统驱动敏感区域，已禁止强力删除"
        }

    if source_text == "keyword":
        return {
            "tier": "high",
            "default_checked": False,
            "reason": "该项来自关键词推断，可能是共享目录或共享注册表项，默认未勾选"
        }

    if has_high_keyword:
        return {
            "tier": "high",
            "default_checked": False,
            "reason": "命中存储、加密、固件或安全相关关键字，请确认确实属于目标软件"
        }

    return {
        "tier": "normal",
        "default_checked": True,
        "reason": ""
    }

def classify_uninstall_entry(name, publisher, install_location, reg_path):
    name_text = str(name or "").strip()
    publisher_text = str(publisher or "").strip()
    path_text = norm_path(install_location)
    reg_text = str(reg_path or "").strip()

    name_lower = name_text.lower()
    publisher_lower = publisher_text.lower()
    path_lower = path_text.lower()
    reg_lower = reg_text.lower()
    risk_blob = " ".join([name_lower, publisher_lower, path_lower, reg_lower])

    system_root = os.environ.get("SystemRoot", r"C:\Windows").lower()
    system_path_prefixes = (
        system_root,
        os.path.join(system_root, "system32").lower(),
        os.path.join(system_root, "winsxs").lower(),
        os.path.join(system_root, "systemapps").lower(),
        os.path.join(system_root, "servicing").lower(),
        os.path.join(system_root, "installer").lower(),
        os.path.join(system_root, "driverstore").lower(),
    )

    if _contains_any_keyword(risk_blob, UNINSTALL_PROTECTION_BLOCK_KEYWORDS):
        return {
            "category": "系统",
            "is_risky": True,
            "risk_kind": "critical",
            "risk_reason": "疑似 BitLocker、TPM 或磁盘加密相关组件，强力卸载已拦截"
        }

    is_windows_path = bool(path_lower) and any(path_lower.startswith(prefix) for prefix in system_path_prefixes)
    is_kb_update = bool(re.search(r"(^|[\s_(])kb\d{4,}", name_lower)) or bool(re.search(r"\\kb\d{4,}$", reg_lower))
    is_windows_component = any(keyword in name_lower for keyword in SYSTEM_SOFTWARE_NAME_KEYWORDS)
    is_driver_vendor = any(keyword in publisher_lower for keyword in SYSTEM_SOFTWARE_PUBLISHER_KEYWORDS) and any(
        token in name_lower for token in ("driver", "chipset", "audio", "bluetooth", "wireless", "graphics", "display", "firmware")
    )

    if is_windows_path or is_kb_update or is_windows_component or is_driver_vendor:
        return {
            "category": "系统",
            "is_risky": True,
            "risk_kind": "system",
            "risk_reason": "系统组件、补丁或驱动，卸载后可能影响系统功能或硬件工作"
        }

    is_sensitive_runtime = any(keyword in name_lower for keyword in SYSTEM_IMPACT_NAME_KEYWORDS)
    is_sensitive_vendor = any(keyword in publisher_lower for keyword in SYSTEM_IMPACT_PUBLISHER_KEYWORDS) and any(
        token in name_lower for token in ("runtime", "redistributable", ".net", "webview2", "security", "antivirus", "vpn", "driver")
    )

    if is_sensitive_runtime or is_sensitive_vendor:
        return {
            "category": "用户",
            "is_risky": True,
            "risk_kind": "impact",
            "risk_reason": "运行库、驱动或安全类软件，卸载后可能影响系统或其他软件"
        }

    return {
        "category": "用户",
        "is_risky": False,
        "risk_kind": "",
        "risk_reason": ""
    }

SAMPLE_RULE_PACKS = [
    ("通用规则", "common_custom_rules.json"),
    ("国产软件", "rules_cn_apps.json"),
    ("开发工具", "rules_dev_tools.json"),
    ("游戏平台", "rules_game_platforms.json")
]
RULE_STORE_INDEX_URL = "https://gitee.com/kio0/c_cleaner_plus/raw/master/config_store.json"
RULE_PACK_DOWNLOAD_BASE = "https://gitee.com/kio0/c_cleaner_plus/raw/master/config"

def _normalize_rule_store_item(item):
    if not isinstance(item, dict):
        return None

    title = str(item.get("title", "")).strip()
    filename = str(item.get("filename", "")).strip()
    if not title or not filename:
        return None

    return {
        "title": title,
        "filename": filename,
        "source": str(item.get("source", "")).strip() or "远程规则源",
        "summary": str(item.get("summary", "")).strip(),
        "detail": str(item.get("detail", "")).strip() or "暂无详细介绍"
    }

def load_rule_store_items():
    try:
        with urllib.request.urlopen(RULE_STORE_INDEX_URL, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        if isinstance(payload, dict):
            raw_items = payload.get("items", [])
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items = []
        for raw in raw_items:
            normalized = _normalize_rule_store_item(raw)
            if normalized:
                items.append(normalized)

        if items:
            return items, ""
        return [], "远程规则清单为空或缺少有效条目"
    except Exception as e:
        return [], f"远程规则清单获取失败: {e}"

def get_rule_pack_cache_dir(base_dir=None):
    if base_dir:
        return base_dir
    return os.path.join(app_root_dir(), "config")

def list_rule_pack_cache_records(store_items, base_dir):
    item_map = {}
    for item in store_items or []:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename", "")).strip()
        if filename and filename not in item_map:
            item_map[filename] = item

    records = []
    seen = set()

    for filename, item in item_map.items():
        path = os.path.join(base_dir, filename)
        if os.path.isfile(path):
            seen.add(filename.lower())
            records.append({
                "title": item.get("title", filename),
                "filename": filename,
                "path": path,
                "size": safe_getsize(path)
            })

    try:
        for filename in os.listdir(base_dir):
            path = os.path.join(base_dir, filename)
            if not os.path.isfile(path):
                continue
            if not filename.lower().endswith(".json"):
                continue
            if filename.lower() in seen:
                continue
            records.append({
                "title": os.path.splitext(filename)[0],
                "filename": filename,
                "path": path,
                "size": safe_getsize(path)
            })
    except Exception:
        pass

    records.sort(key=lambda x: x["title"].lower())
    return records

def get_sample_rule_pack_path(filename, base_dir=None):
    candidates = [
        os.path.join(get_rule_pack_cache_dir(base_dir), filename),
        os.path.join(app_root_dir(), filename),
        resource_path(filename)
    ]
    seen = set()
    for path in candidates:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.exists(path):
            return path
    return candidates[0]

def download_rule_pack(filename, base_dir=None):
    local_path = os.path.join(get_rule_pack_cache_dir(base_dir), filename)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    url = f"{RULE_PACK_DOWNLOAD_BASE}/{filename}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = resp.read()
    with open(local_path, "wb") as f:
        f.write(data)
    return local_path

def resolve_rule_pack(title_text, filename, parent=None, base_dir=None):
    try:
        path = download_rule_pack(filename, base_dir=base_dir)
        return path, ""
    except Exception as e:
        path = get_sample_rule_pack_path(filename, base_dir=base_dir)
        if not os.path.exists(path):
            raise RuntimeError(f"{title_text} 下载失败: {e}") from e
        if parent is not None:
            InfoBar.warning("下载失败", f"{title_text} 下载失败，已回退使用本地缓存", parent=parent)
        return path, str(e)

class AddRuleDialog(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.customTitle = TitleLabel("添加自定义清理规则")
        setFont(self.customTitle, 16, QFont.Weight.Bold)
        self.viewLayout.addWidget(self.customTitle)
        self.viewLayout.addSpacing(10)
        
        self.nameInput = LineEdit(); self.nameInput.setPlaceholderText("规则名称 (例如: 微信图片缓存)")
        self.pathLayout = QHBoxLayout(); self.pathInput = LineEdit(); self.pathInput.setPlaceholderText("绝对路径 (支持 %TEMP% 等环境变量)")
        self.btnBrowse = ToolButton(FIF.FOLDER); self.btnBrowse.clicked.connect(self._browse)
        self.pathLayout.addWidget(self.pathInput, 1); self.pathLayout.addWidget(self.btnBrowse)
        
        self.typeCombo = ComboBox(); self.typeCombo.addItems(["目录内所有文件 (dir)", "指定单个文件 (file)", "指定类型文件 (glob)"])
        self.typeCombo.currentIndexChanged.connect(self._on_type_changed)
        self.patternLayout = QHBoxLayout()
        self.patternInput = LineEdit()
        self.patternInput.setPlaceholderText("匹配模式 (例如: *.log)")
        self.btnPatternHelp = ToolButton(FIF.INFO)
        self.btnPatternHelp.setToolTip("匹配模式说明")
        self.btnPatternHelp.clicked.connect(self._show_pattern_help)
        self.patternLayout.addWidget(self.patternInput, 1)
        self.patternLayout.addWidget(self.btnPatternHelp)
        self.descInput = LineEdit(); self.descInput.setPlaceholderText("说明备注 (例如: 仅限个人使用)")
        
        self.viewLayout.addWidget(StrongBodyLabel("规则名称:")); self.viewLayout.addWidget(self.nameInput)
        self.viewLayout.addWidget(StrongBodyLabel("目标路径:")); self.viewLayout.addLayout(self.pathLayout)
        self.viewLayout.addWidget(StrongBodyLabel("目标类型:")); self.viewLayout.addWidget(self.typeCombo)
        self.viewLayout.addWidget(StrongBodyLabel("匹配模式:")); self.viewLayout.addLayout(self.patternLayout)
        self.viewLayout.addWidget(StrongBodyLabel("备注说明:")); self.viewLayout.addWidget(self.descInput)
        
        self.widget.setMinimumWidth(450); self.yesButton.setText("添加"); self.cancelButton.setText("取消")
        self._on_type_changed(self.typeCombo.currentIndex())
        
    def _browse(self):
        idx = self.typeCombo.currentIndex()
        if idx == 0 or idx == 2:
            folder = QFileDialog.getExistingDirectory(self, "选择清理目录")
            if folder: self.pathInput.setText(folder.replace("/", "\\"))
        else:
            file, _ = QFileDialog.getOpenFileName(self, "选择清理文件")
            if file: self.pathInput.setText(file.replace("/", "\\"))

    def _on_type_changed(self, idx):
        is_glob = idx == 2
        self.patternInput.setEnabled(is_glob)
        self.btnPatternHelp.setEnabled(is_glob)
        if is_glob and not self.patternInput.text().strip():
            self.patternInput.setText(RULE_GLOB_DEFAULT_PATTERN)
        elif not is_glob:
            self.patternInput.clear()

    def _show_pattern_help(self):
        MessageBox(
            "匹配模式说明",
            "匹配模式用于指定目录下哪些文件会被命中\n\n"
            "常见写法：\n"
            "*.log  匹配所有 .log 文件\n"
            "*.tmp  匹配所有 .tmp 文件\n"
            "cache_*  匹配以 cache_ 开头的文件\n"
            "thumbcache*.db  匹配缩略图缓存数据库\n\n"
            "说明：\n"
            "* 代表任意长度字符\n"
            "? 代表任意单个字符\n"
            "[abc] 代表括号中的任意一个字符",
            self
        ).exec()
            
    def get_data(self):
        t_map = {0: "dir", 1: "file", 2: "glob"}
        tp = t_map[self.typeCombo.currentIndex()]
        pattern = normalize_rule_pattern(tp, self.patternInput.text().strip(), "")
        return (
            self.nameInput.text().strip(),
            self.pathInput.text().strip(),
            tp,
            True,
            self.descInput.text().strip() or "自定义附加规则",
            True,
            pattern
        )

class LegacyMigrationDialog(MessageBoxBase):
    def __init__(self, old_dir, new_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("发现旧版配置")
        self.customTitle = TitleLabel("发现旧版配置")
        setFont(self.customTitle, 16, QFont.Weight.Bold)
        self.viewLayout.addWidget(self.customTitle)
        self.viewLayout.addSpacing(10)

        desc = CaptionLabel(
            f"检测到旧版本配置仍保存在系统目录\n\n旧位置：{display_path(old_dir)}\n新位置：{display_path(new_dir)}\n\n请选择迁移方式："
        )
        desc.setWordWrap(True)
        self.viewLayout.addWidget(desc)

        self.mode_combo = ComboBox()
        self.mode_combo.addItems([
            "迁移后自动清理旧配置",
            "迁移后保留旧配置",
            "不迁移"
        ])
        self.viewLayout.addWidget(self.mode_combo)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(520)

    def selected_mode(self):
        return self.mode_combo.currentIndex()

class RulePackManagerDialog(MessageBoxBase):
    def __init__(self, main_win, store_items, parent=None):
        super().__init__(main_win if main_win is not None else parent)
        self.main_win = main_win
        self.store_items = list(store_items or [])
        self.setWindowTitle("规则包管理")
        self.widget.setMinimumWidth(900)
        self.widget.setMinimumHeight(560)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_icon = IconWidget(FIF.DOCUMENT)
        title_icon.setFixedSize(22, 22)
        title_row.addWidget(title_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        title = TitleLabel("规则包管理")
        setFont(title, 18, QFont.Weight.Bold)
        title_row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch()

        btn_close = ToolButton(FIF.CLOSE, self)
        btn_close.setFixedSize(30, 30)
        btn_close.setToolTip("关闭")
        btn_close.clicked.connect(self.reject)
        title_row.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignVCenter)

        self.viewLayout.addLayout(title_row)

        desc = CaptionLabel("管理已下载到本地缓存目录中的规则包文件")
        desc.setTextColor(QColor(128, 128, 128))
        desc.setWordWrap(True)
        self.viewLayout.addWidget(desc)
        self.viewLayout.addSpacing(6)

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        self.lbl_pack_dir = CaptionLabel("")
        self.lbl_pack_dir.setTextColor(QColor(128, 128, 128))
        self.lbl_pack_dir.setWordWrap(True)
        body_layout.addWidget(self.lbl_pack_dir)

        self.tbl_cache = TableWidget()
        self.tbl_cache.setColumnCount(4)
        self.tbl_cache.setHorizontalHeaderLabels(["名称", "文件名", "大小", "路径"])
        self.tbl_cache.verticalHeader().setVisible(False)
        self.tbl_cache.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_cache.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl_cache.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_cache.setColumnWidth(0, 220)
        self.tbl_cache.setColumnWidth(1, 220)
        self.tbl_cache.setColumnWidth(2, 100)
        self.tbl_cache.setColumnHidden(3, True)
        self.tbl_cache.horizontalHeader().setStretchLastSection(True)
        self.tbl_cache.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl_cache.customContextMenuRequested.connect(lambda p: make_ctx(self, self.tbl_cache, p, 3))
        style_table(self.tbl_cache)
        body_layout.addWidget(self.tbl_cache, 1)

        btn_bar = QWidget(body)
        btn_row = QHBoxLayout(btn_bar)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_refresh = PrimaryPushButton(FIF.SYNC, "刷新缓存")
        btn_refresh.clicked.connect(self._refresh_cache_table)
        btn_row.addWidget(btn_refresh)
        btn_open_dir = PushButton(FIF.FOLDER, "打开目录")
        btn_open_dir.clicked.connect(self._open_rule_pack_dir)
        btn_row.addWidget(btn_open_dir)
        btn_del_selected = PushButton(FIF.DELETE, "删除选中")
        btn_del_selected.clicked.connect(self._delete_selected_cache)
        btn_row.addWidget(btn_del_selected)
        btn_clear_all = PushButton(FIF.CANCEL, "清空缓存")
        btn_clear_all.clicked.connect(self._clear_all_cache)
        btn_row.addWidget(btn_clear_all)
        btn_row.addStretch()
        body_layout.addWidget(btn_bar)
        self.viewLayout.addWidget(body)
        self.yesButton.hide()
        self.cancelButton.hide()
        footer = self.cancelButton.parentWidget()
        if footer is not None and footer is not self and footer is not self.widget:
            footer.hide()
            footer.setFixedHeight(0)

        self._refresh_cache_table(show_empty_tip=False)

    def _rule_pack_dir(self):
        return get_rule_pack_cache_dir(self.main_win.config_dir)

    def _refresh_cache_table(self, show_empty_tip=True):
        pack_dir = self._rule_pack_dir()
        self.lbl_pack_dir.setText(f"缓存目录：{display_path(pack_dir)}")
        self.lbl_pack_dir.setToolTip(display_path(pack_dir))

        records = list_rule_pack_cache_records(self.store_items, pack_dir)
        self.tbl_cache.setRowCount(len(records))
        for row, item in enumerate(records):
            self.tbl_cache.setItem(row, 0, QTableWidgetItem(item["title"]))
            self.tbl_cache.setItem(row, 1, QTableWidgetItem(item["filename"]))
            size_item = QTableWidgetItem(human_size(item["size"]))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_cache.setItem(row, 2, size_item)
            self.tbl_cache.setItem(row, 3, QTableWidgetItem(item["path"]))

        if show_empty_tip and not records:
            InfoBar.warning("提示", "当前没有已缓存的规则包", parent=self.main_win)

    def _open_rule_pack_dir(self):
        pack_dir = self._rule_pack_dir()
        os.makedirs(pack_dir, exist_ok=True)
        open_explorer(pack_dir)

    def _delete_selected_cache(self):
        row = self.tbl_cache.currentRow()
        path_item = self.tbl_cache.item(row, 3) if row >= 0 else None
        path = path_item.text() if path_item else ""
        if not path:
            InfoBar.warning("提示", "请先选择一个已下载的规则包", parent=self.main_win)
            return
        if not MessageBox("确认", f"确定删除该规则包缓存？\n{display_path(path)}", self.main_win).exec():
            return
        try:
            os.remove(path)
            self._refresh_cache_table(show_empty_tip=False)
            InfoBar.success("已删除", "规则包缓存已删除", parent=self.main_win)
        except Exception as e:
            InfoBar.error("删除失败", str(e), parent=self.main_win)

    def _clear_all_cache(self):
        records = list_rule_pack_cache_records(self.store_items, self._rule_pack_dir())
        if not records:
            InfoBar.warning("提示", "当前没有可清理的规则包缓存", parent=self.main_win)
            return
        if not MessageBox("确认", f"确定清空这 {len(records)} 个规则包缓存？", self.main_win).exec():
            return
        ok = 0
        fl = 0
        for item in records:
            try:
                os.remove(item["path"])
                ok += 1
            except Exception:
                fl += 1
        self._refresh_cache_table(show_empty_tip=False)
        if fl == 0:
            InfoBar.success("清理完成", f"已清理 {ok} 个规则包缓存", parent=self.main_win)
        else:
            InfoBar.warning("部分完成", f"已清理 {ok} 个，失败 {fl} 个", parent=self.main_win)

class RuleStorePage(ScrollArea):
    def __init__(self, main_win, parent=None):
        super().__init__(parent)
        self.main_win = main_win
        self.selected_item = None
        self.store_items = []

        self.view = QWidget()
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setObjectName("ruleStorePage")
        self.enableTransparentBackground()

        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 12, 28, 20)
        root.setSpacing(12)
        title_row = make_title_row(FIF.DOCUMENT, "规则商店")
        self.btn_refresh = PushButton(FIF.SYNC, "刷新列表")
        self.btn_refresh.clicked.connect(self._refresh_items)
        title_row.addWidget(self.btn_refresh)
        self.btn_manage = PushButton(FIF.FOLDER, "规则包管理")
        self.btn_manage.clicked.connect(self._open_pack_manager)
        title_row.addWidget(self.btn_manage)
        root.addLayout(title_row)

        self.desc = CaptionLabel("从远程规则源选择规则包，一键下载并导入到当前自定义规则列表")
        self.desc.setTextColor(QColor(128, 128, 128))
        self.desc.setWordWrap(True)
        root.addWidget(self.desc)

        content = QHBoxLayout()
        content.setSpacing(12)
        root.addLayout(content, 1)

        left = CardWidget(self.view)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(StrongBodyLabel("可用规则包"))

        self.tbl = TableWidget()
        self.tbl.setColumnCount(4)
        self.tbl.setHorizontalHeaderLabels(["名称", "来源", "说明", "文件名"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setColumnHidden(3, True)
        self.tbl.setColumnWidth(0, 180)
        self.tbl.setColumnWidth(1, 100)
        self.tbl.setColumnWidth(2, 280)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        style_table(self.tbl)
        self.tbl.itemSelectionChanged.connect(self._sync_detail)
        self.tbl.itemDoubleClicked.connect(lambda _: self._confirm_selection())
        left_layout.addWidget(self.tbl, 1)
        content.addWidget(left, 3)

        right = CardWidget(self.view)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)
        right_layout.addWidget(StrongBodyLabel("规则详情"))

        self.lbl_name = TitleLabel("")
        setFont(self.lbl_name, 16, QFont.Weight.Bold)
        right_layout.addWidget(self.lbl_name)

        self.lbl_meta = CaptionLabel("")
        self.lbl_meta.setTextColor(QColor(128, 128, 128))
        self.lbl_meta.setWordWrap(True)
        right_layout.addWidget(self.lbl_meta)

        self.lbl_detail = CaptionLabel("")
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail.setTextColor(QColor(128, 128, 128))
        right_layout.addWidget(self.lbl_detail)
        right_layout.addStretch()

        self.btn_import = PrimaryPushButton(FIF.DOCUMENT, "下载并导入")
        self.btn_import.clicked.connect(self._confirm_selection)
        right_layout.addWidget(self.btn_import)
        content.addWidget(right, 2)

        self._load_items()

    def _load_items(self, notify=False):
        items, err = load_rule_store_items()
        if not err:
            self.store_items = items
        self.desc.setText(
            "从远程规则源选择规则包，一键下载并导入到当前自定义规则列表"
            if not err else err
        )
        self.tbl.setRowCount(len(items))
        for row, item in enumerate(items):
            name_item = QTableWidgetItem(item["title"])
            name_item.setData(Qt.ItemDataRole.UserRole, item)
            self.tbl.setItem(row, 0, name_item)
            self.tbl.setItem(row, 1, QTableWidgetItem(item["source"]))
            self.tbl.setItem(row, 2, QTableWidgetItem(item["summary"]))
            self.tbl.setItem(row, 3, QTableWidgetItem(item["filename"]))
        if self.tbl.rowCount() > 0:
            self.tbl.selectRow(0)
            self._sync_detail()
        else:
            self._sync_detail()

        if notify:
            if err:
                InfoBar.error("刷新失败", err, parent=self.main_win)
            else:
                InfoBar.success("刷新成功", f"已加载 {len(items)} 个规则包", parent=self.main_win)

    def _refresh_items(self):
        self._load_items(notify=True)

    def _open_pack_manager(self):
        dialog = RulePackManagerDialog(self.main_win, self.store_items, self)
        dialog.exec()

    def _sync_detail(self):
        row = self.tbl.currentRow()
        if row < 0:
            self.selected_item = None
            self.lbl_name.setText("未选择规则包")
            self.lbl_meta.setText("")
            self.lbl_detail.setText("请先从左侧选择一个规则包")
            self.btn_import.setEnabled(False)
            return
        item = self.tbl.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selected_item = data if isinstance(data, dict) else None
        if not self.selected_item:
            return
        self.lbl_name.setText(self.selected_item["title"])
        self.lbl_meta.setText(f"来源：{self.selected_item['source']}\n文件：{self.selected_item['filename']}")
        self.lbl_detail.setText(self.selected_item["detail"])
        self.btn_import.setEnabled(True)

    def _confirm_selection(self):
        if not self.selected_item:
            InfoBar.warning("提示", "请先选择一个规则包", parent=self.main_win)
            return
        title_text = self.selected_item["title"]
        filename = self.selected_item["filename"]
        try:
            path, _ = resolve_rule_pack(title_text, filename, parent=self.main_win, base_dir=self.main_win.config_dir)
        except Exception as e:
            InfoBar.error("导入失败", str(e), parent=self.main_win)
            return
        self.main_win.import_rules_from_path(path, title_text)

# ══════════════════════════════════════════════════════════
#  页面：全局设置 (SettingPage)
# ══════════════════════════════════════════════════════════
class SettingPage(ScrollArea):
    def __init__(self, main_win, parent=None):
        super().__init__(parent)
        self.main_win = main_win
        self.view = QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("settingPage"); self.enableTransparentBackground()
        self._apply_setting_style()

        v = QVBoxLayout(self.view); v.setContentsMargins(28, 12, 28, 24); v.setSpacing(10)
        v.addLayout(make_title_row(FIF.SETTING, "系统设置"))
        top_hint = CaptionLabel("管理保存策略、配置目录和更新通道")
        top_hint.setTextColor(QColor(128, 128, 128))
        v.addWidget(top_hint)

        self.switch_save = SwitchButton()
        self.switch_save.setOnText("开启"); self.switch_save.setOffText("关闭")
        self.switch_save.setChecked(self.main_win.global_settings.get("auto_save", True))
        self.switch_save.checkedChanged.connect(self._on_auto_save_changed)

        self.switch_protect_builtin = SwitchButton()
        self.switch_protect_builtin.setOnText("开启"); self.switch_protect_builtin.setOffText("关闭")
        self.switch_protect_builtin.setChecked(self.main_win.global_settings.get("protect_builtin_rules", True))
        self.switch_protect_builtin.checkedChanged.connect(self._on_protect_builtin_changed)

        btn_cache = PushButton(FIF.SYNC, "刷新")
        btn_cache.clicked.connect(self._refresh_cache)
        self._style_action_control(btn_cache, 86)

        btn_migrate = PushButton(FIF.SYNC, "检测")
        btn_migrate.clicked.connect(self._detect_legacy_config)
        self._style_action_control(btn_migrate, 86)

        btn_reset = PushButton(FIF.UPDATE, "恢复")
        btn_reset.clicked.connect(self._reset_defaults)
        self._style_action_control(btn_reset, 86)

        btn_cfg_browse = PushButton(FIF.FOLDER, "更改")
        btn_cfg_browse.clicked.connect(self._choose_config_dir)
        self._style_action_control(btn_cfg_browse, 82)
        btn_cfg_reset = PushButton(FIF.UPDATE, "默认")
        btn_cfg_reset.clicked.connect(self._reset_config_dir)
        self._style_action_control(btn_cfg_reset, 82)

        self.cb_update_channel = ComboBox()
        self.cb_update_channel.addItems(["稳定版", "测试版"])
        saved_channel = self.main_win.global_settings.get("update_channel", "stable")
        self.cb_update_channel.setCurrentIndex(1 if saved_channel == "beta" else 0)
        self.cb_update_channel.currentIndexChanged.connect(self._on_update_channel_changed)
        self.cb_update_channel.setFixedWidth(116)

        btn_check_update = PushButton(FIF.SYNC, "检查")
        btn_check_update.clicked.connect(self._check_update_now)
        self._style_action_control(btn_check_update, 86)

        btn_export_logs = PushButton(FIF.SAVE, "导出")
        btn_export_logs.clicked.connect(self._export_logs)
        self._style_action_control(btn_export_logs, 86)

        self.lbl_config_dir = CaptionLabel("")
        self.lbl_config_dir.setTextColor(QColor(128, 128, 128))
        self.lbl_config_dir.setWordWrap(True)

        self.lbl_latest_version = CaptionLabel("最新版本：获取中...")
        self.lbl_latest_version.setTextColor(QColor(128, 128, 128))
        self.lbl_latest_version.setWordWrap(True)

        v.addSpacing(4)
        v.addWidget(self._make_section_label("基础设置"))
        v.addWidget(self._make_group_card([
            self._make_setting_row(
                FIF.SAVE,
                "退出时自动保存配置",
                "自动保存常规清理中的勾选状态、自定义规则以及拖拽后的排序结果",
                self.switch_save
            ),
            self._make_setting_row(
                FIF.SETTING,
                "内置默认规则保护",
                "开启后内置规则无法删除；关闭后可删除，且删除状态会保留到下次启动",
                self.switch_protect_builtin
            ),
            self._make_setting_row(
                FIF.SYNC,
                "刷新系统扫描缓存",
                "清空硬盘类型检测缓存更换或新增硬盘后，建议执行一次",
                btn_cache
            )
        ]))

        v.addSpacing(6)
        v.addWidget(self._make_section_label("配置"))
        v.addWidget(self._make_group_card([
            self._make_setting_row(
                FIF.DOCUMENT,
                "迁移旧版配置文件",
                "检测 LOCALAPPDATA 中的旧版配置，并按你的选择迁移到当前配置目录",
                btn_migrate
            ),
            self._make_setting_row(
                FIF.DELETE,
                "恢复默认配置",
                "恢复常规清理的默认勾选与顺序，同时清除自定义规则",
                btn_reset
            ),
            self._make_setting_row(
                FIF.FOLDER,
                "配置保存目录",
                "当前软件的规则、状态与全局设置都会保存在这里",
                self._make_action_box(btn_cfg_browse, btn_cfg_reset),
                self.lbl_config_dir
            )
        ]))

        v.addSpacing(6)
        v.addWidget(self._make_section_label("诊断"))
        v.addWidget(self._make_group_card([
            self._make_setting_row(
                FIF.SAVE,
                "导出当前日志",
                "导出本次运行的会话日志、各页面日志快照和后台异常记录，便于排查问题",
                btn_export_logs
            )
        ]))

        v.addSpacing(6)
        v.addWidget(self._make_section_label("更新"))
        v.addWidget(self._make_group_card([
            self._make_setting_row(
                FIF.UPDATE,
                "更新通道",
                "稳定版只接收正式版本；测试版会接收 alpha、beta、rc 等预发布版本",
                self.cb_update_channel
            ),
            self._make_setting_row(
                FIF.INFO,
                "检查更新",
                "",
                btn_check_update,
                self.lbl_latest_version
            )
        ]))

        self._refresh_config_dir_text()
        v.addStretch()

    def _apply_setting_style(self):
        if isDarkTheme():
            self.view.setStyleSheet("""
                CardWidget#settingGroup {
                    background: rgba(24, 24, 24, 0.82);
                    border: 1px solid rgba(255, 255, 255, 0.05);
                    border-radius: 12px;
                }
                QWidget#settingRow {
                    background: transparent;
                    border-radius: 8px;
                }
                QWidget#settingRow:hover {
                    background: rgba(255, 255, 255, 0.03);
                }
                QWidget#settingIconTile {
                    background: transparent;
                    border: none;
                }
                QWidget#settingDivider {
                    background: rgba(255, 255, 255, 0.08);
                    min-height: 1px;
                    max-height: 1px;
                }
            """)
        else:
            self.view.setStyleSheet("""
                CardWidget#settingGroup {
                    background: rgba(255, 255, 255, 0.84);
                    border: 1px solid rgba(0, 0, 0, 0.06);
                    border-radius: 12px;
                }
                QWidget#settingRow {
                    background: transparent;
                    border-radius: 8px;
                }
                QWidget#settingRow:hover {
                    background: rgba(0, 0, 0, 0.03);
                }
                QWidget#settingIconTile {
                    background: transparent;
                    border: none;
                }
                QWidget#settingDivider {
                    background: rgba(0, 0, 0, 0.07);
                    min-height: 1px;
                    max-height: 1px;
                }
            """)

    def _style_action_control(self, widget, width=None):
        widget.setFixedHeight(32)
        if width is not None:
            widget.setFixedWidth(width)

    def _smooth_title_font(self, label):
        setFont(label, 13, QFont.Weight.Medium)
        font = label.font()
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        label.setFont(font)

    def _make_section_label(self, text):
        lbl = CaptionLabel(text)
        setFont(lbl, 12, QFont.Weight.Medium)
        lbl.setTextColor(QColor(128, 128, 128))
        return lbl

    def _make_group_card(self, rows):
        card = CardWidget(self.view)
        card.setObjectName("settingGroup")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        for idx, row in enumerate(rows):
            layout.addWidget(row)
            if idx != len(rows) - 1:
                layout.addWidget(self._make_divider())
        return card

    def _make_divider(self):
        divider = QWidget(self.view)
        divider.setObjectName("settingDivider")
        divider.setFixedHeight(1)
        return divider

    def _make_action_box(self, *widgets):
        box = QWidget(self.view)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for widget in widgets:
            layout.addWidget(widget)
        return box

    def _make_setting_row(self, icon, title, desc, control_widget, detail_widget=None):
        row = QWidget(self.view)
        row.setObjectName("settingRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(10)

        tile = QWidget(row)
        tile.setObjectName("settingIconTile")
        tile.setFixedSize(28, 28)
        tile_layout = QHBoxLayout(tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        tile_layout.setSpacing(0)
        icon_widget = IconWidget(icon)
        icon_widget.setFixedSize(16, 16)
        tile_layout.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_lbl = StrongBodyLabel(title)
        self._smooth_title_font(title_lbl)
        text_col.addWidget(title_lbl)
        if desc:
            desc_lbl = CaptionLabel(str(desc))
            desc_lbl.setWordWrap(True)
            desc_lbl.setTextColor(QColor(128, 128, 128))
            text_col.addWidget(desc_lbl)
        if detail_widget is not None:
            text_col.addSpacing(2)
            text_col.addWidget(detail_widget)
        layout.addLayout(text_col, 1)
        layout.addSpacing(10)
        layout.addWidget(control_widget, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        return row

    def _on_auto_save_changed(self, is_checked):
        self.main_win.global_settings["auto_save"] = is_checked
        self.main_win.save_global_settings()

    def _on_protect_builtin_changed(self, is_checked):
        self.main_win.global_settings["protect_builtin_rules"] = is_checked
        self.main_win.save_global_settings()

    def _refresh_config_dir_text(self):
        cur_dir = self.main_win.config_dir
        default_dir = self.main_win.default_config_dir
        text = f"当前: {display_path(cur_dir)}"
        if os.path.normcase(os.path.abspath(cur_dir)) != os.path.normcase(os.path.abspath(default_dir)):
            text += f"\n默认: {display_path(default_dir)}"
        self.lbl_config_dir.setText(text)
        self.lbl_config_dir.setToolTip(display_path(cur_dir))

    def _choose_config_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择配置保存目录", self.main_win.config_dir)
        if not folder:
            return
        ok, msg = self.main_win.set_config_dir(folder)
        if ok:
            self._refresh_config_dir_text()
            InfoBar.success("已更新", f"配置已切换到: {self.main_win.config_dir}", parent=self.main_win)
        else:
            InfoBar.error("修改失败", msg, parent=self.main_win)

    def _reset_config_dir(self):
        ok, msg = self.main_win.set_config_dir(self.main_win.default_config_dir)
        if ok:
            self._refresh_config_dir_text()
            InfoBar.success("已恢复", "配置保存目录已恢复为软件当前目录下的 configs 文件夹", parent=self.main_win)
        else:
            InfoBar.error("恢复失败", msg, parent=self.main_win)

    def _detect_legacy_config(self):
        self.main_win.prompt_legacy_config_migration(manual=True)

    def _on_update_channel_changed(self, _):
        self.main_win.global_settings["update_channel"] = "beta" if self.cb_update_channel.currentIndex() == 1 else "stable"
        if hasattr(self, "lbl_channel_chip"):
            self.lbl_channel_chip.setText("更新通道 测试版" if self.main_win.global_settings["update_channel"] == "beta" else "更新通道 稳定版")
        self.main_win.save_global_settings()
        self.set_latest_version_text("最新版本：获取中...")
        self.main_win.check_updates(manual=False)

    def _check_update_now(self):
        self.main_win.check_updates(manual=True)

    def set_latest_version_text(self, text):
        self.lbl_latest_version.setText(text)
        if hasattr(self, "lbl_latest_chip"):
            self.lbl_latest_chip.setText(text.replace("：", " ", 1))

    def _refresh_cache(self):
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
            threading.Thread(target=self.main_win._async_detect, daemon=True).start()
            InfoBar.success("刷新成功", "软件缓存已清除并重新开始硬盘测速检测！", parent=self.main_win)
        except Exception as e:
            InfoBar.error("刷新失败", f"无法清除缓存文件: {e}", parent=self.main_win)

    def _export_logs(self):
        filename = f"cdisk_cleaner_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        default_dir = self.main_win.config_dir if os.path.isdir(self.main_win.config_dir) else app_root_dir()
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", os.path.join(default_dir, filename), "文本文件 (*.txt)")
        if not path:
            return
        ok, msg = self.main_win.export_logs_to_path(path)
        if ok:
            InfoBar.success("导出成功", f"日志已导出到: {path}", parent=self.main_win)
        else:
            InfoBar.error("导出失败", msg, parent=self.main_win)

    def _reset_defaults(self):
        w = MessageBox("确认恢复", "确定要将常规清理的选项恢复至默认状态吗？\n警告：这将会清除您所有已添加的自定义规则和排序！", self.main_win)
        if w.exec():
            try:
                # 重置 targets 列表
                with self.main_win._targets_lock:
                    self.main_win.targets.clear()
                    defaults = [parse_rule_entry(t) for t in default_clean_targets()]
                    defaults = [t for t in defaults if t]
                    self.main_win.targets.extend(defaults)
                    self.main_win.builtin_rule_keys = {make_rule_key(t[0], t[1], t[2], t[6]) for t in defaults}
                
                # 重绘常规清理表格
                self.main_win.pg_clean.reload_table()
                
                # 删除本地保存的配置文件
                if os.path.exists(self.main_win.config_path):
                    os.remove(self.main_win.config_path)
                if os.path.exists(self.main_win.custom_rules_path):
                    os.remove(self.main_win.custom_rules_path)
                self.main_win.deleted_builtin_rule_keys = set()
                self.main_win.global_settings["deleted_builtin_rules"] = []
                self.main_win.save_global_settings()
                    
                InfoBar.success("恢复成功", "所有配置已完全恢复为默认初始状态！", parent=self.main_win)
            except Exception as e:
                InfoBar.error("恢复失败", f"恢复默认配置时发生异常: {e}", parent=self.main_win)


# ══════════════════════════════════════════════════════════
#  页面：常规清理
# ══════════════════════════════════════════════════════════
class CleanPage(ScrollArea):
    def __init__(self, sig, targets, stop, targets_lock, parent=None):
        super().__init__(parent); self.sig=sig; self.targets=targets; self.stop=stop; self._targets_lock=targets_lock
        self.estimated_sizes = {}
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("cleanPage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        title_row = make_title_row(FIF.BROOM, "常规清理")
        badge = "管理员" if is_admin() else "非管理员"
        lbl_perm = CaptionLabel(f"当前权限：{badge}  |  长按或框选项目可拖动排序")
        setFont(lbl_perm, 11, QFont.Weight.Normal)
        lbl_perm.setTextColor(QColor(128, 128, 128))
        title_row.insertSpacing(2, 2) 
        title_row.insertWidget(3, lbl_perm, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(title_row)

        search_row = QHBoxLayout()
        self.search_input = SearchLineEdit()
        self.search_input.setPlaceholderText("搜索规则名称、路径或说明...")
        self.search_input.setFixedWidth(320)
        self.search_input.textChanged.connect(self._filter_rules)
        search_row.addWidget(self.search_input)
        search_row.addSpacing(10)
        self.cb_sort = ComboBox()
        self.cb_sort.addItems(["默认顺序", "按名称", "按路径", "按大小"])
        self.cb_sort.setFixedWidth(120)
        self.cb_sort.currentIndexChanged.connect(self._on_sort_mode_changed)
        search_row.addWidget(self.cb_sort)
        search_row.addStretch()
        v.addLayout(search_row)

        opt=QHBoxLayout(); opt.setSpacing(8)
        self.chk_perm=CheckBox("强力模式：永久删除"); self.chk_perm.setChecked(True); opt.addWidget(self.chk_perm)
        self.chk_rst=CheckBox("创建还原点"); opt.addWidget(self.chk_rst)
        opt.addStretch()
        
        b_add = PushButton(FIF.ADD, "新建"); b_add.clicked.connect(self.do_add_rule); opt.addWidget(b_add)
        b_del = PushButton(FIF.DELETE, "删除"); b_del.clicked.connect(self.do_del_rule); opt.addWidget(b_del)
        b_imp = PushButton(FIF.DOCUMENT, "导入"); b_imp.clicked.connect(self.do_import_rules); opt.addWidget(b_imp)
        b_exp = PushButton(FIF.SAVE, "导出"); b_exp.clicked.connect(self.do_export_rules); opt.addWidget(b_exp)
        v.addLayout(opt)

        self.tbl=DragSortTableWidget(); self.tbl.setColumnCount(5); self.tbl.setHorizontalHeaderLabels([" ","项目","路径","说明","大小"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,2))
        self.tbl.setSortingEnabled(False)
        
        self.reload_table() # 初始化时渲染表格
        
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 150); self.tbl.setColumnWidth(2, 400); self.tbl.setColumnWidth(3, 200); self.tbl.setColumnWidth(4, 85)
        self.tbl.setIconSize(QSize(24, 24))
        style_table(self.tbl); v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PushButton(FIF.UNIT,"估算"); b1.setFixedHeight(30); b1.clicked.connect(self.do_est); br.addWidget(b1)
        self.btn_sel_all = PushButton(FIF.ACCEPT, "全选"); self.btn_sel_all.setFixedHeight(30)
        self.btn_sel_all.clicked.connect(self.toggle_sel_all); br.addWidget(self.btn_sel_all)
        br.addStretch()
        bc=PrimaryPushButton(FIF.DELETE,"开始清理"); bc.setFixedHeight(30); bc.clicked.connect(self.do_clean); br.addWidget(bc)
        bs=PushButton(FIF.CANCEL,"停止"); bs.setFixedHeight(30); bs.clicked.connect(lambda:self.stop.set()); br.addWidget(bs); v.addLayout(br)

        pr=QHBoxLayout(); self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pr.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pr.addWidget(self.sl); v.addLayout(pr)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志..."); v.addWidget(self.log)

    def reload_table(self):
        self.tbl.setRowCount(0)
        display_entries = self._get_display_entries()
        self.tbl.setRowCount(len(display_entries))
        for i, (src_idx, entry) in enumerate(display_entries):
            nm, pa, tp, en, nt, is_c, pattern = parse_rule_entry(entry)
            disp_name = f"{nm} (自定义)" if is_c else nm
            chk_item = make_check_item(en)
            name_item = QTableWidgetItem(disp_name)
            name_item.setData(Qt.ItemDataRole.UserRole, (src_idx, nm, pa, tp, is_c, pattern))
            
            self.tbl.setItem(i, 0, chk_item)
            self.tbl.setItem(i, 1, name_item)
            self.tbl.setItem(i, 2, QTableWidgetItem(rule_display_target(pa, tp, pattern)))
            self.tbl.setItem(i, 3, QTableWidgetItem(nt))
            size_item = SizeTableWidgetItem("")
            size_val = self.estimated_sizes.get(self._rule_cache_key(entry), 0)
            size_item.setData(Qt.ItemDataRole.UserRole, size_val)
            size_item.setText(human_size(size_val) if size_val > 0 else "")
            self.tbl.setItem(i, 4, size_item)
        self._filter_rules(self.search_input.text())

    def _rule_cache_key(self, entry):
        nm, pa, tp, _, _, _, pattern = parse_rule_entry(entry)
        return make_rule_key(nm, pa, tp, pattern)

    def _get_display_entries(self):
        with self._targets_lock:
            items = list(enumerate(self.targets))
        mode = self.cb_sort.currentIndex() if hasattr(self, "cb_sort") else 0
        if mode == 1:
            items.sort(key=lambda x: str(parse_rule_entry(x[1])[0]).lower())
        elif mode == 2:
            items.sort(key=lambda x: rule_display_target(parse_rule_entry(x[1])[1], parse_rule_entry(x[1])[2], parse_rule_entry(x[1])[6]).lower())
        elif mode == 3:
            items.sort(key=lambda x: self.estimated_sizes.get(self._rule_cache_key(x[1]), 0), reverse=True)
        return items

    def _on_sort_mode_changed(self, _):
        is_default = self.cb_sort.currentIndex() == 0
        self.tbl.setDragEnabled(is_default)
        self.reload_table()

    def _filter_rules(self, text):
        query = str(text or "").strip().lower()
        for row in range(self.tbl.rowCount()):
            cells = []
            for col in (1, 2, 3):
                item = self.tbl.item(row, col)
                cells.append(item.text().lower() if item and item.text() else "")
            matched = (not query) or any(query in cell for cell in cells)
            self.tbl.setRowHidden(row, not matched)

    def toggle_sel_all(self):
        rows = [r for r in range(self.tbl.rowCount()) if not self.tbl.isRowHidden(r)]
        if not rows: return
        all_checked = True
        for r in rows:
            if not is_row_checked(self.tbl, r):
                all_checked = False; break
        new_state = not all_checked
        for r in rows: set_row_checked(self.tbl, r, new_state)
        self._sync()
        
        if new_state:
            self.btn_sel_all.setText("取消全选"); self.btn_sel_all.setIcon(FIF.CLOSE)
        else:
            self.btn_sel_all.setText("全选"); self.btn_sel_all.setIcon(FIF.ACCEPT)

    def _sync(self):
        new_targets = []
        indexed_updates = []
        mode = self.cb_sort.currentIndex() if hasattr(self, "cb_sort") else 0
        for r in range(self.tbl.rowCount()):
            name_item = self.tbl.item(r, 1)
            if not name_item: continue

            user_data = name_item.data(Qt.ItemDataRole.UserRole)
            if user_data:
                src_idx, nm, pa, tp, is_c, pattern = user_data
            else: continue

            en = is_row_checked(self.tbl, r)
            nt = self.tbl.item(r, 3).text() if self.tbl.item(r, 3) else ""
            new_entry = (nm, pa, tp, en, nt, is_c, normalize_rule_pattern(tp, pattern, nt))
            if mode == 0:
                new_targets.append(new_entry)
            else:
                indexed_updates.append((src_idx, new_entry))

        with self._targets_lock:
            if mode == 0 and new_targets:
                self.targets[:] = new_targets
            else:
                for src_idx, entry in indexed_updates:
                    if 0 <= src_idx < len(self.targets):
                        self.targets[src_idx] = entry

    def _try_rst(self):
        if not getattr(self, 'chk_rst', None) or not self.chk_rst.isChecked(): return
        if not is_admin():
            self.sig.clean_log.emit("[还原点] 需管理员权限，跳过"); return
        self.sig.clean_log.emit("[还原点] 正在创建系统还原点，请稍候...")
        try:
            r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass",
                "Checkpoint-Computer","-Description","'CleanTool_Backup'","-RestorePointType","MODIFY_SETTINGS"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                self.sig.clean_log.emit("[还原点] 创建成功！")
            else:
                self.sig.clean_log.emit(f"[还原点] 创建失败 (系统可能未开启保护或达到限制): {r.stderr.strip()[:100]}")
        except Exception as e:
            self.sig.clean_log.emit(f"[还原点] 创建异常: {e}")

    def save_custom_rules(self):
        self._sync()
        with self._targets_lock:
            customs = [t for t in self.targets if t[5]]
        path = self.window().custom_rules_path
        try:
            payload = [serialize_rule_entry(t) for t in customs]
            payload = [t for t in payload if t is not None]
            write_json_file_atomic(path, payload, ensure_ascii=False, indent=2)
        except Exception as e:
            log_background_error("保存自定义规则失败", e)

    def do_add_rule(self):
        w = AddRuleDialog(self.window())
        if w.exec():
            nm, pa, tp, en, nt, is_c, pattern = w.get_data()
            if not nm or not pa:
                InfoBar.error("错误", "名称和路径不能为空", parent=self.window()); return
            if tp == "glob" and not pattern:
                InfoBar.error("错误", "glob 规则必须填写匹配模式", parent=self.window()); return
            new_rule = (nm, pa, tp, en, nt, is_c, pattern)
            with self._targets_lock:
                self.targets.append(new_rule)
            self.reload_table()
            self.save_custom_rules()
            InfoBar.success("成功", f"规则 '{nm}' 已添加！", parent=self.window())

    def do_del_rule(self):
        # 优先使用“选中行”，若用户只勾选复选框也允许删除
        sel_rows = []
        try:
            sel_rows = [idx.row() for idx in self.tbl.selectionModel().selectedRows()]
        except Exception:
            sel_rows = []

        if not sel_rows:
            cur = self.tbl.currentRow()
            if cur >= 0:
                sel_rows = [cur]

        checked_rows = [r for r in range(self.tbl.rowCount()) if not self.tbl.isRowHidden(r) and is_row_checked(self.tbl, r)]
        candidate_rows = sel_rows if sel_rows else checked_rows
        candidate_rows = sorted(set(candidate_rows))

        if not candidate_rows:
            InfoBar.warning("提示", "请先选中一行，或勾选至少一条规则！", parent=self.window())
            return

        self._sync()
        builtin_keys = getattr(self.window(), "builtin_rule_keys", set())
        protect_builtin = self.window().global_settings.get("protect_builtin_rules", True)
        deleted_builtin_now = []

        deletable_keys = []
        protected_count = 0
        for row in candidate_rows:
            item = self.tbl.item(row, 1)
            if not item:
                continue
            user_data = item.data(Qt.ItemDataRole.UserRole)
            if not user_data:
                continue
            nm, pa, tp, is_c, pattern = user_data
            rule_key = make_rule_key(nm, pa, tp, pattern)
            if protect_builtin and rule_key in builtin_keys:
                protected_count += 1
                continue
            if rule_key in builtin_keys:
                deleted_builtin_now.append(rule_key)
            deletable_keys.append((nm, pa, tp, is_c, pattern))

        # 去重，避免重复删除同一规则
        deletable_keys = list(dict.fromkeys(deletable_keys))

        if not deletable_keys:
            InfoBar.error("拒绝操作", "所选规则均为内置默认规则，无法删除！(系统设置可更改)", parent=self.window())
            return

        tip = f"永久删除 {len(deletable_keys)} 条自定义规则？"
        if protected_count > 0:
            tip += f"\n（将自动跳过 {protected_count} 条内置受保护规则）"
        if not MessageBox("确认", tip, self.window()).exec():
            return

        del_key_set = set(deletable_keys)

        # 先删数据源，避免行号变化导致错删
        with self._targets_lock:
            for i in range(len(self.targets) - 1, -1, -1):
                nm, pa, tp, _, _, is_c, pattern = parse_rule_entry(self.targets[i])
                if (nm, pa, tp, is_c, pattern) in del_key_set:
                    self.targets.pop(i)

        if deleted_builtin_now:
            deleted_keys = getattr(self.window(), "deleted_builtin_rule_keys", set())
            deleted_keys.update(deleted_builtin_now)
            self.window().deleted_builtin_rule_keys = deleted_keys
            self.window().global_settings["deleted_builtin_rules"] = [list(k) for k in sorted(deleted_keys)]
            self.window().save_global_settings()

        self.reload_table()
        self.save_custom_rules()
        if protected_count > 0:
            InfoBar.success(
                "已清除",
                f"已清除 {len(deletable_keys)} 条规则，已跳过 {protected_count} 条内置规则",
                parent=self.window()
            )
        else:
            InfoBar.success("已清除", f"已清除 {len(deletable_keys)} 条规则", parent=self.window())

    def do_export_rules(self):
        self._sync()
        customs = [t for t in self.targets if t[5]]
        if not customs: InfoBar.warning("提示", "当前没有自定义规则可以导出", parent=self.window()); return
        path, _ = QFileDialog.getSaveFileName(self, "导出规则集", "CleanRules.json", "JSON 文件 (*.json)")
        if path:
            payload = [serialize_rule_entry(t) for t in customs]
            payload = [t for t in payload if t is not None]
            write_json_file_atomic(path, payload, ensure_ascii=False, indent=2)
            InfoBar.success("导出成功", f"规则已保存至: {path}", parent=self.window())

    def import_rules_from_path(self, path, source_name="规则集"):
        if not path or not os.path.exists(path):
            InfoBar.error("导入失败", f"未找到 {source_name}: {display_path(path)}", parent=self.window())
            return False
        try:
            with open(path, 'r', encoding='utf-8') as f:
                rules = json.load(f)
            added = 0
            skipped = 0
            with self._targets_lock:
                existing_keys = {make_rule_key(t[0], t[1], t[2], t[6] if len(t) >= 7 else "") for t in self.targets}
            to_add = []
            for r_data in rules:
                parsed = parse_rule_entry(r_data, force_custom=True)
                if not parsed:
                    continue
                nm, pa, tp, en, nt, is_custom, pattern = parsed
                rule_key = make_rule_key(nm, pa, tp, pattern)
                if rule_key in existing_keys:
                    skipped += 1
                    continue
                existing_keys.add(rule_key)
                to_add.append(parsed)
                added += 1
            if to_add:
                with self._targets_lock:
                    self.targets.extend(to_add)
            if added > 0:
                self.reload_table()
                self.save_custom_rules()
                msg = f"{source_name} 已导入 {added} 条规则"
                if skipped > 0:
                    msg += f"，跳过 {skipped} 条重复规则"
                InfoBar.success("导入成功", msg, parent=self.window())
                return True
            else:
                InfoBar.warning("提示", f"{source_name} 未导入任何规则（可能全部重复）", parent=self.window())
                return False
        except Exception as e:
            InfoBar.error("导入失败", f"文件读取错误: {e}", parent=self.window())
            return False

    def apply_estimate(self, idx, size_val):
        with self._targets_lock:
            if not (0 <= idx < len(self.targets)):
                return
            entry = self.targets[idx]
        self.estimated_sizes[self._rule_cache_key(entry)] = size_val
        if hasattr(self, "cb_sort") and self.cb_sort.currentIndex() == 3:
            self.reload_table()
            return
        for row in range(self.tbl.rowCount()):
            name_item = self.tbl.item(row, 1)
            if not name_item:
                continue
            user_data = name_item.data(Qt.ItemDataRole.UserRole)
            if user_data and user_data[0] == idx:
                item = self.tbl.item(row, 4)
                if item:
                    item.setData(Qt.ItemDataRole.UserRole, size_val)
                    item.setText(human_size(size_val) if size_val > 0 else "")
                break

    def do_import_rules(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入规则集",
            app_root_dir(),
            "JSON 文件 (*.json)"
        )
        if path:
            self.window().import_rules_from_path(path, "外部规则集")

    def do_est(self): 
        self.tbl.setDragEnabled(False) 
        self._sync(); self.stop.clear(); threading.Thread(target=self._est_w,daemon=True).start()
        
    def _est_w(self):
        t0 = time.time()
        with self._targets_lock:
            its=[(i,t) for i,t in enumerate(self.targets) if t[3]]
        if not its:
            self.sig.clean_done.emit(f"估算失败：未勾选任何项目")
            return

        job_queue = queue.Queue()
        result_queue = queue.Queue()
        worker_count = min(max(1, len(its)), 8)

        for item in its:
            job_queue.put(item)

        # 估算主要是文件系统 IO，这里并行多个规则能明显缩短总耗时
        def _worker():
            while not self.stop.is_set():
                try:
                    idx, entry = job_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    size = estimate_rule_size(entry, stop_flag=self.stop)
                    result_queue.put((idx, size))
                finally:
                    job_queue.task_done()

        workers = []
        for _ in range(worker_count):
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            workers.append(t)

        self.sig.clean_prog.emit(0,len(its))
        done_count = 0
        while done_count < len(its):
            if self.stop.is_set():
                for t in workers:
                    t.join(timeout=0.1)
                self.sig.clean_done.emit(f"估算已取消，耗时 {time.time()-t0:.1f} 秒")
                return
            try:
                idx, size = result_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            done_count += 1
            self.sig.est.emit(idx, size)
            self.sig.clean_prog.emit(done_count, len(its))

        for t in workers:
            t.join(timeout=0.1)
        self.sig.clean_done.emit(f"估算完成，耗时 {time.time()-t0:.1f} 秒")

    def do_clean(self):
        self.tbl.setDragEnabled(False)
        self._sync()
        selected_rules = [parse_rule_entry(t) for t in self.targets if t[3]]
        selected_rules = [t for t in selected_rules if t]

        risky_lines = []
        for entry in selected_rules:
            risk = get_rule_runtime_risk(entry)
            if risk:
                risky_lines.append(risk)

        if risky_lines:
            preview = risky_lines[:8]
            if len(risky_lines) > 8:
                preview.append(f"另有 {len(risky_lines) - 8} 项未展开")
            content = (
                "当前勾选项中检测到高风险清理规则：\n\n"
                + "\n".join(f"- {line}" for line in preview)
                + "\n\n这些规则可能影响系统、程序或用户目录是否继续清理？"
            )
            if not MessageBox("风险提示", content, self.window()).exec():
                self.tbl.setDragEnabled(True)
                return
        if self.chk_perm.isChecked():
            if not MessageBox("确认", "当前为强力模式，删除后无法恢复继续？", self.window()).exec(): 
                self.tbl.setDragEnabled(True)
                return
        self.stop.clear(); threading.Thread(target=self._cln_w, daemon=True).start()
    
    def _cln_w(self):
        t0 = time.time()
        import fnmatch; pm=self.chk_perm.isChecked()
        with self._targets_lock:
            sel=[parse_rule_entry(t) for t in self.targets if t[3]]
        sel=[t for t in sel if t]
        if not sel: return
        
        # 清理前创建还原点
        self._try_rst()
        
        ok=fl=st=0; tot=len(sel); lf=lambda s:self.sig.clean_log.emit(s)
        for nm, pa, tp, _, nt, _, pattern in sel:
            if self.stop.is_set():
                self.sig.clean_done.emit(f"清理已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            st+=1; p=expand_env(pa)
            try:
                if tp=="dir":
                    try:
                        entries = os.listdir(p)
                    except OSError:
                        entries = []
                    for e in entries:
                        if self.stop.is_set(): break
                        if delete_path(os.path.join(p,e),pm,lf): ok+=1
                        else: fl+=1
                elif tp=="glob":
                    rule_pattern = normalize_rule_pattern(tp, pattern, nt)
                    try:
                        entries = os.listdir(p)
                    except OSError:
                        entries = []
                    for f in entries:
                        if self.stop.is_set(): break
                        if fnmatch.fnmatch(f.lower(), rule_pattern.lower()):
                            if delete_path(os.path.join(p,f),pm,lf): ok+=1
                            else: fl+=1
                elif tp=="file":
                    if delete_path(p,pm,lf): ok+=1
                    else: fl+=1
            except Exception: fl+=1
            self.sig.clean_prog.emit(st,tot)
        self.sig.clean_done.emit(f"清理完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")


class LeftoversDialog(MessageBoxBase):
    def __init__(self, parent, app_name, publisher, install_dir, uninst_reg):
        super().__init__(parent)
        self.app_name = app_name
        self.publisher = publisher
        self.install_dir = install_dir
        self.uninst_reg = uninst_reg
        self.leftovers = {"files": [], "regs": [], "services": [], "tasks": []}
        self.risk_summary = {"normal": 0, "high": 0, "blocked": 0}
        
        self.customTitle = TitleLabel(f"发现 '{app_name}' 的残留痕迹")
        setFont(self.customTitle, 16, QFont.Weight.Bold)
        self.viewLayout.addWidget(self.customTitle)
        self.viewLayout.addSpacing(10) 

        self.tipLabel = CaptionLabel("高风险项默认未勾选，极高风险项已拦截并禁止强力删除")
        self.tipLabel.setWordWrap(True)
        self.tipLabel.setTextColor(QColor(128, 128, 128))
        self.viewLayout.addWidget(self.tipLabel)
        self.viewLayout.addSpacing(6)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["残留项目", "路径"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setMinimumHeight(250)
        self.viewLayout.addWidget(self.tree)
        
        self.yesButton.setText("删除选中项")
        self.cancelButton.setText("取消")
        
        self.widget.setMinimumWidth(600)
        self._scan_leftovers()

    def _add_candidate(self, records, path, source="explicit"):
        text = str(path or "").strip()
        if not text:
            return
        existing = {str(item.get("path", "")).lower() for item in records if isinstance(item, dict)}
        if text.lower() in existing:
            return
        records.append({"path": text, "source": source})

    def _build_item_payload(self, category, raw_data, name, path, detail, source="explicit", service_kind=""):
        protection = classify_uninstall_leftover(
            category,
            name=name,
            path=path,
            detail=detail,
            source=source,
            service_kind=service_kind
        )
        self.risk_summary[protection["tier"]] = self.risk_summary.get(protection["tier"], 0) + 1
        return {
            "data": raw_data,
            "name": name,
            "path": path,
            "detail": detail,
            "source": source,
            "service_kind": service_kind,
            "protection": protection
        }

    def _make_child_item(self, parent_item, title, detail, payload):
        protection = payload["protection"]
        source_text = "关键词推断" if payload.get("source") == "keyword" else ""
        detail_parts = [detail]
        if source_text:
            detail_parts.append(source_text)
        if protection["reason"]:
            detail_parts.append(protection["reason"])
        display_title = title
        if protection["tier"] == "blocked":
            display_title = f"[已拦截] {title}"
        elif protection["tier"] == "high":
            display_title = f"[高风险] {title}"

        child = QTreeWidgetItem(parent_item, [display_title, " | ".join(part for part in detail_parts if part)])
        if protection["tier"] == "blocked":
            child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        else:
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            child.setCheckState(0, Qt.CheckState.Checked if protection["default_checked"] else Qt.CheckState.Unchecked)
        return child
        
    def _scan_leftovers(self):
        paths_to_check = []
        if self.install_dir and os.path.exists(self.install_dir):
            self._add_candidate(paths_to_check, self.install_dir, source="explicit")
            
        app_data = os.environ.get("APPDATA", "")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        prog_data = os.environ.get("PROGRAMDATA", "")
        
        keywords = [k for k in [self.publisher, self.app_name.split()[0]] if k and len(k) > 2]
        for base in [app_data, local_app_data, prog_data]:
            if not base: continue
            for kw in keywords:
                guess = os.path.join(base, kw)
                if os.path.exists(guess):
                    self._add_candidate(paths_to_check, guess, source="keyword")

        startup_dirs = [
            os.path.join(app_data, r"Microsoft\Windows\Start Menu\Programs\Startup"),
            os.path.join(prog_data, r"Microsoft\Windows\Start Menu\Programs\Startup")
        ]
        for startup_dir in startup_dirs:
            if not startup_dir or not os.path.isdir(startup_dir):
                continue
            try:
                for name in os.listdir(startup_dir):
                    full_path = os.path.join(startup_dir, name)
                    lower_name = name.lower()
                    if any(kw in lower_name for kw in keywords):
                        self._add_candidate(paths_to_check, full_path, source="keyword")
            except Exception:
                pass
                    
        regs_to_check = []
        if self.uninst_reg:
            self._add_candidate(regs_to_check, self.uninst_reg, source="explicit")
        
        for base_key_str, hkey in [("HKCU\\Software", winreg.HKEY_CURRENT_USER), ("HKLM\\Software", winreg.HKEY_LOCAL_MACHINE)]:
            for kw in keywords:
                try:
                    with winreg.OpenKey(hkey, f"Software\\{kw}"):
                        self._add_candidate(regs_to_check, f"{base_key_str}\\{kw}", source="keyword")
                except OSError: pass

        services = scan_leftover_services(keywords, self.install_dir)
        tasks = scan_leftover_tasks(keywords, self.install_dir)
        self._populate_tree(paths_to_check, regs_to_check, services, tasks)

    def _populate_tree(self, files, regs, services, tasks):
        if files:
            f_root = QTreeWidgetItem(self.tree, ["文件与文件夹"])
            for f in files:
                path = str(f.get("path", "")) if isinstance(f, dict) else str(f)
                source = str(f.get("source", "explicit")) if isinstance(f, dict) else "explicit"
                item_type = "文件夹" if os.path.isdir(path) else "文件"
                payload = self._build_item_payload("file", path, item_type, path, path, source=source)
                child = self._make_child_item(f_root, item_type, path, payload)
                self.leftovers["files"].append((child, payload))
            f_root.setExpanded(True)
            
        if regs:
            r_root = QTreeWidgetItem(self.tree, ["注册表项"])
            for r in regs:
                path = str(r.get("path", "")) if isinstance(r, dict) else str(r)
                source = str(r.get("source", "explicit")) if isinstance(r, dict) else "explicit"
                payload = self._build_item_payload("reg", path, "注册表键", path, path, source=source)
                child = self._make_child_item(r_root, "注册表键", path, payload)
                self.leftovers["regs"].append((child, payload))
            r_root.setExpanded(True)

        if services:
            s_root = QTreeWidgetItem(self.tree, ["服务与驱动"])
            for service in services:
                detail = service["reg_path"]
                if service.get("image_path"):
                    detail = f'{service.get("kind", "服务")} | {service["image_path"]}'
                payload = self._build_item_payload(
                    "service",
                    service,
                    service.get("display", service.get("name", "服务")),
                    service.get("reg_path", ""),
                    detail,
                    service_kind=service.get("kind", "服务")
                )
                child = self._make_child_item(
                    s_root,
                    f'{service.get("kind", "服务")}：{service["display"]}',
                    detail,
                    payload
                )
                self.leftovers["services"].append((child, payload))
            s_root.setExpanded(True)

        if tasks:
            t_root = QTreeWidgetItem(self.tree, ["计划任务"])
            for task in tasks:
                detail = task.get("actions") or task["full_name"]
                payload = self._build_item_payload(
                    "task",
                    task,
                    task.get("name", "计划任务"),
                    task.get("full_name", ""),
                    detail
                )
                child = self._make_child_item(t_root, f'计划任务：{task["name"]}', detail, payload)
                self.leftovers["tasks"].append((child, payload))
            t_root.setExpanded(True)

    def get_selected_items(self):
        selected_high = 0

        def _collect(key):
            nonlocal selected_high
            results = []
            for item, payload in self.leftovers[key]:
                if item.checkState(0) != Qt.CheckState.Checked:
                    continue
                if payload["protection"]["tier"] == "high":
                    selected_high += 1
                results.append(payload["data"])
            return results

        return {
            "files": _collect("files"),
            "regs": _collect("regs"),
            "services": _collect("services"),
            "tasks": _collect("tasks"),
            "summary": {
                "normal": self.risk_summary.get("normal", 0),
                "high": self.risk_summary.get("high", 0),
                "blocked": self.risk_summary.get("blocked", 0),
                "selected_high": selected_high
            }
        }

class UninstallPage(ScrollArea):
    def __init__(self, sig, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("uninstallPage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        v.addLayout(make_title_row(FIF.APPLICATION, "应用强力卸载"))
        v.addWidget(CaptionLabel("标准卸载后自动扫描残留，或直接强力摧毁顽固软件的目录与注册表"))

        search_layout = QHBoxLayout()
        self.search_input = SearchLineEdit()
        self.search_input.setPlaceholderText("搜索软件名称或发布者...")
        self.search_input.setFixedWidth(300)
        self.search_input.textChanged.connect(self._filter_table)
        search_layout.addWidget(self.search_input)
        search_layout.addSpacing(10)
        self.chk_silent = CheckBox("优先静默卸载")
        self.chk_silent.setToolTip("优先尝试 MSI、Inno、NSIS、InstallShield、Squirrel、Burn/WiX 等常见静默卸载参数")
        search_layout.addWidget(self.chk_silent)
        search_layout.addSpacing(10)
        search_layout.addWidget(CaptionLabel("超时(分钟):"))
        self.sp_timeout = SpinBox()
        self.sp_timeout.setRange(1, 120)
        self.sp_timeout.setValue(20)
        self.sp_timeout.setFixedWidth(120)
        search_layout.addWidget(self.sp_timeout)
        search_layout.addStretch()
        v.addLayout(search_layout)

        self.tbl=TableWidget(); self.tbl.setColumnCount(7)
        self.tbl.setHorizontalHeaderLabels([" ","分类","名称","版本","发布者","安装目录","隐藏卸载命令"])
        self.tbl.verticalHeader().setVisible(False); self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,5))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 70); self.tbl.setColumnWidth(2, 245); self.tbl.setColumnWidth(3, 100); self.tbl.setColumnWidth(4, 180); self.tbl.setColumnWidth(5, 300); self.tbl.setColumnHidden(6, True)
        style_table(self.tbl); v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PushButton(FIF.SYNC,"刷新列表"); b1.setFixedHeight(30); b1.clicked.connect(self.do_scan); br.addWidget(b1)
        br.addStretch()
        b2=PushButton(FIF.REMOVE,"标准卸载"); b2.setFixedHeight(30); b2.clicked.connect(self.do_std_uninstall); br.addWidget(b2)
        b3=PrimaryPushButton(FIF.DELETE,"强力卸载"); b3.setFixedHeight(30); b3.clicked.connect(self.do_force_uninstall); br.addWidget(b3)
        v.addLayout(br)

        pg=QHBoxLayout(); self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pg.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pg.addWidget(self.sl); v.addLayout(pg)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志..."); v.addWidget(self.log)

    def _filter_table(self, text):
        search_str = text.lower()
        for r in range(self.tbl.rowCount()):
            name = self.tbl.item(r, 2).text().lower()
            publisher = self.tbl.item(r, 4).text().lower()
            match = search_str in name or search_str in publisher
            self.tbl.setRowHidden(r, not match)

    def do_scan(self):
        self.stop.clear(); self.sig.uninst_clr.emit(); self.sig.uninst_log.emit("开始扫描系统软件列表...")
        threading.Thread(target=self._scan_w, daemon=True).start()

    def _scan_w(self):
        t0 = time.time()
        software = []
        scan_errors = []
        error_count = 0
        keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
        
        for hkey, subkey_str in keys:
            if self.stop.is_set():
                self.sig.uninst_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
                return
            try:
                with winreg.OpenKey(hkey, subkey_str) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub_key:
                                try:
                                    disp, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                                    if disp:
                                        def get_val(name):
                                            try: return winreg.QueryValueEx(sub_key, name)[0]
                                            except OSError: return ""

                                        ver = get_val("DisplayVersion")
                                        pub = get_val("Publisher")
                                        cmd = get_val("UninstallString")
                                        loc = get_val("InstallLocation")

                                        d_icon = get_val("DisplayIcon")
                                        icon_path = ""
                                        if d_icon:
                                            icon_path = d_icon.split(',')[0].strip(' "')
                                        inferred_loc = infer_install_location(disp, pub, loc, cmd, d_icon)

                                        reg = f"{'HKLM' if hkey==winreg.HKEY_LOCAL_MACHINE else 'HKCU'}\\{subkey_str}\\{sub_name}"
                                        meta = classify_uninstall_entry(disp, pub, inferred_loc or loc, reg)
                                        software.append({
                                            "name": disp,
                                            "version": ver,
                                            "publisher": pub,
                                            "cmd": cmd,
                                            "location": inferred_loc or loc,
                                            "reg": reg,
                                            "icon_path": icon_path,
                                            "category": meta["category"],
                                            "is_risky": meta["is_risky"],
                                            "risk_kind": meta["risk_kind"],
                                            "risk_reason": meta["risk_reason"]
                                        })
                                except Exception as e:
                                    error_count += 1
                                    append_error_sample(scan_errors, f"{subkey_str}\\{sub_name} -> {format_exception_text(e)}")
                        except Exception as e:
                            error_count += 1
                            append_error_sample(scan_errors, f"{subkey_str} 第 {i + 1} 项读取失败 -> {format_exception_text(e)}")
            except Exception as e:
                error_count += 1
                append_error_sample(scan_errors, f"{subkey_str} 无法打开 -> {format_exception_text(e)}")
        
        seen = set()
        unique = []
        for s in software:
            dedupe_key = (s["name"], s["publisher"], s["location"])
            if dedupe_key not in seen: 
                seen.add(dedupe_key)
                unique.append(s)

        unique.sort(key=lambda x: (0 if x["category"] == "用户" else 1, x["name"].lower()))

        user_count = 0
        system_count = 0
        for item in unique:
            if item["category"] == "系统":
                system_count += 1
            else:
                user_count += 1
            self.sig.uninst_add.emit(item)

        if error_count:
            emit_error_summary(self.sig.uninst_log.emit, "扫描异常", scan_errors, error_count)
        self.sig.uninst_done.emit(f"成功扫描出 {len(unique)} 个软件（用户 {user_count}，系统 {system_count}），耗时 {time.time()-t0:.1f} 秒")

    def _get_checked_rows_data(self):
        rows = []
        for r in range(self.tbl.rowCount()):
            if is_row_checked(self.tbl, r) and not self.tbl.isRowHidden(r):
                nm = self.tbl.item(r, 2).text()
                pub = self.tbl.item(r, 4).text()
                loc = self.tbl.item(r, 5).text()
                hidden_item = self.tbl.item(r, 6)
                cmd = hidden_item.text() if hidden_item else ""
                reg = hidden_item.data(Qt.ItemDataRole.UserRole) if hidden_item else ""
                meta = hidden_item.data(Qt.ItemDataRole.UserRole + 1) if hidden_item else {}
                rows.append({
                    "row": r,
                    "name": nm,
                    "publisher": pub,
                    "location": loc,
                    "cmd": cmd,
                    "reg": reg,
                    "category": meta.get("category", "用户"),
                    "is_risky": bool(meta.get("is_risky", False)),
                    "risk_kind": meta.get("risk_kind", ""),
                    "risk_reason": meta.get("risk_reason", "")
                })
        return rows

    def _confirm_risky_selection(self, data, action_text):
        risky_items = [item for item in data if item.get("is_risky")]
        if not risky_items:
            return True

        critical_items = [item for item in risky_items if item.get("risk_kind") == "critical"]
        system_items = [item for item in risky_items if item.get("risk_kind") == "system"]
        impact_items = [item for item in risky_items if item.get("risk_kind") not in {"system", "critical"}]
        lines = ["本次勾选项目中包含高风险卸载项"]

        if critical_items:
            lines.append("")
            lines.append(f"极高风险组件：{len(critical_items)} 项")
            lines.extend(f"- {item['name']}" for item in critical_items[:5])
            if len(critical_items) > 5:
                lines.append(f"- 另有 {len(critical_items) - 5} 项未展开")

        if system_items:
            lines.append("")
            lines.append(f"系统软件/组件：{len(system_items)} 项")
            lines.extend(f"- {item['name']}" for item in system_items[:5])
            if len(system_items) > 5:
                lines.append(f"- 另有 {len(system_items) - 5} 项未展开")

        if impact_items:
            lines.append("")
            lines.append(f"可能影响系统的软件：{len(impact_items)} 项")
            lines.extend(f"- {item['name']}" for item in impact_items[:5])
            if len(impact_items) > 5:
                lines.append(f"- 另有 {len(impact_items) - 5} 项未展开")

        lines.append("")
        lines.append(f"继续{action_text}可能导致驱动、运行库、浏览器内核、安全防护或其他依赖组件异常是否继续？")
        return MessageBox("风险提示", "\n".join(lines), self.window()).exec()

    def _confirm_leftover_protection_summary(self, app_name, picked):
        summary = picked.get("summary", {}) if isinstance(picked, dict) else {}
        selected_high = int(summary.get("selected_high", 0) or 0)
        blocked = int(summary.get("blocked", 0) or 0)
        if selected_high <= 0 and blocked <= 0:
            return True

        lines = [f"'{app_name}' 的残留项中包含受保护内容"]
        if selected_high > 0:
            lines.append("")
            lines.append(f"- 你手动勾选了 {selected_high} 个高风险残留项")
        if blocked > 0:
            lines.append("")
            lines.append(f"- 另有 {blocked} 个极高风险残留项已被拦截，不会进入强力删除")
        lines.append("")
        lines.append("继续仅会删除你当前勾选的项目。是否继续？")
        return MessageBox("分级保护确认", "\n".join(lines), self.window()).exec()

    def do_std_uninstall(self):
        data = self._get_checked_rows_data()
        if not data:
            self.sig.uninst_log.emit("请先勾选至少一个要卸载的软件！"); return
        if not self._confirm_risky_selection(data, "标准卸载"):
            self.sig.uninst_log.emit("已取消高风险标准卸载操作")
            return
        self.stop.clear()
        threading.Thread(
            target=self._std_uninstall_w,
            args=(data, self.chk_silent.isChecked(), self.sp_timeout.value() * 60),
            daemon=True
        ).start()

    def _std_uninstall_w(self, data, prefer_silent=False, timeout_sec=1200):
        t0 = time.time()
        ok = fl = sk = 0
        tot = len(data)
        for i, item in enumerate(data, 1):
            r = item["row"]; nm = item["name"]; pub = item["publisher"]; loc = item["location"]; cmd = item["cmd"]; reg = item["reg"]
            if self.stop.is_set():
                self.sig.uninst_done.emit(f"标准卸载已取消：成功 {ok}，失败 {fl}，跳过 {sk}，耗时 {time.time()-t0:.1f} 秒")
                return

            if not cmd:
                self.sig.uninst_log.emit(f"[标准卸载] 跳过 {nm}：未提供卸载命令，请改用强力卸载")
                sk += 1
                self.sig.uninst_prog.emit(i, tot)
                continue

            run_cmd, mode_text = build_uninstall_command(cmd, prefer_silent=prefer_silent)
            self.sig.uninst_log.emit(f"[标准卸载] 正在调用{mode_text}卸载: {nm}")
            try:
                # 使用 cmd /c 显式调用，避免 shell=True 的隐式解析风险
                proc = subprocess.Popen(
                    ["cmd", "/c", run_cmd],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                try:
                    proc.wait(timeout=max(30, int(timeout_sec)))
                except subprocess.TimeoutExpired:
                    terminate_process_tree(proc.pid)
                    fl += 1
                    self.sig.uninst_log.emit(f"[标准卸载] 超时已终止: {nm}（超过 {max(30, int(timeout_sec)) // 60} 分钟）")
                    self.sig.uninst_prog.emit(i, tot)
                    continue

                if proc.returncode not in (0, None):
                    fl += 1
                    self.sig.uninst_log.emit(f"[标准卸载] 返回码异常: {nm} -> {proc.returncode}")
                    self.sig.uninst_prog.emit(i, tot)
                    continue

                ok += 1
                verify_msgs = self._verify_uninstall_result(nm, loc, reg)
                for msg in verify_msgs:
                    self.sig.uninst_log.emit(msg)

                # 串行等待用户处理“是否扫描残留”的弹窗，避免多选时上下文错位
                self._current_uninstalling = (r, nm, pub, loc, reg)
                self._leftover_prompt_done = threading.Event()
                self._leftover_prompt_done.clear()
                QMetaObject.invokeMethod(self, "prompt_leftover_scan", Qt.ConnectionType.QueuedConnection)
                self._leftover_prompt_done.wait()
            except Exception as e:
                fl += 1
                self.sig.uninst_log.emit(f"[标准卸载] 启动失败: {nm} -> {e}")

            self.sig.uninst_prog.emit(i, tot)

        self.sig.uninst_done.emit(f"标准卸载流程结束：成功 {ok}，失败 {fl}，跳过 {sk}，耗时 {time.time()-t0:.1f} 秒")

    def _verify_uninstall_result(self, app_name, install_dir, reg_path):
        messages = []
        path_text = norm_path(install_dir)
        if path_text and os.path.exists(path_text):
            messages.append(f"[卸载校验] {app_name} 安装目录仍存在: {path_text}")
        if reg_path:
            hive_name, _, subkey = reg_path.partition("\\")
            hive_map = {
                "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCU": winreg.HKEY_CURRENT_USER,
                "HKCR": winreg.HKEY_CLASSES_ROOT
            }
            hkey = hive_map.get(hive_name)
            if hkey and subkey:
                try:
                    with winreg.OpenKey(hkey, subkey):
                        messages.append(f"[卸载校验] {app_name} 卸载注册表项仍存在")
                except OSError:
                    pass
        if not messages:
            messages.append(f"[卸载校验] {app_name} 主要卸载痕迹已移除")
        return messages

    @Slot()
    def prompt_leftover_scan(self):
        if not hasattr(self, "_current_uninstalling") or not self._current_uninstalling:
            if hasattr(self, "_leftover_prompt_done"):
                self._leftover_prompt_done.set()
            return
        r, nm, pub, loc, reg = self._current_uninstalling
        if MessageBox("卸载程序已退出", f"标准卸载流程已结束是否立刻进行深度扫描，清理 '{nm}' 可能遗留的注册表和文件残留？", self.window()).exec():
            self._trigger_leftover_scan(r, nm, pub, loc, reg)
        self._current_uninstalling = None
        if hasattr(self, "_leftover_prompt_done"):
            self._leftover_prompt_done.set()

    def do_force_uninstall(self):
        data = self._get_checked_rows_data()
        if not data:
            self.sig.uninst_log.emit("请先勾选目标软件！"); return
        blocked_apps = [item for item in data if item.get("risk_kind") == "critical"]
        if blocked_apps:
            names = "\n".join(f"- {item['name']}" for item in blocked_apps[:8])
            if len(blocked_apps) > 8:
                names += f"\n- 另有 {len(blocked_apps) - 8} 项未展开"
            MessageBox(
                "已拦截",
                "以下项目疑似 BitLocker、TPM 或磁盘加密关键组件，已禁止强力卸载：\n\n"
                f"{names}\n\n如需处理，请优先使用标准卸载，并确认已备份恢复密钥。",
                self.window()
            ).exec()
            self.sig.uninst_log.emit(f"[分级保护] 已拦截 {len(blocked_apps)} 个极高风险强力卸载项")
            data = [item for item in data if item.get("risk_kind") != "critical"]
            if not data:
                return
        if not self._confirm_risky_selection(data, "强力卸载"):
            self.sig.uninst_log.emit("已取消高风险强力卸载操作")
            return

        all_files, all_regs = [], []
        all_services, all_tasks = [], []
        chosen_apps = 0
        for item in data:
            r = item["row"]; nm = item["name"]; pub = item["publisher"]; loc = item["location"]; reg = item["reg"]
            picked = self._pick_leftovers(nm, pub, loc, reg)
            if picked is None:
                continue
            if not self._confirm_leftover_protection_summary(nm, picked):
                self.sig.uninst_log.emit(f"[分级保护] 已取消 {nm} 的高风险残留强力清理")
                continue
            del_files = picked["files"]; del_regs = picked["regs"]
            del_services = picked["services"]; del_tasks = picked["tasks"]
            if not del_files and not del_regs and not del_services and not del_tasks:
                continue
            chosen_apps += 1
            all_files.extend(del_files)
            all_regs.extend(del_regs)
            all_services.extend(del_services)
            all_tasks.extend(del_tasks)

        if chosen_apps == 0:
            self.sig.uninst_log.emit("未选择任何残留项，操作已取消")
            return

        # 去重并保持顺序，避免重复删除同一路径/注册表键
        all_files = list(dict.fromkeys(all_files))
        all_regs = list(dict.fromkeys(all_regs))
        all_services = list({service["name"].lower(): service for service in all_services}.values())
        all_tasks = list({task["full_name"].lower(): task for task in all_tasks}.values())
        self.sig.uninst_log.emit(
            f"[强力清除] 批量任务已确认：软件 {chosen_apps} 个，文件/目录 {len(all_files)} 项，注册表 {len(all_regs)} 项，服务 {len(all_services)} 项，计划任务 {len(all_tasks)} 项"
        )
        self.stop.clear()
        threading.Thread(target=self._force_uninst_w, args=(all_files, all_regs, all_services, all_tasks), daemon=True).start()

    def _pick_leftovers(self, nm, pub, loc, reg):
        dialog = LeftoversDialog(self.window(), nm, pub, loc, reg)
        if dialog.tree.topLevelItemCount() == 0:
            InfoBar.success("扫描完毕", f"未发现 '{nm}' 的明显残留", parent=self.window())
            return {"files": [], "regs": [], "services": [], "tasks": []}
        if not dialog.exec():
            return None
        return dialog.get_selected_items()

    def _trigger_leftover_scan(self, r, nm, pub, loc, reg):
        picked = self._pick_leftovers(nm, pub, loc, reg)
        if picked is None:
            return
        del_files = picked["files"]; del_regs = picked["regs"]
        del_services = picked["services"]; del_tasks = picked["tasks"]
        if not del_files and not del_regs and not del_services and not del_tasks:
            return
        self.sig.uninst_log.emit(f"[强力清除] 开始清理 {nm} 的残留...")
        self.stop.clear()
        threading.Thread(target=self._force_uninst_w, args=(del_files, del_regs, del_services, del_tasks), daemon=True).start()

    def _force_uninst_w(self, files, regs, services=None, tasks=None):
        t0 = time.time()
        lf = lambda s: self.sig.uninst_log.emit(s)
        services = services or []
        tasks = tasks or []

        stats = {
            "services": {"label": "服务", "ok": 0, "fail": 0, "total": len(services)},
            "tasks": {"label": "计划任务", "ok": 0, "fail": 0, "total": len(tasks)},
            "regs": {"label": "注册表", "ok": 0, "fail": 0, "total": len(regs)},
            "files": {"label": "文件/目录", "ok": 0, "fail": 0, "total": len(files)},
        }
        kill_targets = [f for f in files if os.path.isdir(f)]
        current_stage = "准备"

        def _build_summary(cancelled=False):
            parts = []
            for key in ("services", "tasks", "regs", "files"):
                item = stats[key]
                parts.append(f"{item['label']} 成功 {item['ok']}/{item['total']}，失败 {item['fail']}")
            prefix = "强力清理已取消" if cancelled else "强力清理完成"
            suffix = f"，中断于{current_stage}" if cancelled else ""
            return f"{prefix}：{'；'.join(parts)}{suffix}，耗时 {time.time()-t0:.1f} 秒"

        def _check_stop():
            if self.stop.is_set():
                self.sig.uninst_done.emit(_build_summary(cancelled=True))
                return True
            return False

        if _check_stop():
            return

        current_stage = "进程解除锁定"
        for install_dir in kill_targets:
            if _check_stop():
                return
            kill_app_processes(install_dir, lf)
            time.sleep(0.5)

        current_stage = "服务删除"
        for service in services:
            if _check_stop():
                return
            if delete_service_entry(service.get("name", ""), service.get("reg_path", ""), lf):
                stats["services"]["ok"] += 1
            else:
                stats["services"]["fail"] += 1

        current_stage = "计划任务删除"
        for task in tasks:
            if _check_stop():
                return
            if delete_scheduled_task(task.get("full_name", ""), lf):
                stats["tasks"]["ok"] += 1
            else:
                stats["tasks"]["fail"] += 1

        current_stage = "注册表删除"
        for reg_path in regs:
            if _check_stop():
                return
            if force_delete_registry(reg_path, lf):
                stats["regs"]["ok"] += 1
            else:
                stats["regs"]["fail"] += 1

        current_stage = "文件删除"
        for file_path in files:
            if _check_stop():
                return
            if delete_path(file_path, True, lf):
                stats["files"]["ok"] += 1
                self.sig.uninst_log.emit(f"[强删文件] 成功移除: {file_path}")
            else:
                stats["files"]["fail"] += 1
                self.sig.uninst_log.emit(f"[强删文件] 失败(可能仍有驱动级锁定): {file_path}")

        self.sig.uninst_done.emit(_build_summary(cancelled=False))

class BigFilePage(ScrollArea):
    def __init__(self, sig, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("bigFilePage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        self._disk_threads = 4; self._disk_type = "检测中..."; self.lbl_disk = CaptionLabel("类型：检测中...  线程：4")
        self.lbl_disk.setTextColor(QColor(128, 128, 128))
        self.lbl_disk.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_disk.setContentsMargins(0, 0, 0, 0)

        title_row = make_title_row(FIF.ZOOM, "大文件扫描")
        title_row.insertWidget(2, self.lbl_disk, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(title_row)
        
        self.drives = get_available_drives(); self.drive_actions = []; self.drive_states = {d: (True if d.startswith("C") else False) for d in self.drives}; self._menu_last_close = 0
        dl = QHBoxLayout(); dl.setSpacing(10); dl.addWidget(StrongBodyLabel("选择范围:"))
        self.btn_drives = LeftAlignedPushButton("磁盘: C:\\"); self.menu_drives = RoundMenu(parent=self)
        self.btn_drives.setMinimumWidth(220)
        for d in self.drives:
            action = Action(d); action.setData(d); action.triggered.connect(lambda checked=False, a=action: self._toggle_drive(a))
            self.menu_drives.addAction(action); self.drive_actions.append(action)
        self.btn_drives.clicked.connect(self._show_drives_menu); dl.addWidget(self.btn_drives)
        dl.addStretch(); v.addLayout(dl)
        self._update_drive_btn_text()

        self.sig.disk_ready.connect(self._on_disk_ready)

        pr=QHBoxLayout(); pr.setSpacing(10); pr.addWidget(CaptionLabel("最小文件MB:"))
        self.sp_mb=SpinBox(); self.sp_mb.setRange(50,10240); self.sp_mb.setValue(500); self.sp_mb.setFixedWidth(130); pr.addWidget(self.sp_mb)
        pr.addWidget(CaptionLabel("扫描上限:")); self.sp_mx=SpinBox(); self.sp_mx.setRange(50,2000); self.sp_mx.setValue(200); self.sp_mx.setFixedWidth(130); pr.addWidget(self.sp_mx)
        self.cb_sort = ComboBox()
        self.cb_sort.addItems(["默认顺序", "按文件名", "按大小", "按路径"])
        self.cb_sort.setFixedWidth(120)
        self.cb_sort.currentIndexChanged.connect(self._apply_sort)
        pr.addWidget(self.cb_sort)
        self.chk_skip_special=CheckBox("跳过系统/虚拟机大文件"); self.chk_skip_special.setChecked(True); self.chk_skip_special.setToolTip("跳过分页/休眠/内存转储以及常见虚拟机磁盘镜像")
        pr.addWidget(self.chk_skip_special)
        self.chk_perm=CheckBox("永久删除"); self.chk_perm.setChecked(True); pr.addWidget(self.chk_perm); pr.addStretch(); v.addLayout(pr)

        self.tbl=TableWidget(); self.tbl.setColumnCount(4); self.tbl.setHorizontalHeaderLabels([" ","文件名","大小","路径"])
        self.tbl.verticalHeader().setVisible(False); self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,3))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 200); self.tbl.setColumnWidth(2, 120); self.tbl.setColumnWidth(3, 760)
        style_table(self.tbl); v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PrimaryPushButton(FIF.SEARCH,"扫描"); b1.setFixedHeight(30); b1.clicked.connect(self.do_scan); br.addWidget(b1)
        
        self.btn_sel_all = PushButton(FIF.ACCEPT, "全选"); self.btn_sel_all.setFixedHeight(30)
        self.btn_sel_all.clicked.connect(self.toggle_sel_all); br.addWidget(self.btn_sel_all)

        b3=PushButton(FIF.DELETE,"删除已勾选"); b3.setFixedHeight(30); b3.clicked.connect(self.do_del); br.addWidget(b3)
        b4=PushButton(FIF.CANCEL,"停止"); b4.setFixedHeight(30); b4.clicked.connect(self._stop_current); br.addWidget(b4)
        br.addStretch(); v.addLayout(br)

        pg=QHBoxLayout(); self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pg.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pg.addWidget(self.sl); v.addLayout(pg)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志..."); v.addWidget(self.log)

    def toggle_sel_all(self):
        rc = self.tbl.rowCount()
        if rc == 0: return
        all_checked = True
        for r in range(rc):
            if not is_row_checked(self.tbl, r):
                all_checked = False; break
        new_state = not all_checked
        for r in range(rc): set_row_checked(self.tbl, r, new_state)
            
        if new_state:
            self.btn_sel_all.setText("取消全选"); self.btn_sel_all.setIcon(FIF.CLOSE)
        else:
            self.btn_sel_all.setText("全选"); self.btn_sel_all.setIcon(FIF.ACCEPT)

    def _show_drives_menu(self):
        if time.time() - self._menu_last_close < 0.2: return
        self.menu_drives.exec(self.btn_drives.mapToGlobal(QPoint(0, self.btn_drives.height() + 2))); self._menu_last_close = time.time()
    def _toggle_drive(self, action):
        d = action.data(); self.drive_states[d] = not self.drive_states[d]; self._update_drive_btn_text()
    def _update_drive_btn_text(self):
        sel = [a.data() for a in self.drive_actions if self.drive_states[a.data()]]
        for a in self.drive_actions: a.setText(f"{a.data()} √" if self.drive_states[a.data()] else a.data())
        if not sel:
            txt = "磁盘: (未选择)"
        elif len(sel) == 1:
            txt = f"磁盘: {sel[0]}"
        else:
            txt = f"磁盘: {sel[0]} 等 {len(sel)} 个"
        self.btn_drives.setText(txt)
        self.btn_drives.setToolTip(f"已选磁盘: {', '.join(sel)}" if sel else "未选择磁盘")

    def _on_disk_ready(self, dtype, threads): self._disk_type = dtype; self._disk_threads = threads; self.lbl_disk.setText(f"类型：{dtype}  线程：{threads}")

    def _stop_current(self):
        self.stop.set()

    def _apply_sort(self, _=None):
        mode = self.cb_sort.currentIndex() if hasattr(self, "cb_sort") else 0
        self.tbl.setSortingEnabled(mode != 0)
        if mode == 0:
            return
        column = {1: 1, 2: 2, 3: 3}.get(mode, 2)
        order = Qt.SortOrder.AscendingOrder if mode in (1, 3) else Qt.SortOrder.DescendingOrder
        self.tbl.sortItems(column, order)

    def do_scan(self):
        self.stop.clear(); self.btn_sel_all.setText("全选"); self.btn_sel_all.setIcon(FIF.ACCEPT)
        threading.Thread(target=self._scan_w,daemon=True).start()

    def _scan_w(self):
        t0 = time.time()
        mb=self.sp_mb.value(); mx=self.sp_mx.value()
        roots = [d for d, state in self.drive_states.items() if state]
        if not roots:
            self.sig.big_done.emit("warning", "错误：未选择磁盘")
            return
        w, dtype = get_scan_threads_for_drives_cached(roots)
        self.sig.disk_ready.emit(dtype, w)
        self.sig.big_log.emit(f"扫描 (≥{mb}MB) | 线程: {w}"); self.sig.big_clr.emit()
        self.sig.big_prog.emit(0, 0)
        self.sig.big_scan_count.emit(0)
        skip_optional = self.chk_skip_special.isChecked()
        res = scan_big_files(
            roots,
            mb*1024*1024,
            DEFAULT_EXCLUDES,
            self.stop,
            workers=w,
            result_limit=mx,
            progress_cb=lambda scanned: self.sig.big_scan_count.emit(scanned),
            skip_optional=skip_optional
        )
        if self.stop.is_set():
            self.sig.big_done.emit("warning", f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        for sz,pa in res[:mx]: self.sig.big_add.emit(str(sz), pa)
        self.sig.big_done.emit("success", f"扫描完成，找到 {len(res[:mx])} 条，耗时 {time.time()-t0:.1f} 秒")

    def do_del(self):
        paths=[self.tbl.item(r,3).text() for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r) and self.tbl.item(r,3)]
        if not paths: return
        pm=self.chk_perm.isChecked()
        if pm and not MessageBox("确认",f"将永久删除 {len(paths)} 个文件继续？",self.window()).exec(): return
        self.stop.clear(); threading.Thread(target=self._del_w,args=(paths,pm),daemon=True).start()

    def _del_w(self, paths, pm):
        t0 = time.time()
        ok=fl=0; tot=len(paths); lf=lambda s:self.sig.big_log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set():
                self.sig.big_done.emit("warning", f"删除已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.big_prog.emit(i,tot)
        self.sig.big_done.emit("success", f"删除完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")

class MoreCleanPage(ScrollArea):
    def __init__(self, sig, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("moreCleanPage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        v.addLayout(make_title_row(FIF.MORE, "更多清理"))

        dl = QHBoxLayout(); dl.setSpacing(10)
        self.cb_mode = ComboBox()
        self.cb_mode.addItems(["重复文件查找", "空文件夹扫描", "无效快捷方式清理", "卸载注册表扫描", "右键菜单清理"])
        self.cb_mode.setFixedWidth(200); self.cb_mode.currentIndexChanged.connect(self._on_mode_change)
        dl.addWidget(StrongBodyLabel("扫描类型:")); dl.addWidget(self.cb_mode); dl.addSpacing(20)

        self.drives = get_available_drives()
        self.drive_actions = []
        self.drive_states = {d: False for d in self.drives}
        self._menu_last_close = 0
        self.btn_drives = LeftAlignedPushButton("磁盘: (未选择)"); self.menu_drives = RoundMenu(parent=self)
        self.btn_drives.setMinimumWidth(220)
        for d in self.drives:
            action = Action(d); action.setData(d); action.triggered.connect(lambda checked=False, a=action: self._toggle_drive(a))
            self.menu_drives.addAction(action); self.drive_actions.append(action)
        self.btn_drives.clicked.connect(self._show_drives_menu)
        
        self.lbl_disk_req = StrongBodyLabel("选择范围:"); dl.addWidget(self.lbl_disk_req); dl.addWidget(self.btn_drives); dl.addStretch(); v.addLayout(dl)

        pr = QHBoxLayout(); pr.setSpacing(10)
        self.chk_perm=CheckBox("永久删除(文件不进回收站)"); self.chk_perm.setChecked(True); pr.addWidget(self.chk_perm)
        pr.addStretch()
        self.btn_restore_assoc = PushButton(FIF.SYNC, "恢复默认关联")
        self.btn_restore_assoc.setFixedHeight(30)
        self.btn_restore_assoc.setToolTip("恢复文件夹、磁盘和 .exe/.bat/.cmd/.lnk 等常见默认打开关联")
        self.btn_restore_assoc.clicked.connect(self.do_restore_default_associations)
        pr.addWidget(self.btn_restore_assoc)
        v.addLayout(pr)
        self._on_mode_change()

        self.tbl=TableWidget(); self.tbl.setColumnCount(5); self.tbl.setHorizontalHeaderLabels([" ","类型","名称","详细/大小","路径(注册表键)"])
        self.tbl.verticalHeader().setVisible(False); self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,4))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 100); self.tbl.setColumnWidth(2, 180); self.tbl.setColumnWidth(3, 140); self.tbl.setColumnWidth(4, 550)
        style_table(self.tbl); v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PrimaryPushButton(FIF.SEARCH,"开始扫描"); b1.setFixedHeight(30); b1.clicked.connect(self.do_scan); br.addWidget(b1)
        
        self.btn_sel_all = PushButton(FIF.ACCEPT, "全选"); self.btn_sel_all.setFixedHeight(30)
        self.btn_sel_all.clicked.connect(self.toggle_sel_all); br.addWidget(self.btn_sel_all)

        b2=PushButton(FIF.DELETE,"清理已勾选"); b2.setFixedHeight(30); b2.clicked.connect(self.do_del); br.addWidget(b2)
        b3=PushButton(FIF.CANCEL,"停止"); b3.setFixedHeight(30); b3.clicked.connect(self._stop_current); br.addWidget(b3); br.addStretch(); v.addLayout(br)

        pg=QHBoxLayout(); self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pg.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pg.addWidget(self.sl); v.addLayout(pg)
        
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志..."); v.addWidget(self.log)

    def toggle_sel_all(self):
        rc = self.tbl.rowCount()
        if rc == 0: return
        all_checked = True
        for r in range(rc):
            if not is_row_checked(self.tbl, r):
                all_checked = False; break
        new_state = not all_checked
        for r in range(rc): set_row_checked(self.tbl, r, new_state)
            
        if new_state:
            self.btn_sel_all.setText("取消全选"); self.btn_sel_all.setIcon(FIF.CLOSE)
        else:
            self.btn_sel_all.setText("全选"); self.btn_sel_all.setIcon(FIF.ACCEPT)

    def _on_mode_change(self):
        mode_idx = self.cb_mode.currentIndex()
        is_reg = mode_idx in (3, 4)
        self.btn_drives.setVisible(not is_reg); self.lbl_disk_req.setVisible(not is_reg)
        self.btn_restore_assoc.setVisible(mode_idx == 4)
        hide_c_drive = mode_idx == 0
        for d in self.drives:
            if hide_c_drive and d.upper().startswith("C"):
                self.drive_states[d] = False
        for a in self.drive_actions:
            is_c_drive = str(a.data()).upper().startswith("C")
            a.setVisible(not (hide_c_drive and is_c_drive))
        self._update_drive_btn_text()

    def _show_drives_menu(self):
        if time.time() - self._menu_last_close < 0.2: return
        self.menu_drives.exec(self.btn_drives.mapToGlobal(QPoint(0, self.btn_drives.height() + 2))); self._menu_last_close = time.time()
    def _toggle_drive(self, action):
        d = action.data(); self.drive_states[d] = not self.drive_states[d]; self._update_drive_btn_text()
    def _update_drive_btn_text(self):
        visible_actions = [a for a in self.drive_actions if a.isVisible()]
        sel = [a.data() for a in visible_actions if self.drive_states[a.data()]]
        for a in self.drive_actions: a.setText(f"{a.data()} √" if self.drive_states[a.data()] else a.data())
        if not sel:
            txt = "磁盘: (未选择)"
        elif len(sel) == 1:
            txt = f"磁盘: {sel[0]}"
        else:
            txt = f"磁盘: {sel[0]} 等 {len(sel)} 个"
        self.btn_drives.setText(txt)
        self.btn_drives.setToolTip(f"已选磁盘: {', '.join(sel)}" if sel else "未选择磁盘")

    def _stop_current(self):
        self.stop.set()

    def do_scan(self):
        idx = self.cb_mode.currentIndex(); roots = [d for d, state in self.drive_states.items() if state]
        if idx not in (3, 4) and not roots: self.sig.more_done.emit("错误：未选择磁盘"); return
        self.stop.clear(); self.sig.more_clr.emit(); self.sig.more_log.emit(f"开始 {self.cb_mode.currentText()}...")
        
        self.btn_sel_all.setText("全选")
        self.btn_sel_all.setIcon(FIF.ACCEPT)

        workers = self.window().pg_big._disk_threads if hasattr(self.window(), 'pg_big') else 4

        if idx == 0: threading.Thread(target=self._scan_duplicates, args=(roots, workers), daemon=True).start()
        elif idx == 1: threading.Thread(target=self._scan_empty_dirs, args=(roots, workers), daemon=True).start()
        elif idx == 2: threading.Thread(target=self._scan_shortcuts, args=(roots, workers), daemon=True).start()
        elif idx == 3: threading.Thread(target=self._scan_registry, daemon=True).start()
        elif idx == 4: threading.Thread(target=self._scan_context_menu, daemon=True).start()

    def _walk_files_threaded(self, roots, excl, workers, file_cb=None, dir_cb=None, ext_filter=None, collect_files=False, collect_dirs=False):
        dir_queue = queue.Queue()
        res_files = []
        res_dirs = []
        lock = threading.Lock()
        for r in roots:
            dir_queue.put(r)

        def _worker():
            while True:
                try:
                    d = dir_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if d is _SENTINEL:
                    dir_queue.task_done()
                    break
                if self.stop.is_set():
                    dir_queue.task_done()
                    continue
                try:
                    entries = os.scandir(d)
                except Exception as e:
                    log_sampled_background_error("遍历目录", e)
                    dir_queue.task_done()
                    continue
                try:
                    for e in entries:
                        if self.stop.is_set():
                            break
                        try:
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                if not should_exclude(e.path, excl):
                                    dir_queue.put(e.path)
                                    if collect_dirs:
                                        with lock:
                                            res_dirs.append(e.path)
                                    if dir_cb:
                                        dir_cb(e.path)
                            elif e.is_file(follow_symlinks=False):
                                if ext_filter and not e.name.lower().endswith(ext_filter):
                                    continue
                                file_info = (e.stat(follow_symlinks=False).st_size, e.path)
                                if collect_files:
                                    with lock:
                                        res_files.append(file_info)
                                if file_cb:
                                    file_cb(file_info[0], file_info[1])
                        except Exception as e:
                            log_sampled_background_error("扫描条目", e)
                finally:
                    try:
                        entries.close()
                    except Exception as e:
                        log_sampled_background_error("关闭扫描句柄", e, limit=3)
                dir_queue.task_done()

        threads = []
        for _ in range(workers):
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            threads.append(t)
        join_done = threading.Event()
        threading.Thread(target=lambda: (dir_queue.join(), join_done.set()), daemon=True).start()
        sent_stop_signal = False

        try:
            while not join_done.wait(0.1):
                if self.stop.is_set() and not sent_stop_signal:
                    for _ in threads:
                        dir_queue.put(_SENTINEL)
                    sent_stop_signal = True
        finally:
            if not sent_stop_signal:
                for _ in threads:
                    dir_queue.put(_SENTINEL)
        for t in threads:
            t.join(timeout=1)
        return res_files, res_dirs

    def _scan_duplicates(self, roots, workers):
        t0 = time.time()
        first_path_by_size = {}
        size_groups = defaultdict(list)
        size_lock = threading.Lock()

        self.sig.more_log.emit("[重复文件] 第一阶段：识别可疑大小分组...")

        def _collect_candidates(file_size, path):
            if file_size <= 0:
                return
            with size_lock:
                existing_group = size_groups.get(file_size)
                if existing_group:
                    existing_group.append(path)
                    return

                first_path = first_path_by_size.get(file_size)
                if first_path is None:
                    first_path_by_size[file_size] = path
                    return

                size_groups[file_size] = [first_path, path]
                first_path_by_size.pop(file_size, None)

        self._walk_files_threaded(roots, DEFAULT_EXCLUDES, workers, file_cb=_collect_candidates)
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return

        first_path_by_size.clear()
        if not size_groups:
            self.sig.more_done.emit(f"扫描完成，找到 0 个重复文件，耗时 {time.time()-t0:.1f} 秒")
            return

        suspects = [(sz, paths) for sz, paths in size_groups.items() if len(paths) > 1]
        self.sig.more_log.emit(f"[重复文件] 第二阶段：校验 {len(suspects)} 个可疑大小分组...")

        def _get_hash(path, head_bytes=None, tail_bytes=0, sample_offsets=None):
            m = hashlib.md5()
            try:
                with open(path, 'rb') as f:
                    if sample_offsets:
                        try:
                            file_size = os.path.getsize(path)
                        except Exception:
                            file_size = 0
                        seen_offsets = set()
                        for offset, size in sample_offsets:
                            if size <= 0 or file_size <= 0:
                                continue
                            real_offset = max(0, min(offset, max(0, file_size - size)))
                            if real_offset in seen_offsets:
                                continue
                            seen_offsets.add(real_offset)
                            f.seek(real_offset)
                            m.update(f.read(size))
                    elif head_bytes is not None:
                        head = f.read(head_bytes)
                        m.update(head)
                        if tail_bytes > 0:
                            try:
                                file_size = os.path.getsize(path)
                            except Exception:
                                file_size = len(head)
                            if file_size > len(head):
                                f.seek(max(0, file_size - tail_bytes))
                                m.update(f.read(tail_bytes))
                    else:
                        for chunk in iter(lambda: f.read(1024 * 1024), b''):
                            m.update(chunk)
                return m.hexdigest()
            except Exception:
                return None

        def _get_quick_hash(path, file_size):
            if file_size <= 8 * 1024:
                return _get_hash(path)
            if file_size <= 512 * 1024:
                return _get_hash(path, head_bytes=64 * 1024)
            sample_size = 64 * 1024
            mid_offset = max(0, (file_size // 2) - (sample_size // 2))
            tail_offset = max(0, file_size - sample_size)
            return _get_hash(
                path,
                sample_offsets=[
                    (0, sample_size),
                    (mid_offset, sample_size),
                    (tail_offset, sample_size)
                ]
            )

        # 先按文件大小筛，再用分层采样做快速分桶，最后只对疑似组做全量哈希
        results = []
        tot = len(suspects)
        for i, (file_size, paths) in enumerate(suspects, 1):
            if self.stop.is_set(): break
            self.sig.more_prog.emit(i, tot)

            quick_dict = defaultdict(list)
            for p in paths:
                sig = _get_quick_hash(p, file_size)
                if sig:
                    quick_dict[sig].append(p)

            for quick_paths in quick_dict.values():
                if len(quick_paths) < 2:
                    continue
                full_dict = defaultdict(list)
                for p in quick_paths:
                    fh = _get_hash(p)
                    if fh:
                        full_dict[fh].append(p)
                for duplicates in full_dict.values():
                    if len(duplicates) > 1:
                        results.append((file_size, duplicates))

        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return

        normalized_results = []
        for file_size, duplicates in results:
            sorted_duplicates = sorted(duplicates, key=lambda p: os.path.normcase(p))
            if len(sorted_duplicates) > 1:
                normalized_results.append((file_size, sorted_duplicates))
        normalized_results.sort(key=lambda item: (-item[0], os.path.normcase(item[1][0])))

        cnt = 0
        hidden_cnt = 0
        for grp_id, (file_size, dup_list) in enumerate(normalized_results, 1):
            shown_list = dup_list[:DUPLICATE_GROUP_DISPLAY_LIMIT]
            hidden = max(0, len(dup_list) - len(shown_list))
            for idx, p in enumerate(shown_list):
                self.sig.more_add.emit((idx > 0), "重复文件", f"组 {grp_id}", human_size(file_size), p); cnt += 1
            if hidden > 0:
                hidden_cnt += hidden
                self.sig.more_add.emit(False, "重复文件", f"组 {grp_id}", f"{human_size(file_size)} | 另有 {hidden} 个未展开", "")
        if hidden_cnt > 0:
            self.sig.more_log.emit(f"[重复文件] 已折叠 {hidden_cnt} 个超大重复组结果，仅展示每组前 {DUPLICATE_GROUP_DISPLAY_LIMIT} 项")
            self.sig.more_done.emit(f"扫描完成，展示 {cnt} 个重复文件，另有 {hidden_cnt} 个未展开，耗时 {time.time()-t0:.1f} 秒")
            return
        self.sig.more_done.emit(f"扫描完成，找到 {cnt} 个重复文件，耗时 {time.time()-t0:.1f} 秒")

    def _scan_empty_dirs(self, roots, workers):
        t0 = time.time()
        _, dirs = self._walk_files_threaded(roots, DEFAULT_EXCLUDES, workers, collect_dirs=True)
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        dirs.sort(key=len, reverse=True); empty_set = set(); tot = len(dirs)
        for i, d in enumerate(dirs):
            if self.stop.is_set(): break
            if i % 500 == 0: self.sig.more_prog.emit(i, tot)
            try:
                is_empty = True
                for item in os.scandir(d):
                    if item.is_file(follow_symlinks=False): is_empty = False; break
                    elif item.is_dir(follow_symlinks=False) and item.path not in empty_set: is_empty = False; break
                if is_empty: empty_set.add(d); self.sig.more_add.emit(False, "空文件夹", os.path.basename(d), "无内容", d)
            except Exception: pass
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        self.sig.more_done.emit(f"扫描完成，找到 {len(empty_set)} 个空文件夹，耗时 {time.time()-t0:.1f} 秒")

    def _scan_shortcuts(self, roots, workers):
        t0 = time.time()
        files, _ = self._walk_files_threaded(roots, DEFAULT_EXCLUDES, workers, ext_filter=".lnk", collect_files=True)
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        def resolve_lnk_target(path):
            try:
                import win32com.client
                return win32com.client.Dispatch("WScript.Shell").CreateShortCut(path).TargetPath
            except ImportError:
                try:
                    with open(path, 'rb') as f:
                        m = re.search(rb'[a-zA-Z]:\\[^\x00]+', f.read())
                        if m: return m.group().decode('mbcs', 'ignore')
                except Exception as e:
                    log_sampled_background_error("解析快捷方式", e)
            except Exception as e:
                log_sampled_background_error("解析快捷方式", e)
            return ""
        tot = len(files); invalid_cnt = 0
        for i, (_, p) in enumerate(files):
            if self.stop.is_set(): break
            if i % 100 == 0: self.sig.more_prog.emit(i, tot)
            target = resolve_lnk_target(p)
            if target and not os.path.exists(target):
                self.sig.more_add.emit(False, "无效快捷方式", os.path.basename(p), "指向缺失的文件", p); invalid_cnt += 1
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        self.sig.more_done.emit(f"扫描完成，找到 {invalid_cnt} 个无效快捷方式，耗时 {time.time()-t0:.1f} 秒")

    def _scan_registry(self):
        t0 = time.time()
        res = []; keys_to_check = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
        scan_errors = []
        error_count = 0
        for hkey, subkey_str in keys_to_check:
            try:
                with winreg.OpenKey(hkey, subkey_str) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        if self.stop.is_set(): break
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub_key:
                                try:
                                    install_loc, _ = winreg.QueryValueEx(sub_key, "InstallLocation")
                                    if install_loc and not os.path.exists(install_loc):
                                        try:
                                            disp_name = winreg.QueryValueEx(sub_key, "DisplayName")[0]
                                        except OSError:
                                            disp_name = sub_name
                                        res.append(("无效卸载项", disp_name, "原目录已丢失", f"{'HKLM' if hkey==winreg.HKEY_LOCAL_MACHINE else 'HKCU'}\\{subkey_str}\\{sub_name}"))
                                except OSError:
                                    pass
                        except OSError as e:
                            error_count += 1
                            append_error_sample(scan_errors, f"{subkey_str} 第 {i + 1} 项读取失败 -> {format_exception_text(e)}")
            except OSError as e:
                error_count += 1
                append_error_sample(scan_errors, f"{subkey_str} 无法打开 -> {format_exception_text(e)}")
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        if error_count:
            emit_error_summary(self.sig.more_log.emit, "注册表扫描异常", scan_errors, error_count)
        for tp, nm, det, path in res: self.sig.more_add.emit(False, tp, nm, det, path)
        self.sig.more_done.emit(f"扫描完成，找到 {len(res)} 个无效注册表卸载项，耗时 {time.time()-t0:.1f} 秒")

    def _scan_context_menu(self):
        t0 = time.time()
        res = []; targets = [r"*\shell", r"*\shellex\ContextMenuHandlers", r"Directory\shell", r"Directory\Background\shell", r"Folder\shell", r"Folder\shellex\ContextMenuHandlers"]
        scan_errors = []
        error_count = 0
        for t in targets:
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, t) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        if self.stop.is_set(): break
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            category, detail = classify_context_menu_entry(t, sub_name)
                            res.append((category, sub_name, detail, f"HKCR\\{t}\\{sub_name}"))
                        except Exception as e:
                            error_count += 1
                            append_error_sample(scan_errors, f"{t} 第 {i + 1} 项读取失败 -> {format_exception_text(e)}")
            except Exception as e:
                error_count += 1
                append_error_sample(scan_errors, f"{t} 无法打开 -> {format_exception_text(e)}")
        if self.stop.is_set():
            self.sig.more_done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        if error_count:
            emit_error_summary(self.sig.more_log.emit, "右键菜单扫描异常", scan_errors, error_count)
        system_count = sum(1 for tp, _, _, _ in res if tp == "系统")
        third_party_count = sum(1 for tp, _, _, _ in res if tp == "外部")
        unknown_count = sum(1 for tp, _, _, _ in res if tp == "未知")
        for tp, nm, det, path in res: self.sig.more_add.emit(False, tp, nm, det, path)
        self.sig.more_done.emit(
            f"扫描完成，列出 {len(res)} 个右键菜单扩展（系统 {system_count}，第三方 {third_party_count}，未知 {unknown_count}），耗时 {time.time()-t0:.1f} 秒"
        )

    def do_restore_default_associations(self):
        content = (
            "这会恢复资源管理器常见默认关联：\n\n"
            "- 文件夹、目录、磁盘的默认打开动作\n"
            "- .exe / .bat / .cmd / .com / .lnk 的打开关联\n"
            "- 清除当前用户异常的 UserChoice 记录\n\n"
            "如果系统当前出现“没有与之关联的应用”或文件夹没有“打开”选项，这个修复项就是针对这些问题的。\n\n"
            "是否继续？"
        )
        if not MessageBox("恢复默认资源管理器关联", content, self.window()).exec():
            return
        self.sig.more_log.emit("[恢复关联] 正在恢复默认资源管理器关联...")
        threading.Thread(target=self._restore_default_associations_w, daemon=True).start()

    def _restore_default_associations_w(self):
        ok, msg = restore_default_explorer_associations(self.sig.more_log.emit)
        level = "success" if ok else "error"
        title = "恢复完成" if ok else "恢复失败"
        self.sig.update_status.emit(level, title, msg)

    def do_del(self):
        selected_rows = [r for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r)]
        paths=[self.tbl.item(r,4).text() for r in selected_rows]
        if not paths: return
        mode_idx = self.cb_mode.currentIndex()
        is_reg = mode_idx in (3, 4)

        # 为避免误删系统盘内容，重复文件模式禁止清理 C 盘文件
        if mode_idx == 0:
            blocked = []
            allowed = []
            for p in paths:
                drive = os.path.splitdrive(norm_path(p))[0].upper()
                if drive == "C:":
                    blocked.append(p)
                else:
                    allowed.append(p)

            if blocked:
                self.sig.more_log.emit(f"[保护] 已阻止清理 {len(blocked)} 个位于 C 盘的重复文件")
                InfoBar.warning(
                    "已阻止",
                    f"重复文件模式禁止清理 C 盘文件，已跳过 {len(blocked)} 项",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self.window()
                )
                paths = allowed

            if not paths:
                return

        if mode_idx == 4:
            system_items = []
            for r in selected_rows:
                type_item = self.tbl.item(r, 1)
                name_item = self.tbl.item(r, 2)
                if type_item and type_item.text() == "系统":
                    system_items.append(name_item.text() if name_item else "")
            if system_items:
                preview = [f"- {name}" for name in system_items[:8] if name]
                if len(system_items) > 8:
                    preview.append(f"- 另有 {len(system_items) - 8} 项未展开")
                content = (
                    "当前勾选项中包含系统右键菜单项：\n\n"
                    + "\n".join(preview)
                    + "\n\n删除这些项目可能导致文件夹、目录、磁盘或资源管理器默认操作异常。是否仍要继续？"
                )
                if not MessageBox("风险确认", content, self.window()).exec():
                    return

        if not MessageBox("确认",f"确定清理这 {len(paths)} 个项目？不可恢复",self.window()).exec(): return
        self.stop.clear()
        if is_reg: threading.Thread(target=self._del_reg_w, args=(paths,), daemon=True).start()
        else: threading.Thread(target=self._del_files_w, args=(paths,self.chk_perm.isChecked()), daemon=True).start()

    def _del_files_w(self, paths, pm):
        t0 = time.time()
        ok=fl=0; tot=len(paths); lf=lambda s:self.sig.more_log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set():
                self.sig.more_done.emit(f"清理已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.more_prog.emit(i,tot)
        self.sig.more_done.emit(f"清理完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")

    def _del_reg_w(self, paths):
        t0 = time.time()
        ok=fl=0; tot=len(paths)
        for i, p in enumerate(paths, 1):
            if self.stop.is_set():
                self.sig.more_done.emit(f"清理已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            
            # 使用新的强制删除函数
            if force_delete_registry(p, self.sig.more_log.emit):
                ok += 1
            else:
                fl += 1
                
            self.sig.more_prog.emit(i, tot)
        self.sig.more_done.emit(f"清理完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")


# ══════════════════════════════════════════════════════════
#  主窗口
# ══════════════════════════════════════════════════════════
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 1. 加载配置目录与全局设置
        self.app_dir = app_root_dir()
        self.default_config_dir = os.path.join(self.app_dir, "configs")
        self.config_locator_path = os.path.join(self.app_dir, "cdisk_cleaner_bootstrap.json")
        self.skip_legacy_migration = False
        self.legacy_migration_acknowledged = False
        self.config_dir = self._load_config_dir()
        self._refresh_config_paths()
        self.legacy_config_dir = os.environ.get("LOCALAPPDATA", "")
        self.global_settings = {
            "auto_save": True,
            "update_channel": "stable",
            "protect_builtin_rules": True,
            "deleted_builtin_rules": []
        }
        if os.path.exists(self.global_settings_path):
            try:
                with open(self.global_settings_path, "r", encoding="utf-8") as f:
                    self.global_settings.update(json.load(f))
            except Exception as e:
                log_background_error("加载全局设置失败", e)

        self.targets = [parse_rule_entry(t) for t in default_clean_targets()]
        self.targets = [t for t in self.targets if t]
        # 记录内置默认规则身份，后续删除保护只针对这批规则
        self.builtin_rule_keys = {make_rule_key(t[0], t[1], t[2], t[6]) for t in self.targets}
        self.deleted_builtin_rule_keys = load_rule_keys(self.global_settings.get("deleted_builtin_rules", []))
        if self.deleted_builtin_rule_keys:
            self.targets = [t for t in self.targets if make_rule_key(t[0], t[1], t[2], t[6]) not in self.deleted_builtin_rule_keys]
        
        # 2. 附加自定义规则
        if os.path.exists(self.custom_rules_path):
            try:
                with open(self.custom_rules_path, "r", encoding="utf-8") as f: customs = json.load(f)
                # 兼容历史/外部规则文件：
                # 只要是从 custom_rules_path 读入，都视为“自定义规则”，强制 is_custom=True，
                # 这样仅内置 default_clean_targets() 会保持受保护状态
                for c in customs:
                    parsed = parse_rule_entry(c, force_custom=True)
                    if parsed:
                        self.targets.append(parsed)
            except Exception as e:
                log_background_error("加载自定义规则失败", e)

        # 3. 恢复排序与勾选状态
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f: saved_state = json.load(f)
                
                if "order" in saved_state and "states" in saved_state:
                    order = saved_state["order"]
                    states = saved_state["states"]
                else:
                    order = []
                    states = saved_state 
                    
                if order:
                    t_dict = {t[0]: t for t in self.targets}
                    new_targets = []
                    for nm in order:
                        if nm in t_dict:
                            new_targets.append(t_dict[nm])
                            del t_dict[nm]
                    new_targets.extend(t_dict.values())
                    self.targets = new_targets

                for i in range(len(self.targets)):
                    nm, pa, tp, en, nt, is_c, pattern = self.targets[i]
                    if nm in states:
                        self.targets[i] = (nm, pa, tp, states[nm], nt, is_c, pattern)
            except Exception as e:
                log_background_error("加载排序与勾选状态失败", e)
                
        self.clean_stop = threading.Event(); self.uninstall_stop = threading.Event(); self.big_stop = threading.Event(); self.more_stop = threading.Event(); self.sig = Sig()
        self._targets_lock = threading.Lock()
        self.pg_clean = CleanPage(self.sig, self.targets, self.clean_stop, self._targets_lock, self)
        self.pg_rule_store = RuleStorePage(self, self)
        self.pg_big = BigFilePage(self.sig, self.big_stop, self)
        self.pg_uninstall = UninstallPage(self.sig, self.uninstall_stop, self)
        self.pg_more = MoreCleanPage(self.sig, self.more_stop, self)
        self.pg_setting = SettingPage(self, self)
        self._update_lock = threading.Lock()
        self._update_checking = False

        self._init_nav(); self._init_win(); self._conn()
        threading.Thread(target=self._async_detect, daemon=True).start()
        QTimer.singleShot(2000, lambda: self.check_updates(manual=False))
        self._pending_legacy_migration = self._should_offer_legacy_migration()
        if self._pending_legacy_migration:
            QTimer.singleShot(800, self._prompt_legacy_config_migration)

    def _load_config_dir(self):
        default_dir = self.default_config_dir
        try:
            if os.path.exists(self.config_locator_path):
                with open(self.config_locator_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.skip_legacy_migration = bool(data.get("skip_legacy_migration", False))
                self.legacy_migration_acknowledged = bool(data.get("legacy_migration_acknowledged", False))
                cfg_dir = data.get("config_dir", "")
                if cfg_dir:
                    return os.path.abspath(os.path.expandvars(cfg_dir))
        except Exception as e:
            log_background_error("加载配置目录失败", e)
        return default_dir

    def _save_config_locator(self):
        try:
            write_json_file_atomic(self.config_locator_path, {
                "config_dir": self.config_dir,
                "skip_legacy_migration": self.skip_legacy_migration,
                "legacy_migration_acknowledged": self.legacy_migration_acknowledged
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            log_background_error("保存配置定位文件失败", e)

    def _legacy_config_paths(self):
        base = self.legacy_config_dir
        return {
            "global": os.path.join(base, "cdisk_cleaner_global_settings.json"),
            "custom": os.path.join(base, "cdisk_cleaner_custom_rules.json"),
            "config": os.path.join(base, "cdisk_cleaner_config.json")
        }

    def _has_any_current_config(self):
        return any(os.path.exists(p) for p in (self.global_settings_path, self.custom_rules_path, self.config_path))

    def _should_offer_legacy_migration(self):
        if not self.legacy_config_dir:
            return False
        if self.skip_legacy_migration:
            return False
        if self.legacy_migration_acknowledged:
            return False
        return any(os.path.exists(p) for p in self._legacy_config_paths().values())

    def _prompt_legacy_config_migration(self):
        if not getattr(self, "_pending_legacy_migration", False):
            return
        self._pending_legacy_migration = False
        self.prompt_legacy_config_migration(manual=False)

    def has_legacy_config_files(self):
        if not self.legacy_config_dir:
            return False
        return any(os.path.exists(p) for p in self._legacy_config_paths().values())

    def prompt_legacy_config_migration(self, manual=False):
        if not self.has_legacy_config_files():
            if manual:
                InfoBar.warning("提示", "未找到旧版配置文件", parent=self)
            return False

        dialog = LegacyMigrationDialog(self.legacy_config_dir, self.config_dir, self)
        if not dialog.exec():
            return False

        mode = dialog.selected_mode()
        if mode == 2:
            self.skip_legacy_migration = True
            self.legacy_migration_acknowledged = True
            self._save_config_locator()
            InfoBar.success("已跳过", "本次未迁移旧版配置", parent=self)
            return True

        cleanup_old = mode == 0
        ok, detail = self._migrate_legacy_config(cleanup_old=cleanup_old)
        if ok:
            self.skip_legacy_migration = False
            self.legacy_migration_acknowledged = True
            self._save_config_locator()
            if cleanup_old:
                InfoBar.success("迁移完成", "旧版配置已迁移并清理旧文件，重启软件后生效", parent=self)
            else:
                InfoBar.success("迁移完成", "旧版配置已迁移，旧文件已保留，重启软件后生效", parent=self)
            return True

        InfoBar.error("迁移失败", detail, parent=self)
        return False

    def _migrate_legacy_config(self, cleanup_old=False):
        import shutil

        try:
            os.makedirs(self.config_dir, exist_ok=True)
            legacy_paths = self._legacy_config_paths()
            current_paths = {
                "global": self.global_settings_path,
                "custom": self.custom_rules_path,
                "config": self.config_path
            }

            copied = False
            for key, src in legacy_paths.items():
                dst = current_paths[key]
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    copied = True

            if not copied:
                return False, "未找到可迁移的旧版配置文件"

            if cleanup_old:
                for src in legacy_paths.values():
                    try:
                        if os.path.exists(src):
                            os.remove(src)
                    except Exception:
                        pass

            self._save_config_locator()
            return True, ""
        except Exception as e:
            return False, f"迁移配置文件失败: {e}"

    def _refresh_config_paths(self):
        self.global_settings_path = os.path.join(self.config_dir, "cdisk_cleaner_global_settings.json")
        self.custom_rules_path = os.path.join(self.config_dir, "cdisk_cleaner_custom_rules.json")
        self.config_path = os.path.join(self.config_dir, "cdisk_cleaner_config.json")

    def save_order_state(self):
        try:
            self.pg_clean._sync()
            with self._targets_lock:
                order = [t[0] for t in self.targets]
                states = {t[0]: t[3] for t in self.targets}
            write_json_file_atomic(self.config_path, {"order": order, "states": states}, ensure_ascii=False, indent=2)
        except Exception as e:
            log_background_error("保存排序状态失败", e)

    def set_config_dir(self, new_dir):
        import shutil

        if not new_dir:
            return False, "配置目录不能为空"

        try:
            target_dir = os.path.abspath(os.path.expandvars(new_dir))
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            return False, f"无法创建配置目录: {e}"

        old_global = self.global_settings_path
        old_custom = self.custom_rules_path
        old_config = self.config_path

        try:
            self.save_global_settings()
            if hasattr(self, "pg_clean"):
                self.pg_clean.save_custom_rules()
            if self.global_settings.get("auto_save", True) and hasattr(self, "pg_clean"):
                self.save_order_state()
        except Exception as e:
            log_background_error("切换配置目录前保存当前配置失败", e)

        new_global = os.path.join(target_dir, "cdisk_cleaner_global_settings.json")
        new_custom = os.path.join(target_dir, "cdisk_cleaner_custom_rules.json")
        new_config = os.path.join(target_dir, "cdisk_cleaner_config.json")

        for src, dst in ((old_global, new_global), (old_custom, new_custom), (old_config, new_config)):
            try:
                if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
            except Exception as e:
                return False, f"迁移配置文件失败: {e}"

        self.config_dir = target_dir
        self._refresh_config_paths()
        self._save_config_locator()
        self.save_global_settings()
        if hasattr(self, "pg_clean"):
            self.pg_clean.save_custom_rules()
        if self.global_settings.get("auto_save", True) and hasattr(self, "pg_clean"):
            self.save_order_state()
        return True, ""

    def save_global_settings(self):
        try:
            write_json_file_atomic(self.global_settings_path, self.global_settings, ensure_ascii=False, indent=2)
        except Exception as e:
            log_background_error("保存全局设置失败", e)

    def import_rules_from_path(self, path, source_name="规则集"):
        if hasattr(self, "pg_clean") and self.pg_clean.import_rules_from_path(path, source_name):
            self.switchTo(self.pg_clean)

    def build_export_log_text(self):
        sections = [
            "C盘强力清理工具 日志导出",
            f"导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"软件版本: {CURRENT_VERSION}",
            f"Python版本: {sys.version.split()[0]}",
            f"运行路径: {display_path(sys.executable if getattr(sys, 'frozen', False) else __file__)}",
            f"配置目录: {display_path(self.config_dir)}",
            f"更新通道: {self.global_settings.get('update_channel', 'stable')}",
            ""
        ]

        session_text = get_session_log_text().strip()
        sections.append("===== 会话日志 =====")
        sections.append(session_text if session_text else "(无)")
        sections.append("")

        page_logs = [
            ("常规清理", getattr(self.pg_clean, "log", None)),
            ("大文件扫描", getattr(self.pg_big, "log", None)),
            ("应用强力卸载", getattr(self.pg_uninstall, "log", None)),
            ("更多清理", getattr(self.pg_more, "log", None)),
        ]
        for title, widget in page_logs:
            text = ""
            try:
                text = widget.toPlainText().strip() if widget is not None else ""
            except Exception as e:
                log_background_error(f"读取{title}日志控件失败", e)
                text = ""
            sections.append(f"===== {title} 页面日志 =====")
            sections.append(text if text else "(无)")
            sections.append("")

        return "\n".join(sections).rstrip() + "\n"

    def export_logs_to_path(self, path):
        if not path:
            return False, "导出路径不能为空"
        try:
            target = os.path.abspath(os.path.expandvars(path))
            write_text_file_atomic(target, self.build_export_log_text(), encoding="utf-8")
            append_session_log_line(f"[{time.strftime('%H:%M:%S')}] [日志导出] {target}")
            return True, ""
        except Exception as e:
            log_background_error("导出日志失败", e)
            return False, format_exception_text(e)

    def closeEvent(self, event):
        if self.global_settings.get("auto_save", True):
            try:
                self.pg_clean.save_custom_rules()
                self.save_order_state()
            except Exception as e:
                log_background_error("关闭窗口时自动保存失败", e)
        super().closeEvent(event)

    def _init_nav(self):
        self.navigationInterface.setExpandWidth(200); self.navigationInterface.setCollapsible(True)
        self.addSubInterface(self.pg_clean, FIF.BROOM, "常规清理")
        self.addSubInterface(self.pg_rule_store, FIF.DOCUMENT, "规则商店")
        self.addSubInterface(self.pg_big,   FIF.ZOOM,  "大文件扫描")
        self.addSubInterface(self.pg_uninstall, FIF.APPLICATION, "应用强力卸载")
        self.addSubInterface(self.pg_more,  FIF.MORE,  "更多清理")
        
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.pg_setting, FIF.SETTING, "设置", position=NavigationItemPosition.BOTTOM)
        self.navigationInterface.addItem(routeKey="about", icon=FIF.INFO, text="关于", onClick=self._about, selectable=False, position=NavigationItemPosition.BOTTOM)

    def _init_win(self):
        self.resize(1121, 646); self.setMinimumSize(874, 473); self.setWindowTitle(f"C盘强力清理工具 v{CURRENT_VERSION}")
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path): self.setWindowIcon(QIcon(icon_path))
        scr=QApplication.primaryScreen()
        if scr: g=scr.availableGeometry(); self.move((g.width()-self.width())//2,(g.height()-self.height())//2)

    def _conn(self):
        self.sig.clean_log.connect(lambda t: self._page_log(self.pg_clean, t))
        self.sig.clean_prog.connect(lambda v, m: self._page_prog(self.pg_clean, v, m))
        self.sig.clean_done.connect(self._clean_done)
        self.sig.est.connect(self._est)

        self.sig.big_log.connect(lambda t: self._page_log(self.pg_big, t))
        self.sig.big_clr.connect(lambda: self.pg_big.tbl.setRowCount(0)); self.sig.big_add.connect(self._badd)
        self.sig.big_prog.connect(self._big_prog); self.sig.big_done.connect(self._big_done); self.sig.big_scan_count.connect(self._big_scan_count)

        self.sig.uninst_log.connect(lambda t: self._page_log(self.pg_uninstall, t))
        self.sig.uninst_prog.connect(lambda v, m: self._page_prog(self.pg_uninstall, v, m))
        self.sig.uninst_done.connect(self._uninst_done)
        self.sig.uninst_clr.connect(lambda: self.pg_uninstall.tbl.setRowCount(0)); self.sig.uninst_add.connect(self._uadd)

        self.sig.more_log.connect(lambda t: self._page_log(self.pg_more, t))
        self.sig.more_prog.connect(lambda v, m: self._page_prog(self.pg_more, v, m))
        self.sig.more_done.connect(self._more_done)
        self.sig.more_clr.connect(lambda: self.pg_more.tbl.setRowCount(0)); self.sig.more_add.connect(self._madd)

        self.sig.update_found.connect(self._show_update_dialog)
        self.sig.update_status.connect(self._show_update_status)
        self.sig.update_latest.connect(self.pg_setting.set_latest_version_text)

    def _async_detect(self):
        threads, dtype = get_scan_threads_cached("C"); self.sig.disk_ready.emit(dtype, threads)

    def check_updates(self, manual=False):
        with self._update_lock:
            if self._update_checking:
                if manual:
                    InfoBar.warning("请稍候", "正在检查更新，请稍后再试", parent=self)
                return
            self._update_checking = True
        threading.Thread(target=self._check_update_worker, args=(manual,), daemon=True).start()

    def _get_latest_update(self):
        with urllib.request.urlopen(UPDATE_JSON_URL, timeout=8) as r:
            raw_text = r.read().decode("utf-8")

        payload = _load_update_payload(raw_text)
        if not payload:
            raise ValueError("更新信息解析失败")

        def _extract_entries(obj):
            if isinstance(obj, list):
                return [x for x in obj if isinstance(x, dict)]
            if not isinstance(obj, dict):
                return []

            if isinstance(obj.get("versions"), list):
                return [x for x in obj["versions"] if isinstance(x, dict)]

            entries = []
            for k in ("stable", "beta", "latest"):
                if isinstance(obj.get(k), dict):
                    entries.append(obj[k])
            if entries:
                return entries

            if any(k in obj for k in ("version", "tag", "name")):
                return [obj]
            return []

        channel = self.global_settings.get("update_channel", "stable")
        candidates = []

        for item in _extract_entries(payload):
            ver = item.get("version") or item.get("tag") or item.get("name") or ""
            url = item.get("url") or item.get("download_url") or item.get("download") or ""
            changelog = item.get("changelog") or item.get("notes") or item.get("desc") or ""
            if not ver:
                continue
            if channel == "stable" and (_is_prerelease(ver) or bool(item.get("prerelease", False))):
                continue
            candidates.append((ver, url, changelog))

        if not candidates:
            return None

        return max(candidates, key=lambda x: _version_key(x[0]))

    def _check_update_worker(self, manual=False):
        try:
            latest = self._get_latest_update()
            if latest:
                self.sig.update_latest.emit(f"最新版本：v{latest[0]}")
            else:
                self.sig.update_latest.emit("最新版本：未获取到")

            if latest and _version_key(latest[0]) > _version_key(CURRENT_VERSION):
                self.sig.update_found.emit(latest[0], latest[1], latest[2])
            elif manual:
                self.sig.update_status.emit("success", "提示", "当前已是最新版本")
        except Exception as e:
            self.sig.update_latest.emit("最新版本：获取失败")
            if manual:
                self.sig.update_status.emit("error", "检查失败", f"无法获取更新信息: {e}")
        finally:
            with self._update_lock:
                self._update_checking = False

    def _show_update_dialog(self, version, url, changelog):
        if MessageBox(f"发现新版本 v{version}", f"更新内容：\n{changelog}\n\n是否立即前往下载？", self.window()).exec() and url: webbrowser.open(url)

    def _show_update_status(self, level, title, content):
        bar_fn = {
            "success": InfoBar.success,
            "warning": InfoBar.warning,
            "error": InfoBar.error
        }.get(level, InfoBar.success)
        bar_fn(title, content, orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self)

    def _ts(self): return time.strftime("%H:%M:%S")

    def _page_log(self, page, t):
        line=f"[{self._ts()}] {t}"
        append_session_log_line(line)
        append_capped_log(page.log, line)
        page.sl.setText(t[:80])

    def _page_prog(self, page, v, m):
        if m <= 0:
            page.pb.setRange(0, 0)
        else:
            page.pb.setRange(0, max(1, m))
            page.pb.setValue(v)

    def _est(self, idx, val):
        try:
            safe_val = max(0, int(val))
        except Exception:
            safe_val = 0
        self.pg_clean.apply_estimate(idx, safe_val)

    def _big_prog(self, v, m):
        if m <= 0:
            self.pg_big.pb.setRange(0, 0)
        else:
            self.pg_big.pb.setRange(0, max(1, m))
            self.pg_big.pb.setValue(v)

    def _big_scan_count(self, scanned):
        self.pg_big.sl.setText(f"已扫描 {max(0, int(scanned))} 个文件")

    def _big_done(self, level, msg):
        self.pg_big.pb.setRange(0, 100)
        self.pg_big.pb.setValue(0)
        self.pg_big.sl.setText("完成" if level == "success" else msg[:80])
        line = f"[{self._ts()}] [完成] {msg}"
        append_capped_log(self.pg_big.log, line)
        bar_fn = {
            "success": InfoBar.success,
            "warning": InfoBar.warning,
            "error": InfoBar.error
        }.get(level, InfoBar.success)
        bar_fn("完成" if level == "success" else "提示", msg, orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=4000, parent=self)

    def _finish_page(self, page, msg, title="完成"):
        page.pb.setRange(0, 100)
        page.pb.setValue(0)
        page.sl.setText("完成")
        append_capped_log(page.log, f"[{self._ts()}] [完成] {msg}")
        InfoBar.success(title, msg, orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=4000, parent=self)

    def _clean_done(self, msg):
        self.pg_clean.tbl.setDragEnabled(True) 
        self._finish_page(self.pg_clean, msg)

    def _uninst_done(self, msg):
        self._finish_page(self.pg_uninstall, msg)

    def _more_done(self, msg):
        self._finish_page(self.pg_more, msg)

    def _badd(self, sz_str, pa):
        t=self.pg_big.tbl; r=t.rowCount(); t.setRowCount(r+1); t.setItem(r, 0, make_check_item(False)); t.setItem(r, 1, QTableWidgetItem(os.path.basename(pa) if pa else ""))
        s=SizeTableWidgetItem(human_size(int(sz_str))); s.setData(Qt.ItemDataRole.UserRole, int(sz_str)); s.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        t.setItem(r, 2, s); t.setItem(r, 3, QTableWidgetItem(pa))
        self.pg_big._apply_sort()

    def _madd(self, chk, tp, nm, det, pa):
        t=self.pg_more.tbl; r=t.rowCount(); t.setRowCount(r+1)
        type_item = QTableWidgetItem(tp)
        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if tp == "系统":
            type_item.setForeground(QColor(196, 92, 32))
        elif tp == "外部":
            type_item.setForeground(QColor(0, 120, 215) if not isDarkTheme() else QColor(120, 180, 255))
        elif tp == "未知":
            type_item.setForeground(QColor(180, 120, 0))
        t.setItem(r, 0, make_check_item(chk)); t.setItem(r, 1, type_item); t.setItem(r, 2, QTableWidgetItem(nm))
        t.setItem(r, 3, QTableWidgetItem(det)); t.setItem(r, 4, QTableWidgetItem(pa))

    def _uadd(self, item): 
        t=self.pg_uninstall.tbl; r=t.rowCount(); t.setRowCount(r+1)
        nm = item.get("name", "")
        ver = item.get("version", "")
        pub = item.get("publisher", "")
        loc = item.get("location", "")
        reg = item.get("reg", "")
        cmd = item.get("cmd", "")
        icon_path = item.get("icon_path", "")
        category = item.get("category", "用户")
        is_risky = bool(item.get("is_risky", False))
        risk_kind = item.get("risk_kind", "")
        risk_reason = item.get("risk_reason", "")

        name_item = QTableWidgetItem(nm)
        if icon_path and os.path.exists(icon_path):
            provider = QFileIconProvider()
            icon = provider.icon(QFileInfo(icon_path))
            if not icon.isNull():
                name_item.setIcon(icon)
        else:
            name_item.setIcon(FIF.APPLICATION.icon())

        risk_tip = "普通项目"
        if category == "系统":
            risk_tip = f"高风险：系统组件\n{risk_reason}" if risk_reason else "高风险：系统组件"
        elif is_risky:
            risk_tip = f"高风险：可能影响系统或其他软件\n{risk_reason}" if risk_reason else "高风险：可能影响系统或其他软件"
        elif risk_reason:
            risk_tip = risk_reason

        name_item.setToolTip(risk_tip)

        category_item = QTableWidgetItem(category)
        if category == "系统":
            category_item.setForeground(QColor(196, 92, 32))
        elif is_risky:
            category_item.setForeground(QColor(180, 120, 0))
        else:
            category_item.setForeground(QColor(96, 96, 96))
        category_item.setToolTip(risk_tip)

        t.setItem(r, 0, make_check_item(False))
        t.setItem(r, 1, category_item)
        t.setItem(r, 2, name_item) 
        t.setItem(r, 3, QTableWidgetItem(ver))
        t.setItem(r, 4, QTableWidgetItem(pub))
        t.setItem(r, 5, QTableWidgetItem(loc))
        hidden_item = QTableWidgetItem(cmd)
        hidden_item.setData(Qt.ItemDataRole.UserRole, reg)
        hidden_item.setData(Qt.ItemDataRole.UserRole + 1, {
            "category": category,
            "is_risky": is_risky,
            "risk_kind": risk_kind,
            "risk_reason": risk_reason
        })
        t.setItem(r, 6, hidden_item)

    def _about(self):
        MessageBox("关于", f"C盘强力清理工具 v{CURRENT_VERSION}\nQQ交流群：670804369\nUI：Fluent Widgets\nby Kio",self).exec()

def relaunch_as_admin():
    try:
        if getattr(sys, "frozen", False):
            params = subprocess.list2cmdline(sys.argv[1:])
        else:
            params = subprocess.list2cmdline(sys.argv)
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            params or None,
            None,
            1
        )
    except Exception:
        pass
    sys.exit(0)

def main():
    if sys.platform != "win32": sys.exit(1)
    if not is_admin(): relaunch_as_admin()
    app = QApplication(sys.argv); setFontFamilies(["微软雅黑"]); setTheme(Theme.AUTO); setThemeColor("#0078d4")
    w = MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": main()

