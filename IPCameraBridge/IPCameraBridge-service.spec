# A service must use a permanent onedir payload, never a onefile temp directory.
import pythoncom
import pywintypes

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[(pythoncom.__file__, '.'), (pywintypes.__file__, '.')],
    datas=[],
    hiddenimports=[
        'servicemanager', 'win32serviceutil', 'win32service', 'win32timezone',
        'win32api', 'win32con', 'win32event', 'win32file', 'win32job',
        'win32pipe', 'win32process', 'win32security', 'win32com.client',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='IPCameraBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='IPCameraBridge')
