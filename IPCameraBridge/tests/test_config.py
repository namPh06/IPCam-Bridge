import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ip_camera_bridge.config import build_rtsp_url, load_config, redact, save_config, setup_logging, validate_rtsp_url


class ConfigTests(unittest.TestCase):
    def test_rtsp_credentials_are_quoted_without_changing_ipv6_or_query(self):
        self.assertEqual(
            build_rtsp_url('rtsps://[2001:db8::1]:8554/live?channel=2', 'a@b', 'p:/?# %'),
            'rtsps://a%40b:p%3A%2F%3F%23%20%25@[2001:db8::1]:8554/live?channel=2',
        )
        self.assertEqual(build_rtsp_url('rtsp://old:secret@camera/live', 'new', 'pass'),
                         'rtsp://new:pass@camera/live')
        self.assertEqual(build_rtsp_url('rtsp://old:secret@camera/live'), 'rtsp://old:secret@camera/live')

    def test_raw_credentials_are_encoded_once_including_literal_percent_sequences(self):
        for username, password, userinfo in (
            ('fake@user', 'fake@password', 'fake%40user:fake%40password'),
            ('fake%40user', 'fake%40password', 'fake%2540user:fake%2540password'),
        ):
            self.assertTrue(
                build_rtsp_url('rtsp://camera.example/live', username, password)
                == f'rtsp://{userinfo}@camera.example/live',
                'Raw credentials must be quoted once without decoding percent sequences',
            )

    def test_replacing_url_credentials_preserves_address_path_and_query(self):
        address = 'rtsp://old-fake:old-fake@camera.example:554/h264/media.amp?stream=1&name=main%20stream'
        self.assertTrue(
            build_rtsp_url(address, 'fake-user', 'fake@password')
            == 'rtsp://fake-user:fake%40password@camera.example:554/h264/media.amp?stream=1&name=main%20stream',
            'Replacing userinfo must preserve the camera address, path, and query',
        )

    def test_bad_urls_are_rejected_without_echoing_input(self):
        for address in ('http://camera/live', 'rtsp:///live', 'rtsp://camera:99999',
                        'rtsp://[broken', 'rtsp://camera\n/live', 'rtsp://camera\\evil/live', '', None):
            with self.subTest(address=address), self.assertRaises(ValueError) as raised:
                validate_rtsp_url(address)
            self.assertNotIn(str(address), str(raised.exception)) if address else None

    def test_redaction_covers_userinfo_and_encoded_secret_query_keys(self):
        value = redact('Failed rtsp://alice:p%40ss@[::1]:554/live?channel=2&token=abc&pa%73sword=pw&api_key=key and https://bob:secret@host/a?access_token=xyz')
        for secret in ('alice', 'p%40ss', 'abc', '=pw', '=key', 'bob', 'secret@', 'xyz'):
            self.assertNotIn(secret, value)
        self.assertIn('channel=2', value)
        self.assertIn('[::1]:554/live', value)

    def test_disk_config_never_saves_connection_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(path, {'source_kind': 'rtsp', 'file_path': 'C:/clip.mp4', 'resolution': '1080p',
                               'url': 'rtsp://secret:pass@camera', 'username': 'secret', 'password': 'pass'})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),
                             {'source_kind': 'rtsp', 'file_path': 'C:/clip.mp4', 'resolution': '1080p'})
            self.assertEqual(load_config(path)['resolution'], '1080p')
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(load_config(path), {'source_kind': 'test', 'file_path': '', 'resolution': '720p'})
            path.write_text('{"source_kind":"bad","resolution":null,"file_path":42,"password":"secret"}', encoding='utf-8')
            self.assertEqual(load_config(path), {'source_kind': 'test', 'file_path': '', 'resolution': '720p'})

    def test_atomic_save_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(path, {'resolution': '720p'})
            before = path.read_bytes()
            with patch('ip_camera_bridge.config.os.replace', side_effect=OSError('test')):
                with self.assertRaises(OSError):
                    save_config(path, {'resolution': '1080p'})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['config.json'])

    def test_log_redacts_formatted_messages_and_tracebacks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.log'
            logger = setup_logging(path)
            try:
                logger.warning('Address: %s', 'rtsp://alice:secret@camera/live?token=sensitive')
                try:
                    raise ValueError('raw-private-payload')
                except ValueError:
                    logger.exception('Connection failed')
                for handler in logger.handlers:
                    handler.flush()
                content = path.read_text(encoding='utf-8')
                for secret in ('alice', 'secret', 'sensitive', 'raw-private-payload'):
                    self.assertNotIn(secret, content)
                self.assertIn('Connection failed', content)
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()



class WindowsConfigTests(unittest.TestCase):
    def test_windows_utf8_bom_config_preserves_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"source_kind":"file","resolution":"1080p","file_path":"demo.mp4"}', encoding='utf-8-sig')
            self.assertEqual(load_config(path)['resolution'], '1080p')

if __name__ == '__main__':
    unittest.main()
