# -*- coding: utf-8 -*-
"""
C盘强力清理工具 v0.1.9-alpha01 (包含强力卸载与右键管理版)
PySide6 + PySide6-Fluent-Widgets (Fluent2 UI)
包含：常规清理、开发缓存、大文件多盘扫描、重复文件、空文件夹、无效快捷方式
新增：强力软件卸载（类 Geek Uninstaller）、右键菜单管理（注册表递归清理）、智能全选
修复：系统还原点创建逻辑及黑框闪烁问题、修复_uadd缩进报错
"""

import os, sys, time, ctypes, threading, subprocess, queue, json, hashlib, winreg, re
import urllib.request
import webbrowser
from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QObject, QPoint, QMetaObject, Slot, QFileInfo, QSize
from PySide6.QtGui import QFont, QIcon, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QAbstractItemView, QTableWidgetItem, QStyledItemDelegate,
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QFileIconProvider 
)

from qfluentwidgets import (
    FluentIcon as FIF,
    setTheme, Theme, setThemeColor, setFontFamilies, setFont,
    NavigationItemPosition, FluentWindow,
    PushButton, PrimaryPushButton, ComboBox,
    CheckBox, SpinBox, ProgressBar,
    TitleLabel, CaptionLabel, StrongBodyLabel,
    IconWidget, TableWidget, TextEdit,
    RoundMenu, Action, MessageBox, InfoBar, InfoBarPosition, ScrollArea,
    SearchLineEdit, MessageBoxBase
)

# ══════════════════════════════════════════════════════════
#  版本与更新配置
# ══════════════════════════════════════════════════════════
CURRENT_VERSION = "0.1.9-alpha01"
UPDATE_JSON_URL = "https://gitee.com/kio0/c_cleaner_plus/raw/master/update.json"

from qfluentwidgets.components.widgets.table_view import TableItemDelegate

def resource_path(relative_path):
    if getattr(sys, '_MEIPASS', None): return os.path.join(sys._MEIPASS, relative_path)
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
        if os.path.isfile(path) or os.path.islink(path): os.remove(path)
        else: shutil.rmtree(path, ignore_errors=False)
        log_fn(f"[永久删除] {path}"); return True
    except Exception as e: log_fn(f"[失败] {path} -> {e}"); return False

def expand_env(p): return os.path.expandvars(p)

def get_available_drives():
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i): drives.append(chr(65 + i) + ":\\")
    return drives

def delete_reg_key_recursive(hkey, subkey):
    try:
        key = winreg.OpenKey(hkey, subkey, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return False
    while True:
        try:
            sub = winreg.EnumKey(key, 0)
            delete_reg_key_recursive(key, sub)
        except OSError:
            break
    winreg.CloseKey(key)
    try:
        winreg.DeleteKey(hkey, subkey)
        return True
    except OSError:
        return False

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
#  默认清理目标
# ══════════════════════════════════════════════════════════
def default_clean_targets():
    sr = os.environ.get("SystemRoot", r"C:\Windows")
    la = os.environ.get("LOCALAPPDATA", "")
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    up = os.environ.get("USERPROFILE", "")
    J = os.path.join
    
    return [
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
        
        ("D3DSCache", J(la, "D3DSCache"), "dir", False, "d3d着色器缓存"),
        ("NVIDIA DX", J(la, "NVIDIA", "DXCache"), "dir", False, "NV着色器缓存"),
        ("NVIDIA GL", J(la, "NVIDIA", "GLCache"), "dir", False, "NV OpenGL缓存"),
        ("NVIDIA Compute", J(la, "NVIDIA", "ComputeCache"), "dir", False, "CUDA"),
        ("NV_Cache", J(pd, "NVIDIA Corporation", "NV_Cache"), "dir", False, "NV CUDA/计算缓存"),
        ("AMD DX", J(la, "AMD", "DxCache"), "dir", False, "AMD着色器缓存"),
        ("AMD GL", J(la, "AMD", "GLCache"), "dir", False, "AMD OpenGL缓存"),
        ("Steam Shader", J(la, "Steam", "steamapps", "shadercache"), "dir", False, "Steam"),
        ("Steam 下载临时", J(la, "Steam", "steamapps", "downloading"), "dir", False, "下载残留"),
        
        ("Edge Cache", J(la, "Microsoft", "Edge", "User Data", "Default", "Cache"), "dir", False, "浏览器"),
        ("Edge Code", J(la, "Microsoft", "Edge", "User Data", "Default", "Code Cache"), "dir", False, "JS"),
        ("Chrome Cache", J(la, "Google", "Chrome", "User Data", "Default", "Cache"), "dir", False, "浏览器"),
        ("Chrome Code", J(la, "Google", "Chrome", "User Data", "Default", "Code Cache"), "dir", False, "JS"),
        
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
        
        ("WU Download", J(sr, "SoftwareDistribution", "Download"), "dir", False, "更新缓存"),
        ("Delivery Opt", J(sr, "SoftwareDistribution", "DeliveryOptimization"), "dir", False, "需管理员"),
    ]

DEFAULT_EXCLUDES=[r"C:\Windows\WinSxS",r"C:\Windows\Installer",r"C:\Program Files",r"C:\Program Files (x86)"]
BIGFILE_SKIP_EXT={".sys"}

def should_exclude(p, prefixes):
    n=os.path.normcase(os.path.abspath(p))
    return any(n.startswith(os.path.normcase(os.path.abspath(e))) for e in prefixes)

# ══════════════════════════════════════════════════════════
#  多线程文件扫描（共用模块）
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

# ══════════════════════════════════════════════════════════
#  信号与公共函数
# ══════════════════════════════════════════════════════════
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

def parse_sz(t):
    try:
        v,u=t.strip().split(); return int(float(v)*{"B":1,"KB":1024,"MB":1024**2,"GB":1024**3,"TB":1024**4}.get(u,1))
    except: return 0

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
        v.addWidget(CaptionLabel(f"当前权限：{badge}  |  部分项目可能需要管理员权限"))

        opt=QHBoxLayout(); opt.setSpacing(16)
        self.chk_perm=CheckBox("强力模式：永久删除"); self.chk_perm.setChecked(True); opt.addWidget(self.chk_perm)
        
        # 修复：补回创建还原点 CheckBox
        self.chk_rst=CheckBox("创建还原点"); opt.addWidget(self.chk_rst)
        
        opt.addStretch(); v.addLayout(opt)

        self.tbl=TableWidget(); self.tbl.setColumnCount(5); self.tbl.setHorizontalHeaderLabels([" ","项目","路径","说明","大小"])
        self.tbl.verticalHeader().setVisible(False); self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,2))
        
        self.tbl.setRowCount(len(self.targets))
        for i,(nm,pa,tp,en,nt) in enumerate(self.targets):
            self.tbl.setItem(i, 0, make_check_item(en)); self.tbl.setItem(i, 1, QTableWidgetItem(nm))
            self.tbl.setItem(i, 2, QTableWidgetItem(pa if tp!="glob" else f"{pa} | thumbcache*.db")); self.tbl.setItem(i, 3, QTableWidgetItem(nt)); self.tbl.setItem(i, 4, QTableWidgetItem(""))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 150); self.tbl.setColumnWidth(2, 400); self.tbl.setColumnWidth(3, 200); self.tbl.setColumnWidth(4, 85)
        self.tbl.setIconSize(QSize(24, 24))
        style_table(self.tbl); v.addWidget(self.tbl, 1)

        br=QHBoxLayout(); br.setSpacing(8)
        b1=PushButton(FIF.UNIT,"估算"); b1.setFixedHeight(30); b1.clicked.connect(self.do_est); br.addWidget(b1)
        b2=PushButton(FIF.ACCEPT,"全选"); b2.setFixedHeight(30); b2.clicked.connect(lambda: [set_row_checked(self.tbl, i, True) for i in range(len(self.targets))]); br.addWidget(b2)
        b3=PushButton(FIF.CLOSE,"全不选"); b3.setFixedHeight(30); b3.clicked.connect(lambda: [set_row_checked(self.tbl, i, False) for i in range(len(self.targets))]); br.addWidget(b3)
        br.addStretch()
        bc=PrimaryPushButton(FIF.DELETE,"开始清理"); bc.setFixedHeight(30); bc.clicked.connect(self.do_clean); br.addWidget(bc)
        bs=PushButton(FIF.CANCEL,"停止"); bs.setFixedHeight(30); bs.clicked.connect(lambda:self.stop.set()); br.addWidget(bs); v.addLayout(br)

        pr=QHBoxLayout(); self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pr.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pr.addWidget(self.sl); v.addLayout(pr)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志..."); v.addWidget(self.log)

    def _sync(self):
        for i in range(len(self.targets)): n,p,t,_,nt = self.targets[i]; self.targets[i] = (n,p,t, is_row_checked(self.tbl, i), nt)

    # 修复：补充创建系统还原点的执行方法
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

    def do_est(self): self._sync(); self.stop.clear(); threading.Thread(target=self._est_w,daemon=True).start()
    def _est_w(self):
        import fnmatch
        its=[(i,t) for i,t in enumerate(self.targets) if t[3]]
        if not its: return
        self.sig.prog.emit(0,len(its))
        for n,(idx,t) in enumerate(its,1):
            if self.stop.is_set(): break
            nm,pa,tp,_,_=t; e=0
            try:
                if tp=="dir": e=dir_size(expand_env(pa)) if os.path.isdir(expand_env(pa)) else 0
                elif tp=="glob": 
                    fo=expand_env(pa)
                    if os.path.isdir(fo): e=sum(safe_getsize(os.path.join(fo,f)) for f in os.listdir(fo) if fnmatch.fnmatch(f.lower(),"thumbcache*.db"))
                elif tp=="file": e=safe_getsize(expand_env(pa)) if os.path.isfile(expand_env(pa)) else 0
            except: pass
            self.sig.est.emit(idx,e); self.sig.prog.emit(n,len(its))
        self.sig.done.emit("估算完成")

    def do_clean(self):
        self._sync()
        if self.chk_perm.isChecked():
            if not MessageBox("确认", "当前为强力模式，删除后无法恢复。继续？", self.window()).exec(): return
        self.stop.clear(); threading.Thread(target=self._cln_w, daemon=True).start()
    
    def _cln_w(self):
        import fnmatch; pm=self.chk_perm.isChecked(); sel=[(n,p,t) for n,p,t,en,_ in self.targets if en]
        if not sel: return
        
        # 修复：执行清理前调用还原点方法
        self._try_rst()
        
        ok=fl=st=0; tot=len(sel); lf=lambda s:self.sig.log.emit(s)
        for nm,pa,tp in sel:
            if self.stop.is_set(): break
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
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}")

# ══════════════════════════════════════════════════════════
#  新增：Geek 风格的残留扫描对话框 (已修复属性错误)
# ══════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════
#  页面：强力软件卸载 (Geek Uninstaller 风格)
# ══════════════════════════════════════════════════════════
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
            software = []
            keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
            
            for hkey, subkey_str in keys:
                if self.stop.is_set(): break
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
                
            self.sig.done.emit(f"成功扫描出 {len(unique)} 个软件。")

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
            self.sig.log.emit("请先勾选一个要卸载的软件！"); return
        if len(data) > 1:
            self.sig.log.emit("标准卸载一次只能进行一个，请只勾选一个目标。"); return
            
        r, nm, pub, loc, cmd, reg = data[0]
        if not cmd:
            self.sig.log.emit("该软件未提供标准卸载命令，请直接使用强力清除。"); return
            
        self.sig.log.emit(f"[标准卸载] 正在调用官方卸载程序: {nm}")
        self._current_uninstalling = (r, nm, pub, loc, reg)
        
        def run_and_wait():
            try: 
                proc = subprocess.Popen(cmd, shell=True)
                proc.wait() 
                QMetaObject.invokeMethod(self, "prompt_leftover_scan", Qt.ConnectionType.QueuedConnection)
            except Exception as e:
                self.sig.log.emit(f"启动卸载程序失败: {e}")
                
        threading.Thread(target=run_and_wait, daemon=True).start()

    @Slot()
    def prompt_leftover_scan(self):
        if not hasattr(self, "_current_uninstalling") or not self._current_uninstalling: return
        r, nm, pub, loc, reg = self._current_uninstalling
        if MessageBox("卸载程序已退出", f"标准卸载流程已结束。是否立刻进行深度扫描，清理 '{nm}' 可能遗留的注册表和文件残留？", self.window()).exec():
            self._trigger_leftover_scan(r, nm, pub, loc, reg)
        self._current_uninstalling = None

    def do_force_uninstall(self):
        data = self._get_checked_rows_data()
        if not data:
            self.sig.log.emit("请先勾选目标软件！"); return
        if len(data) > 1:
            self.sig.log.emit("强力清除一次只能进行一个，请只勾选一个目标。"); return
            
        r, nm, pub, loc, cmd, reg = data[0]
        self._trigger_leftover_scan(r, nm, pub, loc, reg)

    def _trigger_leftover_scan(self, r, nm, pub, loc, reg):
        dialog = LeftoversDialog(self.window(), nm, pub, loc, reg)
        if dialog.tree.topLevelItemCount() == 0:
            InfoBar.success("扫描完毕", "没有发现任何明显的残留文件或注册表项。", parent=self.window())
            return
            
        if dialog.exec():
            del_files, del_regs = dialog.get_selected_items()
            if not del_files and not del_regs: return
            
            self.sig.log.emit(f"[强力清除] 开始清理 {nm} 的残留...")
            self.stop.clear()
            threading.Thread(target=self._force_uninst_w, args=(del_files, del_regs), daemon=True).start()

    def _force_uninst_w(self, files, regs):
        lf = lambda s: self.sig.log.emit(s)
        for r in regs:
            parts = r.split('\\', 1)
            base = winreg.HKEY_LOCAL_MACHINE if "HKLM" in parts[0] else winreg.HKEY_CURRENT_USER
            if delete_reg_key_recursive(base, parts[1]): self.sig.log.emit(f"清除注册表: {parts[1]}")
            else: self.sig.log.emit(f"清除注册表失败: {parts[1]}")
            
        for f in files:
            if delete_path(f, True, lf): self.sig.log.emit(f"摧毁残留目录: {f}")
            
        QMetaObject.invokeMethod(self, "_refresh_after_delete", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _refresh_after_delete(self):
        self.sig.done.emit("强力摧毁完毕！")
        self.do_scan()

# ══════════════════════════════════════════════════════════
#  页面：大文件扫描
# ══════════════════════════════════════════════════════════
class BigFilePage(ScrollArea):
    def __init__(self, sig, stop, parent=None):
        super().__init__(parent); self.sig=sig; self.stop=stop
        self.view=QWidget(); self.setWidget(self.view); self.setWidgetResizable(True); self.setObjectName("bigFilePage"); self.enableTransparentBackground()
        v=QVBoxLayout(self.view); v.setContentsMargins(28,12,28,20); v.setSpacing(8)
        v.addLayout(make_title_row(FIF.ZOOM, "大文件扫描"))
        
        self.drives = get_available_drives(); self.drive_actions = []; self.drive_states = {d: (True if d.startswith("C") else False) for d in self.drives}; self._menu_last_close = 0
        dl = QHBoxLayout(); dl.setSpacing(10); dl.addWidget(StrongBodyLabel("选择范围:"))
        self.btn_drives = PushButton("磁盘: C:\\"); self.menu_drives = RoundMenu(parent=self)
        for d in self.drives:
            action = Action(d); action.setData(d); action.triggered.connect(lambda checked=False, a=action: self._toggle_drive(a))
            self.menu_drives.addAction(action); self.drive_actions.append(action)
        self.btn_drives.clicked.connect(self._show_drives_menu); dl.addWidget(self.btn_drives); dl.addStretch(); v.addLayout(dl)
        self._update_drive_btn_text()

        self._disk_threads = 4; self._disk_type = "检测中..."; self.lbl_disk = CaptionLabel(f"类型: 检测中...  |  线程: 4"); v.addWidget(self.lbl_disk)
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
        b3=PushButton(FIF.DELETE,"删除已勾选"); b3.setFixedHeight(30); b3.clicked.connect(self.do_del); br.addWidget(b3)
        b4=PushButton(FIF.CANCEL,"停止"); b4.setFixedHeight(30); b4.clicked.connect(lambda:self.stop.set()); br.addWidget(b4)
        br.addStretch(); v.addLayout(br)

        pg=QHBoxLayout(); self.pb=ProgressBar(); self.pb.setRange(0,100); self.pb.setValue(0); self.pb.setFixedHeight(3)
        pg.addWidget(self.pb,1); self.sl=CaptionLabel("就绪"); pg.addWidget(self.sl); v.addLayout(pg)
        self.log=TextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setFont(QFont("Consolas",9)); self.log.setPlaceholderText("日志..."); v.addWidget(self.log)

    def _show_drives_menu(self):
        if time.time() - self._menu_last_close < 0.2: return
        self.menu_drives.exec(self.btn_drives.mapToGlobal(QPoint(0, self.btn_drives.height() + 2))); self._menu_last_close = time.time()
    def _toggle_drive(self, action):
        d = action.data(); self.drive_states[d] = not self.drive_states[d]; self._update_drive_btn_text()
    def _update_drive_btn_text(self):
        sel = [a.data() for a in self.drive_actions if self.drive_states[a.data()]]
        for a in self.drive_actions: a.setText(f"{a.data()} √" if self.drive_states[a.data()] else a.data())
        self.btn_drives.setText(f"磁盘: {', '.join(sel)}" if sel else "磁盘: (未选择)")

    def _on_disk_ready(self, dtype, threads): self._disk_type = dtype; self._disk_threads = threads; self.lbl_disk.setText(f"类型: {dtype}  |  线程: {threads}")

    def do_scan(self):
        self.stop.clear(); threading.Thread(target=self._scan_w,daemon=True).start()

    def _scan_w(self):
        mb=self.sp_mb.value(); mx=self.sp_mx.value(); w = self._disk_threads
        roots = [d for d, state in self.drive_states.items() if state]
        if not roots: return
        self.sig.log.emit(f"扫描 (≥{mb}MB) | 线程: {w}"); self.sig.big_clr.emit()
        res = scan_big_files(roots, mb*1024*1024, DEFAULT_EXCLUDES, self.stop, lambda n: self.sig.prog.emit(n % 100, 100), workers=w)
        if self.stop.is_set(): return
        for sz,pa in res[:mx]: self.sig.big_add.emit(str(sz), pa)
        self.sig.done.emit(f"扫描完成，找到 {len(res[:mx])} 条")

    def do_del(self):
        paths=[self.tbl.item(r,3).text() for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r) and self.tbl.item(r,3)]
        if not paths: return
        pm=self.chk_perm.isChecked()
        if pm and not MessageBox("确认",f"将永久删除 {len(paths)} 个文件。继续？",self.window()).exec(): return
        self.stop.clear(); threading.Thread(target=self._del_w,args=(paths,pm),daemon=True).start()

    def _del_w(self, paths, pm):
        ok=fl=0; tot=len(paths); lf=lambda s:self.sig.log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set(): break
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.prog.emit(i,tot)
        self.sig.done.emit(f"删除完成：成功 {ok}，失败 {fl}")

# ══════════════════════════════════════════════════════════
#  页面：更多清理 (重复文件, 空文件夹, 快捷方式, 注册表, 右键菜单, 智能全选)
# ══════════════════════════════════════════════════════════
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

        self.drives = get_available_drives(); self.drive_actions = []; self.drive_states = {d: (True if d.startswith("C") else False) for d in self.drives}; self._menu_last_close = 0
        self.btn_drives = PushButton("磁盘: C:\\"); self.menu_drives = RoundMenu(parent=self)
        for d in self.drives:
            action = Action(d); action.setData(d); action.triggered.connect(lambda checked=False, a=action: self._toggle_drive(a))
            self.menu_drives.addAction(action); self.drive_actions.append(action)
        self.btn_drives.clicked.connect(self._show_drives_menu); self._update_drive_btn_text()
        
        self.lbl_disk_req = StrongBodyLabel("选择范围:"); dl.addWidget(self.lbl_disk_req); dl.addWidget(self.btn_drives); dl.addStretch(); v.addLayout(dl)

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
                all_checked = False
                break

        new_state = not all_checked
        for r in range(rc):
            set_row_checked(self.tbl, r, new_state)
            
        if new_state:
            self.btn_sel_all.setText("取消全选")
            self.btn_sel_all.setIcon(FIF.CLOSE)
        else:
            self.btn_sel_all.setText("全选")
            self.btn_sel_all.setIcon(FIF.ACCEPT)

    def _on_mode_change(self):
        is_reg = self.cb_mode.currentIndex() in (3, 4)
        self.btn_drives.setVisible(not is_reg); self.lbl_disk_req.setVisible(not is_reg)

    def _show_drives_menu(self):
        if time.time() - self._menu_last_close < 0.2: return
        self.menu_drives.exec(self.btn_drives.mapToGlobal(QPoint(0, self.btn_drives.height() + 2))); self._menu_last_close = time.time()
    def _toggle_drive(self, action):
        d = action.data(); self.drive_states[d] = not self.drive_states[d]; self._update_drive_btn_text()
    def _update_drive_btn_text(self):
        sel = [a.data() for a in self.drive_actions if self.drive_states[a.data()]]
        for a in self.drive_actions: a.setText(f"{a.data()} √" if self.drive_states[a.data()] else a.data())
        self.btn_drives.setText(f"磁盘: {', '.join(sel)}" if sel else "磁盘: (未选择)")

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
        files, _ = self._collect_files_threaded(roots, DEFAULT_EXCLUDES, workers)
        if self.stop.is_set(): return
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
        if self.stop.is_set(): return
        cnt = 0
        for grp_id, dup_list in enumerate(results, 1):
            for idx, p in enumerate(dup_list):
                self.sig.more_add.emit((idx > 0), "重复文件", f"组 {grp_id}", human_size(os.path.getsize(p)), p); cnt += 1
        self.sig.done.emit(f"扫描完成，找到 {cnt} 个重复文件")

    def _scan_empty_dirs(self, roots, workers):
        _, dirs = self._collect_files_threaded(roots, DEFAULT_EXCLUDES, workers)
        if self.stop.is_set(): return
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
        self.sig.done.emit(f"扫描完成，找到 {len(empty_set)} 个空文件夹")

    def _scan_shortcuts(self, roots, workers):
        files, _ = self._collect_files_threaded(roots, DEFAULT_EXCLUDES, workers, ext_filter=".lnk")
        if self.stop.is_set(): return
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
        self.sig.done.emit(f"扫描完成，找到 {invalid_cnt} 个无效快捷方式")

    def _scan_registry(self):
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
        for tp, nm, det, path in res: self.sig.more_add.emit(False, tp, nm, det, path)
        self.sig.done.emit(f"扫描完成，找到 {len(res)} 个无效注册表卸载项")

    def _scan_context_menu(self):
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
        for tp, nm, det, path in res: self.sig.more_add.emit(False, tp, nm, det, path)
        self.sig.done.emit(f"扫描完成，列出 {len(res)} 个右键菜单扩展（请谨慎勾选删除）")

    def do_del(self):
        paths=[self.tbl.item(r,4).text() for r in range(self.tbl.rowCount()) if is_row_checked(self.tbl, r)]
        if not paths: return
        is_reg = self.cb_mode.currentIndex() in (3, 4)
        if not MessageBox("确认",f"确定清理这 {len(paths)} 个项目？不可恢复。",self.window()).exec(): return
        self.stop.clear()
        if is_reg: threading.Thread(target=self._del_reg_w, args=(paths,), daemon=True).start()
        else: threading.Thread(target=self._del_files_w, args=(paths,self.chk_perm.isChecked()), daemon=True).start()

    def _del_files_w(self, paths, pm):
        ok=fl=0; tot=len(paths); lf=lambda s:self.sig.log.emit(s)
        for i,p in enumerate(paths,1):
            if self.stop.is_set(): break
            if delete_path(p,pm,lf): ok+=1
            else: fl+=1
            self.sig.prog.emit(i,tot)
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}")

    def _del_reg_w(self, paths):
        ok=fl=0; tot=len(paths)
        for i,p in enumerate(paths,1):
            if self.stop.is_set(): break
            try:
                parts = p.split('\\', 1)
                if "HKLM" in parts[0] or "MACHINE" in parts[0]: base = winreg.HKEY_LOCAL_MACHINE
                elif "HKCU" in parts[0] or "USER" in parts[0]: base = winreg.HKEY_CURRENT_USER
                elif "HKCR" in parts[0] or "CLASSES" in parts[0]: base = winreg.HKEY_CLASSES_ROOT
                else: raise Exception("Unknown HKEY")
                
                if delete_reg_key_recursive(base, parts[1]):
                    self.sig.log.emit(f"[清理注册表] 成功: {parts[1]}"); ok += 1
                else:
                    self.sig.log.emit(f"[清理注册表] 失败: 键不存在或无权限"); fl += 1
            except Exception as e:
                self.sig.log.emit(f"[清理失败] {p} -> {e}"); fl += 1
            self.sig.prog.emit(i,tot)
        self.sig.done.emit(f"清理完成：成功 {ok}，失败 {fl}")


# ══════════════════════════════════════════════════════════
#  主窗口
# ══════════════════════════════════════════════════════════
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
        except Exception:
            pass

        self.targets = default_clean_targets()
        self.config_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cdisk_cleaner_config.json")
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f: saved_state = json.load(f)
                for i in range(len(self.targets)):
                    nm, pa, tp, en, nt = self.targets[i]
                    if nm in saved_state: self.targets[i] = (nm, pa, tp, saved_state[nm], nt)
            except: pass
                
        self.stop = threading.Event(); self.sig = Sig()
        self.pg_clean = CleanPage(self.sig, self.targets, self.stop, self)
        self.pg_big = BigFilePage(self.sig, self.stop, self)
        self.pg_uninstall = UninstallPage(self.sig, self.stop, self)
        self.pg_more = MoreCleanPage(self.sig, self.stop, self)
        
        self._init_nav(); self._init_win(); self._conn()
        threading.Thread(target=self._async_detect, daemon=True).start()
        threading.Timer(2.0, self._check_update_worker).start()

    def closeEvent(self, event):
        try:
            self.pg_clean._sync()
            with open(self.config_path, "w", encoding="utf-8") as f: json.dump({nm: en for nm, _, _, en, _ in self.targets}, f, ensure_ascii=False, indent=2)
        except: pass
        super().closeEvent(event)

    def _init_nav(self):
        self.navigationInterface.setExpandWidth(200); self.navigationInterface.setCollapsible(True)
        self.addSubInterface(self.pg_clean, FIF.BROOM, "常规清理")
        self.addSubInterface(self.pg_big,   FIF.ZOOM,  "大文件扫描")
        self.addSubInterface(self.pg_uninstall, FIF.APPLICATION, "应用卸载与清理")
        self.addSubInterface(self.pg_more,  FIF.MORE,  "更多清理")
        self.navigationInterface.addSeparator()
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

    def _check_update_worker(self): pass

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
        self._log(f"[完成] {msg}"); InfoBar.success("完成",msg,orient=Qt.Orientation.Horizontal, isClosable=True,position=InfoBarPosition.TOP,duration=4000,parent=self)

    def _badd(self, sz_str, pa):
        t=self.pg_big.tbl; r=t.rowCount(); t.setRowCount(r+1); t.setItem(r, 0, make_check_item(False)); t.setItem(r, 1, QTableWidgetItem(os.path.basename(pa) if pa else ""))
        s=QTableWidgetItem(human_size(int(sz_str))); s.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        t.setItem(r, 2, s); t.setItem(r, 3, QTableWidgetItem(pa))

    def _madd(self, chk, tp, nm, det, pa):
        t=self.pg_more.tbl; r=t.rowCount(); t.setRowCount(r+1)
        t.setItem(r, 0, make_check_item(chk)); t.setItem(r, 1, QTableWidgetItem(tp)); t.setItem(r, 2, QTableWidgetItem(nm))
        t.setItem(r, 3, QTableWidgetItem(det)); t.setItem(r, 4, QTableWidgetItem(pa))

    # 修复：整理好的 _uadd 方法缩进，避免引发 IndentationError 报错
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
        hidden_item = QTableWidgetItem(cmd); hidden_item.setData(Qt.ItemDataRole.UserRole, reg)
        t.setItem(r, 5, hidden_item)

    def _about(self):
        MessageBox("关于", f"C盘强力清理工具 v{CURRENT_VERSION}\n包含了最新支持的右键管理、Geek式软件卸载等高级特性。\nUI：Fluent Widgets\nby Kio",self).exec()

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