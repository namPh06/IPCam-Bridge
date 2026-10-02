"""Opt-in, per-user Windows startup and DPAPI-protected camera profiles."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import winreg

from .config import atomic_write

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'IPCameraBridge'
MAX_CAMERAS = 16
APP_ID = 'IPCameraBridge.Desktop'


def set_app_id():
    """Give source and packaged UI the same Windows taskbar identity."""
    operation = ctypes.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
    operation.argtypes = [wintypes.LPCWSTR]
    operation.restype = wintypes.LONG
    return operation(APP_ID)


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = _Blob(len(data), buffer), _Blob()
    # UI_FORBIDDEN only: do NOT use LOCAL_MACHINE (other users could decrypt).
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('Windows không mã hóa/giải mã được danh sách camera.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        if decrypt:
            ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(target.data)


def _validate(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Danh sách camera không hợp lệ.')
    cameras = data.get('cameras')
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= MAX_CAMERAS:
        raise ValueError('Danh sách camera không hợp lệ.')
    for camera in cameras:
        if not isinstance(camera, dict) or camera.get('kind') not in ('test', 'file', 'rtsp'):
            raise ValueError('Loại camera không hợp lệ.')
        if any(not isinstance(camera.get(key), str) for key in ('name', 'address', 'username', 'password')):
            raise ValueError('Thông tin camera không hợp lệ.')
    selected = data.get('selected')
    if type(selected) is not int or not 0 <= selected < len(cameras) or type(data.get('auto_connect')) is not bool:
        raise ValueError('Tùy chọn camera không hợp lệ.')
    return data


def save_profiles(path, data):
    encrypted = _crypt(json.dumps(_validate(data), ensure_ascii=False).encode('utf-8'))
    atomic_write(path, encrypted)  # Even the temporary file contains ciphertext only.


def load_profiles(path):
    try:
        encrypted = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    try:
        return _validate(json.loads(_crypt(encrypted, decrypt=True)))
    except (ValueError, OSError):
        raise ValueError('Không đọc được camera đã lưu. Cần đúng tài khoản Windows trên máy đã lưu; hãy nhập và lưu lại.') from None


def startup_command():
    executable = Path(sys.executable).resolve()
    if getattr(sys, 'frozen', False):
        args = [str(executable)]
    else:
        windowed = executable.with_name('pythonw.exe')
        args = [str(windowed if windowed.exists() else executable),
                str(Path(__file__).resolve().parents[1] / 'run.py')]
    command = subprocess.list2cmdline(args)
    if len(command) > 260:
        raise ValueError('Đường dẫn quá dài để tự khởi động. Hãy đặt ứng dụng trong thư mục ngắn hơn.')
    return command


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, RUN_NAME)[0])
    except FileNotFoundError:
        return False


def set_startup(enabled):
    if enabled:
        command = startup_command()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass
