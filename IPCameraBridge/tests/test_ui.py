import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ip_camera_bridge.ui import MainWindow
from ip_camera_bridge.windows_settings import load_profiles
from test_controller import hold_output


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_connect_preview_stop_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(config_path=Path(directory) / 'config.json', automation=False)
            window.show()
            self.assertEqual(window.resolution.currentData(), '720p')
            self.assertEqual(window.password.echoMode(), window.password.EchoMode.Password)
            window.toggle_source()
            deadline = time.monotonic() + 10
            ticks = 0
            while window.bridge.source_state != 'connected' and time.monotonic() < deadline:
                QTest.qWait(50)
                ticks += 1
            self.assertEqual(window.bridge.source_state, 'connected')
            self.assertGreater(ticks, 0)
            self.assertFalse(window.preview.pixmap().isNull())
            self.assertTrue(window.output_button.isEnabled())
            window.close()
            deadline = time.monotonic() + 5
            while window.isVisible() and time.monotonic() < deadline:
                QTest.qWait(50)
            self.assertFalse(window.isVisible())
            self.assertTrue(window.bridge.closed)
            self.assertTrue((Path(directory) / 'config.json').exists())


    def test_rtsp_fields_pass_raw_credentials_without_persisting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            window = MainWindow(config_path=config_path, automation=False)
            try:
                address = 'rtsp://camera.example:554/h264/media.amp?stream=1'
                username = ' fake@user%40 '
                password = ' fake@password%40 '
                window.source_kind.setCurrentIndex(window.source_kind.findData('rtsp'))
                window.url.setText(address)
                window.username.setText(username)
                window.password.setText(password)
                with patch.object(window.bridge.source, 'start') as start:
                    window.toggle_source()
                start.assert_called_once()
                spec = start.call_args.args[1][0]
                self.assertEqual(spec.kind, 'rtsp')
                self.assertEqual(spec.address, address)
                self.assertTrue(spec.username == username, 'Username must pass through unchanged')
                self.assertTrue(spec.password == password, 'Password must pass through unchanged')
            finally:
                window.close()
                window.refresh()
            self.assertTrue(window.bridge.closed)
            self.assertEqual(json.loads(config_path.read_text(encoding='utf-8')),
                             {'source_kind': 'rtsp', 'file_path': '', 'resolution': '720p'})
            self.assertTrue(all(field.text() == '' for field in (window.url, window.username, window.password)),
                            'Connection fields must be cleared on close')

    def test_multi_camera_fields_switch_without_encoding_or_losing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(config_path=Path(directory) / 'config.json', automation=False)
            try:
                window.source_kind.setCurrentIndex(window.source_kind.findData('rtsp'))
                window.url.setText('rtsp://camera.example/one')
                window.password.setText('fake@%40')
                window.add_camera()
                window.url.setText('rtsp://camera.example/two')
                window.password.setText('fake-two')
                window.camera_selector.setCurrentIndex(0)
                self.assertEqual(window.password.text(), 'fake@%40')
                self.assertEqual(window.url.text(), 'rtsp://camera.example/one')
                window.camera_selector.setCurrentIndex(1)
                self.assertEqual(window.password.text(), 'fake-two')
                window.remove_camera()
                self.assertEqual(len(window.group.cameras), 1)
                self.assertEqual(window.password.text(), 'fake@%40')
            finally:
                window.close()
                window.refresh()
            self.assertTrue(window.group.closed)
            self.assertTrue(all(not profile['password'] for profile in window.profiles))

    def test_saved_cameras_auto_connect_and_opt_out_removes_saved_data(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('ip_camera_bridge.ui.startup_enabled', return_value=False), \
                patch('ip_camera_bridge.ui.set_startup') as startup, \
                patch('ip_camera_bridge.cameras.run_output', hold_output):
            path = Path(directory) / 'config.json'
            window = MainWindow(config_path=path)
            window.add_camera()
            window.source_kind.setCurrentIndex(0)
            window.password.setText('fake@%40')
            window.remember.setChecked(True)
            window.auto_connect.setChecked(True)
            window.start_windows.setChecked(True)
            self.assertTrue(window.save_settings())
            startup.assert_called_with(True)
            window.close()
            window.refresh()
            restored = MainWindow(config_path=path)
            try:
                self.assertEqual(restored.group.selected, 1)
                self.assertEqual(restored.password.text(), 'fake@%40')
                deadline = time.monotonic() + 10
                while restored.group.output_state != 'running' and time.monotonic() < deadline:
                    QTest.qWait(50)
                self.assertEqual([camera.source_state for camera in restored.group.cameras], ['connected'] * 2)
                self.assertEqual(restored.group.output_state, 'running')
                restored.camera_selector.setCurrentIndex(0)
                self.assertTrue(restored.save_settings())
                self.assertEqual(load_profiles(restored.profiles_path)['selected'], 0)
                restored.remember.setChecked(False)
                self.assertFalse(restored.auto_connect.isChecked())
                with patch('ip_camera_bridge.ui.startup_enabled', return_value=True):
                    restored.start_windows.setChecked(False)
                    self.assertTrue(restored.save_settings())
                startup.assert_called_with(False)
                self.assertFalse(restored.profiles_path.exists())
            finally:
                restored.close()
                deadline = time.monotonic() + 5
                while not restored.group.closed and time.monotonic() < deadline:
                    QTest.qWait(50)
            self.assertTrue(restored.group.closed)


if __name__ == '__main__':
    unittest.main()
