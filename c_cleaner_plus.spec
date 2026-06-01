# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# PyInstaller 执行 spec 时不保证存在 __file__。
# SPECPATH 是 PyInstaller 注入的 spec 所在目录。
base_dir = os.path.abspath(SPECPATH)

qfw_datas = collect_data_files("qfluentwidgets", include_py_files=False)
qfw_hidden = collect_submodules(
    "qfluentwidgets",
    filter=lambda name: not name.startswith("qfluentwidgets.multimedia"),
)

datas = [item for item in qfw_datas if item and len(item) == 2]
binaries = []


def add_data_if_exists(path, dest="."):
    full_path = os.path.join(base_dir, path)
    if os.path.exists(full_path):
        datas.append((full_path, dest))


def add_binary_if_exists(path, dest="."):
    full_path = os.path.join(base_dir, path)
    if os.path.exists(full_path):
        binaries.append((full_path, dest))
        return True
    return False


add_data_if_exists("app.ico", ".")
add_data_if_exists("icon.ico", ".")
add_data_if_exists("i18n", "i18n")
add_data_if_exists("config", "config")
add_data_if_exists("configs", "configs")
add_data_if_exists("config_store.json", ".")
add_data_if_exists("common_custom_rules.json", ".")
add_data_if_exists("rules_cn_apps.json", ".")
add_data_if_exists("rules_dev_tools.json", ".")
add_data_if_exists("rules_game_platforms.json", ".")

fast_mft_found = False
for helper_path in (
    os.path.join("tools", "fast_large_files", "target", "release", "fast_large_files.exe"),
    os.path.join("tools", "fast_large_files", "target", "debug", "fast_large_files.exe"),
    "fast_large_files.exe",
):
    if add_binary_if_exists(helper_path, "."):
        fast_mft_found = True
        break

if fast_mft_found:
    print("[MFT] fast_large_files.exe 已加入打包")
else:
    print("[MFT] 未找到 fast_large_files.exe，将使用普通扫描回退路径")

a = Analysis(
    ["main.py"],
    pathex=[base_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=qfw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQml",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtHelp",
        "PySide6.QtDesigner",
        "PySide6.QtConcurrent",
        "PySide6.QtNetworkAuth",
        "PySide6.QtDBus",
        "PySide6.QtHttpServer",
        "PySide6.QtSpatialAudio",
        "numpy",
        "scipy",
        "PIL",
        "Pillow",
        "colorthief",
        "matplotlib",
        "pandas",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "sklearn",
        "skimage",
        "nltk",
        "IPython",
        "ipykernel",
        "jupyter",
        "jupyter_client",
        "jupyter_core",
        "nbclient",
        "nbconvert",
        "nbformat",
        "notebook",
        "sympy",
        "traitlets",
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exclude_keywords = [
    "opengl32sw",
    "d3dcompiler",
    "Qt6Quick",
    "Qt6Qml",
    "Qt6Multimedia",
    "Qt6WebEngine",
    "Qt63D",
    "Qt6Pdf",
    "Qt6Charts",
    "Qt6DataVis",
    "Qt6Bluetooth",
    "Qt6Sensors",
    "Qt6Serial",
    "Qt6Remote",
    "Qt6Help",
    "Qt6Designer",
    "Qt6Test",
    "Qt6Spatial",
    "Qt6HttpServer",
    "Qt6OpenGL",
    "QtOpenGL",
]


def should_keep(name, src):
    combined = (str(name) + "|" + str(src)).lower()
    return not any(keyword.lower() in combined for keyword in exclude_keywords)


before_b = len(a.binaries)
before_d = len(a.datas)

a.binaries = [b for b in a.binaries if should_keep(b[0], b[1])]
a.datas = [
    d for d in a.datas
    if should_keep(d[0], d[1])
    and not d[0].lower().startswith(("qml/", "qml\\"))
    and not d[0].lower().startswith(("translations/", "translations\\"))
    and not d[0].lower().startswith(("pyside6/translations", "pyside6\\translations"))
]

print(f"[过滤] binaries: {before_b} -> {len(a.binaries)}")
print(f"[过滤] datas: {before_d} -> {len(a.datas)}")

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="c_cleaner_plus",
    icon=os.path.join(base_dir, "app.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        "python3.dll",
        "python311.dll",
        "python312.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "ucrtbase.dll",
        "shiboken6.dll",
        "shiboken6.abi3.dll",
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
        "qwindows.dll",
        "fast_large_files.exe",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
