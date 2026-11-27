# -*- mode: python ; coding: utf-8 -*-
# AliCore PyInstaller 打包配置（onedir）
#
# 包内只放 Python 代码与第三方依赖；用户数据默认在 %APPDATA%\AliCore，
# 不打进只读包。路径全部按 spec 文件所在目录推导，换盘符不用改。

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

_HERE = SPECPATH

datas = []
binaries = []
hiddenimports = []

# 带数据资源 / 子模块、且 PyInstaller 没有自带钩子的包，整体收齐。
for _pkg in ("jieba", "reportlab", "pptx", "docx", "pypdf", "PIL", "clr_loader", "pythonnet"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# agent 整包子模块（静态分析基本能看到，列上保险）。
hiddenimports += collect_submodules("alicore_agent")

# webview Windows 后端 + pythonnet 启动模块 + requests（平台后端是运行时按系统选的，要显式列）。
hiddenimports += [
    "clr",
    "webview",
    "webview.platforms.winforms",
    "requests",
]

a = Analysis(
    [os.path.join(_HERE, "ali_app.py")],
    pathex=[_HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="阿鲤",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                      # noconsole；入口已补 stdout/stderr=devnull 兜底
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(_HERE, "assets", "ali.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="阿鲤",
)
