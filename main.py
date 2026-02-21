# -*- coding: utf-8 -*-
"""
C盘强力清理工具 v0.1.0
PySide6 + PySide6-Fluent-Widgets (Fluent2 UI)
Python 3.12.6 / PySide6 6.10.2 / PySide6-Fluent-Widgets 1.11.1
"""

import os, sys, time, ctypes, fnmatch, shutil, threading, subprocess, queue
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, Signal, QObject, QModelIndex
from PySide6.QtGui import QFont, QIcon, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QAbstractItemView, QTableWidgetItem, QStyleOptionViewItem,
    QStyledItemDelegate,
)

from qfluentwidgets import (
    FluentIcon as FIF,
    setTheme, Theme, setThemeColor,
    setFontFamilies, setFont, getFont,
    NavigationItemPosition, FluentWindow,
    PushButton, PrimaryPushButton,
    CheckBox, SpinBox, ProgressBar,
    BodyLabel, SubtitleLabel, TitleLabel, CaptionLabel, StrongBodyLabel,
    SimpleCardWidget, HeaderCardWidget, CardWidget, IconWidget,
    TableWidget, TextEdit,
    RoundMenu, Action,
    MessageBox, InfoBar, InfoBarPosition,
    ScrollArea,
)


from qfluentwidgets.components.widgets.table_view import TableItemDelegate

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

# ══════════════════════════════════════════════════════════
#  磁盘类型检测
# ══════════════════════════════════════════════════════════
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

#  线程设置

def get_scan_threads(drive_letter="C"):
    dtype = detect_disk_type(drive_letter)
    thread_map = {"SSD": 8, "HDD": 2, "Unknown": 4}
    return thread_map.get(dtype, 4), dtype

# ══════════════════════════════════════════════════════════
#  默认清理目标
# ══════════════════════════════════════════════════════════
def default_clean_targets():
    sr=os.environ.get("SystemRoot",r"C:\Windows")
    la=os.environ.get("LOCALAPPDATA","")
    pd=os.environ.get("PROGRAMDATA",r"C:\ProgramData")
    J=os.path.join
    return [
        ("用户临时文件",expand_env(r"%TEMP%"),"dir",True,"常见垃圾，安全"),
        ("系统临时文件",J(sr,"Temp"),"dir",True,"可能需管理员"),
        ("Prefetch",J(sr,"Prefetch"),"dir",False,"影响首次启动"),
        ("CBS 日志",J(sr,"Logs","CBS"),"dir",True,"较安全"),
        ("DISM 日志",J(sr,"Logs","DISM"),"dir",True,"较安全"),
        ("LiveKernelReports",J(sr,"LiveKernelReports"),"dir",True,"内核转储"),
        ("WER(用户)",J(la,"Microsoft","Windows","WER"),"dir",True,"崩溃报告"),
        ("WER(系统)",J(sr,"System32","config","systemprofile","AppData","Local","Microsoft","Windows","WER"),"dir",False,"需管理员"),
        ("Minidump",J(sr,"Minidump"),"dir",True,"崩溃转储"),
        ("MEMORY.DMP",J(sr,"MEMORY.DMP"),"file",False,"确认不调试时勾选"),
        ("缩略图缓存",J(la,"Microsoft","Windows","Explorer"),"glob",True,"thumbcache*.db"),
        ("D3DSCache",J(la,"D3DSCache"),"dir",False,"d3d着色器缓存"),
        ("NVIDIA DX",J(la,"NVIDIA","DXCache"),"dir",False,"NV着色器缓存"),
        ("NVIDIA GL",J(la,"NVIDIA","GLCache"),"dir",False,"NV OpenGL缓存"),
        ("NVIDIA Compute",J(la,"NVIDIA","ComputeCache"),"dir",False,"CUDA"),
        ("NV_Cache",J(pd,"NVIDIA Corporation","NV_Cache"),"dir",False,"NV CUDA/计算缓存"),
        ("AMD DX",J(la,"AMD","DxCache"),"dir",False,"AMD着色器缓存"),
        ("AMD GL",J(la,"AMD","GLCache"),"dir",False,"AMD OpenGL缓存"),
        ("Steam Shader",J(la,"Steam","steamapps","shadercache"),"dir",False,"Steam"),
        ("Steam 下载临时",J(la,"Steam","steamapps","downloading"),"dir",False,"下载残留"),
        ("Edge Cache",J(la,"Microsoft","Edge","User Data","Default","Cache"),"dir",False,"浏览器"),
        ("Edge Code",J(la,"Microsoft","Edge","User Data","Default","Code Cache"),"dir",False,"JS"),
        ("Chrome Cache",J(la,"Google","Chrome","User Data","Default","Cache"),"dir",False,"浏览器"),
        ("Chrome Code",J(la,"Google","Chrome","User Data","Default","Code Cache"),"dir",False,"JS"),
        ("pip Cache",J(la,"pip","Cache"),"dir",True,"Python"),
        ("NuGet Cache",J(la,"NuGet","v3-cache"),"dir",True,".NET"),
        ("WU Download",J(sr,"SoftwareDistribution","Download"),"dir",False,"更新缓存"),
        ("Delivery Opt",J(sr,"SoftwareDistribution","DeliveryOptimization"),"dir",False,"需管理员"),
    ]

DEFAULT_EXCLUDES=[r"C:\Windows\WinSxS",r"C:\Windows\Installer",r"C:\Program Files",r"C:\Program Files (x86)",r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive"]
BIGFILE_SKIP_EXT={".sys"}

def should_exclude(p, prefixes):
    n=os.path.normcase(os.path.abspath(p))
    return any(n.startswith(os.path.normcase(os.path.abspath(e))) for e in prefixes)

# ══════════════════════════════════════════════════════════
#  大文件扫描（生产者-消费者并发模型）
# ══════════════════════════════════════════════════════════
_SENTINEL = None  # 队列终止标记

def _dir_worker(dir_queue, min_b, excl, stop_flag, results, counter, lock):
    """消费者线程：从队列中取目录，扫描其直属文件，
    并将子目录放回队列供其他线程消费。"""
    while not stop_flag.is_set():
        try:
            dirpath = dir_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        if dirpath is _SENTINEL:
            dir_queue.put(_SENTINEL)  # 传播终止信号给其他线程
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


def scan_big_files(root, min_b, excl, stop, cb, workers=4):
    """生产者-消费者模型并发扫描。
    主线程将 root 放入队列，worker 线程扫描目录并将子目录放回队列，
    实现动态负载均衡——大目录（如 Windows）的子目录会被多个线程分摊。
    """
    dir_queue = queue.Queue()
    results = []
    counter = [0]  # 可变容器用于线程间共享计数
    lock = threading.Lock()

    # 种子：root 本身
    dir_queue.put(root)

    # 启动 worker 线程
    threads = []
    for _ in range(workers):
        t = threading.Thread(
            target=_dir_worker,
            args=(dir_queue, min_b, excl, stop, results, counter, lock),
            daemon=True
        )
        t.start()
        threads.append(t)

    # 等待队列清空（所有目录都处理完毕）
    tk = time.time()
    while not stop.is_set():
        try:
            # 短超时轮询，兼顾响应速度和 CPU 占用
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

    # 发送终止信号
    dir_queue.put(_SENTINEL)
    for t in threads:
        t.join(timeout=2)

    # 最终回调
    cb(counter[0])

    results.sort(reverse=True, key=lambda x: x[0])
    return results


# ══════════════════════════════════════════════════════════
#  信号（big_add 用 str,str 避免 int32 溢出）
# ══════════════════════════════════════════════════════════
class Sig(QObject):
    log=Signal(str); prog=Signal(int,int); est=Signal(int,int)
    big_clr=Signal(); big_add=Signal(str,str); done=Signal(str)

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
            "复制成功",
            raw,
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
        if self.chk_perm.isChecked():
            w=MessageBox("确认","当前为强力模式，删除不可恢复。继续？",self.window())
            if not w.exec(): return
        self.stop.clear(); threading.Thread(target=self._cln_w,daemon=True).start()

    def _cln_w(self):
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

        self._disk_threads, self._disk_type = get_scan_threads("C")
        self.lbl_disk = CaptionLabel(
            f"扫描 C 盘  |  "
            f"磁盘: {self._disk_type}  |  "
            f"线程: {self._disk_threads}"
        )
        v.addWidget(self.lbl_disk)

        pr=QHBoxLayout(); pr.setSpacing(10)
        pr.addWidget(CaptionLabel("最小文件MB:"))
        self.sp_mb=SpinBox(); self.sp_mb.setRange(50,10240); self.sp_mb.setValue(500); self.sp_mb.setFixedWidth(130); pr.addWidget(self.sp_mb)
        pr.addWidget(CaptionLabel("扫描上限:"))
        self.sp_mx=SpinBox(); self.sp_mx.setRange(50,2000); self.sp_mx.setValue(200); self.sp_mx.setFixedWidth(130); pr.addWidget(self.sp_mx)
        self.chk_perm=CheckBox("永久删除"); self.chk_perm.setChecked(True); pr.addWidget(self.chk_perm)
        pr.addStretch(); v.addLayout(pr)


# 大文件列表控件
        self.tbl=TableWidget(); self.tbl.setColumnCount(4)
        self.tbl.setHorizontalHeaderLabels([" ","文件名","大小","路径"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(lambda p: make_ctx(self,self.tbl,p,3))
        self.tbl.setColumnWidth(0, 36); self.tbl.setColumnWidth(1, 200); self.tbl.setColumnWidth(2, 120); self.tbl.setColumnWidth(3, 770)
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

    def _redetect(self):
        self._disk_threads, self._disk_type = get_scan_threads("C")
        self.lbl_disk.setText(
            f"扫描 C 盘  |  "
            f"磁盘: {self._disk_type}  |  "
            f"线程: {self._disk_threads}"
        )


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
        self.sig.log.emit(
            f"扫描 C:\\ (≥{mb}MB, 上限{mx}) | "
            f"{self._disk_type}, {w} 线程"
        )
        self.sig.big_clr.emit()
        def cb(n): self.sig.prog.emit(n % 100, 100)
        t0 = time.time()
        res = scan_big_files("C:\\", mb*1024*1024, DEFAULT_EXCLUDES, self.stop, cb, workers=w)
        elapsed = time.time() - t0
        if self.stop.is_set():
            self.sig.done.emit(f"已取消，耗时 {elapsed:.1f}s")
            return
        for sz,pa in res[:mx]:
            self.sig.big_add.emit(str(sz), pa)  # str 避免 int32 溢出
        self.sig.done.emit(
            f"扫描完成，{len(res[:mx])} 条，"
            f"{elapsed:.1f}s ({self._disk_type}/{w}线程)"
        )

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
        self.targets=default_clean_targets(); self.stop=threading.Event(); self.sig=Sig()
        self.pg_clean=CleanPage(self.sig,self.targets,self.stop,self)
        self.pg_big=BigFilePage(self.sig,self.stop,self)
        self._init_nav(); self._init_win(); self._conn()

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
        self.setWindowTitle("C盘强力清理工具 v0.1.0")
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
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
        sz = int(sz_str)  # 字符串转回整数
        t=self.pg_big.tbl; r=t.rowCount(); t.setRowCount(r+1)
        t.setItem(r, 0, make_check_item(False))
        t.setItem(r, 1, QTableWidgetItem(os.path.basename(pa) if pa else ""))
        s=QTableWidgetItem(human_size(sz)); s.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        t.setItem(r, 2, s); t.setItem(r, 3, QTableWidgetItem(pa))

    def _about(self):
        MessageBox("关于",
            "C盘强力清理工具 v0.1.0\n"
            "UI：Fluent Widgets\nby Kio",self).exec()

# ══════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════
def relaunch_as_admin():
    """以管理员权限重新启动自身"""
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

    # 自动提权：非管理员时弹 UAC 提升
    if not is_admin():
        relaunch_as_admin()

    app = QApplication(sys.argv)
    setFontFamilies(["微软雅黑"])
    setTheme(Theme.AUTO); setThemeColor("#0078d4")
    w = MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": main()
