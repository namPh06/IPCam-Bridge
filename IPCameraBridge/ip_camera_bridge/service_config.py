"""Bounded service profiles. Passwords are written only as service-user DPAPI data."""
import copy
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import UUID, uuid4

from .config import atomic_write, validate_rtsp_url
from .windows_settings import MAX_CAMERAS, _crypt, _validate

MAX_JSON = 1024 * 1024
CONFIG_KEYS = {'version', 'revision', 'resolution', 'auto_connect', 'selected_id', 'cameras'}
CAMERA_KEYS = {'id', 'name', 'kind', 'address', 'username', 'password'}
INVALID = 'Cấu hình service không hợp lệ.'


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError(INVALID)


def validate_service_config(data):
    try:
        if not isinstance(data, dict) or set(data) != CONFIG_KEYS:
            raise ValueError
        if type(data['version']) is not int or data['version'] != 2:
            raise ValueError
        if type(data['revision']) is not int or not 0 <= data['revision'] < 2**53:
            raise ValueError
        if data['resolution'] not in ('720p', '1080p') or type(data['auto_connect']) is not bool:
            raise ValueError
        cameras = data['cameras']
        if not isinstance(cameras, list) or not 1 <= len(cameras) <= MAX_CAMERAS:
            raise ValueError
        ids = set()
        for camera in cameras:
            if not isinstance(camera, dict) or set(camera) != CAMERA_KEYS:
                raise ValueError
            _uuid(camera['id'])
            if camera['id'] in ids or camera['kind'] not in ('test', 'file', 'rtsp'):
                raise ValueError
            ids.add(camera['id'])
            for key, limit in (('name', 128), ('address', 4096), ('username', 1024), ('password', 1024)):
                value = camera[key]
                if not isinstance(value, str) or len(value) > limit or '\0' in value:
                    raise ValueError
            if not camera['name'].strip():
                raise ValueError
            if camera['kind'] == 'rtsp':
                validate_rtsp_url(camera['address'])
                if urlsplit(camera['address']).username is not None:
                    raise ValueError
            if camera['kind'] == 'file' and (not camera['address'] or '://' in camera['address']):
                raise ValueError
        if data['selected_id'] not in ids:
            raise ValueError
        if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_JSON:
            raise ValueError
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
        raise ValueError(INVALID) from None
    return copy.deepcopy(data)


def merge_update(current, update):
    try:
        if not isinstance(update, dict) or type(update.get('revision')) is not int:
            raise ValueError
        if update['revision'] != current['revision']:
            raise ValueError('Cấu hình đã thay đổi; hãy tải lại trước khi lưu.')
        result = copy.deepcopy(update)
        previous = {camera['id']: camera for camera in current['cameras']}
        for camera in result['cameras']:
            for key in ('password', 'address'):
                if camera[key] is None:
                    camera[key] = previous[camera['id']][key]
        result['revision'] += 1
        return validate_service_config(result)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValueError(INVALID) from None


def public_config(config):
    result = copy.deepcopy(config)
    for camera in result['cameras']:
        camera['has_password'] = bool(camera.pop('password'))
        if camera['kind'] == 'rtsp':
            parts = urlsplit(camera['address'])
            if parts.query or parts.fragment or parts.username is not None:
                camera['address_hint'] = urlunsplit(parts._replace(
                    netloc=parts.netloc.rsplit('@', 1)[-1], query='', fragment=''))
                camera['address'] = None
    return result


def migrate_profiles(legacy, resolution):
    try:
        _validate(legacy)
        cameras = []
        for original in legacy['cameras']:
            camera = {key: original[key] for key in ('name', 'kind', 'address', 'username', 'password')}
            camera['id'] = str(uuid4())
            if camera['kind'] == 'rtsp':
                parts = urlsplit(camera['address'])
                if parts.username is not None:
                    if not camera['username'] and not camera['password']:
                        camera['username'] = unquote(parts.username)
                        camera['password'] = unquote(parts.password or '')
                    camera['address'] = urlunsplit(parts._replace(netloc=parts.netloc.rsplit('@', 1)[-1]))
            cameras.append(camera)
        return validate_service_config({
            'version': 2, 'revision': 0, 'resolution': resolution,
            'auto_connect': legacy['auto_connect'], 'cameras': cameras,
            'selected_id': cameras[legacy['selected']]['id'],
        })
    except (ValueError, TypeError, KeyError, IndexError):
        raise ValueError(INVALID) from None


def save_service_config(path, data):
    try:
        path = Path(path)
        if not path.parent.is_dir():
            raise ValueError
        encoded = json.dumps(validate_service_config(data), ensure_ascii=False).encode('utf-8')
        atomic_write(path, _crypt(encoded))
    except (ValueError, OSError, UnicodeError):
        raise ValueError('Không lưu được cấu hình service đã mã hóa.') from None


def load_service_config(path):
    try:
        with Path(path).open('rb') as stream:
            encrypted = stream.read(MAX_JSON + 65537)
        if len(encrypted) > MAX_JSON + 65536:
            raise ValueError
        return validate_service_config(json.loads(_crypt(encrypted, decrypt=True)))
    except FileNotFoundError:
        return None
    except (ValueError, OSError, UnicodeError):
        raise ValueError('Không đọc được cấu hình service đã mã hóa.') from None
