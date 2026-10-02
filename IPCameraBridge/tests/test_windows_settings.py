import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
import ctypes
from ctypes import wintypes

from ip_camera_bridge import windows_settings as settings


class WindowsSettingsTests(unittest.TestCase):
    def test_taskbar_identity_matches_installer_shortcuts(self):
        self.assertEqual(settings.set_app_id(), 0)
        shell = ctypes.WinDLL('shell32')
        operation = shell.GetCurrentProcessExplicitAppUserModelID
        operation.argtypes = [ctypes.POINTER(wintypes.LPWSTR)]
        operation.restype = wintypes.LONG
        app_id = wintypes.LPWSTR()
        self.assertEqual(operation(ctypes.byref(app_id)), 0)
        try:
            self.assertEqual(app_id.value, settings.APP_ID)
        finally:
            free = ctypes.WinDLL('ole32').CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = None
            free(ctypes.cast(app_id, ctypes.c_void_p))
        installer = (Path(settings.__file__).resolve().parents[1] / 'installer.iss').read_text(encoding='utf-8')
        self.assertEqual(installer.count(f'AppUserModelID: "{settings.APP_ID}"'), 2)

    def test_real_dpapi_roundtrip_and_atomic_failure_preserve_secrets(self):
        profile = {'version': 1, 'selected': 0, 'auto_connect': True, 'cameras': [
            {'name': 'Test camera', 'kind': 'rtsp', 'address': 'rtsp://camera.example/live',
             'username': 'fake-user', 'password': 'fake-secret@%40'},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cameras.dat'
            settings.save_profiles(path, profile)
            before = path.read_bytes()
            for private in (b'fake-user', b'fake-secret', b'camera.example'):
                self.assertNotIn(private, before)
            self.assertEqual(settings.load_profiles(path), profile)
            with patch('ip_camera_bridge.config.os.replace', side_effect=OSError('Test')):
                with self.assertRaises(OSError):
                    settings.save_profiles(path, profile)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            path.write_bytes(b'not encrypted')
            with self.assertRaises(ValueError):
                settings.load_profiles(path)

    def test_startup_changes_only_our_current_user_value_with_quoted_executable(self):
        registry = MagicMock()
        with patch.object(settings, 'winreg', registry), patch.object(settings.sys, 'frozen', True, create=True), \
             patch.object(settings.sys, 'executable', r'C:\My Apps\Camera Bridge.exe'):
            settings.set_startup(True)
            self.assertEqual(registry.CreateKeyEx.call_args.args[0], registry.HKEY_CURRENT_USER)
            self.assertEqual(registry.CreateKeyEx.call_args.args[1], settings.RUN_KEY)
            self.assertEqual(registry.SetValueEx.call_args.args[1], settings.RUN_NAME)
            command = registry.SetValueEx.call_args.args[-1]
            self.assertEqual(command, '"C:\\My Apps\\Camera Bridge.exe"')
            self.assertNotIn('_MEI', command)
            settings.set_startup(False)
            self.assertEqual(registry.DeleteValue.call_args.args[1], settings.RUN_NAME)

    def test_source_startup_uses_absolute_pythonw_and_run_script(self):
        command = settings.startup_command()
        self.assertIn('pythonw.exe', command)
        self.assertIn(str(Path(settings.__file__).resolve().parents[1] / 'run.py'), command)
