import os
import sys
import time
import ctypes
import fnmatch
import queue
import shutil
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

# =========================
#  Windows Recycle Bin API
# =========================
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
FOF_NOERRORUI = 0x0400


class SHFILEOPSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def send_to_recycle_bin(path: str) -> bool:
    from_buf = path + "\0\0"
    op = SHFILEOPSTRUCT()
    op.hwnd = None
    op.wFunc = 0x0003  # FO_DELETE
    op.pFrom = from_buf
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = 0
    op.hNameMappings = None
    op.lpszProgressTitle = None
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return res == 0 and op.fAnyOperationsAborted == 0


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def relaunch_as_admin():
    """以管理员权限重新启动当前脚本"""
    params = " ".join([f'"{arg}"' for arg in sys.argv])
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1
    )


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for u in units:
        if size < 1024.0 or u == units[-1]:
            return f"{size:.2f} {u}"
        size /= 1024.0
    return f"{num_bytes} B"


def safe_getsize(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def dir_size(path: str) -> int:
    total = 0
    for root, dirs, files in os.walk(path, topdown=True):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for f in files:
            total += safe_getsize(os.path.join(root, f))
    return total


def delete_path(path: str, permanent: bool, log_fn) -> bool:
    try:
        if not os.path.exists(path):
            return True

        if not permanent:
            ok = send_to_recycle_bin(path)
            if ok:
                log_fn(f"[回收站] {path}")
                return True
            log_fn(f"[回收站失败，尝试永久删除] {path}")

        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
        else:
            shutil.rmtree(path, ignore_errors=False)

        log_fn(f"[永久删除] {path}")
        return True
    except Exception as e:
        log_fn(f"[失败] {path} -> {e}")
        return False


def expand_env(p: str) -> str:
    return os.path.expandvars(p)


def default_clean_targets():
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    localapp = os.environ.get("LOCALAPPDATA", "")

    return [
        ("用户临时文件", expand_env(r"%TEMP%"), "dir", True, "常见垃圾，安全"),
        ("系统临时文件", os.path.join(system_root, "Temp"), "dir", True, "可能需要管理员权限"),
        ("Windows 预取 Prefetch", os.path.join(system_root, "Prefetch"), "dir", False, "可能影响首次启动速度，默认关闭"),
        ("Windows 日志 CBS", os.path.join(system_root, "Logs", "CBS"), "dir", True, "日志较大，较安全"),
        ("Windows DISM 日志", os.path.join(system_root, "Logs", "DISM"), "dir", True, "较安全"),
        ("WER 报告(用户)", os.path.join(localapp, "Microsoft", "Windows", "WER"), "dir", True, "崩溃报告缓存"),
        ("WER 报告(系统)",
         os.path.join(system_root, "System32", "config", "systemprofile",
                      "AppData", "Local", "Microsoft", "Windows", "WER"),
         "dir", False, "可能需要管理员权限"),
        ("崩溃转储 Minidump", os.path.join(system_root, "Minidump"), "dir", True, "蓝屏/崩溃小转储"),
        ("内存转储 MEMORY.DMP", os.path.join(system_root, "MEMORY.DMP"), "file", False, "很大；仅在你确认不需要调试时勾选"),
        ("缩略图缓存(Explorer)", os.path.join(localapp, "Microsoft", "Windows", "Explorer"), "glob", True, "thumbcache*.db"),
        ("DirectX Shader Cache", os.path.join(localapp, "D3DSCache"), "dir", True, "游戏/图形缓存，安全"),
        ("NVIDIA Shader Cache(新)", os.path.join(localapp, "NVIDIA", "DXCache"), "dir", True, "较安全"),
        ("NVIDIA Shader Cache(旧)", os.path.join(localapp, "NVIDIA", "GLCache"), "dir", True, "较安全"),
        ("Edge 缓存", os.path.join(localapp, "Microsoft", "Edge", "User Data", "Default", "Cache"), "dir", False, "可能登出/变慢，默认关闭"),
        ("Chrome 缓存", os.path.join(localapp, "Google", "Chrome", "User Data", "Default", "Cache"), "dir", False, "可能登出/变慢，默认关闭"),
        ("系统更新下载缓存(SoftwareDistribution\\Download)", os.path.join(system_root, "SoftwareDistribution", "Download"), "dir", False, "可能影响更新；建议先暂停更新再清"),
        ("Delivery Optimization 缓存", os.path.join(system_root, "SoftwareDistribution", "DeliveryOptimization"), "dir", False, "可能需要管理员权限"),
    ]


# =========================
#  Big file scanning
# =========================
DEFAULT_EXCLUDES = [
    r"C:\Windows\WinSxS",
    r"C:\Windows\Installer",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive",
]
BIGFILE_SKIP_EXT = {".sys"}


def should_exclude(path: str, exclude_prefixes: list[str]) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    for ex in exclude_prefixes:
        exn = os.path.normcase(os.path.abspath(ex))
        if p.startswith(exn):
            return True
    return False


def scan_big_files(root: str, min_bytes: int, exclude_prefixes: list[str], stop_flag, progress_cb):
    results = []
    scanned = 0
    last_tick = time.time()

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        if stop_flag.is_set():
            break

        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]

        if should_exclude(dirpath, exclude_prefixes):
            dirnames[:] = []
            continue

        for f in filenames:
            if stop_flag.is_set():
                break

            fp = os.path.join(dirpath, f)
            ext = os.path.splitext(f)[1].lower()
            if ext in BIGFILE_SKIP_EXT:
                continue

            try:
                st = os.stat(fp)
                scanned += 1
                if st.st_size >= min_bytes:
                    results.append((st.st_size, fp))
            except Exception:
                pass

            now = time.time()
            if now - last_tick >= 0.2:
                progress_cb(scanned)
                last_tick = now

    results.sort(reverse=True, key=lambda x: x[0])
    return results


# =========================
#  GUI
# =========================
class CleanerApp(tk.Tk):
    SASH_RATIO = 0.55  # 常规 55% / 大文件 45%

    def __init__(self):
        super().__init__()
        self.title("C盘强力清理工具")
        self.geometry("1180x820")
        self.minsize(980, 640)

        self.msg_q = queue.Queue()
        self.stop_flag = threading.Event()
        self.targets = default_clean_targets()

        # 默认值
        self.permanent_var = tk.BooleanVar(value=True)
        self.make_restore_var = tk.BooleanVar(value=False)

        self.big_scan_var = tk.BooleanVar(value=True)
        self.big_threshold_mb = tk.IntVar(value=500)
        self.big_max_results = tk.IntVar(value=200)

        self.big_collapsed = tk.BooleanVar(value=False)  # 默认展开
        self.popup_on_done = False

        # UI refs
        self.target_tree = None
        self.big_tree = None
        self.big_toggle_btn = None
        self.progress = None
        self.status_text = None
        self.log = None

        # Layout refs
        self.main_lf = None
        self.paned = None
        self.reg_pane = None
        self.big_pane = None

        self.style = ttk.Style()
        self._apply_theme_defaults()

        # 防抖 resize
        self._resize_after_id = None
        self._last_paned_h = None
        self._last_big_w = None

        self._build_ui()
        self._poll_queue()

        self.after_idle(self._bootstrap_layout)
        self.bind("<Configure>", self._on_window_configure)

    # ---------- Theme ----------
    def _apply_theme_defaults(self):
        themes = self.style.theme_names()
        prefer = ["vista", "xpnative", "clam"]
        chosen = None
        for t in prefer:
            if t in themes:
                chosen = t
                break
        if not chosen:
            chosen = themes[0] if themes else "clam"
        try:
            self.style.theme_use(chosen)
        except Exception:
            pass

        self.style.configure(".", font=("Microsoft YaHei UI", 10))
        self.style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        self.style.configure("Treeview", rowheight=28)
        self.style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    # ---------- Layout ----------
    def _set_sash_ratio(self):
        if not self.paned or self.big_collapsed.get():
            return
        self.update_idletasks()
        h = self.paned.winfo_height()
        if not h or h <= 120:
            return
        top_h = int(h * self.SASH_RATIO)
        try:
            self.paned.sashpos(0, top_h)
        except Exception:
            pass

    def _bootstrap_layout(self):
        self._set_sash_ratio()
        self._fix_big_columns_width()
        self.after(120, lambda: (self._set_sash_ratio(), self._fix_big_columns_width()))
        self.after(260, lambda: (self._set_sash_ratio(), self._fix_big_columns_width()))

    def _on_window_configure(self, event):
        if self._resize_after_id:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(120, self._refresh_layout_on_resize)

    def _refresh_layout_on_resize(self):
        self._resize_after_id = None
        if not self.paned:
            return

        try:
            paned_h = self.paned.winfo_height()
        except Exception:
            paned_h = None

        try:
            big_w = self.big_tree.winfo_width() if self.big_tree else None
        except Exception:
            big_w = None

        if paned_h and paned_h != self._last_paned_h:
            self._last_paned_h = paned_h
            self._set_sash_ratio()

        if big_w and big_w != self._last_big_w:
            self._last_big_w = big_w
            self._fix_big_columns_width()

    def _fix_big_columns_width(self):
        """✅ 四列：✓ | 文件名 | 大小 | 路径。固定：✓/大小；文件名适中；路径吃满剩余。"""
        if not self.big_tree:
            return

        cols = ("pick", "fname", "size", "path")
        try:
            self.big_tree.configure(columns=cols, displaycolumns=cols, show="headings")
        except Exception:
            return

        self.big_tree.heading("pick", text="✓")
        self.big_tree.heading("fname", text="文件名")
        self.big_tree.heading("size", text="大小")
        self.big_tree.heading("path", text="路径")

        self.big_tree.column("pick", width=54, minwidth=54, anchor="center", stretch=False)
        self.big_tree.column("size", width=120, minwidth=100, anchor="e", stretch=False)

        # 文件名给一个稳定宽度（不让它挤掉路径）
        self.big_tree.column("fname", width=220, minwidth=160, anchor="w", stretch=False)

        self.update_idletasks()
        total_w = self.big_tree.winfo_width()
        if not total_w or total_w <= 240:
            total_w = 980

        padding = 38
        path_w = max(260, total_w - 54 - 220 - 120 - padding)
        self.big_tree.column("path", width=path_w, minwidth=200, anchor="w", stretch=True)

    # ---------- Explorer helpers ----------
    def _normalize_path_for_open(self, text: str) -> str:
        """
        ✅ 修复 C:\\ 这类导致 explorer 不认的情况：
        - 去引号/空格
        - expandvars
        - 统一斜杠
        - normpath（会把 C:\\\\Users 归一为 C:\\Users）
        """
        if not text:
            return ""
        p = text.split(" |", 1)[0].strip()
        p = p.strip().strip('"').strip("'")
        p = expand_env(p)
        p = p.replace("/", "\\")
        try:
            p = os.path.normpath(p)
        except Exception:
            pass
        return p

    def _open_in_explorer(self, path: str):
        p = self._normalize_path_for_open(path)
        if not p:
            return
        try:
            # ✅ 文件：直接选中（更符合“跳转到资源管理器”）
            if os.path.isfile(p):
                subprocess.Popen(["explorer", "/select,", p])
                return

            # ✅ 目录：直接打开
            if os.path.isdir(p):
                subprocess.Popen(["explorer", p])
                return

            # 不存在就尽量打开父目录
            parent = os.path.dirname(p)
            if parent and os.path.isdir(parent):
                subprocess.Popen(["explorer", parent])
            else:
                subprocess.Popen(["explorer", p])
        except Exception as e:
            self.msg_q.put(("log", f"[打开失败] {e}"))

    def _select_in_explorer(self, path: str):
        p = self._normalize_path_for_open(path)
        if not p:
            return
        try:
            subprocess.Popen(["explorer", "/select,", p])
        except Exception as e:
            self.msg_q.put(("log", f"[资源管理器定位失败] {e}"))

    # ---------- Clipboard / Context menu ----------
    def _copy_to_clipboard(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self.msg_q.put(("log", f"[剪贴板] 已复制：{text}"))
        except Exception as e:
            self.msg_q.put(("log", f"[剪贴板失败] {e}"))

    def _show_context_menu(self, event, tree: ttk.Treeview, path_col: str):
        iid = tree.identify_row(event.y)
        if not iid:
            return
        tree.selection_set(iid)

        menu = tk.Menu(self, tearoff=0)
        raw = tree.set(iid, path_col) or ""
        norm = self._normalize_path_for_open(raw)

        exists = False
        try:
            exists = bool(norm) and os.path.exists(norm)
        except Exception:
            exists = False

        if raw:
            menu.add_command(label="复制路径到剪贴板", command=lambda: self._copy_to_clipboard(raw))
        else:
            menu.add_command(label="复制路径到剪贴板", state="disabled")

        menu.add_separator()

        # ✅ 新增：打开文件（存在且是文件才启用）
        if exists and os.path.isfile(norm):
            menu.add_command(label="打开文件", command=lambda: subprocess.Popen(["explorer", norm]))
        else:
            menu.add_command(label="打开文件", state="disabled")

        # ✅ “打开所在文件夹”改成：文件就选中，目录就打开
        if exists:
            menu.add_command(label="打开所在位置", command=lambda: self._open_in_explorer(norm))
            menu.add_command(label="在资源管理器中选中", command=lambda: self._select_in_explorer(norm))
        else:
            menu.add_command(label="打开所在位置", state="disabled")
            menu.add_command(label="在资源管理器中选中", state="disabled")

        menu.tk_popup(event.x_root, event.y_root)

    # ---------- UI ----------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="🧹 C盘强力清理工具", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        badge = "管理员" if is_admin() else "非管理员"
        ttk.Label(header, text=f"当前权限：{badge} | 建议管理员运行以使用更完整功能").grid(row=1, column=0, sticky="w", pady=(4, 0))

        opts = ttk.LabelFrame(self, text="模式与选项")
        opts.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        opts.columnconfigure(9, weight=1)

        ttk.Checkbutton(opts, text="强力模式：永久删除（不进回收站）", variable=self.permanent_var)\
            .grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Checkbutton(opts, text="清理前创建系统还原点（需要管理员且系统保护已开启）", variable=self.make_restore_var)\
            .grid(row=0, column=1, sticky="w", padx=10, pady=6)

        bigopts = ttk.LabelFrame(self, text="大文件扫描")
        bigopts.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        bigopts.columnconfigure(9, weight=1)

        ttk.Checkbutton(bigopts, text="扫描C盘大文件（可勾选删除）", variable=self.big_scan_var)\
            .grid(row=0, column=0, sticky="w", padx=10, pady=6)

        ttk.Label(bigopts, text="阈值(MB)：").grid(row=0, column=1, sticky="e", padx=(10, 4), pady=6)
        ttk.Spinbox(bigopts, from_=50, to=10240, textvariable=self.big_threshold_mb, width=8)\
            .grid(row=0, column=2, sticky="w", padx=(4, 10), pady=6)

        ttk.Label(bigopts, text="条目上限：").grid(row=0, column=3, sticky="e", padx=(10, 4), pady=6)
        ttk.Spinbox(bigopts, from_=50, to=2000, textvariable=self.big_max_results, width=8)\
            .grid(row=0, column=4, sticky="w", padx=(4, 10), pady=6)

        self.big_toggle_btn = ttk.Button(
            bigopts,
            text="▼ 收起大文件列表" if not self.big_collapsed.get() else "▶ 展开大文件列表",
            command=self.toggle_big_section
        )
        self.big_toggle_btn.grid(row=0, column=5, sticky="w", padx=(10, 8), pady=6)

        ttk.Button(bigopts, text="🔎 扫描大文件", command=self.start_big_scan).grid(row=0, column=6, sticky="w", padx=6, pady=6)
        ttk.Button(bigopts, text="⇩ 按大小排序", command=self._sort_big).grid(row=0, column=7, sticky="w", padx=6, pady=6)

        self.main_lf = ttk.LabelFrame(self, text="常规清理项（先扫描/估算大小，再勾选执行）")
        self.main_lf.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.main_lf.columnconfigure(0, weight=1)
        self.main_lf.rowconfigure(0, weight=1)

        self.paned = ttk.Panedwindow(self.main_lf, orient="vertical")
        self.paned.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # Regular pane
        self.reg_pane = ttk.Frame(self.paned)
        self.reg_pane.columnconfigure(0, weight=1)
        self.reg_pane.rowconfigure(0, weight=1)

        reg_wrap = ttk.Frame(self.reg_pane)
        reg_wrap.grid(row=0, column=0, sticky="nsew")
        reg_wrap.columnconfigure(0, weight=1)
        reg_wrap.rowconfigure(0, weight=1)

        self.target_tree = ttk.Treeview(reg_wrap, columns=("enabled", "name", "path", "note", "est"), show="headings")
        for col, text in [("enabled", "✓"), ("name", "项目"), ("path", "路径/规则"), ("note", "说明"), ("est", "可清理大小")]:
            self.target_tree.heading(col, text=text)

        self.target_tree.column("enabled", width=54, anchor="center")
        self.target_tree.column("name", width=170)
        self.target_tree.column("path", width=640)
        self.target_tree.column("note", width=220)
        self.target_tree.column("est", width=140, anchor="e")

        xscroll1 = ttk.Scrollbar(reg_wrap, orient="horizontal", command=self.target_tree.xview)
        yscroll1 = ttk.Scrollbar(reg_wrap, orient="vertical", command=self.target_tree.yview)
        self.target_tree.configure(xscrollcommand=xscroll1.set, yscrollcommand=yscroll1.set)

        self.target_tree.grid(row=0, column=0, sticky="nsew")
        yscroll1.grid(row=0, column=1, sticky="ns")
        xscroll1.grid(row=1, column=0, sticky="ew")

        self.target_tree.bind("<Double-1>", self._toggle_target)
        self.target_tree.bind("<Button-3>", lambda e: self._show_context_menu(e, self.target_tree, "path"))

        btns = ttk.Frame(self.reg_pane)
        btns.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="📏 扫描/估算大小", command=self.start_estimate).pack(side="left")
        ttk.Button(btns, text="✅ 全选(安全项为主)", command=self.select_safe_defaults).pack(side="left", padx=8)
        ttk.Button(btns, text="🧼 全不选", command=self.unselect_all).pack(side="left")

        # Big pane
        self.big_pane = ttk.Frame(self.paned)
        self.big_pane.columnconfigure(0, weight=1)
        self.big_pane.rowconfigure(1, weight=1)

        big_header = ttk.Frame(self.big_pane)
        big_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        big_header.columnconfigure(0, weight=1)
        ttk.Label(big_header, text="大文件列表（右键：复制/打开/定位；双击勾选）").grid(row=0, column=0, sticky="w")

        big_wrap = ttk.Frame(self.big_pane)
        big_wrap.grid(row=1, column=0, sticky="nsew")
        big_wrap.columnconfigure(0, weight=1)
        big_wrap.rowconfigure(0, weight=1)

        # ✅ 新增 fname 列：✓ | 文件名 | 大小 | 路径
        self.big_tree = ttk.Treeview(big_wrap, columns=("pick", "fname", "size", "path"), show="headings")
        self.big_tree.heading("pick", text="✓")
        self.big_tree.heading("fname", text="文件名")
        self.big_tree.heading("size", text="大小")
        self.big_tree.heading("path", text="路径")

        self.big_tree.column("pick", width=54, anchor="center")
        self.big_tree.column("fname", width=220, anchor="w")
        self.big_tree.column("size", width=120, anchor="e")
        self.big_tree.column("path", width=760, anchor="w")

        xscroll2 = ttk.Scrollbar(big_wrap, orient="horizontal", command=self.big_tree.xview)
        yscroll2 = ttk.Scrollbar(big_wrap, orient="vertical", command=self.big_tree.yview)
        self.big_tree.configure(xscrollcommand=xscroll2.set, yscrollcommand=yscroll2.set)

        self.big_tree.grid(row=0, column=0, sticky="nsew")
        yscroll2.grid(row=0, column=1, sticky="ns")
        xscroll2.grid(row=1, column=0, sticky="ew")

        self.big_tree.bind("<Double-1>", self._toggle_big)
        self.big_tree.bind("<Button-3>", lambda e: self._show_context_menu(e, self.big_tree, "path"))

        self.paned.add(self.reg_pane, weight=3)
        if not self.big_collapsed.get():
            self.paned.add(self.big_pane, weight=2)

        try:
            self.paned.paneconfigure(self.reg_pane, minsize=240)
            self.paned.paneconfigure(self.big_pane, minsize=220)
        except Exception:
            pass

        # Bottom
        bottom = ttk.Frame(self)
        bottom.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        bottom.columnconfigure(0, weight=1)

        status = ttk.Frame(bottom)
        status.grid(row=0, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=0)

        self.progress = ttk.Progressbar(status, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=6)

        self.status_text = ttk.Label(status, text="就绪", anchor="w")
        self.status_text.grid(row=0, column=1, sticky="w", pady=6)

        actions = ttk.Frame(bottom)
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(actions, text="🚀 开始清理", command=self.start_clean).pack(side="left")
        ttk.Button(actions, text="⛔ 停止/取消", command=self.stop_work).pack(side="left", padx=10)

        self.log = tk.Text(bottom, height=6, padx=10, pady=8, wrap="none")
        self.log.grid(row=2, column=0, sticky="ew")
        self.log.configure(font=("Consolas", 10))

        for i, t in enumerate(self.targets):
            name, path, typ, enabled, note = t
            enabled_txt = "✅" if enabled else ""
            shown_path = path if typ != "glob" else f"{path} | thumbcache*.db"
            self.target_tree.insert("", "end", iid=str(i), values=(enabled_txt, name, shown_path, note, ""))

        self.select_safe_defaults()

    def toggle_big_section(self):
        self.big_collapsed.set(not self.big_collapsed.get())
        panes = list(self.paned.panes())

        if self.big_collapsed.get():
            if str(self.big_pane) in panes:
                try:
                    self.paned.forget(self.big_pane)
                except Exception:
                    pass
            self.big_toggle_btn.config(text="▶ 展开大文件列表")
        else:
            if str(self.big_pane) not in panes:
                try:
                    self.paned.add(self.big_pane, weight=2)
                except Exception:
                    pass
            self.big_toggle_btn.config(text="▼ 收起大文件列表")
            self.after_idle(self._bootstrap_layout)

    # ---------- Tree toggles ----------
    def _toggle_target(self, event):
        item = self.target_tree.identify_row(event.y)
        if not item:
            return
        i = int(item)
        name, path, typ, enabled, note = self.targets[i]
        enabled = not enabled
        self.targets[i] = (name, path, typ, enabled, note)
        self.target_tree.set(item, "enabled", "✅" if enabled else "")

    def _toggle_big(self, event):
        item = self.big_tree.identify_row(event.y)
        if not item:
            return
        cur = self.big_tree.set(item, "pick")
        self.big_tree.set(item, "pick", "" if cur == "✅" else "✅")

    def _sort_big(self):
        items = list(self.big_tree.get_children(""))
        if not items:
            return

        def key(iid):
            s = self.big_tree.set(iid, "size")
            parts = s.split()
            if len(parts) != 2:
                return 0
            val = float(parts[0])
            unit = parts[1]
            mul = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}.get(unit, 1)
            return int(val * mul)

        items.sort(key=key, reverse=True)
        for idx, iid in enumerate(items):
            self.big_tree.move(iid, "", idx)

    # ---------- Log / queue ----------
    def _ts(self) -> str:
        return time.strftime("%H:%M:%S")

    def log_line(self, s: str):
        self.log.insert("end", f"[{self._ts()}] {s}\n")
        self.log.see("end")

    def _set_status(self, text: str):
        if self.status_text:
            self.status_text.config(text=text)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()

                if kind == "log":
                    self.log_line(payload)
                    self._set_status(payload[:60])

                elif kind == "prog":
                    val, mx = payload
                    self.progress["maximum"] = mx
                    self.progress["value"] = val

                elif kind == "target_est":
                    idx, est_bytes = payload
                    self.target_tree.set(str(idx), "est", human_size(est_bytes))

                elif kind == "big_clear":
                    for iid in self.big_tree.get_children(""):
                        self.big_tree.delete(iid)

                elif kind == "big_add":
                    size, path = payload
                    fname = os.path.basename(path) if path else ""
                    iid = f"b{len(self.big_tree.get_children(''))}"
                    self.big_tree.insert("", "end", iid=iid, values=("", fname, human_size(size), path))

                elif kind == "big_show":
                    if self.big_collapsed.get():
                        self.big_collapsed.set(False)
                        self.toggle_big_section()
                    self.after_idle(self._bootstrap_layout)

                elif kind == "done":
                    self.progress["value"] = 0
                    self._set_status("完成")
                    self.log_line(f"[完成] {payload}")
                    if self.popup_on_done:
                        msg = payload
                        self.after(80, lambda m=msg: messagebox.showinfo("完成", m))

        except queue.Empty:
            pass

        self.after(120, self._poll_queue)

    def stop_work(self):
        self.stop_flag.set()
        self.msg_q.put(("log", "[用户请求] 停止/取消中..."))

    # ---------- Estimate ----------
    def start_estimate(self):
        self.stop_flag.clear()
        threading.Thread(target=self._estimate_worker, daemon=True).start()

    def _estimate_worker(self):
        self.msg_q.put(("log", "开始扫描/估算..."))
        enabled_targets = [(i, t) for i, t in enumerate(self.targets) if t[3]]
        if not enabled_targets:
            self.msg_q.put(("done", "没有勾选任何常规清理项"))
            return

        self.msg_q.put(("prog", (0, max(1, len(enabled_targets)))))
        for n, (idx, t) in enumerate(enabled_targets, start=1):
            if self.stop_flag.is_set():
                self.msg_q.put(("done", "已取消估算"))
                return

            name, path, typ, enabled, note = t
            est = 0
            try:
                if typ == "dir":
                    p = expand_env(path)
                    if os.path.isdir(p):
                        est = dir_size(p)
                elif typ == "glob":
                    folder = expand_env(path)
                    pattern = "thumbcache*.db"
                    if os.path.isdir(folder):
                        for f in os.listdir(folder):
                            if fnmatch.fnmatch(f.lower(), pattern.lower()):
                                est += safe_getsize(os.path.join(folder, f))
                elif typ == "file":
                    p = expand_env(path)
                    if os.path.isfile(p):
                        est = safe_getsize(p)
            except Exception:
                est = 0

            self.msg_q.put(("target_est", (idx, est)))
            self.msg_q.put(("prog", (n, len(enabled_targets))))
            self.msg_q.put(("log", f"[估算] {name} -> {human_size(est)}"))

        self.msg_q.put(("done", "估算完成"))

    # ---------- Big scan ----------
    def start_big_scan(self):
        if not self.big_scan_var.get():
            messagebox.showinfo("提示", "你已关闭“大文件扫描”选项")
            return

        if self.big_collapsed.get():
            self.big_collapsed.set(False)
            self.toggle_big_section()

        self.stop_flag.clear()
        threading.Thread(target=self._big_scan_worker, daemon=True).start()

    def _big_scan_worker(self):
        min_mb = int(self.big_threshold_mb.get())
        min_bytes = min_mb * 1024 * 1024
        max_results = int(self.big_max_results.get())

        self.msg_q.put(("log", f"开始扫描 C:\\ 大文件（阈值 {min_mb}MB，条数上限 {max_results} 条）..."))
        self.msg_q.put(("big_clear", None))

        def prog_cb(scanned):
            self.msg_q.put(("prog", (scanned % 100, 100)))

        # ✅ root 用标准形式 "C:\\"（避免出现 C:\\\\ 之类路径传递到 explorer 的边界问题）
        results = scan_big_files(
            root="C:\\",
            min_bytes=min_bytes,
            exclude_prefixes=DEFAULT_EXCLUDES,
            stop_flag=self.stop_flag,
            progress_cb=prog_cb
        )

        if self.stop_flag.is_set():
            self.msg_q.put(("done", "已取消大文件扫描"))
            return

        results = results[:max_results]
        for size, path in results:
            self.msg_q.put(("big_add", (size, path)))

        self.msg_q.put(("big_show", None))
        self.msg_q.put(("done", f"大文件扫描完成，共命中 {len(results)} 条"))

    # ---------- Select helpers ----------
    def select_safe_defaults(self):
        for i, t in enumerate(self.targets):
            self.target_tree.set(str(i), "enabled", "✅" if t[3] else "")

    def unselect_all(self):
        for i, t in enumerate(self.targets):
            name, path, typ, enabled, note = t
            self.targets[i] = (name, path, typ, False, note)
            self.target_tree.set(str(i), "enabled", "")

    # ---------- Restore point ----------
    def _try_restore_point(self):
        if not self.make_restore_var.get():
            return True
        if not is_admin():
            self.msg_q.put(("log", "[还原点] 需要管理员权限，已跳过"))
            return False
        try:
            cmd = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "Checkpoint-Computer", "-Description", "C盘清理工具自动还原点", "-RestorePointType", "MODIFY_SETTINGS"
            ]
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0:
                self.msg_q.put(("log", "[还原点] 创建成功"))
                return True
            self.msg_q.put(("log", f"[还原点] 创建失败：{p.stderr.strip() or p.stdout.strip()}"))
            return False
        except Exception as e:
            self.msg_q.put(("log", f"[还原点] 创建异常：{e}"))
            return False

    # ---------- Clean ----------
    def start_clean(self):
        if self.permanent_var.get():
            ok = messagebox.askyesno("确认", "当前为【强力模式】\n删除将不会进入回收站，可能无法恢复\n\n确认继续？")
            if not ok:
                self.msg_q.put(("log", "[取消] 用户取消强力模式清理"))
                return

        self.stop_flag.clear()
        threading.Thread(target=self._clean_worker, daemon=True).start()

    def _clean_worker(self):
        permanent = bool(self.permanent_var.get())
        if permanent:
            self.msg_q.put(("log", "⚠️强力模式已开启：将永久删除，不进入回收站"))

        self._try_restore_point()

        selected = [(name, path, typ) for (name, path, typ, enabled, note) in self.targets if enabled]
        big_selected = []
        for iid in self.big_tree.get_children(""):
            if self.big_tree.set(iid, "pick") == "✅":
                big_selected.append(self.big_tree.set(iid, "path"))

        if not selected and not big_selected:
            self.msg_q.put(("done", "你没有勾选任何要清理的项目（常规项/大文件）"))
            return

        total_steps = len(selected) + len(big_selected)
        self.msg_q.put(("prog", (0, max(1, total_steps))))

        ok_count = 0
        fail_count = 0
        step = 0

        for name, path, typ in selected:
            if self.stop_flag.is_set():
                self.msg_q.put(("done", "已取消清理"))
                return

            step += 1
            self.msg_q.put(("log", f"开始：{name}"))
            p = expand_env(path)

            try:
                if typ == "dir":
                    if os.path.isdir(p):
                        for entry in os.listdir(p):
                            if self.stop_flag.is_set():
                                break
                            ep = os.path.join(p, entry)
                            if delete_path(ep, permanent=permanent, log_fn=lambda s: self.msg_q.put(("log", s))):
                                ok_count += 1
                            else:
                                fail_count += 1
                    else:
                        self.msg_q.put(("log", f"[跳过] 不存在：{p}"))

                elif typ == "glob":
                    folder = p
                    pattern = "thumbcache*.db"
                    if os.path.isdir(folder):
                        for f in os.listdir(folder):
                            if self.stop_flag.is_set():
                                break
                            if fnmatch.fnmatch(f.lower(), pattern.lower()):
                                fp = os.path.join(folder, f)
                                if delete_path(fp, permanent=permanent, log_fn=lambda s: self.msg_q.put(("log", s))):
                                    ok_count += 1
                                else:
                                    fail_count += 1
                    else:
                        self.msg_q.put(("log", f"[跳过] 不存在：{folder}"))

                elif typ == "file":
                    if os.path.exists(p):
                        if delete_path(p, permanent=permanent, log_fn=lambda s: self.msg_q.put(("log", s))):
                            ok_count += 1
                        else:
                            fail_count += 1
                    else:
                        self.msg_q.put(("log", f"[跳过] 不存在：{p}"))

            except Exception as e:
                self.msg_q.put(("log", f"[失败] {name} -> {e}"))
                fail_count += 1

            self.msg_q.put(("prog", (step, total_steps)))

        for p in big_selected:
            if self.stop_flag.is_set():
                self.msg_q.put(("done", "已取消清理"))
                return
            step += 1
            self.msg_q.put(("log", f"[大文件] 删除：{p}"))
            if delete_path(p, permanent=permanent, log_fn=lambda s: self.msg_q.put(("log", s))):
                ok_count += 1
            else:
                fail_count += 1
            self.msg_q.put(("prog", (step, total_steps)))

        self.msg_q.put(("done", f"清理完成：成功 {ok_count} 项，失败 {fail_count} 项建议重启一次以释放占用"))


def main():
    if sys.platform != "win32":
        print("本工具仅支持 Windows")
        sys.exit(1)

    # ✅ 如果不是管理员，则自动请求管理员权限
    if not is_admin():
        relaunch_as_admin()
        sys.exit(0)

    app = CleanerApp()
    app.mainloop()



if __name__ == "__main__":
    if sys.platform != "win32":
        print("本工具仅支持 Windows")
        sys.exit(1)
    main()
