# -*- coding: utf-8 -*-
"""
C盘强力清理工具 v0.2.5
PySide6 + PySide6-Fluent-Widgets (Fluent2 UI)
包含：常规清理(支持拖拽排序与自定义规则)、大文件扫描、重复文件、空文件夹、无效快捷方式
新增
- 应用强力卸载支持多选批量操作。
- 标准卸载支持多选顺序执行，单个软件卸载完成后可继续选择是否扫描残留。
- 强力卸载支持多选后统一清理，自动合并并去重残留文件与注册表项。
优化
- 大文件扫描页将“类型 / 线程”信息移动到标题右侧，信息展示更集中。
- 卸载与强力清理流程日志更清晰，完成提示更明确。
- 优化“选择范围”可读性与多盘显示体验
"""

import os, sys, time, ctypes, threading, subprocess, queue, json, hashlib, winreg, re
import urllib.request
import webbrowser
from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QObject, QPoint, QMetaObject, Slot, QFileInfo, QSize
from PySide6.QtGui import QFont, QIcon, QColor, QPainter, QDrag, QPixmap, QRegion
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
CURRENT_VERSION = "0.2.5"
UPDATE_JSON_URL = "https://gitee.com/kio0/c_cleaner_plus/raw/master/update.json"

from qfluentwidgets.components.widgets.table_view import TableItemDelegate

def resource_path(relative_path):
    if getattr(sys, '_MEIPASS', None): return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

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
    except: return False

def human_size(n):
    s=float(n)
    for u in ("B","KB","MB","GB","TB"):
        if s<1024 or u=="TB": return f"{s:.2f} {u}"
        s/=1024
    return f"{n} B"

def safe_getsize(p):
    try: return os.path.getsize(p)
    except: return 0

def dir_size(path):
    t=0
    for r,ds,fs in os.walk(path,topdown=True):
        ds[:]=[d for d in ds if not os.path.islink(os.path.join(r,d))]
        for f in fs: t+=safe_getsize(os.path.join(r,f))
    return t

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

def force_delete_registry(full_path, log_fn):
    """使用 Windows 原生 reg delete 命令进行强制递归删除，穿透力更强"""
    try:
        # full_path 格式如 "HKLM\SOFTWARE\Tencent"
        cmd = ['reg', 'delete', full_path, '/f']
        # creationflags=subprocess.CREATE_NO_WINDOW 防止弹黑框
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            log_fn(f"[强删注册表] 成功: {full_path}")
            return True
        else:
            # 如果依然失败，说明是 TrustedInstaller 或 SYSTEM 级死锁保护
            err_msg = r.stderr.strip().replace('\n', ' ')
            log_fn(f"[强删注册表] 权限不足(可能受系统保护): {full_path} -> {err_msg}")
            return False
    except Exception as e:
        log_fn(f"[强删注册表] 异常: {e}")
        return False
    
def kill_app_processes(install_dir, log_fn):
    """强力猎杀目标目录下的所有运行中进程、Windows服务 以及 内核驱动"""
    if not install_dir or not os.path.exists(install_dir): return
    try:
        log_fn(f"[内核猎杀] 正在扫描并解除 '{install_dir}' 的进程与驱动锁定...")
        ps_script = f"""
        $target = [regex]::Escape("{install_dir}")
        
        # 1. 杀常规进程
        Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -match $target }} | Stop-Process -Force -ErrorAction SilentlyContinue
        
        # 2. 停服务并删除
        Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {{ $_.PathName -match $target }} | ForEach-Object {{
            Stop-Service -Name $_.Name -Force -ErrorAction SilentlyContinue
            & sc.exe delete $_.Name
        }}
        
        # 3. 停内核驱动并删除
        Get-CimInstance Win32_SystemDriver -ErrorAction SilentlyContinue | Where-Object {{ $_.PathName -match $target }} | ForEach-Object {{
            & sc.exe stop $_.Name
            & sc.exe delete $_.Name
        }}
        """
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        log_fn(f"[内核猎杀] 异常: {e}")

# ══════════════════════════════════════════════════════════
#  类型检测 + 缓存
# ══════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.environ.get("TEMP", "."), "cdisk_cleaner_cache.json")

def detect_disk_type(drive_letter="C"):
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
    return {"SSD": 16, "HDD": 2, "Unknown": 4}.get(dtype, 4), dtype

def get_scan_threads_cached(drive_letter="C"):
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if time.time() - cache.get("ts", 0) < 86400:
                return cache["threads"], cache["dtype"]
    except: pass
    threads, dtype = get_scan_threads(drive_letter)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f: json.dump({"threads": threads, "dtype": dtype, "ts": time.time()}, f)
    except: pass
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
        ("缩略图缓存", J(la, "Microsoft", "Windows", "Explorer"), "glob", True, "thumbcache*.db", False),
        
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

def should_exclude(p, prefixes):
    n=os.path.normcase(os.path.abspath(p))
    return any(n.startswith(os.path.normcase(os.path.abspath(e))) for e in prefixes)

# ══════════════════════════════════════════════════════════
#  多线程文件扫描
# ══════════════════════════════════════════════════════════
_SENTINEL = None

def _dir_worker(dir_queue, min_b, excl, stop_flag, results, counter, lock):
    while not stop_flag.is_set():
        try: dirpath = dir_queue.get(timeout=0.05)
        except queue.Empty: continue
        if dirpath is _SENTINEL: dir_queue.put(_SENTINEL); break
        try: entries = os.scandir(dirpath)
        except: dir_queue.task_done(); continue
        local_res = []; local_count = 0
        try:
            for entry in entries:
                if stop_flag.is_set(): break
                try:
                    if entry.is_symlink(): continue
                    if entry.is_dir(follow_symlinks=False):
                        if not should_exclude(entry.path, excl): dir_queue.put(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        if os.path.splitext(entry.name)[1].lower() in BIGFILE_SKIP_EXT: continue
                        st = entry.stat(follow_symlinks=False); local_count += 1
                        if st.st_size >= min_b: local_res.append((st.st_size, entry.path))
                except: pass
        finally:
            try: entries.close()
            except: pass
        if local_res or local_count:
            with lock: results.extend(local_res); counter[0] += local_count
        dir_queue.task_done()

def scan_big_files(roots, min_b, excl, stop, cb, workers=4):
    dir_queue = queue.Queue(); results = []; counter = [0]; lock = threading.Lock()
    for root in roots: dir_queue.put(root)
    threads = []
    for _ in range(workers):
        t = threading.Thread(target=_dir_worker, args=(dir_queue, min_b, excl, stop, results, counter, lock), daemon=True)
        t.start(); threads.append(t)
    tk = time.time()
    while not stop.is_set():
        try:
            dir_queue.all_tasks_done.acquire()
            if dir_queue.unfinished_tasks == 0: dir_queue.all_tasks_done.release(); break
            dir_queue.all_tasks_done.release()
        except: pass
        now = time.time()
        if now - tk >= 0.3: cb(counter[0]); tk = now
        time.sleep(0.05)
    dir_queue.put(_SENTINEL)
    for t in threads: t.join(timeout=2)
    cb(counter[0])
    results.sort(reverse=True, key=lambda x: x[0])
    return results

class Sig(QObject):
    log=Signal(str); prog=Signal(int,int); est=Signal(int,int)
    big_clr=Signal(); big_add=Signal(str,str); done=Signal(str)
    disk_ready=Signal(str,int); update_found=Signal(str, str, str)
    more_clr=Signal(); more_add=Signal(bool, str, str, str, str)
    uninst_clr=Signal(); uninst_add=Signal(str, str, str, str, str, str, str)

def style_table(tbl: TableWidget):
    setFont(tbl, 12, QFont.Weight.Normal)
    setFont(tbl.horizontalHeader(), 12, QFont.Weight.DemiBold)
    tbl.verticalHeader().setDefaultSectionSize(30)
    tbl.setItemDelegate(FluentOnlyCheckDelegate(tbl))

def norm_path(text):
    if not text: return ""
    p=text.split(" |",1)[0].strip().strip('"').strip("'")
    p=expand_env(p).replace("/","\\")
    try: p=os.path.normpath(p)
    except: pass
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
    except: pass

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

def make_rule_key(nm, pa, tp):
    return (nm, pa, tp)

def load_rule_keys(raw_items):
    keys = set()
    for item in raw_items or []:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            keys.add((item[0], item[1], item[2]))
    return keys

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
        self.descInput = LineEdit(); self.descInput.setPlaceholderText("说明备注 (例如: 仅限个人使用)")
        
        self.viewLayout.addWidget(StrongBodyLabel("规则名称:")); self.viewLayout.addWidget(self.nameInput)
        self.viewLayout.addWidget(StrongBodyLabel("目标路径:")); self.viewLayout.addLayout(self.pathLayout)
        self.viewLayout.addWidget(StrongBodyLabel("目标类型:")); self.viewLayout.addWidget(self.typeCombo)
        self.viewLayout.addWidget(StrongBodyLabel("备注说明:")); self.viewLayout.addWidget(self.descInput)
        
        self.widget.setMinimumWidth(450); self.yesButton.setText("添加"); self.cancelButton.setText("取消")
        
    def _browse(self):
        idx = self.typeCombo.currentIndex()
        if idx == 0 or idx == 2:
            folder = QFileDialog.getExistingDirectory(self, "选择清理目录")
            if folder: self.pathInput.setText(folder.replace("/", "\\"))
        else:
            file, _ = QFileDialog.getOpenFileName(self, "选择清理文件")
            if file: self.pathInput.setText(file.replace("/", "\\"))
            
    def get_data(self):
        t_map = {0: "dir", 1: "file", 2: "glob"}
        return (self.nameInput.text().strip(), self.pathInput.text().strip(), t_map[self.typeCombo.currentIndex()], True, self.descInput.text().strip() or "自定义附加规则", True)

# ══════════════════════════════════════════════════════════
#  页面：全局设置 (SettingPage)
# ══════════════════════════════════════════════════════════
class SettingPage(ScrollArea):
    def __init__(self, main_win, parent=None):
        super().__init__(parent)
        self.main_win = main_win
        self.view = QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("settingPage"); self.enableTransparentBackground()
        v = QVBoxLayout(self.view); v.setContentsMargins(28, 12, 28, 20); v.setSpacing(16)
        v.addLayout(make_title_row(FIF.SETTING, "系统设置"))

        def _smooth_title_font(label):
            # 仅处理设置页卡片标题，降低粗体带来的锯齿感
            setFont(label, 13, QFont.Weight.Medium)
            f = label.font()
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            label.setFont(f)

        # 1. 自动保存设置卡片
        card_save = CardWidget(self.view)
        cv_save = QVBoxLayout(card_save)
        h_save = QHBoxLayout()
        text_v_save = QVBoxLayout(); text_v_save.setSpacing(2)
        lbl1 = StrongBodyLabel("退出时自动保存配置")
        _smooth_title_font(lbl1)
        lbl2 = CaptionLabel("开启后，将自动保存常规清理中的勾选状态、自定义规则以及你所拖动的排序结果。")
        lbl2.setTextColor(QColor(128, 128, 128))
        text_v_save.addWidget(lbl1); text_v_save.addWidget(lbl2)
        h_save.addLayout(text_v_save); h_save.addStretch()

        self.switch_save = SwitchButton()
        self.switch_save.setOnText("开启"); self.switch_save.setOffText("关闭")
        self.switch_save.setChecked(self.main_win.global_settings.get("auto_save", True))
        self.switch_save.checkedChanged.connect(self._on_auto_save_changed)
        h_save.addWidget(self.switch_save)
        cv_save.addLayout(h_save)
        v.addWidget(card_save)

        # 2. 内置规则保护卡片
        card_protect = CardWidget(self.view)
        cv_protect = QVBoxLayout(card_protect)
        h_protect = QHBoxLayout()
        text_v_protect = QVBoxLayout(); text_v_protect.setSpacing(2)
        lbl_protect1 = StrongBodyLabel("内置默认规则保护")
        _smooth_title_font(lbl_protect1)
        lbl_protect2 = CaptionLabel("开启后，常规清理中的内置默认规则无法删除；关闭后可删除，且删除结果会保留到下次启动。")
        lbl_protect2.setTextColor(QColor(128, 128, 128))
        text_v_protect.addWidget(lbl_protect1); text_v_protect.addWidget(lbl_protect2)
        h_protect.addLayout(text_v_protect); h_protect.addStretch()

        self.switch_protect_builtin = SwitchButton()
        self.switch_protect_builtin.setOnText("开启"); self.switch_protect_builtin.setOffText("关闭")
        self.switch_protect_builtin.setChecked(self.main_win.global_settings.get("protect_builtin_rules", True))
        self.switch_protect_builtin.checkedChanged.connect(self._on_protect_builtin_changed)
        h_protect.addWidget(self.switch_protect_builtin)
        cv_protect.addLayout(h_protect)
        v.addWidget(card_protect)

        # 3. 刷新缓存卡片
        card_cache = CardWidget(self.view)
        cv_cache = QVBoxLayout(card_cache)
        h_cache = QHBoxLayout()
        text_v_cache = QVBoxLayout(); text_v_cache.setSpacing(2)
        lbl_cache1 = StrongBodyLabel("刷新系统扫描缓存")
        _smooth_title_font(lbl_cache1)
        lbl_cache2 = CaptionLabel("刷新自身软件对硬盘类型的检测缓存，当更换或添加硬盘后建议执行。")
        lbl_cache2.setTextColor(QColor(128, 128, 128))
        text_v_cache.addWidget(lbl_cache1); text_v_cache.addWidget(lbl_cache2)
        h_cache.addLayout(text_v_cache); h_cache.addStretch()
        
        btn_cache = PushButton(FIF.SYNC, "刷新")
        btn_cache.clicked.connect(self._refresh_cache)
        h_cache.addWidget(btn_cache)
        cv_cache.addLayout(h_cache)
        v.addWidget(card_cache)

        # 4. 恢复默认配置卡片
        card_reset = CardWidget(self.view)
        cv_reset = QVBoxLayout(card_reset)
        h_reset = QHBoxLayout()
        text_v_reset = QVBoxLayout(); text_v_reset.setSpacing(2)
        lbl_reset1 = StrongBodyLabel("恢复默认配置")
        _smooth_title_font(lbl_reset1)
        lbl_reset2 = CaptionLabel("将常规清理的勾选项、拖拽排序恢复为初始状态，并清除所有自定义规则。")
        lbl_reset2.setTextColor(QColor(128, 128, 128))
        text_v_reset.addWidget(lbl_reset1); text_v_reset.addWidget(lbl_reset2)
        h_reset.addLayout(text_v_reset); h_reset.addStretch()
        
        btn_reset = PushButton(FIF.UPDATE, "恢复")
        btn_reset.clicked.connect(self._reset_defaults)
        h_reset.addWidget(btn_reset)
        cv_reset.addLayout(h_reset)
        v.addWidget(card_reset)

        # 5. 更新通道卡片
        card_update = CardWidget(self.view)
        cv_update = QVBoxLayout(card_update)
        h_update = QHBoxLayout()
        text_v_update = QVBoxLayout(); text_v_update.setSpacing(2)
        lbl_up1 = StrongBodyLabel("更新通道")
        _smooth_title_font(lbl_up1)
        lbl_up2 = CaptionLabel("选择稳定版仅接收正式版本推送；测试版会接收 alpha/beta/rc 等预发布版本。")
        lbl_up2.setTextColor(QColor(128, 128, 128))
        text_v_update.addWidget(lbl_up1); text_v_update.addWidget(lbl_up2)
        h_update.addLayout(text_v_update); h_update.addStretch()

        self.cb_update_channel = ComboBox()
        self.cb_update_channel.addItems(["稳定版", "测试版"])
        saved_channel = self.main_win.global_settings.get("update_channel", "stable")
        self.cb_update_channel.setCurrentIndex(1 if saved_channel == "beta" else 0)
        self.cb_update_channel.currentIndexChanged.connect(self._on_update_channel_changed)
        h_update.addWidget(self.cb_update_channel)
        cv_update.addLayout(h_update)
        v.addWidget(card_update)

        v.addStretch()

    def _on_auto_save_changed(self, is_checked):
        self.main_win.global_settings["auto_save"] = is_checked
        self.main_win.save_global_settings()

    def _on_protect_builtin_changed(self, is_checked):
        self.main_win.global_settings["protect_builtin_rules"] = is_checked
        self.main_win.save_global_settings()

    def _on_update_channel_changed(self, _):
        self.main_win.global_settings["update_channel"] = "beta" if self.cb_update_channel.currentIndex() == 1 else "stable"
        self.main_win.save_global_settings()

    def _refresh_cache(self):
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
            threading.Thread(target=self.main_win._async_detect, daemon=True).start()
            InfoBar.success("刷新成功", "软件缓存已清除并重新开始硬盘测速检测！", parent=self.main_win)
        except Exception as e:
            InfoBar.error("刷新失败", f"无法清除缓存文件: {e}", parent=self.main_win)

    def _reset_defaults(self):
        w = MessageBox("确认恢复", "确定要将常规清理的选项恢复至默认状态吗？\n警告：这将会清除您所有已添加的自定义规则和排序！", self.main_win)
        if w.exec():
            try:
                # 重置 targets 列表
                self.main_win.targets.clear()
                self.main_win.targets.extend(default_clean_targets())
                
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
    def __init__(self, sig, targets, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.targets=targets; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("cleanPage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        v.addLayout(make_title_row(FIF.BROOM, "常规清理"))
        badge = "管理员" if is_admin() else "非管理员"
        v.addWidget(CaptionLabel(f"当前权限：{badge}  |  长按或框选项目可拖动排序"))

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
        self.tbl.setRowCount(len(self.targets))
        for i,(nm,pa,tp,en,nt,is_c) in enumerate(self.targets):
            disp_name = f"{nm} (自定义)" if is_c else nm
            chk_item = make_check_item(en)
            name_item = QTableWidgetItem(disp_name)
            name_item.setData(Qt.ItemDataRole.UserRole, (nm, pa, tp, is_c))
            
            self.tbl.setItem(i, 0, chk_item)
            self.tbl.setItem(i, 1, name_item)
            self.tbl.setItem(i, 2, QTableWidgetItem(pa if tp!="glob" else f"{pa} | thumbcache*.db"))
            self.tbl.setItem(i, 3, QTableWidgetItem(nt))
            self.tbl.setItem(i, 4, QTableWidgetItem(""))

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
        self._sync()

    def _sync(self):
        new_targets = []
        for r in range(self.tbl.rowCount()):
            name_item = self.tbl.item(r, 1)
            if not name_item: continue
            
            user_data = name_item.data(Qt.ItemDataRole.UserRole)
            if user_data:
                nm, pa, tp, is_c = user_data
            else: continue
                
            en = is_row_checked(self.tbl, r)
            nt = self.tbl.item(r, 3).text() if self.tbl.item(r, 3) else ""
            new_targets.append((nm, pa, tp, en, nt, is_c))
        
        if new_targets:
            self.targets[:] = new_targets

    def _try_rst(self):
        if not getattr(self, 'chk_rst', None) or not self.chk_rst.isChecked(): return
        if not is_admin():
            self.sig.log.emit("[还原点] 需管理员权限，跳过"); return
        self.sig.log.emit("[还原点] 正在创建系统还原点，请稍候...")
        try:
            r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass",
                "Checkpoint-Computer","-Description","'CleanTool_Backup'","-RestorePointType","MODIFY_SETTINGS"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                self.sig.log.emit("[还原点] 创建成功！")
            else:
                self.sig.log.emit(f"[还原点] 创建失败 (系统可能未开启保护或达到限制): {r.stderr.strip()[:100]}")
        except Exception as e:
            self.sig.log.emit(f"[还原点] 创建异常: {e}")

    def save_custom_rules(self):
        self._sync() 
        customs = [t for t in self.targets if t[5]]
        path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cdisk_cleaner_custom_rules.json")
        try:
            with open(path, 'w', encoding='utf-8') as f: json.dump(customs, f, ensure_ascii=False, indent=2)
        except: pass

    def do_add_rule(self):
        w = AddRuleDialog(self.window())
        if w.exec():
            nm, pa, tp, en, nt, is_c = w.get_data()
            if not nm or not pa:
                InfoBar.error("错误", "名称和路径不能为空", parent=self.window()); return
            self.targets.append((nm, pa, tp, en, nt, is_c))
            r = self.tbl.rowCount(); self.tbl.setRowCount(r + 1)
            name_item = QTableWidgetItem(nm + " (自定义)")
            name_item.setData(Qt.ItemDataRole.UserRole, (nm, pa, tp, is_c))
            
            self.tbl.setItem(r, 0, make_check_item(en))
            self.tbl.setItem(r, 1, name_item)
            self.tbl.setItem(r, 2, QTableWidgetItem(pa))
            self.tbl.setItem(r, 3, QTableWidgetItem(nt))
            self.tbl.setItem(r, 4, QTableWidgetItem(""))
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

        checked_rows = [r for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r)]
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
            nm, pa, tp, is_c = user_data
            rule_key = make_rule_key(nm, pa, tp)
            if protect_builtin and rule_key in builtin_keys:
                protected_count += 1
                continue
            if rule_key in builtin_keys:
                deleted_builtin_now.append(rule_key)
            deletable_keys.append((nm, pa, tp, is_c))

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
        for i in range(len(self.targets) - 1, -1, -1):
            nm, pa, tp, _, _, is_c = self.targets[i]
            if (nm, pa, tp, is_c) in del_key_set:
                self.targets.pop(i)

        # 再删表格行（倒序）
        for r in range(self.tbl.rowCount() - 1, -1, -1):
            it = self.tbl.item(r, 1)
            if not it:
                continue
            ud = it.data(Qt.ItemDataRole.UserRole)
            if ud and tuple(ud) in del_key_set:
                self.tbl.removeRow(r)

        if deleted_builtin_now:
            deleted_keys = getattr(self.window(), "deleted_builtin_rule_keys", set())
            deleted_keys.update(deleted_builtin_now)
            self.window().deleted_builtin_rule_keys = deleted_keys
            self.window().global_settings["deleted_builtin_rules"] = [list(k) for k in sorted(deleted_keys)]
            self.window().save_global_settings()

        self.save_custom_rules()
        if protected_count > 0:
            InfoBar.success(
                "已清除",
                f"已清除 {len(deletable_keys)} 条规则，已跳过 {protected_count} 条内置规则。",
                parent=self.window()
            )
        else:
            InfoBar.success("已清除", f"已清除 {len(deletable_keys)} 条规则。", parent=self.window())

    def do_export_rules(self):
        self._sync()
        customs = [t for t in self.targets if t[5]]
        if not customs: InfoBar.warning("提示", "当前没有自定义规则可以导出", parent=self.window()); return
        path, _ = QFileDialog.getSaveFileName(self, "导出规则集", "CleanRules.json", "JSON 文件 (*.json)")
        if path:
            with open(path, 'w', encoding='utf-8') as f: json.dump(customs, f, ensure_ascii=False, indent=2)
            InfoBar.success("导出成功", f"规则已保存至: {path}", parent=self.window())

    def do_import_rules(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入规则集", "", "JSON 文件 (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f: rules = json.load(f)
                added = 0
                for r_data in rules:
                    if len(r_data) >= 5:
                        nm, pa, tp, en, nt = r_data[0], r_data[1], r_data[2], r_data[3], r_data[4]
                        if any(t[0] == nm and t[1] == pa for t in self.targets): continue
                        self.targets.append((nm, pa, tp, en, nt, True))
                        r = self.tbl.rowCount(); self.tbl.setRowCount(r + 1)
                        name_item = QTableWidgetItem(nm + " (自定义)")
                        name_item.setData(Qt.ItemDataRole.UserRole, (nm, pa, tp, True))
                        
                        self.tbl.setItem(r, 0, make_check_item(en)); self.tbl.setItem(r, 1, name_item)
                        self.tbl.setItem(r, 2, QTableWidgetItem(pa)); self.tbl.setItem(r, 3, QTableWidgetItem(nt)); self.tbl.setItem(r, 4, QTableWidgetItem(""))
                        added += 1
                if added > 0:
                    self.save_custom_rules(); InfoBar.success("导入成功", f"成功导入 {added} 条自定义规则", parent=self.window())
                else: InfoBar.warning("提示", "未导入任何规则（可能存在重复）", parent=self.window())
            except Exception as e: InfoBar.error("导入失败", f"文件读取错误: {e}", parent=self.window())

    def do_est(self): 
        self.tbl.setDragEnabled(False) 
        self._sync(); self.stop.clear(); threading.Thread(target=self._est_w,daemon=True).start()
        
    def _est_w(self):
        t0 = time.time()
        import fnmatch
        its=[(i,t) for i,t in enumerate(self.targets) if t[3]]
        if not its:
            self.sig.done.emit(f"估算失败：未勾选任何项目")
            return
        self.sig.prog.emit(0,len(its))
        for n,(idx,t) in enumerate(its,1):
            if self.stop.is_set():
                self.sig.done.emit(f"估算已取消，耗时 {time.time()-t0:.1f} 秒")
                return
            nm,pa,tp,_,_,_ = t
            e=0
            try:
                if tp=="dir": e=dir_size(expand_env(pa)) if os.path.isdir(expand_env(pa)) else 0
                elif tp=="glob": 
                    fo=expand_env(pa)
                    if os.path.isdir(fo): e=sum(safe_getsize(os.path.join(fo,f)) for f in os.listdir(fo) if fnmatch.fnmatch(f.lower(),"thumbcache*.db"))
                elif tp=="file": e=safe_getsize(expand_env(pa)) if os.path.isfile(expand_env(pa)) else 0
            except: pass
            self.sig.est.emit(idx,e); self.sig.prog.emit(n,len(its))
        self.sig.done.emit(f"估算完成，耗时 {time.time()-t0:.1f} 秒")

    def do_clean(self):
        self.tbl.setDragEnabled(False)
        self._sync()
        if self.chk_perm.isChecked():
            if not MessageBox("确认", "当前为强力模式，删除后无法恢复。继续？", self.window()).exec(): 
                self.tbl.setDragEnabled(True)
                return
        self.stop.clear(); threading.Thread(target=self._cln_w, daemon=True).start()
    
    def _cln_w(self):
        t0 = time.time()
        import fnmatch; pm=self.chk_perm.isChecked(); sel=[(n,p,t) for n,p,t,en,_,_ in self.targets if en]
        if not sel: return
        
        # 清理前创建还原点
        self._try_rst()
        
        ok=fl=st=0; tot=len(sel); lf=lambda s:self.sig.log.emit(s)
        for nm,pa,tp in sel:
            if self.stop.is_set():
                self.sig.done.emit(f"清理已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            st+=1; p=expand_env(pa)
            try:
                if tp=="dir" and os.path.isdir(p):
                    for e in os.listdir(p):
                        if self.stop.is_set(): break
                        if delete_path(os.path.join(p,e),pm,lf): ok+=1
                        else: fl+=1
                elif tp=="glob" and os.path.isdir(p):
                    for f in os.listdir(p):
                        if self.stop.is_set(): break
                        if fnmatch.fnmatch(f.lower(),"thumbcache*.db"):
                            if delete_path(os.path.join(p,f),pm,lf): ok+=1
                            else: fl+=1
                elif tp=="file" and os.path.exists(p):
                    if delete_path(p,pm,lf): ok+=1
                    else: fl+=1
            except: fl+=1
            self.sig.prog.emit(st,tot)
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")


class LeftoversDialog(MessageBoxBase):
    def __init__(self, parent, app_name, publisher, install_dir, uninst_reg):
        super().__init__(parent)
        self.app_name = app_name
        self.publisher = publisher
        self.install_dir = install_dir
        self.uninst_reg = uninst_reg
        self.leftovers = {"files": [], "regs": []}
        
        self.customTitle = TitleLabel(f"发现 '{app_name}' 的残留痕迹")
        setFont(self.customTitle, 16, QFont.Weight.Bold)
        self.viewLayout.addWidget(self.customTitle)
        self.viewLayout.addSpacing(10) 
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["残留项目", "路径"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setMinimumHeight(250)
        self.viewLayout.addWidget(self.tree)
        
        self.yesButton.setText("删除选中项")
        self.cancelButton.setText("取消")
        
        self.widget.setMinimumWidth(600)
        self._scan_leftovers()
        
    def _scan_leftovers(self):
        paths_to_check = []
        if self.install_dir and os.path.exists(self.install_dir):
            paths_to_check.append(self.install_dir)
            
        app_data = os.environ.get("APPDATA", "")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        prog_data = os.environ.get("PROGRAMDATA", "")
        
        keywords = [k for k in [self.publisher, self.app_name.split()[0]] if k and len(k) > 2]
        for base in [app_data, local_app_data, prog_data]:
            if not base: continue
            for kw in keywords:
                guess = os.path.join(base, kw)
                if os.path.exists(guess) and guess not in paths_to_check:
                    paths_to_check.append(guess)
                    
        regs_to_check = []
        if self.uninst_reg: regs_to_check.append(self.uninst_reg)
        
        for base_key_str, hkey in [("HKCU\\Software", winreg.HKEY_CURRENT_USER), ("HKLM\\Software", winreg.HKEY_LOCAL_MACHINE)]:
            for kw in keywords:
                try:
                    k = winreg.OpenKey(hkey, f"Software\\{kw}")
                    winreg.CloseKey(k)
                    regs_to_check.append(f"{base_key_str}\\{kw}")
                except OSError: pass

        self._populate_tree(paths_to_check, regs_to_check)

    def _populate_tree(self, files, regs):
        if files:
            f_root = QTreeWidgetItem(self.tree, ["文件与文件夹"])
            f_root.setFlags(f_root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            f_root.setCheckState(0, Qt.CheckState.Checked)
            for f in files:
                child = QTreeWidgetItem(f_root, ["文件夹", f])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                self.leftovers["files"].append((child, f))
            f_root.setExpanded(True)
            
        if regs:
            r_root = QTreeWidgetItem(self.tree, ["注册表项"])
            r_root.setFlags(r_root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            r_root.setCheckState(0, Qt.CheckState.Checked)
            for r in regs:
                child = QTreeWidgetItem(r_root, ["注册表键", r])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                self.leftovers["regs"].append((child, r))
            r_root.setExpanded(True)

    def get_selected_items(self):
        del_files = [path for item, path in self.leftovers["files"] if item.checkState(0) == Qt.CheckState.Checked]
        del_regs = [path for item, path in self.leftovers["regs"] if item.checkState(0) == Qt.CheckState.Checked]
        return del_files, del_regs

class UninstallPage(ScrollArea):
    def __init__(self, sig, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("uninstallPage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        v.addLayout(make_title_row(FIF.APPLICATION, "应用强力卸载"))
        v.addWidget(CaptionLabel("标准卸载后自动扫描残留，或直接强力摧毁顽固软件的目录与注册表。"))

        search_layout = QHBoxLayout()
        self.search_input = SearchLineEdit()
        self.search_input.setPlaceholderText("搜索软件名称或发布者...")
        self.search_input.setFixedWidth(300)
        self.search_input.textChanged.connect(self._filter_table)
        search_layout.addWidget(self.search_input)
        search_layout.addStretch()
        v.addLayout(search_layout)

        self.tbl=TableWidget(); self.tbl.setColumnCount(6)
        self.tbl.setHorizontalHeaderLabels([" ","名称","版本","发布者","安装目录","隐藏卸载命令"])
        self.tbl.verticalHeader().setVisible(False); self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,4))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 250); self.tbl.setColumnWidth(2, 100); self.tbl.setColumnWidth(3, 180); self.tbl.setColumnWidth(4, 350); self.tbl.setColumnHidden(5, True)
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
            name = self.tbl.item(r, 1).text().lower()
            publisher = self.tbl.item(r, 3).text().lower()
            match = search_str in name or search_str in publisher
            self.tbl.setRowHidden(r, not match)

    def do_scan(self):
        self.stop.clear(); self.sig.uninst_clr.emit(); self.sig.log.emit("开始扫描系统软件列表...")
        threading.Thread(target=self._scan_w, daemon=True).start()

    def _scan_w(self):
        t0 = time.time()
        software = []
        keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
        
        for hkey, subkey_str in keys:
            if self.stop.is_set():
                self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒。")
                return
            try:
                key = winreg.OpenKey(hkey, subkey_str)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        sub_key = winreg.OpenKey(key, sub_name)
                        try:
                            disp, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                            if disp:
                                def get_val(name):
                                    try: return winreg.QueryValueEx(sub_key, name)[0]
                                    except: return ""
                                    
                                ver = get_val("DisplayVersion")
                                pub = get_val("Publisher")
                                cmd = get_val("UninstallString")
                                loc = get_val("InstallLocation")
                                
                                d_icon = get_val("DisplayIcon")
                                icon_path = ""
                                if d_icon:
                                    icon_path = d_icon.split(',')[0].strip(' "')
                                
                                reg = f"{'HKLM' if hkey==winreg.HKEY_LOCAL_MACHINE else 'HKCU'}\\{subkey_str}\\{sub_name}"
                                software.append((disp, ver, pub, cmd, loc, reg, icon_path))
                        except: 
                            pass
                        finally: 
                            winreg.CloseKey(sub_key)
                    except: 
                        pass
                winreg.CloseKey(key)
            except: 
                pass
        
        seen = set()
        unique = []
        for s in software:
            if s[0] not in seen: 
                seen.add(s[0])
                unique.append(s)
                
        unique.sort(key=lambda x: x[0].lower())
        
        for n, v, p, c, l, r, ic in unique: 
            self.sig.uninst_add.emit(n, v, p, l, r, c, ic)
            
        self.sig.done.emit(f"成功扫描出 {len(unique)} 个软件，耗时 {time.time()-t0:.1f} 秒。")

    def _get_checked_rows_data(self):
        rows = []
        for r in range(self.tbl.rowCount()):
            if is_row_checked(self.tbl, r) and not self.tbl.isRowHidden(r):
                nm = self.tbl.item(r, 1).text()
                pub = self.tbl.item(r, 3).text()
                loc = self.tbl.item(r, 4).text()
                cmd = self.tbl.item(r, 5).text()
                reg = self.tbl.item(r, 5).data(Qt.ItemDataRole.UserRole)
                rows.append((r, nm, pub, loc, cmd, reg))
        return rows

    def do_std_uninstall(self):
        data = self._get_checked_rows_data()
        if not data:
            self.sig.log.emit("请先勾选至少一个要卸载的软件！"); return
        self.stop.clear()
        threading.Thread(target=self._std_uninstall_w, args=(data,), daemon=True).start()

    def _std_uninstall_w(self, data):
        t0 = time.time()
        ok = fl = sk = 0
        tot = len(data)
        for i, (r, nm, pub, loc, cmd, reg) in enumerate(data, 1):
            if self.stop.is_set():
                self.sig.done.emit(f"标准卸载已取消：成功 {ok}，失败 {fl}，跳过 {sk}，耗时 {time.time()-t0:.1f} 秒")
                return

            if not cmd:
                self.sig.log.emit(f"[标准卸载] 跳过 {nm}：未提供卸载命令，请改用强力卸载。")
                sk += 1
                self.sig.prog.emit(i, tot)
                continue

            self.sig.log.emit(f"[标准卸载] 正在调用官方卸载程序: {nm}")
            try:
                proc = subprocess.Popen(cmd, shell=True)
                proc.wait()
                ok += 1

                # 串行等待用户处理“是否扫描残留”的弹窗，避免多选时上下文错位
                self._current_uninstalling = (r, nm, pub, loc, reg)
                self._leftover_prompt_done = threading.Event()
                self._leftover_prompt_done.clear()
                QMetaObject.invokeMethod(self, "prompt_leftover_scan", Qt.ConnectionType.QueuedConnection)
                self._leftover_prompt_done.wait()
            except Exception as e:
                fl += 1
                self.sig.log.emit(f"[标准卸载] 启动失败: {nm} -> {e}")

            self.sig.prog.emit(i, tot)

        self.sig.done.emit(f"标准卸载流程结束：成功 {ok}，失败 {fl}，跳过 {sk}，耗时 {time.time()-t0:.1f} 秒")

    @Slot()
    def prompt_leftover_scan(self):
        if not hasattr(self, "_current_uninstalling") or not self._current_uninstalling:
            if hasattr(self, "_leftover_prompt_done"):
                self._leftover_prompt_done.set()
            return
        r, nm, pub, loc, reg = self._current_uninstalling
        if MessageBox("卸载程序已退出", f"标准卸载流程已结束。是否立刻进行深度扫描，清理 '{nm}' 可能遗留的注册表和文件残留？", self.window()).exec():
            self._trigger_leftover_scan(r, nm, pub, loc, reg)
        self._current_uninstalling = None
        if hasattr(self, "_leftover_prompt_done"):
            self._leftover_prompt_done.set()

    def do_force_uninstall(self):
        data = self._get_checked_rows_data()
        if not data:
            self.sig.log.emit("请先勾选目标软件！"); return

        all_files, all_regs = [], []
        chosen_apps = 0
        for r, nm, pub, loc, cmd, reg in data:
            picked = self._pick_leftovers(nm, pub, loc, reg)
            if picked is None:
                continue
            del_files, del_regs = picked
            if not del_files and not del_regs:
                continue
            chosen_apps += 1
            all_files.extend(del_files)
            all_regs.extend(del_regs)

        if chosen_apps == 0:
            self.sig.log.emit("未选择任何残留项，操作已取消。")
            return

        # 去重并保持顺序，避免重复删除同一路径/注册表键
        all_files = list(dict.fromkeys(all_files))
        all_regs = list(dict.fromkeys(all_regs))
        self.sig.log.emit(f"[强力清除] 批量任务已确认：软件 {chosen_apps} 个，文件/目录 {len(all_files)} 项，注册表 {len(all_regs)} 项。")
        self.stop.clear()
        threading.Thread(target=self._force_uninst_w, args=(all_files, all_regs), daemon=True).start()

    def _pick_leftovers(self, nm, pub, loc, reg):
        dialog = LeftoversDialog(self.window(), nm, pub, loc, reg)
        if dialog.tree.topLevelItemCount() == 0:
            InfoBar.success("扫描完毕", f"未发现 '{nm}' 的明显残留。", parent=self.window())
            return [], []
        if not dialog.exec():
            return None
        return dialog.get_selected_items()

    def _trigger_leftover_scan(self, r, nm, pub, loc, reg):
        picked = self._pick_leftovers(nm, pub, loc, reg)
        if picked is None:
            return
        del_files, del_regs = picked
        if not del_files and not del_regs:
            return
        self.sig.log.emit(f"[强力清除] 开始清理 {nm} 的残留...")
        self.stop.clear()
        threading.Thread(target=self._force_uninst_w, args=(del_files, del_regs), daemon=True).start()

    def _force_uninst_w(self, files, regs):
        t0 = time.time()
        lf = lambda s: self.sig.log.emit(s)
        
        # 1. 第一步：猎杀后台进程，解除文件死锁
        for f in files:
            # 只有是文件夹时才尝试扫进程（通常 files 里包含了主安装目录）
            if os.path.isdir(f):
                kill_app_processes(f, lf)
                time.sleep(0.5) # 给系统一点时间释放文件句柄

        # 2. 第二步：强力粉碎注册表 (调用原生 reg delete)
        for r in regs:
            # 这里的 r 格式是 "HKLM\Software\xxx"
            force_delete_registry(r, lf)
            
        # 3. 第三步：强制摧毁残留文件与目录
        for f in files:
            if delete_path(f, True, lf): 
                self.sig.log.emit(f"[强删文件] 成功移除: {f}")
            else:
                self.sig.log.emit(f"[强删文件] 失败(可能仍有驱动级锁定): {f}")
            
        self.sig.done.emit(f"强力清理完成，耗时 {time.time()-t0:.1f} 秒")

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
        b4=PushButton(FIF.CANCEL,"停止"); b4.setFixedHeight(30); b4.clicked.connect(lambda:self.stop.set()); br.addWidget(b4)
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

    def do_scan(self):
        self.stop.clear(); self.btn_sel_all.setText("全选"); self.btn_sel_all.setIcon(FIF.ACCEPT)
        threading.Thread(target=self._scan_w,daemon=True).start()

    def _scan_w(self):
        t0 = time.time()
        mb=self.sp_mb.value(); mx=self.sp_mx.value(); w = self._disk_threads
        roots = [d for d, state in self.drive_states.items() if state]
        if not roots: return
        self.sig.log.emit(f"扫描 (≥{mb}MB) | 线程: {w}"); self.sig.big_clr.emit()
        res = scan_big_files(roots, mb*1024*1024, DEFAULT_EXCLUDES, self.stop, lambda n: self.sig.prog.emit(n % 100, 100), workers=w)
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        for sz,pa in res[:mx]: self.sig.big_add.emit(str(sz), pa)
        self.sig.done.emit(f"扫描完成，找到 {len(res[:mx])} 条，耗时 {time.time()-t0:.1f} 秒")

    def do_del(self):
        paths=[self.tbl.item(r,3).text() for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r) and self.tbl.item(r,3)]
        if not paths: return
        pm=self.chk_perm.isChecked()
        if pm and not MessageBox("确认",f"将永久删除 {len(paths)} 个文件。继续？",self.window()).exec(): return
        self.stop.clear(); threading.Thread(target=self._del_w,args=(paths,pm),daemon=True).start()

    def _del_w(self, paths, pm):
        t0 = time.time()
        ok=fl=0; tot=len(paths); lf=lambda s:self.sig.log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set():
                self.sig.done.emit(f"删除已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.prog.emit(i,tot)
        self.sig.done.emit(f"删除完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")

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
        self._on_mode_change()

        pr = QHBoxLayout(); pr.setSpacing(10)
        self.chk_perm=CheckBox("永久删除(文件不进回收站)"); self.chk_perm.setChecked(True); pr.addWidget(self.chk_perm); pr.addStretch(); v.addLayout(pr)

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
        b3=PushButton(FIF.CANCEL,"停止"); b3.setFixedHeight(30); b3.clicked.connect(lambda:self.stop.set()); br.addWidget(b3); br.addStretch(); v.addLayout(br)

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

    def do_scan(self):
        idx = self.cb_mode.currentIndex(); roots = [d for d, state in self.drive_states.items() if state]
        if idx not in (3, 4) and not roots: self.sig.done.emit("错误：未选择磁盘"); return
        self.stop.clear(); self.sig.more_clr.emit(); self.sig.log.emit(f"开始 {self.cb_mode.currentText()}...")
        
        self.btn_sel_all.setText("全选")
        self.btn_sel_all.setIcon(FIF.ACCEPT)

        workers = self.window().pg_big._disk_threads if hasattr(self.window(), 'pg_big') else 4

        if idx == 0: threading.Thread(target=self._scan_duplicates, args=(roots, workers), daemon=True).start()
        elif idx == 1: threading.Thread(target=self._scan_empty_dirs, args=(roots, workers), daemon=True).start()
        elif idx == 2: threading.Thread(target=self._scan_shortcuts, args=(roots, workers), daemon=True).start()
        elif idx == 3: threading.Thread(target=self._scan_registry, daemon=True).start()
        elif idx == 4: threading.Thread(target=self._scan_context_menu, daemon=True).start()

    def _collect_files_threaded(self, roots, excl, workers, ext_filter=None):
        dir_queue = queue.Queue(); res_files = []; res_dirs = []; lock = threading.Lock()
        for r in roots: dir_queue.put(r)
        def _worker():
            while not self.stop.is_set():
                try: d = dir_queue.get(timeout=0.05)
                except queue.Empty: continue
                if d is _SENTINEL: dir_queue.put(_SENTINEL); break
                try: entries = os.scandir(d)
                except: dir_queue.task_done(); continue
                lf = []; ld = []
                try:
                    for e in entries:
                        if self.stop.is_set(): break
                        try:
                            if e.is_symlink(): continue
                            if e.is_dir(follow_symlinks=False):
                                if not should_exclude(e.path, excl): dir_queue.put(e.path); ld.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                if ext_filter and not e.name.lower().endswith(ext_filter): continue
                                lf.append((e.stat(follow_symlinks=False).st_size, e.path))
                        except: pass
                finally:
                    try: entries.close()
                    except: pass
                with lock:
                    if lf: res_files.extend(lf)
                    if ld: res_dirs.extend(ld)
                dir_queue.task_done()
        threads = []
        for _ in range(workers): t = threading.Thread(target=_worker, daemon=True); t.start(); threads.append(t)
        while not self.stop.is_set():
            try:
                dir_queue.all_tasks_done.acquire()
                if dir_queue.unfinished_tasks == 0: dir_queue.all_tasks_done.release(); break
                dir_queue.all_tasks_done.release()
            except: pass
            time.sleep(0.1)
        dir_queue.put(_SENTINEL); [t.join(timeout=1) for t in threads]
        return res_files, res_dirs

    def _scan_duplicates(self, roots, workers):
        t0 = time.time()
        files, _ = self._collect_files_threaded(roots, DEFAULT_EXCLUDES, workers)
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        size_dict = defaultdict(list)
        for sz, p in files:
            if sz > 0: size_dict[sz].append(p)
        suspects = [paths for paths in size_dict.values() if len(paths) > 1]
        def _get_hash(path, limit=None):
            m = hashlib.md5()
            try:
                with open(path, 'rb') as f:
                    if limit: m.update(f.read(limit))
                    else:
                        for chunk in iter(lambda: f.read(8192), b''): m.update(chunk)
                return m.hexdigest()
            except: return None
        results = []; tot = len(suspects)
        for i, paths in enumerate(suspects):
            if self.stop.is_set(): break
            self.sig.prog.emit(i, tot)
            h4_dict = defaultdict(list)
            for p in paths:
                h = _get_hash(p, 4096)
                if h: h4_dict[h].append(p)
            for h4_paths in h4_dict.values():
                if len(h4_paths) > 1:
                    full_dict = defaultdict(list)
                    for p in h4_paths:
                        fh = _get_hash(p)
                        if fh: full_dict[fh].append(p)
                    for duplicates in full_dict.values():
                        if len(duplicates) > 1: results.append(duplicates)
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        cnt = 0
        for grp_id, dup_list in enumerate(results, 1):
            for idx, p in enumerate(dup_list):
                self.sig.more_add.emit((idx > 0), "重复文件", f"组 {grp_id}", human_size(os.path.getsize(p)), p); cnt += 1
        self.sig.done.emit(f"扫描完成，找到 {cnt} 个重复文件，耗时 {time.time()-t0:.1f} 秒")

    def _scan_empty_dirs(self, roots, workers):
        t0 = time.time()
        _, dirs = self._collect_files_threaded(roots, DEFAULT_EXCLUDES, workers)
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        dirs.sort(key=len, reverse=True); empty_set = set(); tot = len(dirs)
        for i, d in enumerate(dirs):
            if self.stop.is_set(): break
            if i % 500 == 0: self.sig.prog.emit(i, tot)
            try:
                is_empty = True
                for item in os.scandir(d):
                    if item.is_file(follow_symlinks=False): is_empty = False; break
                    elif item.is_dir(follow_symlinks=False) and item.path not in empty_set: is_empty = False; break
                if is_empty: empty_set.add(d); self.sig.more_add.emit(False, "空文件夹", os.path.basename(d), "无内容", d)
            except: pass
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        self.sig.done.emit(f"扫描完成，找到 {len(empty_set)} 个空文件夹，耗时 {time.time()-t0:.1f} 秒")

    def _scan_shortcuts(self, roots, workers):
        t0 = time.time()
        files, _ = self._collect_files_threaded(roots, DEFAULT_EXCLUDES, workers, ext_filter=".lnk")
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
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
                except: pass
            except: pass
            return ""
        tot = len(files); invalid_cnt = 0
        for i, (_, p) in enumerate(files):
            if self.stop.is_set(): break
            if i % 100 == 0: self.sig.prog.emit(i, tot)
            target = resolve_lnk_target(p)
            if target and not os.path.exists(target):
                self.sig.more_add.emit(False, "无效快捷方式", os.path.basename(p), "指向缺失的文件", p); invalid_cnt += 1
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        self.sig.done.emit(f"扫描完成，找到 {invalid_cnt} 个无效快捷方式，耗时 {time.time()-t0:.1f} 秒")

    def _scan_registry(self):
        t0 = time.time()
        res = []; keys_to_check = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
        for hkey, subkey_str in keys_to_check:
            try:
                key = winreg.OpenKey(hkey, subkey_str)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    if self.stop.is_set(): break
                    try:
                        sub_name = winreg.EnumKey(key, i); sub_key = winreg.OpenKey(key, sub_name)
                        try:
                            install_loc, _ = winreg.QueryValueEx(sub_key, "InstallLocation")
                            if install_loc and not os.path.exists(install_loc):
                                disp_name = winreg.QueryValueEx(sub_key, "DisplayName")[0] if "DisplayName" in [winreg.EnumValue(sub_key, j)[0] for j in range(winreg.QueryInfoKey(sub_key)[1])] else sub_name
                                res.append(("无效卸载项", disp_name, "原目录已丢失", f"{'HKLM' if hkey==winreg.HKEY_LOCAL_MACHINE else 'HKCU'}\\{subkey_str}\\{sub_name}"))
                        except OSError: pass
                        winreg.CloseKey(sub_key)
                    except OSError: pass
                winreg.CloseKey(key)
            except OSError: pass
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        for tp, nm, det, path in res: self.sig.more_add.emit(False, tp, nm, det, path)
        self.sig.done.emit(f"扫描完成，找到 {len(res)} 个无效注册表卸载项，耗时 {time.time()-t0:.1f} 秒")

    def _scan_context_menu(self):
        t0 = time.time()
        res = []; targets = [r"*\shell", r"*\shellex\ContextMenuHandlers", r"Directory\shell", r"Directory\Background\shell", r"Folder\shell", r"Folder\shellex\ContextMenuHandlers"]
        for t in targets:
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, t)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    if self.stop.is_set(): break
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        res.append(("右键扩展", sub_name, t, f"HKCR\\{t}\\{sub_name}"))
                    except: pass
                winreg.CloseKey(key)
            except: pass
        if self.stop.is_set():
            self.sig.done.emit(f"扫描已取消，耗时 {time.time()-t0:.1f} 秒")
            return
        for tp, nm, det, path in res: self.sig.more_add.emit(False, tp, nm, det, path)
        self.sig.done.emit(f"扫描完成，列出 {len(res)} 个右键菜单扩展，耗时 {time.time()-t0:.1f} 秒")

    def do_del(self):
        paths=[self.tbl.item(r,4).text() for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r)]
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
                self.sig.log.emit(f"[保护] 已阻止清理 {len(blocked)} 个位于 C 盘的重复文件。")
                InfoBar.warning(
                    "已阻止",
                    f"重复文件模式禁止清理 C 盘文件，已跳过 {len(blocked)} 项。",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self.window()
                )
                paths = allowed

            if not paths:
                return

        if not MessageBox("确认",f"确定清理这 {len(paths)} 个项目？不可恢复。",self.window()).exec(): return
        self.stop.clear()
        if is_reg: threading.Thread(target=self._del_reg_w, args=(paths,), daemon=True).start()
        else: threading.Thread(target=self._del_files_w, args=(paths,self.chk_perm.isChecked()), daemon=True).start()

    def _del_files_w(self, paths, pm):
        t0 = time.time()
        ok=fl=0; tot=len(paths); lf=lambda s:self.sig.log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set():
                self.sig.done.emit(f"清理已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.prog.emit(i,tot)
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")

    def _del_reg_w(self, paths):
        t0 = time.time()
        ok=fl=0; tot=len(paths)
        for i, p in enumerate(paths, 1):
            if self.stop.is_set():
                self.sig.done.emit(f"清理已取消：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")
                return
            
            # 使用新的强制删除函数
            if force_delete_registry(p, self.sig.log.emit):
                ok += 1
            else:
                fl += 1
                
            self.sig.prog.emit(i, tot)
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}，耗时 {time.time()-t0:.1f} 秒")


# ══════════════════════════════════════════════════════════
#  主窗口
# ══════════════════════════════════════════════════════════
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 1. 加载全局设置
        self.global_settings_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cdisk_cleaner_global_settings.json")
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
            except: pass

        self.targets = default_clean_targets()
        # 记录内置默认规则身份，后续删除保护只针对这批规则
        self.builtin_rule_keys = {make_rule_key(t[0], t[1], t[2]) for t in self.targets}
        self.deleted_builtin_rule_keys = load_rule_keys(self.global_settings.get("deleted_builtin_rules", []))
        if self.deleted_builtin_rule_keys:
            self.targets = [t for t in self.targets if make_rule_key(t[0], t[1], t[2]) not in self.deleted_builtin_rule_keys]
        self.custom_rules_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cdisk_cleaner_custom_rules.json")
        
        # 2. 附加自定义规则
        if os.path.exists(self.custom_rules_path):
            try:
                with open(self.custom_rules_path, "r", encoding="utf-8") as f: customs = json.load(f)
                # 兼容历史/外部规则文件：
                # 只要是从 custom_rules_path 读入，都视为“自定义规则”，强制 is_custom=True，
                # 这样仅内置 default_clean_targets() 会保持受保护状态。
                for c in customs:
                    if not isinstance(c, (list, tuple)) or len(c) < 5:
                        continue
                    nm, pa, tp, en, nt = c[0], c[1], c[2], c[3], c[4]
                    self.targets.append((nm, pa, tp, en, nt, True))
            except: pass

        # 3. 恢复排序与勾选状态
        self.config_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cdisk_cleaner_config.json")
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
                    nm, pa, tp, en, nt, is_c = self.targets[i]
                    if nm in states:
                        self.targets[i] = (nm, pa, tp, states[nm], nt, is_c)
            except: pass
                
        self.stop = threading.Event(); self.sig = Sig()
        self.pg_clean = CleanPage(self.sig, self.targets, self.stop, self)
        self.pg_big = BigFilePage(self.sig, self.stop, self)
        self.pg_uninstall = UninstallPage(self.sig, self.stop, self)
        self.pg_more = MoreCleanPage(self.sig, self.stop, self)
        self.pg_setting = SettingPage(self, self)
        
        self._init_nav(); self._init_win(); self._conn()
        threading.Thread(target=self._async_detect, daemon=True).start()
        threading.Timer(2.0, self._check_update_worker).start()

    def save_global_settings(self):
        try:
            with open(self.global_settings_path, "w", encoding="utf-8") as f:
                json.dump(self.global_settings, f, ensure_ascii=False, indent=2)
        except: pass

    def closeEvent(self, event):
        if self.global_settings.get("auto_save", True):
            try:
                self.pg_clean._sync()
                self.pg_clean.save_custom_rules()
                order = [t[0] for t in self.targets]
                states = {t[0]: t[3] for t in self.targets}
                with open(self.config_path, "w", encoding="utf-8") as f: 
                    json.dump({"order": order, "states": states}, f, ensure_ascii=False, indent=2)
            except: pass
        super().closeEvent(event)

    def _init_nav(self):
        self.navigationInterface.setExpandWidth(200); self.navigationInterface.setCollapsible(True)
        self.addSubInterface(self.pg_clean, FIF.BROOM, "常规清理")
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
        self.sig.log.connect(self._log); self.sig.prog.connect(self._prog); self.sig.est.connect(self._est); self.sig.done.connect(self._done)
        self.sig.big_clr.connect(lambda: self.pg_big.tbl.setRowCount(0)); self.sig.big_add.connect(self._badd)
        self.sig.more_clr.connect(lambda: self.pg_more.tbl.setRowCount(0)); self.sig.more_add.connect(self._madd)
        self.sig.uninst_clr.connect(lambda: self.pg_uninstall.tbl.setRowCount(0)); self.sig.uninst_add.connect(self._uadd)
        self.sig.update_found.connect(self._show_update_dialog)

    def _async_detect(self):
        threads, dtype = get_scan_threads_cached("C"); self.sig.disk_ready.emit(dtype, threads)

    def _check_update_worker(self):
        try:
            with urllib.request.urlopen(UPDATE_JSON_URL, timeout=8) as r:
                raw_text = r.read().decode("utf-8")
        except:
            return

        payload = _load_update_payload(raw_text)
        if not payload:
            return

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
        current_key = _version_key(CURRENT_VERSION)
        candidates = []

        for item in _extract_entries(payload):
            ver = item.get("version") or item.get("tag") or item.get("name") or ""
            url = item.get("url") or item.get("download_url") or item.get("download") or ""
            changelog = item.get("changelog") or item.get("notes") or item.get("desc") or ""
            if not ver:
                continue
            if channel == "stable" and (_is_prerelease(ver) or bool(item.get("prerelease", False))):
                continue
            if _version_key(ver) > current_key:
                candidates.append((ver, url, changelog))

        if not candidates:
            return

        latest = max(candidates, key=lambda x: _version_key(x[0]))
        self.sig.update_found.emit(latest[0], latest[1], latest[2])

    def _show_update_dialog(self, version, url, changelog):
        if MessageBox(f"发现新版本 v{version}", f"更新内容：\n{changelog}\n\n是否立即前往下载？", self.window()).exec() and url: webbrowser.open(url)

    def _ts(self): return time.strftime("%H:%M:%S")

    def _log(self, t):
        line=f"[{self._ts()}] {t}"
        for p in (self.pg_clean, self.pg_big, self.pg_uninstall, self.pg_more):
            p.log.append(line); p.sl.setText(t[:80])

    def _prog(self, v, m):
        for p in (self.pg_clean, self.pg_big, self.pg_uninstall, self.pg_more): p.pb.setRange(0,max(1,m)); p.pb.setValue(v)

    def _est(self, idx, val):
        if 0<=idx<self.pg_clean.tbl.rowCount():
            it=self.pg_clean.tbl.item(idx,4); it.setText(human_size(val)) if it else None

    def _done(self, msg):
        for p in (self.pg_clean, self.pg_big, self.pg_uninstall, self.pg_more): p.pb.setValue(0); p.sl.setText("完成")
        self.pg_clean.tbl.setDragEnabled(True) 
        self._log(f"[完成] {msg}"); InfoBar.success("完成",msg,orient=Qt.Orientation.Horizontal, isClosable=True,position=InfoBarPosition.TOP,duration=4000,parent=self)

    def _badd(self, sz_str, pa):
        t=self.pg_big.tbl; r=t.rowCount(); t.setRowCount(r+1); t.setItem(r, 0, make_check_item(False)); t.setItem(r, 1, QTableWidgetItem(os.path.basename(pa) if pa else ""))
        s=QTableWidgetItem(human_size(int(sz_str))); s.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        t.setItem(r, 2, s); t.setItem(r, 3, QTableWidgetItem(pa))

    def _madd(self, chk, tp, nm, det, pa):
        t=self.pg_more.tbl; r=t.rowCount(); t.setRowCount(r+1)
        t.setItem(r, 0, make_check_item(chk)); t.setItem(r, 1, QTableWidgetItem(tp)); t.setItem(r, 2, QTableWidgetItem(nm))
        t.setItem(r, 3, QTableWidgetItem(det)); t.setItem(r, 4, QTableWidgetItem(pa))

    def _uadd(self, nm, ver, pub, loc, reg, cmd, icon_path): 
        t=self.pg_uninstall.tbl; r=t.rowCount(); t.setRowCount(r+1)
                  
        name_item = QTableWidgetItem(nm)
        if icon_path and os.path.exists(icon_path):
            provider = QFileIconProvider()
            icon = provider.icon(QFileInfo(icon_path))
            if not icon.isNull():
                name_item.setIcon(icon)
        else:
            name_item.setIcon(FIF.APPLICATION.icon())

        t.setItem(r, 0, make_check_item(False))
        t.setItem(r, 1, name_item) 
        t.setItem(r, 2, QTableWidgetItem(ver))
        t.setItem(r, 3, QTableWidgetItem(pub))
        t.setItem(r, 4, QTableWidgetItem(loc))
        hidden_item = QTableWidgetItem(cmd); hidden_item.setData(Qt.ItemDataRole.UserRole, reg); t.setItem(r, 5, hidden_item)

    def _about(self):
        MessageBox("关于", f"C盘强力清理工具 v{CURRENT_VERSION}\nQQ交流群：670804369\nUI：Fluent Widgets\nby Kio",self).exec()

def relaunch_as_admin():
    try: ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(f'"{a}"' for a in sys.argv), None, 1)
    except: pass
    sys.exit(0)

def main():
    if sys.platform != "win32": sys.exit(1)
    if not is_admin(): relaunch_as_admin()
    app = QApplication(sys.argv); setFontFamilies(["微软雅黑"]); setTheme(Theme.AUTO); setThemeColor("#0078d4")
    w = MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": main()
