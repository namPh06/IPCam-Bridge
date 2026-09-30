"""Editable RTSP templates; credentials are handled separately by the source."""
from ipaddress import ip_address
from urllib.parse import urlsplit

from .config import validate_rtsp_url

DEFAULT_PATH = '/Src/MediaInput/stream_1'


def expand_rtsp_address(value, path=DEFAULT_PATH):
    value = value.strip()
    if not value:
        return value
    if '://' in value:
        validate_rtsp_url(value)
        return value
    try:
        address = ip_address(value)
        host = f'[{address}]' if address.version == 6 else str(address)
        port = 554
    except ValueError:
        parts = urlsplit('//' + value)
        if parts.username is not None or parts.path or parts.query or parts.fragment:
            raise ValueError('Nhập IP, IP:cổng hoặc URL RTSP đầy đủ.')
        address = ip_address(parts.hostname or '')
        host = f'[{address}]' if address.version == 6 else str(address)
        port = parts.port if parts.port is not None else 554
    if not path.startswith('/') or any(char in path for char in ('\r', '\n', '#', '@')):
        raise ValueError('Đường dẫn mẫu phải bắt đầu bằng / và không chứa thông tin đăng nhập.')
    result = f'rtsp://{host}:{port}{path}'
    validate_rtsp_url(result)
    return result
