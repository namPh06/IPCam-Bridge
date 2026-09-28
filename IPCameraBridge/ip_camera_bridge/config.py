"""Validate transient credentials and persist only non-sensitive preferences."""

import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit


DEFAULT_CONFIG = {'source_kind': 'test', 'file_path': '', 'resolution': '720p'}


def validate_rtsp_url(address):
    """Reject malformed/non-RTSP input without putting it in an error message."""
    try:
        if not isinstance(address, str) or not address or any(c.isspace() or ord(c) < 32 for c in address):
            raise ValueError
        parts = urlsplit(address)
        if parts.scheme.lower() not in ('rtsp', 'rtsps') or not parts.hostname or '\\' in parts.netloc:
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Địa chỉ RTSP không hợp lệ. Dùng rtsp:// hoặc rtsps:// với tên máy/IP và cổng hợp lệ.') from None


def build_rtsp_url(address, username='', password=''):
    validate_rtsp_url(address)
    if not username and not password:
        return address
    parts = urlsplit(address)
    authority = parts.netloc.rsplit('@', 1)[-1]
    userinfo = quote(username, safe='') + ':' + quote(password, safe='')
    return urlunsplit(parts._replace(netloc=userinfo + '@' + authority))


def redact(text):
    """Mask URL userinfo and common secret query parameters in diagnostic text."""
    text = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^\s]*@', r'\1***@', str(text))

    def mask_parameter(match):
        key = unquote_plus(match[2]).casefold().replace('-', '_')
        sensitive = key in ('pass', 'pwd', 'key', 'sig', 'session', 'sessionid') or any(
            name in key for name in ('password', 'passwd', 'token', 'secret', 'auth', 'credential', 'api_key', 'apikey', 'signature')
        )
        return match[1] + match[2] + '=' + ('***' if sensitive else match[3])

    return re.sub(r'([?&;])([^\s=&;?#]+)=([^\s&;#]*)', mask_parameter, text)


def _preferences(data):
    result = DEFAULT_CONFIG.copy()
    if isinstance(data, dict):
        if data.get('source_kind') in ('test', 'file', 'rtsp'):
            result['source_kind'] = data['source_kind']
        if data.get('resolution') in ('720p', '1080p'):
            result['resolution'] = data['resolution']
        file_path = data.get('file_path')
        if isinstance(file_path, str) and '://' not in file_path and not any(ord(c) < 32 for c in file_path):
            result['file_path'] = file_path
    return result


def load_config(path):
    try:
        return _preferences(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, TypeError):
        return DEFAULT_CONFIG.copy()


def save_config(path, data):
    atomic_write(path, (json.dumps(_preferences(data), ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class _SafeFormatter(logging.Formatter):
    def format(self, record):
        safe = copy.copy(record)
        safe.msg = redact(record.getMessage())
        safe.args = ()
        # Native decoder exceptions can contain credentials outside a URL.
        safe.exc_info = safe.exc_text = safe.stack_info = None
        return super().format(safe)


def setup_logging(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('ip_camera_bridge')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(_SafeFormatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    return logger
