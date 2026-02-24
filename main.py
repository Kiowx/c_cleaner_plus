# -*- coding: utf-8 -*-
"""
C盘强力清理工具 v0.1.1-alpha03
PySide6 + PySide6-Fluent-Widgets (Fluent2 UI)
包含：常规清理、开发缓存清理、大文件多盘扫描、偏好状态记忆、自定义下拉选框体验优化、自动检查更新
"""

import os, sys, time, ctypes, threading, subprocess, queue, json
import urllib.request
import webbrowser

from PySide6.QtCore import Qt, Signal, QObject, QModelIndex, QPoint
from PySide6.QtGui import QFont, QIcon, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QAbstractItemView, QTableWidgetItem, QStyleOptionViewItem,
    QStyledItemDelegate, QSplashScreen,
)

from qfluentwidgets import (
    FluentIcon as FIF,
    setTheme, Theme, setThemeColor,
    setFontFamilies, setFont, getFont,
    NavigationItemPosition, FluentWindow,
    PushButton, PrimaryPushButton, DropDownPushButton,
    CheckBox, SpinBox, ProgressBar,
    BodyLabel, SubtitleLabel, TitleLabel, CaptionLabel, StrongBodyLabel,
    SimpleCardWidget, HeaderCardWidget, CardWidget, IconWidget,
    TableWidget, TextEdit,
    RoundMenu, Action,
    MessageBox, InfoBar, InfoBarPosition,
    ScrollArea,
)

# ══════════════════════════════════════════════════════════
#  版本与更新配置
# ══════════════════════════════════════════════════════════
CURRENT_VERSION = "0.1.1"
# 必须使用 raw 链接获取纯 JSON 数据
UPDATE_JSON_URL = "https://gitee.com/kio0/c_cleaner_plus/raw/master/update.json"

# ══════════════════════════════════════════════════════════
#  自定义 Delegate：只保留 Fluent 风格勾选框
# ══════════════════════════════════════════════════════════
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

def resource_path(relative_path):
    """兼容开发环境和 PyInstaller 打包后的路径"""
    if getattr(sys, '_MEIPASS', None):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

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

        if index.data(Qt.ItemDataRole.BackgroundRole):
            painter.setBrush(index.data(Qt.ItemDataRole.BackgroundRole))
        else:
            painter.setBrush(QColor(c, c, c, alpha))
        self._drawBackground(painter, option, index)

        if (index.row() in self.selectedRows and index.column() == 0
                and self.parent().horizontalScrollBar().value() == 0):
            self._drawIndicator(painter, option, index)

        if index.data(Qt.ItemDataRole.CheckStateRole) is not None:
            self._drawCheckBox(painter, option, index)

        painter.restore()

        model = index.model()
        orig_check = model.data(index, Qt.ItemDataRole.CheckStateRole)
        if orig_check is not None:
            model.setData(index, None, Qt.ItemDataRole.CheckStateRole)
        QStyledItemDelegate.paint(self, painter, option, index)
        if orig_check is not None:
            model.setData(index, orig_check, Qt.ItemDataRole.CheckStateRole)


# ══════════════════════════════════════════════════════════
#  Windows API / 工具
# ══════════════════════════════════════════════════════════
FOF_ALLOWUNDO = 0x0040; FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004; FOF_NOERRORUI = 0x0400

class SHFILEOPSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hwnd",ctypes.c_void_p),("wFunc",ctypes.c_uint),
        ("pFrom",ctypes.c_wchar_p),("pTo",ctypes.c_wchar_p),
        ("fFlags",ctypes.c_ushort),("fAnyOperationsAborted",ctypes.c_int),
        ("hNameMappings",ctypes.c_void_p),("lpszProgressTitle",ctypes.c_wchar_p),
    ]

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
        if os.path.isfile(path) or os.path.islink(path): os.remove(path)
        else: shutil.rmtree(path, ignore_errors=False)
        log_fn(f"[永久删除] {path}"); return True
    except Exception as e: log_fn(f"[失败] {path} -> {e}"); return False

def expand_env(p): return os.path.expandvars(p)

def get_available_drives():
    """获取系统当前所有可用的盘符列表"""
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            drives.append(chr(65 + i) + ":\\")
    return drives

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
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW
        )
        media = r.stdout.strip()
        if "SSD" in media or "Solid" in media: return "SSD"
        elif "HDD" in media or "Unspecified" in media: return "HDD"
        else: return "Unknown"
    except Exception:
        return "Unknown"

# 线程
def get_scan_threads(drive_letter="C"):
    dtype = detect_disk_type(drive_letter)
    thread_map = {"SSD": 12, "HDD": 2, "Unknown": 4}
    return thread_map.get(dtype, 4), dtype

def get_scan_threads_cached(drive_letter="C"):
    """优先读缓存（24小时有效），否则实际检测并写入缓存"""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if time.time() - cache.get("ts", 0) < 86400:
                return cache["threads"], cache["dtype"]
    except:
        pass

    threads, dtype = get_scan_threads(drive_letter)

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"threads": threads, "dtype": dtype, "ts": time.time()}, f)
    except:
        pass

    return threads, dtype

# ══════════════════════════════════════════════════════════
#  默认清理目标
# ══════════════════════════════════════════════════════════
def default_clean_targets():
    sr = os.environ.get("SystemRoot", r"C:\Windows")
    la = os.environ.get("LOCALAPPDATA", "")
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    up = os.environ.get("USERPROFILE", "")
    J = os.path.join
    
    return [
        # --- 基础系统临时文件 ---
        ("用户临时文件", expand_env(r"%TEMP%"), "dir", True, "常见垃圾，安全"),
        ("系统临时文件", J(sr, "Temp"), "dir", True, "可能需管理员"),
        ("Prefetch", J(sr, "Prefetch"), "dir", False, "影响首次启动"),
        ("CBS 日志", J(sr, "Logs", "CBS"), "dir", True, "较安全"),
        ("DISM 日志", J(sr, "Logs", "DISM"), "dir", True, "较安全"),
        ("LiveKernelReports", J(sr, "LiveKernelReports"), "dir", True, "内核转储"),
        ("WER(用户)", J(la, "Microsoft", "Windows", "WER"), "dir", True, "崩溃报告"),
        ("WER(系统)", J(sr, "System32", "config", "systemprofile", "AppData", "Local", "Microsoft", "Windows", "WER"), "dir", False, "需管理员"),
        ("Minidump", J(sr, "Minidump"), "dir", True, "崩溃转储"),
        ("MEMORY.DMP", J(sr, "MEMORY.DMP"), "file", False, "确认不调试时勾选"),
        ("缩略图缓存", J(la, "Microsoft", "Windows", "Explorer"), "glob", True, "thumbcache*.db"),
        
        # --- 显卡与游戏缓存 ---
        ("D3DSCache", J(la, "D3DSCache"), "dir", False, "d3d着色器缓存"),
        ("NVIDIA DX", J(la, "NVIDIA", "DXCache"), "dir", False, "NV着色器缓存"),
        ("NVIDIA GL", J(la, "NVIDIA", "GLCache"), "dir", False, "NV OpenGL缓存"),
        ("NVIDIA Compute", J(la, "NVIDIA", "ComputeCache"), "dir", False, "CUDA"),
        ("NV_Cache", J(pd, "NVIDIA Corporation", "NV_Cache"), "dir", False, "NV CUDA/计算缓存"),
        ("AMD DX", J(la, "AMD", "DxCache"), "dir", False, "AMD着色器缓存"),
        ("AMD GL", J(la, "AMD", "GLCache"), "dir", False, "AMD OpenGL缓存"),
        ("Steam Shader", J(la, "Steam", "steamapps", "shadercache"), "dir", False, "Steam"),
        ("Steam 下载临时", J(la, "Steam", "steamapps", "downloading"), "dir", False, "下载残留"),
        
        # --- 浏览器缓存 ---
        ("Edge Cache", J(la, "Microsoft", "Edge", "User Data", "Default", "Cache"), "dir", False, "浏览器"),
        ("Edge Code", J(la, "Microsoft", "Edge", "User Data", "Default", "Code Cache"), "dir", False, "JS"),
        ("Chrome Cache", J(la, "Google", "Chrome", "User Data", "Default", "Cache"), "dir", False, "浏览器"),
        ("Chrome Code", J(la, "Google", "Chrome", "User Data", "Default", "Code Cache"), "dir", False, "JS"),
        
        # --- 开发环境缓存 (默认不勾选，避免频繁重新下载耗时) ---
        ("pip Cache", J(la, "pip", "Cache"), "dir", False, "Python 包缓存"),
        ("NuGet Cache", J(la, "NuGet", "v3-cache"), "dir", False, ".NET 包缓存"),
        ("npm Cache", J(la, "npm-cache"), "dir", False, "Node.js 包缓存"),
        ("Yarn Cache", J(la, "Yarn", "Cache"), "dir", False, "Yarn 全局缓存"),
        ("pnpm Store", J(la, "pnpm", "store"), "dir", False, "pnpm 内容寻址存储库"),
        ("Go Build Cache", J(la, "go-build"), "dir", False, "Go 编译缓存"),
        ("Cargo Cache", J(up, ".cargo", "registry", "cache"), "dir", False, "Rust 包下载缓存"),
        ("Gradle Cache", J(up, ".gradle", "caches"), "dir", False, "Java/Android 构建缓存"),
        ("Maven Repository", J(up, ".m2", "repository"), "dir", False, "Java 本地依赖库"),
        ("Composer Cache", J(la, "Composer"), "dir", False, "PHP 包缓存"),
        
        # --- 系统更新 ---
        ("WU Download", J(sr, "SoftwareDistribution", "Download"), "dir", False, "更新缓存"),
        ("Delivery Opt", J(sr, "SoftwareDistribution", "DeliveryOptimization"), "dir", False, "需管理员"),
    ]

DEFAULT_EXCLUDES=[r"C:\Windows\WinSxS",r"C:\Windows\Installer",r"C:\Program Files",r"C:\Program Files (x86)",r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive"]
BIGFILE_SKIP_EXT={".sys"}

def should_exclude(p, prefixes):
    n=os.path.normcase(os.path.abspath(p))
    return any(n.startswith(os.path.normcase(os.path.abspath(e))) for e in prefixes)

# ══════════════════════════════════════════════════════════
#  大文件扫描（生产者-消费者并发模型）
# ══════════════════════════════════════════════════════════
_SENTINEL = None

def _dir_worker(dir_queue, min_b, excl, stop_flag, results, counter, lock):
    while not stop_flag.is_set():
        try:
            dirpath = dir_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        if dirpath is _SENTINEL:
            dir_queue.put(_SENTINEL)
            break

        try:
            entries = os.scandir(dirpath)
        except (PermissionError, OSError):
            dir_queue.task_done()
            continue

        local_res = []
        local_count = 0
        try:
            for entry in entries:
                if stop_flag.is_set():
                    break
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        full = entry.path
                        if not should_exclude(full, excl):
                            dir_queue.put(full)
                    elif entry.is_file(follow_symlinks=False):
                        if os.path.splitext(entry.name)[1].lower() in BIGFILE_SKIP_EXT:
                            continue
                        st = entry.stat(follow_symlinks=False)
                        local_count += 1
                        if st.st_size >= min_b:
                            local_res.append((st.st_size, entry.path))
                except (PermissionError, OSError):
                    pass
        finally:
            try:
                entries.close()
            except:
                pass

        if local_res or local_count:
            with lock:
                results.extend(local_res)
                counter[0] += local_count

        dir_queue.task_done()


def scan_big_files(roots, min_b, excl, stop, cb, workers=4):
    dir_queue = queue.Queue()
    results = []
    counter = [0]
    lock = threading.Lock()

    for root in roots:
        dir_queue.put(root)

    threads = []
    for _ in range(workers):
        t = threading.Thread(
            target=_dir_worker,
            args=(dir_queue, min_b, excl, stop, results, counter, lock),
            daemon=True
        )
        t.start()
        threads.append(t)

    tk = time.time()
    while not stop.is_set():
        try:
            dir_queue.all_tasks_done.acquire()
            try:
                if dir_queue.unfinished_tasks == 0:
                    break
            finally:
                dir_queue.all_tasks_done.release()
        except:
            pass

        now = time.time()
        if now - tk >= 0.3:
            cb(counter[0])
            tk = now
        time.sleep(0.05)

    dir_queue.put(_SENTINEL)
    for t in threads:
        t.join(timeout=2)

    cb(counter[0])

    results.sort(reverse=True, key=lambda x: x[0])
    return results


# ══════════════════════════════════════════════════════════
#  信号
# ══════════════════════════════════════════════════════════
class Sig(QObject):
    log=Signal(str); prog=Signal(int,int); est=Signal(int,int)
    big_clr=Signal(); big_add=Signal(str,str); done=Signal(str)
    disk_ready=Signal(str,int)
    update_found=Signal(str, str, str)  # 增加更新信号

# ══════════════════════════════════════════════════════════
#  公共
# ══════════════════════════════════════════════════════════
TBL_FONT_SIZE     = 12
TBL_HEADER_SIZE   = 12
TBL_ROW_HEIGHT    = 30
TITLE_FONT_SIZE   = 22

def style_table(tbl: TableWidget):
    setFont(tbl, TBL_FONT_SIZE, QFont.Weight.Normal)
    setFont(tbl.horizontalHeader(), TBL_HEADER_SIZE, QFont.Weight.DemiBold)
    tbl.verticalHeader().setDefaultSectionSize(TBL_ROW_HEIGHT)
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
        InfoBar.success(
            "复制成功", raw,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=parent.window()
        )

    a1=Action(FIF.COPY,"复制路径");a1.triggered.connect(_copy_path);a1.setEnabled(bool(raw));m.addAction(a1)
    m.addSeparator()
    a2=Action(FIF.DOCUMENT,"打开文件"); a2.triggered.connect(lambda:subprocess.Popen(["explorer",n]) if n else None); a2.setEnabled(ex and os.path.isfile(n)); m.addAction(a2)
    a3=Action(FIF.FOLDER,"打开位置"); a3.triggered.connect(lambda:open_explorer(n)); a3.setEnabled(ex); m.addAction(a3)
    a4=Action(FIF.PIN,"选中文件"); a4.triggered.connect(lambda:subprocess.Popen(["explorer","/select,",n]) if n else None); a4.setEnabled(ex); m.addAction(a4)
    m.exec(table.viewport().mapToGlobal(pos))

def parse_sz(t):
    try:
        v,u=t.strip().split()
        return int(float(v)*{"B":1,"KB":1024,"MB":1024**2,"GB":1024**3,"TB":1024**4}.get(u,1))
    except: return 0

def make_check_item(checked=False):
    item = QTableWidgetItem()
    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item

def is_row_checked(table, row):
    it = table.item(row, 0)
    return it is not None and it.checkState() == Qt.CheckState.Checked

def set_row_checked(table, row, checked):
    it = table.item(row, 0)
    if it: it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

def make_title_row(icon: FIF, text: str):
    row = QHBoxLayout(); row.setSpacing(8)
    iw = IconWidget(icon); iw.setFixedSize(24, 24); row.addWidget(iw)
    lbl = TitleLabel(text); setFont(lbl, TITLE_FONT_SIZE, QFont.Weight.Bold); row.addWidget(lbl)
    row.addStretch()
    return row

# ══════════════════════════════════════════════════════════
#  常规清理页
# ══════════════════════════════════════════════════════════
class CleanPage(ScrollArea):
    def __init__(self, sig, targets, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.targets=targets; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True)
        self.setObjectName("cleanPage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)

        v.addLayout(make_title_row(FIF.BROOM, "常规清理"))
        badge = "管理员" if is_admin() else "非管理员"
        v.addWidget(CaptionLabel(f"当前权限：{badge}  |  部分项目可能需要管理员权限"))

        opt=QHBoxLayout(); opt.setSpacing(16)
        self.chk_perm=CheckBox("强力模式：永久删除"); self.chk_perm.setChecked(True); opt.addWidget(self.chk_perm)
        self.chk_rst=CheckBox("创建还原点"); opt.addWidget(self.chk_rst)
        opt.addStretch(); v.addLayout(opt)

        self.tbl=TableWidget(); self.tbl.setColumnCount(5)
        self.tbl.setHorizontalHeaderLabels([" ","项目","路径","说明","大小"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,2))

        self.tbl.setRowCount(len(self.targets))
        for i,(nm,pa,tp,en,nt) in enumerate(self.targets):
            self.tbl.setItem(i, 0, make_check_item(en))
            self.tbl.setItem(i, 1, QTableWidgetItem(nm))
            self.tbl.setItem(i, 2, QTableWidgetItem(pa if tp!="glob" else f"{pa} | thumbcache*.db"))
            self.tbl.setItem(i, 3, QTableWidgetItem(nt))
            self.tbl.setItem(i, 4, QTableWidgetItem(""))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 150)
        self.tbl.setColumnWidth(2, 400); self.tbl.setColumnWidth(3, 200); self.tbl.setColumnWidth(4, 85)
        style_table(self.tbl)
        v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PushButton(FIF.UNIT,"估算"); b1.setFixedHeight(30); b1.clicked.connect(self.do_est); br.addWidget(b1)
        b2=PushButton(FIF.ACCEPT,"全选安全项"); b2.setFixedHeight(30); b2.clicked.connect(self.sel_safe); br.addWidget(b2)
        b3=PushButton(FIF.CLOSE,"全不选"); b3.setFixedHeight(30); b3.clicked.connect(self.unsel); br.addWidget(b3)
        br.addStretch()
        bc=PrimaryPushButton(FIF.DELETE,"开始清理"); bc.setFixedHeight(30); bc.clicked.connect(self.do_clean); br.addWidget(bc)
        bs=PushButton(FIF.CANCEL,"停止"); bs.setFixedHeight(30); bs.clicked.connect(lambda:self.stop.set()); br.addWidget(bs)
        v.addLayout(br)

        pr=QHBoxLayout()
        self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pr.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pr.addWidget(self.sl)
        v.addLayout(pr)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120)
        self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志...")
        v.addWidget(self.log)

    def _sync_targets_from_table(self):
        for i in range(len(self.targets)):
            n,p,t,_,nt = self.targets[i]
            self.targets[i] = (n,p,t, is_row_checked(self.tbl, i), nt)

    def sel_safe(self):
        for i,(n,p,t,de,nt) in enumerate(self.targets):
            self.targets[i]=(n,p,t,de,nt); set_row_checked(self.tbl, i, de)

    def unsel(self):
        for i,(n,p,t,_,nt) in enumerate(self.targets):
            self.targets[i]=(n,p,t,False,nt); set_row_checked(self.tbl, i, False)

    def do_est(self):
        self._sync_targets_from_table()
        self.stop.clear(); threading.Thread(target=self._est_w,daemon=True).start()

    def _est_w(self):
        import fnmatch
        self.sig.log.emit("开始估算...")
        its=[(i,t) for i,t in enumerate(self.targets) if t[3]]
        if not its: self.sig.done.emit("没有勾选项目"); return
        self.sig.prog.emit(0,len(its))
        for n,(idx,t) in enumerate(its,1):
            if self.stop.is_set(): self.sig.done.emit("已取消"); return
            nm,pa,tp,_,_=t; e=0
            try:
                if tp=="dir":
                    p=expand_env(pa)
                    if os.path.isdir(p): e=dir_size(p)
                elif tp=="glob":
                    fo=expand_env(pa)
                    if os.path.isdir(fo):
                        for f in os.listdir(fo):
                            if fnmatch.fnmatch(f.lower(),"thumbcache*.db"): e+=safe_getsize(os.path.join(fo,f))
                elif tp=="file":
                    p=expand_env(pa)
                    if os.path.isfile(p): e=safe_getsize(p)
            except: e=0
            self.sig.est.emit(idx,e); self.sig.prog.emit(n,len(its))
            self.sig.log.emit(f"[估算] {nm} -> {human_size(e)}")
        self.sig.done.emit("估算完成")

    def do_clean(self):
        self._sync_targets_from_table()
        
        dev_cache_names = {
            "pip Cache", "NuGet Cache", "npm Cache", "Yarn Cache", 
            "pnpm Store", "Go Build Cache", "Cargo Cache", 
            "Gradle Cache", "Maven Repository", "Composer Cache"
        }
        
        selected_dev_caches = [nm for nm, _, _, en, _ in self.targets if en and nm in dev_cache_names]
        
        if selected_dev_caches:
            msg = (
                "你勾选了以下开发环境缓存：\n"
                f"{', '.join(selected_dev_caches)}\n\n"
                "⚠️ 注意：清理这些缓存会导致下次编译或安装包时强制重新下载，非常耗时。\n\n"
                "确定要继续清理吗？（将直接执行清理）"
            )
            w = MessageBox("清理开发缓存确认", msg, self.window())
            if not w.exec():
                return
        elif self.chk_perm.isChecked():
            w = MessageBox("强力模式确认", "当前为强力模式，删除后将无法从回收站恢复。继续？", self.window())
            if not w.exec(): 
                return
                
        self.stop.clear()
        threading.Thread(target=self._cln_w, daemon=True).start()

    def _cln_w(self):
        import fnmatch
        pm=self.chk_perm.isChecked()
        if pm: self.sig.log.emit("[警告] 强力模式已开启")
        self._try_rst()
        sel=[(n,p,t) for n,p,t,en,_ in self.targets if en]
        if not sel: self.sig.done.emit("没有勾选项目"); return
        tot=len(sel); ok=fl=st=0; self.sig.prog.emit(0,tot); lf=lambda s:self.sig.log.emit(s)
        for nm,pa,tp in sel:
            if self.stop.is_set(): self.sig.done.emit("已取消"); return
            st+=1; self.sig.log.emit(f"开始：{nm}"); p=expand_env(pa)
            try:
                if tp=="dir":
                    if os.path.isdir(p):
                        for e in os.listdir(p):
                            if self.stop.is_set(): break
                            if delete_path(os.path.join(p,e),pm,lf): ok+=1
                            else: fl+=1
                    else: self.sig.log.emit(f"[跳过] {p}")
                elif tp=="glob":
                    if os.path.isdir(p):
                        for f in os.listdir(p):
                            if self.stop.is_set(): break
                            if fnmatch.fnmatch(f.lower(),"thumbcache*.db"):
                                if delete_path(os.path.join(p,f),pm,lf): ok+=1
                                else: fl+=1
                    else: self.sig.log.emit(f"[跳过] {p}")
                elif tp=="file":
                    if os.path.exists(p):
                        if delete_path(p,pm,lf): ok+=1
                        else: fl+=1
                    else: self.sig.log.emit(f"[跳过] {p}")
            except Exception as e: self.sig.log.emit(f"[失败] {nm}->{e}"); fl+=1
            self.sig.prog.emit(st,tot)
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}")

    def _try_rst(self):
        if not self.chk_rst.isChecked(): return
        if not is_admin(): self.sig.log.emit("[还原点] 需管理员，跳过"); return
        try:
            r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass",
                "Checkpoint-Computer","-Description","CleanTool","-RestorePointType","MODIFY_SETTINGS"],
                capture_output=True,text=True)
            self.sig.log.emit("[还原点] "+("成功" if r.returncode==0 else f"失败:{r.stderr.strip()}"))
        except Exception as e: self.sig.log.emit(f"[还原点] {e}")

# ══════════════════════════════════════════════════════════
#  大文件扫描页
# ══════════════════════════════════════════════════════════
class BigFilePage(ScrollArea):
    def __init__(self, sig, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True)
        self.setObjectName("bigFilePage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)

        v.addLayout(make_title_row(FIF.ZOOM, "大文件扫描"))

        self.drives = get_available_drives()
        self.drive_actions = []
        self.drive_states = {d: (True if d.startswith("C") else False) for d in self.drives}
        self._menu_last_close = 0

        dl = QHBoxLayout(); dl.setSpacing(10)
        dl.addWidget(StrongBodyLabel("选择扫描范围:"))

        self.btn_drives = PushButton("磁盘: C:\\")
        self.menu_drives = RoundMenu(parent=self)

        for d in self.drives:
            action = Action(d)
            action.setData(d) 
            action.triggered.connect(lambda checked=False, a=action: self._toggle_drive(a))
            self.menu_drives.addAction(action)
            self.drive_actions.append(action)

        self.btn_drives.clicked.connect(self._show_drives_menu)
        dl.addWidget(self.btn_drives)
        dl.addStretch()
        v.addLayout(dl)
        
        self._update_drive_btn_text()

        self._disk_threads = 4
        self._disk_type = "检测中..."
        self.lbl_disk = CaptionLabel(f"类型: 检测中...  |  线程: 4")
        v.addWidget(self.lbl_disk)

        self.sig.disk_ready.connect(self._on_disk_ready)
        threading.Thread(target=self._async_detect, daemon=True).start()

        pr=QHBoxLayout(); pr.setSpacing(10)
        pr.addWidget(CaptionLabel("最小文件MB:"))
        self.sp_mb=SpinBox(); self.sp_mb.setRange(50,10240); self.sp_mb.setValue(500); self.sp_mb.setFixedWidth(130); pr.addWidget(self.sp_mb)
        pr.addWidget(CaptionLabel("扫描上限:"))
        self.sp_mx=SpinBox(); self.sp_mx.setRange(50,2000); self.sp_mx.setValue(200); self.sp_mx.setFixedWidth(130); pr.addWidget(self.sp_mx)
        self.chk_perm=CheckBox("永久删除"); self.chk_perm.setChecked(True); pr.addWidget(self.chk_perm)
        pr.addStretch(); v.addLayout(pr)

        self.tbl=TableWidget(); self.tbl.setColumnCount(4)
        self.tbl.setHorizontalHeaderLabels([" ","文件名","大小","路径"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,3))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 200); self.tbl.setColumnWidth(2, 120); self.tbl.setColumnWidth(3, 760)
        style_table(self.tbl)
        v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PrimaryPushButton(FIF.SEARCH,"扫描"); b1.setFixedHeight(30); b1.clicked.connect(self.do_scan); br.addWidget(b1)
        b2=PushButton(FIF.FILTER,"排序"); b2.setFixedHeight(30); b2.clicked.connect(self._sort); br.addWidget(b2)
        b3=PushButton(FIF.DELETE,"删除已勾选"); b3.setFixedHeight(30); b3.clicked.connect(self.do_del); br.addWidget(b3)
        b4=PushButton(FIF.CANCEL,"停止"); b4.setFixedHeight(30); b4.clicked.connect(lambda:self.stop.set()); br.addWidget(b4)
        b5=PushButton(FIF.SYNC,"重新检测"); b5.setFixedHeight(30); b5.clicked.connect(self._redetect); br.addWidget(b5)
        br.addStretch(); v.addLayout(br)

        pg=QHBoxLayout()
        self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pg.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pg.addWidget(self.sl)
        v.addLayout(pg)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120)
        self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志...")
        v.addWidget(self.log)

    def _show_drives_menu(self):
        if time.time() - self._menu_last_close < 0.2:
            return
        pos = self.btn_drives.mapToGlobal(QPoint(0, self.btn_drives.height() + 2))
        self.menu_drives.exec(pos)
        self._menu_last_close = time.time()

    def _toggle_drive(self, action):
        d = action.data()
        self.drive_states[d] = not self.drive_states[d]
        self._update_drive_btn_text()

    def _update_drive_btn_text(self):
        selected = []
        for a in self.drive_actions:
            d = a.data()
            if self.drive_states[d]:
                selected.append(d)
                a.setText(f"{d} √") 
            else:
                a.setText(d) 
                
        if not selected:
            self.btn_drives.setText("磁盘: (未选择)")
        else:
            self.btn_drives.setText(f"磁盘: {', '.join(selected)}")

    def _async_detect(self):
        threads, dtype = get_scan_threads_cached("C")
        self.sig.disk_ready.emit(dtype, threads)

    def _on_disk_ready(self, dtype, threads):
        self._disk_type = dtype
        self._disk_threads = threads
        self.lbl_disk.setText(f"类型: {self._disk_type}  |  线程: {self._disk_threads}")

    def _redetect(self):
        try:
            if os.path.exists(CACHE_FILE): os.remove(CACHE_FILE)
        except: pass
        self._disk_type = "检测中..."
        self._disk_threads = 4
        self.lbl_disk.setText("类型: 检测中...  |  线程: 4")
        threading.Thread(target=self._async_detect, daemon=True).start()

    def _sort(self):
        rc=self.tbl.rowCount()
        if not rc: return
        rows=[]
        for r in range(rc):
            g=lambda c: self.tbl.item(r,c).text() if self.tbl.item(r,c) else ""
            chk = is_row_checked(self.tbl, r)
            rows.append((chk, g(1), g(2), g(3), parse_sz(g(2))))
        rows.sort(key=lambda x:x[4], reverse=True)
        self.tbl.setRowCount(0); self.tbl.setRowCount(len(rows))
        for i,(chk,fn,sz,pa,_) in enumerate(rows):
            self.tbl.setItem(i, 0, make_check_item(chk))
            self.tbl.setItem(i, 1, QTableWidgetItem(fn))
            s=QTableWidgetItem(sz); s.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
            self.tbl.setItem(i, 2, s)
            self.tbl.setItem(i, 3, QTableWidgetItem(pa))

    def do_scan(self):
        self.stop.clear(); threading.Thread(target=self._scan_w,daemon=True).start()

    def _scan_w(self):
        mb=self.sp_mb.value(); mx=self.sp_mx.value()
        w = self._disk_threads
        
        roots = [d for d, state in self.drive_states.items() if state]
        if not roots:
            self.sig.done.emit("错误：未选择任何要扫描的磁盘")
            return
            
        self.sig.log.emit(f"扫描 {', '.join(roots)} (≥{mb}MB, 上限{mx}) | 线程: {w}")
        self.sig.big_clr.emit()
        def cb(n): self.sig.prog.emit(n % 100, 100)
        t0 = time.time()
        
        res = scan_big_files(roots, mb*1024*1024, DEFAULT_EXCLUDES, self.stop, cb, workers=w)
        
        elapsed = time.time() - t0
        if self.stop.is_set():
            self.sig.done.emit(f"已取消，耗时 {elapsed:.1f}s")
            return
        for sz,pa in res[:mx]:
            self.sig.big_add.emit(str(sz), pa)
        self.sig.done.emit(f"扫描完成，共找到 {len(res[:mx])} 条，耗时 {elapsed:.1f}s")

    def do_del(self):
        paths=[]
        for r in range(self.tbl.rowCount()):
            if is_row_checked(self.tbl, r):
                pa=self.tbl.item(r,3)
                if pa: paths.append(pa.text())
        if not paths: self.sig.log.emit("未勾选文件"); return
        pm=self.chk_perm.isChecked()
        if pm:
            w=MessageBox("确认",f"将永久删除 {len(paths)} 个文件，不可恢复。继续？",self.window())
            if not w.exec(): return
        self.stop.clear(); threading.Thread(target=self._del_w,args=(paths,pm),daemon=True).start()

    def _del_w(self, paths, pm):
        ok=fl=0; tot=len(paths); self.sig.prog.emit(0,tot); lf=lambda s:self.sig.log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set(): self.sig.done.emit("已取消"); return
            self.sig.log.emit(f"[大文件] {p}")
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.prog.emit(i,tot)
        self.sig.done.emit(f"删除完成：成功 {ok}，失败 {fl}")

# ══════════════════════════════════════════════════════════
#  主窗口
# ══════════════════════════════════════════════════════════
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.targets = default_clean_targets()
        
        self.config_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cdisk_cleaner_config.json")
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved_state = json.load(f)
                for i in range(len(self.targets)):
                    nm, pa, tp, en, nt = self.targets[i]
                    if nm in saved_state:
                        self.targets[i] = (nm, pa, tp, saved_state[nm], nt)
            except Exception:
                pass
                
        self.stop = threading.Event()
        self.sig = Sig()
        self.pg_clean = CleanPage(self.sig, self.targets, self.stop, self)
        self.pg_big = BigFilePage(self.sig, self.stop, self)
        self._init_nav()
        self._init_win()
        self._conn()

        # 启动后延时 2 秒在后台检测更新
        threading.Timer(2.0, self._check_update_worker).start()

    def closeEvent(self, event):
        try:
            self.pg_clean._sync_targets_from_table()
            saved_state = {nm: en for nm, _, _, en, _ in self.targets}
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(saved_state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        super().closeEvent(event)

    def _init_nav(self):
        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setCollapsible(True)
        self.addSubInterface(self.pg_clean, FIF.BROOM, "常规清理")
        self.addSubInterface(self.pg_big,   FIF.ZOOM,  "大文件扫描")
        self.navigationInterface.addSeparator()
        self.navigationInterface.addItem(
            routeKey="about", icon=FIF.INFO, text="关于",
            onClick=self._about, selectable=False,
            position=NavigationItemPosition.BOTTOM)

    def _init_win(self):
        self.resize(1121, 646); self.setMinimumSize(874, 473)
        self.setWindowTitle("C盘强力清理工具 v0.1.1-alpha03")
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        scr=QApplication.primaryScreen()
        if scr:
            g=scr.availableGeometry()
            self.move((g.width()-self.width())//2,(g.height()-self.height())//2)

    def _conn(self):
        self.sig.log.connect(self._log); self.sig.prog.connect(self._prog)
        self.sig.est.connect(self._est); self.sig.done.connect(self._done)
        self.sig.big_clr.connect(lambda: self.pg_big.tbl.setRowCount(0))
        self.sig.big_add.connect(self._badd)
        self.sig.update_found.connect(self._show_update_dialog)

    def _check_update_worker(self):
        try:
            req = urllib.request.Request(UPDATE_JSON_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                remote_version = data.get("version", "").replace("v", "")
                local_version = CURRENT_VERSION.replace("v", "")
                
                if remote_version and remote_version > local_version:
                    download_url = data.get("url", "")
                    changelog = data.get("changelog", "发现新版本可用！")
                    self.sig.update_found.emit(remote_version, download_url, changelog)
        except Exception:
            pass

    def _show_update_dialog(self, version, url, changelog):
        title = f"发现新版本 v{version}"
        content = f"更新内容：\n{changelog}\n\n是否立即前往下载？"
        w = MessageBox(title, content, self.window())
        # 取消了强制宽度，让它根据文字自动撑开
        if w.exec():
            if url:
                webbrowser.open(url)

    def _ts(self): return time.strftime("%H:%M:%S")

    def _log(self, t):
        line=f"[{self._ts()}] {t}"
        self.pg_clean.log.append(line); self.pg_clean.sl.setText(t[:80])
        self.pg_big.log.append(line);   self.pg_big.sl.setText(t[:80])

    def _prog(self, v, m):
        for pb in (self.pg_clean.pb,self.pg_big.pb): pb.setRange(0,max(1,m)); pb.setValue(v)

    def _est(self, idx, val):
        if 0<=idx<self.pg_clean.tbl.rowCount():
            it=self.pg_clean.tbl.item(idx,4)
            if it: it.setText(human_size(val))

    def _done(self, msg):
        for pb in (self.pg_clean.pb,self.pg_big.pb): pb.setValue(0)
        for sl in (self.pg_clean.sl,self.pg_big.sl): sl.setText("完成")
        self._log(f"[完成] {msg}")
        InfoBar.success("完成",msg,orient=Qt.Orientation.Horizontal,
            isClosable=True,position=InfoBarPosition.TOP,duration=4000,parent=self)

    def _badd(self, sz_str, pa):
        sz = int(sz_str)
        t=self.pg_big.tbl; r=t.rowCount(); t.setRowCount(r+1)
        t.setItem(r, 0, make_check_item(False))
        t.setItem(r, 1, QTableWidgetItem(os.path.basename(pa) if pa else ""))
        s=QTableWidgetItem(human_size(sz)); s.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        t.setItem(r, 2, s); t.setItem(r, 3, QTableWidgetItem(pa))

    def _about(self):
        MessageBox("关于",
            "C盘强力清理工具 v0.1.1-alpha03\n"
            "支持多盘扫描与开发环境深度清理\n"
            "UI：Fluent Widgets\nQQ交流群：670804369\nby Kio",self).exec()

# ══════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════
def relaunch_as_admin():
    try:
        params = " ".join(f'"{a}"' for a in sys.argv)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
    except Exception:
        pass
    sys.exit(0)

def main():
    if sys.platform != "win32":
        print("Windows only"); sys.exit(1)

    if not is_admin():
        relaunch_as_admin()

    app = QApplication(sys.argv)

    setFontFamilies(["微软雅黑"])
    setTheme(Theme.AUTO); setThemeColor("#0078d4")

    w = MainWindow(); w.show()

    sys.exit(app.exec())

if __name__=="__main__": main()