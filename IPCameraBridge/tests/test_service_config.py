import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ip_camera_bridge import service_config as config


def camera_config():
    return {'version': 2, 'revision': 0, 'resolution': '720p', 'auto_connect': True,
            'selected_id': '00000000-0000-4000-8000-000000000001', 'cameras': [
                {'id': '00000000-0000-4000-8000-000000000001', 'name': 'One',
                 'kind': 'rtsp', 'address': 'rtsp://camera.example/live',
                 'username': 'fake-user', 'password': 'fake@%40'},
                {'id': '00000000-0000-4000-8000-000000000002', 'name': 'Two',
                 'kind': 'test', 'address': '', 'username': '', 'password': 'second'},
            ]}


class ServiceConfigTests(unittest.TestCase):
    def test_reorder_preserves_password_by_id_and_rejects_stale_revision(self):
        original = camera_config()
        update = copy.deepcopy(original)
        update['cameras'].reverse()
        for item in update['cameras']:
            item.update(password=None, address=None)
        merged = config.merge_update(original, update)
        self.assertEqual([c['password'] for c in merged['cameras']], ['second', 'fake@%40'])
        self.assertEqual(merged['revision'], 1)
        self.assertEqual(original['revision'], 0)
        with self.assertRaises(ValueError):
            config.merge_update(merged, update)
        update['cameras'][0]['id'] = '00000000-0000-4000-8000-000000000003'
        with self.assertRaises(ValueError):
            config.merge_update(original, update)
        update = copy.deepcopy(original)
        update['cameras'][0]['password'] = ''
        self.assertEqual(config.merge_update(original, update)['cameras'][0]['password'], '')

    def test_service_config_keeps_secret_out_of_public_data_and_disk(self):
        original = camera_config()
        original['cameras'][0]['address'] += '?opaque=secret-query'
        public = config.public_config(original)
        encoded = json.dumps(public)
        for secret in ('fake@%40', 'second', 'secret-query'):
            self.assertNotIn(secret, encoded)
        self.assertIsNone(public['cameras'][0]['address'])
        self.assertEqual(public['cameras'][0]['address_hint'], 'rtsp://camera.example/live')
        self.assertTrue(public['cameras'][0]['has_password'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cameras.dat'
            config.save_service_config(path, original)
            before = path.read_bytes()
            self.assertNotIn(b'fake@%40', before)
            self.assertEqual(config.load_service_config(path), original)
            with patch('ip_camera_bridge.config.os.replace', side_effect=OSError('fake@%40')):
                with self.assertRaises(ValueError) as error:
                    config.save_service_config(path, original)
            self.assertNotIn('fake@%40', str(error.exception))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            path.write_bytes(b'not encrypted')
            with self.assertRaises(ValueError):
                config.load_service_config(path)

    def test_schema_rejects_ambiguous_or_oversized_data_without_echo(self):
        cases = []
        for key, value in (('version', True), ('revision', True), ('resolution', '4k'),
                           ('selected_id', 'invalid'), ('auto_connect', 1), ('unknown', 'secret')):
            item = camera_config()
            item[key] = value
            cases.append(item)
        item = camera_config()
        item['cameras'] = [item['cameras'][0]] * 17
        cases.append(item)
        item = camera_config()
        item['cameras'][1]['id'] = item['cameras'][0]['id']
        cases.append(item)
        for key, value in (('name', 'n' * 129), ('password', 's' * 1025),
                           ('address', 'rtsp://user:secret@host/live'), ('kind', 'shell')):
            item = camera_config()
            item['cameras'][0][key] = value
            cases.append(item)
        for item in cases:
            with self.subTest(case=len(str(item))):
                with self.assertRaises(ValueError) as error:
                    config.validate_service_config(item)
                self.assertNotIn('secret', str(error.exception))

    def test_migration_decodes_embedded_credentials_once_and_keeps_raw_fields(self):
        legacy = {'version': 1, 'selected': 0, 'auto_connect': True, 'cameras': [
            {'name': 'Legacy', 'kind': 'rtsp',
             'address': 'rtsp://fake-user:p%40%2540@camera.example/live',
             'username': '', 'password': ''}]}
        migrated = config.migrate_profiles(legacy, '1080p')
        self.assertEqual(migrated['cameras'][0]['password'], 'p@%40')
        self.assertEqual(migrated['cameras'][0]['username'], 'fake-user')
        self.assertEqual(migrated['cameras'][0]['address'], 'rtsp://camera.example/live')
        self.assertEqual(migrated['selected_id'], migrated['cameras'][0]['id'])
        legacy['cameras'][0].update(username='new', password='raw@%40')
        self.assertEqual(config.migrate_profiles(legacy, '720p')['cameras'][0]['password'], 'raw@%40')
