# -*- mode: python ; coding: utf-8 -*-
# C盘清理工具.spec
import os, sys, re
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 收集 qfluentwidgets 的所有子模块和资源
qfw_datas = collect_data_files('qfluentwidgets', include_py_files=False)
qfw_hidden = collect_submodules('qfluentwidgets')

# 过滤掉空元素，再加上自己的资源
my_datas = [item for item in qfw_datas if item and len(item) == 2]
my_datas.append(('icon.ico', '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=my_datas,
    hiddenimports=qfw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
    optimize=2,
    excludes=[
        'PySide6.QtWebEngine', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
        'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQuickWidgets',
        'PySide6.QtQml', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
        'PySide6.QtLocation', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
        'PySide6.QtRemoteObjects', 'PySide6.QtSql', 'PySide6.QtTest',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtCharts',
        'PySide6.QtDataVisualization', 'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtHelp', 'PySide6.QtDesigner',
        'PySide6.QtConcurrent', 'PySide6.QtNetworkAuth', 'PySide6.QtDBus',
        'PySide6.QtHttpServer', 'PySide6.QtSpatialAudio',
    ],
)

pyz = PYZ(a.pure)

# ══════ 过滤不需要的大体积 DLL 和资源 ══════
exclude_keywords = [
    'opengl32sw', 'd3dcompiler',
    'Qt6Quick', 'Qt6Qml', 'Qt6Multimedia',
    'Qt6WebEngine', 'Qt63D', 'Qt6Pdf',
    'Qt6Charts', 'Qt6DataVis', 'Qt6Bluetooth',
    'Qt6Sensors', 'Qt6Serial', 'Qt6Remote',
    'Qt6Help', 'Qt6Designer', 'Qt6Test',
    'Qt6Spatial', 'Qt6HttpServer',
    'Qt6OpenGL', 'QtOpenGL',
]

def should_keep(name, src):
    """检查目标名和源路径，任一匹配则排除"""
    combined = (name + '|' + str(src)).lower()
    for kw in exclude_keywords:
        if kw.lower() in combined:
            return False
    return True

before_b = len(a.binaries)
before_d = len(a.datas)

a.binaries = [b for b in a.binaries if should_keep(b[0], b[1])]
a.datas = [d for d in a.datas
           if should_keep(d[0], d[1])
           and not d[0].lower().startswith(('qml/', 'qml\\'))
           and not d[0].lower().startswith(('translations/', 'translations\\'))
           and not d[0].lower().startswith(('pyside6/translations', 'pyside6\\translations'))]

print(f"[过滤] binaries: {before_b} -> {len(a.binaries)}")
print(f"[过滤] datas: {before_d} -> {len(a.datas)}")

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='c_cleaner_plus',
    icon='app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    
    # 👇 关键修改 1：Strip 必须保持 False！(强行剔除签名极易被杀毒软件误杀并导致 DLL 损坏)
    strip=False,         
    
    # 👇 关键修改 2：满足你的心愿，开启 UPX 压缩！
    upx=True,            
    
    # 👇 关键修改 3：免死金牌名单！把报错的元凶和 C++ 底层核心全部保护起来
    upx_exclude=[
        # 1. 解决你报错截图的绝对元凶
        'python3.dll', 
        'python311.dll', 
        'python312.dll',
        
        # 2. 极其脆弱的 C++ 底层运行库（压了必崩）
        'vcruntime140.dll', 
        'vcruntime140_1.dll',
        'msvcp140.dll', 
        'msvcp140_1.dll', 
        'msvcp140_2.dll',
        'ucrtbase.dll',
        
        # 3. PySide6 图形界面核心（压了可能白屏或闪退）
        'shiboken6.dll', 
        'shiboken6.abi3.dll',
        'Qt6Core.dll', 
        'Qt6Gui.dll', 
        'Qt6Widgets.dll',
        'qwindows.dll'
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




